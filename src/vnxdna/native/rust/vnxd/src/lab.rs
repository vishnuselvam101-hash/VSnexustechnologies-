//! Future physical-DNA adapter INTERFACE `vnx.dna-lab/1` (master directive §13 D; docs/V14_MEDIA_ADAPTERS.md §5).
//!
//! STATUS: interface + simulator + conformance tests only. No synthesis vendor, sequencer or laboratory system is
//! connected; no DNA has been synthesised, stored, retrieved or sequenced through it; nothing here is physically
//! validated. The interface names no vendor command and no vendor API: a real implementation maps these calls onto
//! whatever a provider actually offers (an order portal, a LIMS, an instrument's control software) and must be
//! written against that provider's own documentation.
//!
//! What the interface models (sources in docs/V14_MEDIA_ADAPTERS.md §5.1):
//!
//! * **synthesis jobs**: an order of named strands (A/C/G/T, within the provider's length limits; oligo-pool
//!   providers publish limits such as 20–350 nt or 40–350 nt) for one archive; the job yields a stored *pool*;
//! * **pools**: id, archive id, strand count, physical location (site, container, position), preservation metadata
//!   (method and storage condition as recorded by the lab), lifecycle (`ordered`, `stored`, `in-retrieval`,
//!   `depleted`, `destroyed`), retrievals done and aliquots left as the lab reports them;
//! * **retrieval jobs**: take material out of a pool (all of it, or the strands one primer pair selects) and yield a
//!   *sample*; every retrieval consumes an aliquot;
//! * **sequencing jobs**: sequence a sample on a platform, yield a *read delivery*: files (FASTQ, gzipped FASTQ,
//!   BAM, POD5 raw signal, or a BCL run folder) with SHA-256 and size, plus quality metrics;
//! * **job lifecycle**: `submitted -> queued -> running -> finishing -> completed`, or `failed` / `cancelled`
//!   (the run states mirror those of instrument control software, e.g. starting/running/finishing/completed);
//!   [`JobState::can_move_to`] is the state machine; `cancel` and `retry` (a new job linked to the failed one, only
//!   for a retryable [`EquipmentError`]);
//! * **equipment errors**: typed, with a retryable flag;
//! * **reconstruction handoff**: [`LabMedia`] verifies each delivered file against its announced SHA-256 and hands
//!   the reads to the VNX-DNA decoder ([`Codec::decode`]); only bytes that decode to the recorded SHA-256 return;
//! * **lab health**: online / degraded / offline with per-instrument state.
//!
//! [`SimulatedLab`] implements the interface in software: virtual time (every status poll advances a job by one
//! state), seeded equipment failures, aliquot accounting, reads from [`crate::codec::simple_channel`]. All its
//! output is labelled SIMULATED. [`LabMedia`] is the media adapter over any [`DnaLab`] and a [`Codec`]; it refuses a
//! lab that is not simulated, because media contract v1 has no durability class for physical DNA (adding one is a
//! versioned contract change for the phase that connects real equipment).

use crate::addr::GenerationRef;
use crate::codec::{frame, metadata_only, simple_channel, unframe, Codec, Envelope, PoolFile, FRAME_OVERHEAD};
use crate::digest::{hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::fault::SplitMix64;
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const INTERFACE: &str = "vnx.dna-lab/1";

// ------------------------------------------------------------------------------------------------------ values

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ReadFormat {
    Fastq,
    FastqGz,
    Bam,
    /// Oxford Nanopore raw signal (Apache Arrow based); needs basecalling before decoding
    Pod5,
    /// an Illumina run folder of BCL files; needs conversion to FASTQ before decoding
    BclRunFolder,
}

impl ReadFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            ReadFormat::Fastq => "fastq",
            ReadFormat::FastqGz => "fastq.gz",
            ReadFormat::Bam => "bam",
            ReadFormat::Pod5 => "pod5",
            ReadFormat::BclRunFolder => "bcl-run-folder",
        }
    }

    /// Whether the VNX-DNA decoder reads this format directly (others need basecalling or conversion first).
    pub fn decodable(self) -> bool {
        matches!(self, ReadFormat::Fastq)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Platform {
    /// short-read sequencing by synthesis (Illumina-class)
    ShortRead,
    /// nanopore sequencing
    Nanopore,
    Other(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LabCapabilities {
    pub name: String,
    /// true for a software simulation; a real lab adapter reports false
    pub simulated: bool,
    pub min_strand_nt: usize,
    pub max_strand_nt: usize,
    pub max_strands_per_order: usize,
    pub platforms: Vec<Platform>,
    pub read_formats: Vec<ReadFormat>,
    /// retrieval of a primer-selected subset (random access) is offered
    pub primer_selection: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Strand {
    /// unique within the order: 1..=64 of [A-Za-z0-9._-]
    pub name: String,
    pub sequence: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SynthesisOrder {
    /// the client's reference, 1..=64 of [A-Za-z0-9._-]
    pub order_ref: String,
    pub archive_id: String,
    pub strands: Vec<Strand>,
}

fn valid_name(s: &str) -> bool {
    (1..=64).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

impl SynthesisOrder {
    /// Check the order against a lab's capabilities (INVALID_ARGUMENT, nothing submitted).
    pub fn validate(&self, caps: &LabCapabilities) -> Result<()> {
        let bad = |m: String| err(ErrorCode::InvalidArgument, format!("synthesis order {}: {m}", self.order_ref));
        if !valid_name(&self.order_ref) || !valid_name(&self.archive_id) {
            return bad("order_ref and archive_id must be 1..=64 of [A-Za-z0-9._-]".into());
        }
        if self.strands.is_empty() || self.strands.len() > caps.max_strands_per_order {
            return bad(format!("{} strands; the lab accepts 1..={}", self.strands.len(), caps.max_strands_per_order));
        }
        let mut seen = std::collections::BTreeSet::new();
        for s in &self.strands {
            if !valid_name(&s.name) || !seen.insert(s.name.as_str()) {
                return bad(format!("strand name {:?} is invalid or repeated", s.name));
            }
            let n = s.sequence.len();
            if n < caps.min_strand_nt || n > caps.max_strand_nt {
                return bad(format!("strand {} has {n} nt; the lab accepts {}..={}", s.name, caps.min_strand_nt, caps.max_strand_nt));
            }
            if !s.sequence.bytes().all(|b| matches!(b, b'A' | b'C' | b'G' | b'T')) {
                return bad(format!("strand {} has bases outside ACGT", s.name));
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct JobId(pub String);
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct PoolId(pub String);
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct SampleId(pub String);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum JobKind {
    Synthesis,
    Retrieval,
    Sequencing,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum JobState {
    Submitted,
    Queued,
    Running,
    Finishing,
    Completed,
    Failed,
    Cancelled,
}

impl JobState {
    pub fn as_str(self) -> &'static str {
        match self {
            JobState::Submitted => "submitted",
            JobState::Queued => "queued",
            JobState::Running => "running",
            JobState::Finishing => "finishing",
            JobState::Completed => "completed",
            JobState::Failed => "failed",
            JobState::Cancelled => "cancelled",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, JobState::Completed | JobState::Failed | JobState::Cancelled)
    }

    /// The job state machine. Progress is forward only; a job can fail from any non-terminal state; it can be
    /// cancelled until it is finishing (results are being written); terminal states never change.
    pub fn can_move_to(self, to: JobState) -> bool {
        use JobState::*;
        matches!(
            (self, to),
            (Submitted, Queued) | (Queued, Running) | (Running, Finishing) | (Finishing, Completed)
                | (Submitted | Queued | Running | Finishing, Failed)
                | (Submitted | Queued | Running, Cancelled)
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EquipmentErrorKind {
    /// the instrument stopped with a fault
    InstrumentFault,
    /// reagents, flow cells or other consumables ran out
    ConsumableExhausted,
    /// the run's quality control failed
    QcFailed,
    /// temperature or another environmental condition went out of range
    EnvironmentOutOfRange,
    /// the controller lost contact with the instrument or the provider
    CommunicationLost,
    /// stopped by an operator
    OperatorAbort,
    /// the material is gone (lost, destroyed, used up)
    SampleLost,
    /// the provider refused the order or request
    Rejected,
    Unknown,
}

impl EquipmentErrorKind {
    pub fn as_str(self) -> &'static str {
        match self {
            EquipmentErrorKind::InstrumentFault => "instrument-fault",
            EquipmentErrorKind::ConsumableExhausted => "consumable-exhausted",
            EquipmentErrorKind::QcFailed => "qc-failed",
            EquipmentErrorKind::EnvironmentOutOfRange => "environment-out-of-range",
            EquipmentErrorKind::CommunicationLost => "communication-lost",
            EquipmentErrorKind::OperatorAbort => "operator-abort",
            EquipmentErrorKind::SampleLost => "sample-lost",
            EquipmentErrorKind::Rejected => "rejected",
            EquipmentErrorKind::Unknown => "unknown",
        }
    }

    /// Whether running the same job again can succeed.
    pub fn retryable(self) -> bool {
        matches!(
            self,
            EquipmentErrorKind::InstrumentFault
                | EquipmentErrorKind::ConsumableExhausted
                | EquipmentErrorKind::QcFailed
                | EquipmentErrorKind::EnvironmentOutOfRange
                | EquipmentErrorKind::CommunicationLost
                | EquipmentErrorKind::OperatorAbort
        )
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EquipmentError {
    pub kind: EquipmentErrorKind,
    pub detail: String,
}

impl EquipmentError {
    /// The storage-service error a failed job becomes: retryable faults are BACKEND_UNAVAILABLE; lost material is
    /// CORRUPTION (the data cannot be read back); a rejection is INVALID_ARGUMENT; anything unknown is INTERNAL.
    pub fn to_vnx(&self) -> VnxError {
        let code = if self.kind.retryable() {
            ErrorCode::BackendUnavailable
        } else {
            match self.kind {
                EquipmentErrorKind::SampleLost => ErrorCode::Corruption,
                EquipmentErrorKind::Rejected => ErrorCode::InvalidArgument,
                _ => ErrorCode::Internal,
            }
        };
        VnxError::new(code, format!("lab equipment error {}: {}", self.kind.as_str(), self.detail))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct JobStatus {
    pub id: JobId,
    pub kind: JobKind,
    pub state: JobState,
    /// 0..=1000
    pub progress_permille: u16,
    pub error: Option<EquipmentError>,
    /// the failed job this one retries
    pub retry_of: Option<JobId>,
    /// every state the job went through, in order
    pub history: Vec<JobState>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PhysicalLocation {
    pub site: String,
    pub container: String,
    pub position: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PreservationMetadata {
    /// as recorded by the lab, e.g. "dried", "in solution", "encapsulated" (free text, never inferred)
    pub method: String,
    /// as recorded by the lab, e.g. "-20 C freezer" (free text)
    pub storage_condition: String,
    /// seconds since the Unix epoch when the pool was put into storage (virtual time in the simulator)
    pub stored_at: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PoolLifecycle {
    Ordered,
    Stored,
    InRetrieval,
    Depleted,
    Destroyed,
}

impl PoolLifecycle {
    pub fn as_str(self) -> &'static str {
        match self {
            PoolLifecycle::Ordered => "ordered",
            PoolLifecycle::Stored => "stored",
            PoolLifecycle::InRetrieval => "in-retrieval",
            PoolLifecycle::Depleted => "depleted",
            PoolLifecycle::Destroyed => "destroyed",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QualityMetrics {
    pub reads: u64,
    pub bases: u64,
    /// mean Phred quality x 100
    pub mean_phred_x100: u32,
    /// bases with Phred >= 30, per mille
    pub q30_permille: u16,
    /// true when the numbers come from a simulation
    pub simulated: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PoolRecord {
    pub id: PoolId,
    pub archive_id: String,
    pub order_ref: String,
    pub strands: usize,
    pub location: PhysicalLocation,
    pub preservation: PreservationMetadata,
    pub lifecycle: PoolLifecycle,
    pub retrievals: u32,
    /// aliquots left, as the lab reports them (None = not reported)
    pub aliquots_left: Option<u32>,
    pub synthesis_job: JobId,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Selection {
    All,
    /// the strands a primer pair amplifies (random access)
    PrimerPair { forward: String, reverse: String },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RetrievalRequest {
    pub pool: PoolId,
    pub selection: Selection,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SequencingRequest {
    pub sample: SampleId,
    pub platform: Platform,
    /// reads per strand of the pool the lab should aim for (coverage)
    pub coverage: u32,
    pub format: ReadFormat,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeliveredFile {
    pub name: String,
    pub format: ReadFormat,
    pub sha256: [u8; 32],
    pub bytes: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReadDelivery {
    pub job: JobId,
    pub run_id: String,
    pub files: Vec<DeliveredFile>,
    pub quality: QualityMetrics,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LabState {
    Online,
    Degraded,
    Offline,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LabHealth {
    pub state: LabState,
    /// (instrument name, state)
    pub instruments: Vec<(String, LabState)>,
    pub detail: String,
}

/// The interface a physical DNA lab (or its simulator) implements. Every call is quick: work happens in jobs whose
/// status is polled. Errors: INVALID_ARGUMENT (bad request), NOT_FOUND (unknown id), BACKEND_UNAVAILABLE /
/// BACKEND_BUSY (the lab cannot take the call now; retryable).
pub trait DnaLab: Send + Sync {
    fn capabilities(&self) -> LabCapabilities;
    fn health(&self) -> Result<LabHealth>;
    fn submit_synthesis(&self, order: &SynthesisOrder) -> Result<JobId>;
    fn submit_retrieval(&self, req: &RetrievalRequest) -> Result<JobId>;
    fn submit_sequencing(&self, req: &SequencingRequest) -> Result<JobId>;
    fn status(&self, job: &JobId) -> Result<JobStatus>;
    /// Cancel a job that has not reached `finishing`; INVALID_ARGUMENT otherwise.
    fn cancel(&self, job: &JobId) -> Result<()>;
    /// Re-run a failed job whose error is retryable; returns the new job (its `retry_of` names the old one).
    fn retry(&self, job: &JobId) -> Result<JobId>;
    /// The pool a completed synthesis job produced.
    fn synthesis_result(&self, job: &JobId) -> Result<PoolId>;
    /// The sample a completed retrieval job produced.
    fn retrieval_result(&self, job: &JobId) -> Result<SampleId>;
    /// The read delivery of a completed sequencing job.
    fn read_data(&self, job: &JobId) -> Result<ReadDelivery>;
    /// Download one delivered file (the caller verifies its SHA-256 against the delivery).
    fn fetch(&self, job: &JobId, file: &str) -> Result<Vec<u8>>;
    fn pool(&self, id: &PoolId) -> Result<PoolRecord>;
    fn pools(&self) -> Result<Vec<PoolId>>;
    /// Destroy a pool's material (lifecycle `destroyed`); its record stays.
    fn destroy_pool(&self, id: &PoolId) -> Result<()>;
}

// -------------------------------------------------------------------------------------------- the simulator

/// Simulated lab settings (all SIMULATED model inputs).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SimLabConfig {
    pub seed: u64,
    /// per job run (decided when the job starts running), parts per million
    pub synthesis_failure_ppm: u32,
    pub retrieval_failure_ppm: u32,
    pub sequencing_failure_ppm: u32,
    /// strand dropout and per-base substitutions of the simulated reads, parts per million
    pub dropout_ppm: u32,
    pub substitution_ppm: u32,
    /// retrievals a pool supports before it is depleted
    pub aliquots: u32,
    pub min_strand_nt: usize,
    pub max_strand_nt: usize,
    pub max_strands_per_order: usize,
}

impl Default for SimLabConfig {
    fn default() -> SimLabConfig {
        SimLabConfig {
            seed: 0,
            synthesis_failure_ppm: 0,
            retrieval_failure_ppm: 0,
            sequencing_failure_ppm: 0,
            dropout_ppm: 0,
            substitution_ppm: 0,
            aliquots: 8,
            min_strand_nt: 20,
            max_strand_nt: 350,
            max_strands_per_order: 1_000_000,
        }
    }
}

#[derive(Clone)]
enum JobInput {
    Synthesis(SynthesisOrder),
    Retrieval(RetrievalRequest),
    Sequencing(SequencingRequest),
}

struct SimJob {
    status: JobStatus,
    input: JobInput,
    /// what a completed job produced
    pool: Option<PoolId>,
    sample: Option<SampleId>,
    delivery: Option<(ReadDelivery, Vec<u8>)>,
}

struct SimPool {
    record: PoolRecord,
    strands: Vec<String>,
}

#[derive(Default)]
struct SimState {
    jobs: BTreeMap<JobId, SimJob>,
    pools: BTreeMap<PoolId, SimPool>,
    /// sample -> (pool, strands taken out)
    samples: BTreeMap<SampleId, (PoolId, Vec<String>)>,
    next: u64,
    clock: u64,
    offline: bool,
}

/// A laboratory in software (SIMULATED): virtual time, seeded failures, in-memory pools (lost when the process
/// exits).
pub struct SimulatedLab {
    cfg: SimLabConfig,
    state: Mutex<SimState>,
}

const EPOCH: u64 = 1_800_000_000; // virtual time origin of the simulator (seconds)

impl SimulatedLab {
    pub fn new(cfg: SimLabConfig) -> SimulatedLab {
        SimulatedLab { cfg, state: Mutex::new(SimState::default()) }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, SimState> {
        self.state.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Take the whole simulated lab offline (calls fail BACKEND_UNAVAILABLE; jobs do not advance) or back online.
    pub fn set_offline(&self, offline: bool) {
        self.lock().offline = offline;
    }

    fn submit(&self, kind: JobKind, input: JobInput, retry_of: Option<JobId>) -> Result<JobId> {
        let mut s = self.lock();
        if s.offline {
            return err(ErrorCode::BackendUnavailable, "SIMULATED lab is offline");
        }
        s.next += 1;
        let id = JobId(format!("sim-{}-{:06}", match kind {
            JobKind::Synthesis => "syn",
            JobKind::Retrieval => "ret",
            JobKind::Sequencing => "seq",
        }, s.next));
        let status = JobStatus {
            id: id.clone(),
            kind,
            state: JobState::Submitted,
            progress_permille: 0,
            error: None,
            retry_of,
            history: vec![JobState::Submitted],
        };
        s.jobs.insert(id.clone(), SimJob { status, input, pool: None, sample: None, delivery: None });
        Ok(id)
    }

    fn ppm(&self, kind: JobKind) -> u32 {
        match kind {
            JobKind::Synthesis => self.cfg.synthesis_failure_ppm,
            JobKind::Retrieval => self.cfg.retrieval_failure_ppm,
            JobKind::Sequencing => self.cfg.sequencing_failure_ppm,
        }
    }

    /// Advance a job by one state (one status poll = one step of virtual time).
    fn step(&self, s: &mut SimState, id: &JobId) -> Result<()> {
        s.clock += 1;
        let clock = s.clock;
        let Some(job) = s.jobs.get(id) else { return err(ErrorCode::NotFound, format!("no job {}", id.0)) };
        let from = job.status.state;
        if from.is_terminal() {
            return Ok(());
        }
        let kind = job.status.kind;
        let input = job.input.clone();
        let seq_no: u64 = id.0.rsplit('-').next().and_then(|n| n.parse().ok()).unwrap_or(0);
        let (to, error) = match from {
            JobState::Submitted => (JobState::Queued, None),
            JobState::Queued => {
                let draw = SplitMix64::keyed(&[self.cfg.seed, 0x4A4F_42, seq_no]);
                if draw % 1_000_000 < u64::from(self.ppm(kind)) {
                    let kinds = [
                        EquipmentErrorKind::InstrumentFault,
                        EquipmentErrorKind::ConsumableExhausted,
                        EquipmentErrorKind::QcFailed,
                        EquipmentErrorKind::CommunicationLost,
                    ];
                    let k = kinds[((draw >> 32) % kinds.len() as u64) as usize];
                    (JobState::Failed, Some(EquipmentError { kind: k, detail: "SIMULATED equipment failure".into() }))
                } else {
                    (JobState::Running, None)
                }
            }
            JobState::Running => (JobState::Finishing, None),
            JobState::Finishing => match self.complete(s, id, kind, &input, clock) {
                Ok(()) => (JobState::Completed, None),
                Err(e) => (JobState::Failed, Some(e)),
            },
            _ => unreachable!("terminal states return early"),
        };
        debug_assert!(from.can_move_to(to));
        let job = s.jobs.get_mut(id).expect("present");
        job.status.state = to;
        job.status.error = error;
        job.status.history.push(to);
        job.status.progress_permille = match to {
            JobState::Queued => 0,
            JobState::Running => 300,
            JobState::Finishing => 900,
            JobState::Completed => 1000,
            _ => job.status.progress_permille,
        };
        Ok(())
    }

    fn complete(&self, s: &mut SimState, id: &JobId, kind: JobKind, input: &JobInput, clock: u64) -> std::result::Result<(), EquipmentError> {
        let lost = |d: &str| EquipmentError { kind: EquipmentErrorKind::SampleLost, detail: d.into() };
        match (kind, input) {
            (JobKind::Synthesis, JobInput::Synthesis(order)) => {
                let pid = PoolId(format!("sim-pool-{:06}", s.pools.len() + 1));
                let record = PoolRecord {
                    id: pid.clone(),
                    archive_id: order.archive_id.clone(),
                    order_ref: order.order_ref.clone(),
                    strands: order.strands.len(),
                    location: PhysicalLocation {
                        site: "SIMULATED".into(),
                        container: format!("sim-box-{}", s.pools.len() / 96 + 1),
                        position: format!("{}", s.pools.len() % 96 + 1),
                    },
                    preservation: PreservationMetadata {
                        method: "SIMULATED (no physical material)".into(),
                        storage_condition: "SIMULATED".into(),
                        stored_at: EPOCH + clock,
                    },
                    lifecycle: PoolLifecycle::Stored,
                    retrievals: 0,
                    aliquots_left: Some(self.cfg.aliquots),
                    synthesis_job: id.clone(),
                };
                let strands = order.strands.iter().map(|x| x.sequence.clone()).collect();
                s.pools.insert(pid.clone(), SimPool { record, strands });
                s.jobs.get_mut(id).expect("present").pool = Some(pid);
                Ok(())
            }
            (JobKind::Retrieval, JobInput::Retrieval(req)) => {
                let Some(p) = s.pools.get_mut(&req.pool) else { return Err(lost("pool unknown")) };
                match p.record.lifecycle {
                    PoolLifecycle::Stored | PoolLifecycle::InRetrieval => {}
                    other => return Err(lost(&format!("pool is {}", other.as_str()))),
                }
                let left = p.record.aliquots_left.unwrap_or(0);
                if left == 0 {
                    p.record.lifecycle = PoolLifecycle::Depleted;
                    return Err(lost("pool depleted"));
                }
                p.record.aliquots_left = Some(left - 1);
                p.record.retrievals += 1;
                p.record.lifecycle = if left == 1 { PoolLifecycle::Depleted } else { PoolLifecycle::Stored };
                let strands = p.strands.clone();
                let sid = SampleId(format!("sim-sample-{:06}", s.samples.len() + 1));
                s.samples.insert(sid.clone(), (req.pool.clone(), strands));
                s.jobs.get_mut(id).expect("present").sample = Some(sid);
                Ok(())
            }
            (JobKind::Sequencing, JobInput::Sequencing(req)) => {
                let Some((_, strands)) = s.samples.get(&req.sample) else { return Err(lost("sample unknown")) };
                let seed = SplitMix64::keyed(&[self.cfg.seed, 0x5345_51, clock]);
                let reads = simple_channel(strands, req.coverage, self.cfg.substitution_ppm, self.cfg.dropout_ppm, seed);
                let quality = fastq_quality(&reads, true);
                let file = DeliveredFile { name: "reads.fastq".into(), format: ReadFormat::Fastq, sha256: sha256(&reads), bytes: reads.len() as u64 };
                let delivery = ReadDelivery { job: id.clone(), run_id: format!("sim-run-{clock}"), files: vec![file], quality };
                s.jobs.get_mut(id).expect("present").delivery = Some((delivery, reads));
                Ok(())
            }
            _ => Err(EquipmentError { kind: EquipmentErrorKind::Unknown, detail: "job input does not match its kind".into() }),
        }
    }

    fn completed(&self, job: &JobId, kind: JobKind) -> Result<std::sync::MutexGuard<'_, SimState>> {
        let s = self.lock();
        let Some(j) = s.jobs.get(job) else { return err(ErrorCode::NotFound, format!("no job {}", job.0)) };
        if j.status.kind != kind || j.status.state != JobState::Completed {
            return err(ErrorCode::InvalidArgument, format!("job {} is a {:?} job in state {}", job.0, j.status.kind, j.status.state.as_str()));
        }
        Ok(s)
    }
}

/// Read count, bases and Phred statistics of FASTQ text (Phred+33).
pub fn fastq_quality(fastq: &[u8], simulated: bool) -> QualityMetrics {
    let text = String::from_utf8_lossy(fastq);
    let lines: Vec<&str> = text.lines().collect();
    let (mut reads, mut bases, mut qsum, mut q30) = (0u64, 0u64, 0u64, 0u64);
    for r in lines.chunks(4).filter(|r| r.len() == 4) {
        reads += 1;
        for q in r[3].bytes() {
            let p = u64::from(q.saturating_sub(33));
            bases += 1;
            qsum += p;
            q30 += u64::from(p >= 30);
        }
    }
    QualityMetrics {
        reads,
        bases,
        mean_phred_x100: if bases == 0 { 0 } else { (qsum * 100 / bases) as u32 },
        q30_permille: if bases == 0 { 0 } else { (q30 * 1000 / bases) as u16 },
        simulated,
    }
}

impl DnaLab for SimulatedLab {
    fn capabilities(&self) -> LabCapabilities {
        LabCapabilities {
            name: "vnxd simulated lab".into(),
            simulated: true,
            min_strand_nt: self.cfg.min_strand_nt,
            max_strand_nt: self.cfg.max_strand_nt,
            max_strands_per_order: self.cfg.max_strands_per_order,
            platforms: vec![Platform::Other("SIMULATED".into())],
            read_formats: vec![ReadFormat::Fastq],
            primer_selection: false,
        }
    }

    fn health(&self) -> Result<LabHealth> {
        let offline = self.lock().offline;
        let st = if offline { LabState::Offline } else { LabState::Online };
        Ok(LabHealth { state: st, instruments: vec![("simulated-synthesizer".into(), st), ("simulated-sequencer".into(), st)], detail: "SIMULATED".into() })
    }

    fn submit_synthesis(&self, order: &SynthesisOrder) -> Result<JobId> {
        order.validate(&self.capabilities())?;
        self.submit(JobKind::Synthesis, JobInput::Synthesis(order.clone()), None)
    }

    fn submit_retrieval(&self, req: &RetrievalRequest) -> Result<JobId> {
        if req.selection != Selection::All {
            return err(ErrorCode::InvalidArgument, "the simulated lab does not model primer selection (random access)");
        }
        if !self.lock().pools.contains_key(&req.pool) {
            return err(ErrorCode::NotFound, format!("no pool {}", req.pool.0));
        }
        self.submit(JobKind::Retrieval, JobInput::Retrieval(req.clone()), None)
    }

    fn submit_sequencing(&self, req: &SequencingRequest) -> Result<JobId> {
        if req.format != ReadFormat::Fastq || req.coverage == 0 || req.coverage > 1000 {
            return err(ErrorCode::InvalidArgument, "the simulated lab delivers FASTQ at coverage 1..=1000");
        }
        if !self.lock().samples.contains_key(&req.sample) {
            return err(ErrorCode::NotFound, format!("no sample {}", req.sample.0));
        }
        self.submit(JobKind::Sequencing, JobInput::Sequencing(req.clone()), None)
    }

    fn status(&self, job: &JobId) -> Result<JobStatus> {
        let mut s = self.lock();
        if s.offline {
            return err(ErrorCode::BackendUnavailable, "SIMULATED lab is offline");
        }
        self.step(&mut s, job)?;
        Ok(s.jobs[job].status.clone())
    }

    fn cancel(&self, job: &JobId) -> Result<()> {
        let mut s = self.lock();
        let Some(j) = s.jobs.get_mut(job) else { return err(ErrorCode::NotFound, format!("no job {}", job.0)) };
        if !j.status.state.can_move_to(JobState::Cancelled) {
            return err(ErrorCode::InvalidArgument, format!("job {} is {}: it cannot be cancelled", job.0, j.status.state.as_str()));
        }
        j.status.state = JobState::Cancelled;
        j.status.history.push(JobState::Cancelled);
        Ok(())
    }

    fn retry(&self, job: &JobId) -> Result<JobId> {
        let (kind, input) = {
            let s = self.lock();
            let Some(j) = s.jobs.get(job) else { return err(ErrorCode::NotFound, format!("no job {}", job.0)) };
            match (&j.status.state, &j.status.error) {
                (JobState::Failed, Some(e)) if e.kind.retryable() => (j.status.kind, j.input.clone()),
                _ => return err(ErrorCode::InvalidArgument, format!("job {} has not failed retryably", job.0)),
            }
        };
        self.submit(kind, input, Some(job.clone()))
    }

    fn synthesis_result(&self, job: &JobId) -> Result<PoolId> {
        Ok(self.completed(job, JobKind::Synthesis)?.jobs[job].pool.clone().expect("completed synthesis has a pool"))
    }

    fn retrieval_result(&self, job: &JobId) -> Result<SampleId> {
        Ok(self.completed(job, JobKind::Retrieval)?.jobs[job].sample.clone().expect("completed retrieval has a sample"))
    }

    fn read_data(&self, job: &JobId) -> Result<ReadDelivery> {
        Ok(self.completed(job, JobKind::Sequencing)?.jobs[job].delivery.clone().expect("completed sequencing has reads").0)
    }

    fn fetch(&self, job: &JobId, file: &str) -> Result<Vec<u8>> {
        let s = self.completed(job, JobKind::Sequencing)?;
        let (d, bytes) = s.jobs[job].delivery.as_ref().expect("completed sequencing has reads");
        if d.files.iter().any(|f| f.name == file) {
            Ok(bytes.clone())
        } else {
            err(ErrorCode::NotFound, format!("job {} delivered no file {file}", job.0))
        }
    }

    fn pool(&self, id: &PoolId) -> Result<PoolRecord> {
        self.lock().pools.get(id).map(|p| p.record.clone()).ok_or_else(|| VnxError::new(ErrorCode::NotFound, format!("no pool {}", id.0)))
    }

    fn pools(&self) -> Result<Vec<PoolId>> {
        Ok(self.lock().pools.keys().cloned().collect())
    }

    fn destroy_pool(&self, id: &PoolId) -> Result<()> {
        let mut s = self.lock();
        if s.offline {
            return err(ErrorCode::BackendUnavailable, "SIMULATED lab is offline");
        }
        let Some(p) = s.pools.get_mut(id) else { return err(ErrorCode::NotFound, format!("no pool {}", id.0)) };
        p.record.lifecycle = PoolLifecycle::Destroyed;
        p.strands.clear();
        Ok(())
    }
}

// -------------------------------------------------------------------------------------- the media adapter

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LabMediaConfig {
    /// status polls before a job that does not finish is abandoned (cancelled) with TIMEOUT
    pub max_polls: u32,
    /// wait between polls (zero for the simulator; hours for a real lab)
    pub poll_interval: Duration,
    /// times a job that failed retryably is retried
    pub job_retries: u32,
    pub platform: Platform,
    pub coverage: u32,
    pub max_in_flight: u32,
}

impl Default for LabMediaConfig {
    fn default() -> LabMediaConfig {
        LabMediaConfig { max_polls: 64, poll_interval: Duration::ZERO, job_retries: 2, platform: Platform::Other("SIMULATED".into()), coverage: 5, max_in_flight: 2 }
    }
}

/// Media adapter over a [`DnaLab`]: `put` = encode + synthesis order + storage; `get` = retrieval + sequencing +
/// verified read download + reconstruction by the codec. A catalogue (an electronic media adapter) keeps, per
/// generation, the pool id and the frame's SHA-256 and length; it is electronic metadata, not DNA.
pub struct LabMedia {
    lab: Arc<dyn DnaLab>,
    codec: Arc<dyn Codec>,
    catalogue: Arc<dyn MediaAdapter>,
    cfg: LabMediaConfig,
}

impl LabMedia {
    pub fn new(lab: Arc<dyn DnaLab>, codec: Arc<dyn Codec>, catalogue: Arc<dyn MediaAdapter>, cfg: LabMediaConfig) -> Result<LabMedia> {
        if !lab.capabilities().simulated {
            return err(
                ErrorCode::InvalidArgument,
                "media contract v1 has no durability class for physical DNA: a real lab cannot back a device yet",
            );
        }
        if cfg.max_polls == 0 || cfg.coverage == 0 || cfg.max_in_flight == 0 {
            return err(ErrorCode::InvalidArgument, "dna-lab: max_polls, coverage and max_in_flight must be >= 1");
        }
        Ok(LabMedia { lab, codec, catalogue, cfg })
    }

    /// Poll a job to a terminal state, retrying retryable failures; returns the id of the job that completed.
    fn run(&self, mut job: JobId) -> Result<JobId> {
        let mut retries = 0;
        loop {
            let mut st = None;
            for _ in 0..self.cfg.max_polls {
                let s = self.lab.status(&job)?;
                if s.state.is_terminal() {
                    st = Some(s);
                    break;
                }
                if !self.cfg.poll_interval.is_zero() {
                    std::thread::sleep(self.cfg.poll_interval);
                }
            }
            let Some(s) = st else {
                let _ = self.lab.cancel(&job);
                return err(ErrorCode::Timeout, format!("lab job {} did not finish within {} polls", job.0, self.cfg.max_polls));
            };
            match (s.state, s.error) {
                (JobState::Completed, _) => return Ok(job),
                (JobState::Failed, Some(e)) if e.kind.retryable() && retries < self.cfg.job_retries => {
                    retries += 1;
                    job = self.lab.retry(&job)?;
                }
                (JobState::Failed, Some(e)) => return Err(e.to_vnx()),
                (state, _) => return err(ErrorCode::BackendUnavailable, format!("lab job {} ended {}", job.0, state.as_str())),
            }
        }
    }

    fn entry(&self, at: GenerationRef) -> Result<Envelope> {
        Envelope::parse(at, &self.catalogue.get(at)?)
    }
}

/// Catalogue payload of one generation: canonical JSON {"pool": id, "pool_header": codec header}.
fn catalogue_payload(pool: &PoolId, p: &PoolFile) -> Vec<u8> {
    obj([("pool", Value::Str(pool.0.clone())), ("pool_header", Value::Obj(p.header.clone()))]).canonical().into_bytes()
}

impl MediaAdapter for LabMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "dna-lab-sim", durability: Durability::SimulatedDna, max_in_flight: self.cfg.max_in_flight }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        match self.catalogue.get(at) {
            Ok(_) => return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored")),
            Err(e) if e.code == ErrorCode::NotFound => {}
            Err(e) => return Err(e),
        }
        let envelope = if metadata_only(at) {
            Envelope::Metadata { sha256: sha256(data), bytes: data.to_vec() }
        } else {
            let framed = frame(at, data);
            let pool = PoolFile::parse(&self.codec.encode(at, &framed)?)?;
            let order_ref = format!("vnxd-{:x}-{:x}", at.extent.0, at.generation.get());
            let strands = pool
                .strands()?
                .into_iter()
                .enumerate()
                .map(|(i, sequence)| Strand { name: format!("s{i:07}"), sequence })
                .collect();
            let order = SynthesisOrder { order_ref: order_ref.clone(), archive_id: order_ref, strands };
            let job = self.run(self.lab.submit_synthesis(&order)?)?;
            let pid = self.lab.synthesis_result(&job)?;
            Envelope::Dna { frame_sha256: sha256(&framed), frame_len: framed.len() as u64, payload: catalogue_payload(&pid, &pool) }
        };
        let stored = envelope.to_bytes();
        let r = self.catalogue.put(at, &stored);
        match r {
            Ok(r) if r.length == stored.len() as u64 && r.sha256 == sha256(&stored) => {}
            other => {
                if let Envelope::Dna { payload, .. } = &envelope {
                    if let Some(p) = Value::parse(std::str::from_utf8(payload).unwrap_or("")).ok().and_then(|v| v.as_obj().and_then(|o| o.get("pool")).and_then(Value::as_str).map(String::from)) {
                        let _ = self.lab.destroy_pool(&PoolId(p)); // never leave an unreferenced pool behind
                    }
                }
                return match other {
                    Err(e) => Err(e),
                    Ok(_) => err(ErrorCode::Corruption, format!("catalogue receipt for {at:?} does not describe the entry")),
                };
            }
        }
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let (frame_sha256, frame_len, payload) = match self.entry(at)? {
            Envelope::Metadata { bytes, .. } if metadata_only(at) => return Ok(bytes),
            Envelope::Dna { frame_sha256, frame_len, payload } if !metadata_only(at) && frame_len >= FRAME_OVERHEAD as u64 => {
                (frame_sha256, frame_len, payload)
            }
            _ => return err(ErrorCode::Corruption, format!("{at:?}: catalogue entry does not fit this generation")),
        };
        let bad = || VnxError::new(ErrorCode::Corruption, format!("{at:?}: catalogue entry is damaged"));
        let v = Value::parse(std::str::from_utf8(&payload).map_err(|_| bad())?).map_err(|_| bad())?;
        let o = v.as_obj().ok_or_else(bad)?;
        let pool = PoolId(o.get("pool").and_then(Value::as_str).ok_or_else(bad)?.to_string());
        let Some(Value::Obj(header)) = o.get("pool_header") else { return Err(bad()) };
        // retrieval -> sample -> sequencing -> delivery
        let rj = self.run(self.lab.submit_retrieval(&RetrievalRequest { pool, selection: Selection::All })?)?;
        let sample = self.lab.retrieval_result(&rj)?;
        let req = SequencingRequest { sample, platform: self.cfg.platform.clone(), coverage: self.cfg.coverage, format: ReadFormat::Fastq };
        let sj = self.run(self.lab.submit_sequencing(&req)?)?;
        let delivery = self.lab.read_data(&sj)?;
        let mut reads = Vec::new();
        for f in &delivery.files {
            if !f.format.decodable() {
                return err(ErrorCode::BackendUnavailable, format!("delivered {} needs basecalling/conversion first", f.format.as_str()));
            }
            let bytes = self.lab.fetch(&sj, &f.name)?;
            if bytes.len() as u64 != f.bytes || sha256(&bytes) != f.sha256 {
                return err(ErrorCode::Corruption, format!("delivered file {} does not match its announced SHA-256 {}", f.name, hex(&f.sha256)));
            }
            reads.extend_from_slice(&bytes);
        }
        if reads.is_empty() {
            return err(ErrorCode::Corruption, format!("{at:?}: the sequencing run delivered no reads"));
        }
        // reconstruction handoff: the codec decodes the delivered reads; only the recorded bytes come back
        let handoff = PoolFile { header: header.clone(), body: vec![] }.with_body("reads", reads).to_bytes();
        let framed = self.codec.decode(at, &handoff, &frame_sha256, frame_len, None)?;
        unframe(at, &framed)
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        if let Envelope::Dna { payload, .. } = self.entry(at)? {
            let pool = Value::parse(std::str::from_utf8(&payload).unwrap_or(""))
                .ok()
                .and_then(|v| v.as_obj().and_then(|o| o.get("pool")).and_then(Value::as_str).map(String::from));
            if let Some(p) = pool {
                match self.lab.destroy_pool(&PoolId(p)) {
                    Ok(()) => {}
                    Err(e) if e.code == ErrorCode::NotFound => {}
                    Err(e) => return Err(e),
                }
            }
        }
        self.catalogue.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.catalogue.list()
    }

    fn sync(&self) -> Result<()> {
        self.catalogue.sync()
    }
}

/// The interface status table (docs and tests read this one source).
pub const STATUS: &[(&str, &str)] = &[
    ("interface vnx.dna-lab/1", "specified and implemented in software"),
    ("SimulatedLab", "implemented; SIMULATED"),
    ("LabMedia over SimulatedLab", "passes media::conformance; SIMULATED"),
    ("real synthesis provider adapter", "not implemented: no provider connected"),
    ("real sequencer adapter", "not implemented: no instrument connected"),
    ("physical validation", "none"),
];

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation};
    use crate::codec::ToyCodec;
    use crate::media::{conformance, MemoryMedia};

    fn at(e: u64, g: u64) -> GenerationRef {
        GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
    }

    fn media(cfg: SimLabConfig) -> (Arc<SimulatedLab>, LabMedia) {
        let lab = Arc::new(SimulatedLab::new(cfg));
        let m = LabMedia::new(lab.clone(), Arc::new(ToyCodec), Arc::new(MemoryMedia::new()), LabMediaConfig::default()).unwrap();
        (lab, m)
    }

    #[test]
    fn lab_media_over_the_simulated_lab_passes_conformance() {
        let (lab, m) = media(SimLabConfig { substitution_ppm: 300, ..Default::default() });
        conformance(&m).unwrap();
        assert_eq!(m.info().durability, Durability::SimulatedDna);
        // remove destroyed the pool's material; records stay
        assert!(lab.pools().unwrap().iter().any(|p| lab.pool(p).unwrap().lifecycle == PoolLifecycle::Destroyed));
    }

    #[test]
    fn job_state_machine() {
        use JobState::*;
        let all = [Submitted, Queued, Running, Finishing, Completed, Failed, Cancelled];
        for t in [Completed, Failed, Cancelled] {
            assert!(all.iter().all(|to| !t.can_move_to(*to)), "{t:?} is terminal");
        }
        assert!(Submitted.can_move_to(Queued) && Queued.can_move_to(Running) && Running.can_move_to(Finishing) && Finishing.can_move_to(Completed));
        assert!(!Submitted.can_move_to(Running) && !Running.can_move_to(Queued) && !Finishing.can_move_to(Cancelled));
        assert!(all[..4].iter().all(|s| s.can_move_to(Failed)));
    }

    #[test]
    fn a_job_walks_the_lifecycle_and_records_its_history() {
        let lab = SimulatedLab::new(SimLabConfig::default());
        let order = SynthesisOrder { order_ref: "o1".into(), archive_id: "a1".into(), strands: vec![Strand { name: "s0".into(), sequence: "ACGT".repeat(10) }] };
        let j = lab.submit_synthesis(&order).unwrap();
        assert_eq!(lab.synthesis_result(&j).unwrap_err().code, ErrorCode::InvalidArgument, "not completed yet");
        let mut st = lab.status(&j).unwrap();
        while !st.state.is_terminal() {
            st = lab.status(&j).unwrap();
        }
        assert_eq!(st.history, vec![JobState::Submitted, JobState::Queued, JobState::Running, JobState::Finishing, JobState::Completed]);
        assert!(st.history.windows(2).all(|w| w[0].can_move_to(w[1])));
        let pool = lab.pool(&lab.synthesis_result(&j).unwrap()).unwrap();
        assert_eq!((pool.lifecycle, pool.strands, pool.aliquots_left), (PoolLifecycle::Stored, 1, Some(8)));
        assert_eq!(pool.location.site, "SIMULATED");
        assert_eq!(lab.cancel(&j).unwrap_err().code, ErrorCode::InvalidArgument, "a completed job cannot be cancelled");
    }

    #[test]
    fn orders_outside_the_lab_limits_are_refused_before_submission() {
        let lab = SimulatedLab::new(SimLabConfig::default());
        let mk = |seq: &str, name: &str| SynthesisOrder { order_ref: "o".into(), archive_id: "a".into(), strands: vec![Strand { name: name.into(), sequence: seq.into() }] };
        for o in [mk(&"A".repeat(19), "s"), mk(&"A".repeat(351), "s"), mk(&"ACGN".repeat(10), "s"), mk(&"ACGT".repeat(10), "bad name")] {
            assert_eq!(lab.submit_synthesis(&o).unwrap_err().code, ErrorCode::InvalidArgument);
        }
        let dup = SynthesisOrder { order_ref: "o".into(), archive_id: "a".into(), strands: vec![Strand { name: "x".into(), sequence: "ACGT".repeat(10) }; 2] };
        assert_eq!(lab.submit_synthesis(&dup).unwrap_err().code, ErrorCode::InvalidArgument);
        assert!(lab.pools().unwrap().is_empty() && lab.lock().jobs.is_empty());
    }

    #[test]
    fn cancel_retry_and_equipment_errors() {
        let lab = SimulatedLab::new(SimLabConfig { synthesis_failure_ppm: 1_000_000, ..Default::default() });
        let order = SynthesisOrder { order_ref: "o".into(), archive_id: "a".into(), strands: vec![Strand { name: "s".into(), sequence: "ACGT".repeat(10) }] };
        let c = lab.submit_synthesis(&order).unwrap();
        lab.cancel(&c).unwrap();
        assert_eq!(lab.status(&c).unwrap().state, JobState::Cancelled);
        assert_eq!(lab.retry(&c).unwrap_err().code, ErrorCode::InvalidArgument, "only failed jobs are retried");
        let j = lab.submit_synthesis(&order).unwrap();
        let mut st = lab.status(&j).unwrap();
        while !st.state.is_terminal() {
            st = lab.status(&j).unwrap();
        }
        assert_eq!(st.state, JobState::Failed);
        let e = st.error.unwrap();
        assert!(e.kind.retryable());
        assert_eq!(e.to_vnx().code, ErrorCode::BackendUnavailable);
        let r = lab.retry(&j).unwrap();
        assert_eq!(lab.status(&r).unwrap().retry_of, Some(j));
        // the adapter gives up after its retries and reports a retryable error, storing nothing
        let (_, m) = media(SimLabConfig { synthesis_failure_ppm: 1_000_000, ..Default::default() });
        assert_eq!(m.put(at(0, 1), b"x").unwrap_err().code, ErrorCode::BackendUnavailable);
        assert!(m.list().unwrap().is_empty());
        assert_eq!(EquipmentError { kind: EquipmentErrorKind::SampleLost, detail: String::new() }.to_vnx().code, ErrorCode::Corruption);
    }

    #[test]
    fn aliquots_deplete_and_a_depleted_pool_is_reported_not_faked() {
        let (lab, m) = media(SimLabConfig { aliquots: 2, ..Default::default() });
        m.put(at(1, 1), b"two reads only").unwrap();
        assert_eq!(m.get(at(1, 1)).unwrap(), b"two reads only");
        assert_eq!(m.get(at(1, 1)).unwrap(), b"two reads only");
        let p = lab.pool(&lab.pools().unwrap()[0]).unwrap();
        assert_eq!((p.lifecycle, p.retrievals, p.aliquots_left), (PoolLifecycle::Depleted, 2, Some(0)));
        assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::Corruption, "material gone: unreadable, never invented");
    }

    #[test]
    fn a_lossy_run_or_tampered_delivery_never_returns_other_bytes_and_an_offline_lab_is_retryable() {
        let (_, m) = media(SimLabConfig { dropout_ppm: 900_000, seed: 3, ..Default::default() });
        m.put(at(1, 1), &[9u8; 300]).unwrap();
        assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::Corruption);
        let (lab, m) = media(SimLabConfig::default());
        m.put(at(1, 1), b"x").unwrap();
        lab.set_offline(true);
        assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(lab.health().unwrap().state, LabState::Offline);
        lab.set_offline(false);
        assert_eq!(m.get(at(1, 1)).unwrap(), b"x");
    }

    #[test]
    fn quality_metrics_come_from_the_delivered_reads() {
        let q = fastq_quality(b"@r\nACGT\n+\n??++\n", true);
        assert_eq!((q.reads, q.bases, q.mean_phred_x100, q.q30_permille, q.simulated), (1, 4, 2000, 500, true));
    }

    #[test]
    fn a_real_lab_cannot_back_a_device_under_contract_v1() {
        struct Real(SimulatedLab);
        impl DnaLab for Real {
            fn capabilities(&self) -> LabCapabilities {
                LabCapabilities { simulated: false, ..self.0.capabilities() }
            }
            fn health(&self) -> Result<LabHealth> { self.0.health() }
            fn submit_synthesis(&self, o: &SynthesisOrder) -> Result<JobId> { self.0.submit_synthesis(o) }
            fn submit_retrieval(&self, r: &RetrievalRequest) -> Result<JobId> { self.0.submit_retrieval(r) }
            fn submit_sequencing(&self, r: &SequencingRequest) -> Result<JobId> { self.0.submit_sequencing(r) }
            fn status(&self, j: &JobId) -> Result<JobStatus> { self.0.status(j) }
            fn cancel(&self, j: &JobId) -> Result<()> { self.0.cancel(j) }
            fn retry(&self, j: &JobId) -> Result<JobId> { self.0.retry(j) }
            fn synthesis_result(&self, j: &JobId) -> Result<PoolId> { self.0.synthesis_result(j) }
            fn retrieval_result(&self, j: &JobId) -> Result<SampleId> { self.0.retrieval_result(j) }
            fn read_data(&self, j: &JobId) -> Result<ReadDelivery> { self.0.read_data(j) }
            fn fetch(&self, j: &JobId, f: &str) -> Result<Vec<u8>> { self.0.fetch(j, f) }
            fn pool(&self, p: &PoolId) -> Result<PoolRecord> { self.0.pool(p) }
            fn pools(&self) -> Result<Vec<PoolId>> { self.0.pools() }
            fn destroy_pool(&self, p: &PoolId) -> Result<()> { self.0.destroy_pool(p) }
        }
        let r = LabMedia::new(Arc::new(Real(SimulatedLab::new(SimLabConfig::default()))), Arc::new(ToyCodec), Arc::new(MemoryMedia::new()), LabMediaConfig::default());
        assert_eq!(r.err().unwrap().code, ErrorCode::InvalidArgument);
    }
}
