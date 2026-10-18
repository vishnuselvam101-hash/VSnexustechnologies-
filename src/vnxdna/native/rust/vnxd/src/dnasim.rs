//! DNA software simulator adapter (master directive §13 A; docs/V14_MEDIA_ADAPTERS.md §2). Everything it does is
//! SIMULATED: no DNA is synthesised, stored or sequenced, and no number it reports is a physical measurement.
//!
//! [`DnaSimMedia`] stores each generation as a simulated strand pool:
//!
//! * `put`: frame the bytes ([`crate::codec::frame`]), encode them with the VNX-DNA codec ([`Codec::encode`], the
//!   existing encoder and ECC in the Python codec worker) into a `vnx.dna-pool/1` file, and keep that pool in an
//!   electronic store (any media adapter: [`crate::media::MemoryMedia`], or [`crate::image::ImageMedia`] for a
//!   simulation that survives restarts) inside an envelope recording the frame's SHA-256 and length. The receipt
//!   describes the bytes given; durability is `simulated-dna`;
//! * `get`: a simulated sequencing run per attempt: the pool's strands pass the configured channel model (the
//!   existing staged channel simulator) with a seed derived from (seed, extent, generation, read number), and the
//!   VNX-DNA decoder reconstructs the frame. A decode is accepted only if its SHA-256 and length match the envelope
//!   and the frame names the generation asked for; a failed decode is retried with a fresh simulated run up to
//!   `read_attempts` times and is then CORRUPTION, never other bytes;
//! * a seeded [`FailureModel`] makes simulated synthesis runs fail (put: BACKEND_UNAVAILABLE, nothing stored) and
//!   simulated sequencing runs fail (get: BACKEND_UNAVAILABLE), both retryable, deterministically per seed;
//! * a [`LatencyModel`] adds MODELLED synthesis and sequencing times: accounted in [`DnaSimMedia::report`], and
//!   optionally slept (for scheduler experiments). They are model inputs, never measurements;
//! * the volume's binding marker (extent u64::MAX, beyond the codec protocol's range) is kept in the electronic
//!   metadata region only (an `M` envelope), never presented as DNA.

use crate::addr::GenerationRef;
use crate::codec::{frame, metadata_only, unframe, ChannelRun, Codec, Envelope, FRAME_OVERHEAD};
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result};
use crate::fault::SplitMix64;
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

/// MODELLED times of the simulated medium (model inputs, not measurements).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LatencyModel {
    pub synthesis_fixed: Duration,
    pub synthesis_per_kib: Duration,
    pub sequencing_fixed: Duration,
    pub sequencing_per_kib: Duration,
    /// sleep the modelled time (scheduler experiments) instead of only accounting it
    pub sleep: bool,
}

impl Default for LatencyModel {
    fn default() -> LatencyModel {
        LatencyModel {
            synthesis_fixed: Duration::ZERO,
            synthesis_per_kib: Duration::ZERO,
            sequencing_fixed: Duration::ZERO,
            sequencing_per_kib: Duration::ZERO,
            sleep: false,
        }
    }
}

impl LatencyModel {
    fn time(fixed: Duration, per_kib: Duration, bytes: usize) -> Duration {
        let kib = (bytes as u64).div_ceil(1024);
        fixed.saturating_add(per_kib.saturating_mul(u32::try_from(kib).unwrap_or(u32::MAX)))
    }
}

/// Seeded failures of the simulated runs, in parts per million.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FailureModel {
    /// a simulated synthesis run fails (put: BACKEND_UNAVAILABLE, nothing stored)
    pub synthesis_failure_ppm: u32,
    /// a simulated sequencing run fails (get: BACKEND_UNAVAILABLE)
    pub sequencing_failure_ppm: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DnaSimConfig {
    /// shipped channel model of each simulated sequencing run (`vnx.channel-model/1` name); None = decode the
    /// synthesised strands as they are (a perfect channel)
    pub channel: Option<String>,
    pub seed: u64,
    /// simulated sequencing runs per `get` before a failed decode is reported as CORRUPTION (>= 1)
    pub read_attempts: u32,
    pub latency: LatencyModel,
    pub failures: FailureModel,
    pub max_in_flight: u32,
}

impl Default for DnaSimConfig {
    fn default() -> DnaSimConfig {
        DnaSimConfig {
            channel: None,
            seed: 0,
            read_attempts: 3,
            latency: LatencyModel::default(),
            failures: FailureModel::default(),
            max_in_flight: 4,
        }
    }
}

/// Counters of one simulator (all SIMULATED).
#[derive(Default, Debug)]
pub struct DnaSimStats {
    pub synthesis_runs: AtomicU64,
    pub synthesis_failures: AtomicU64,
    pub sequencing_runs: AtomicU64,
    pub sequencing_failures: AtomicU64,
    pub decode_failures: AtomicU64,
    pub reads_unrecoverable: AtomicU64,
    pub modelled_synthesis_ns: AtomicU64,
    pub modelled_sequencing_ns: AtomicU64,
}

pub struct DnaSimMedia {
    store: Arc<dyn MediaAdapter>,
    codec: Arc<dyn Codec>,
    cfg: DnaSimConfig,
    /// per-get read numbers (part of the channel seed: every simulated run of a pool differs)
    reads: AtomicU64,
    puts: AtomicU64,
    pub stats: DnaSimStats,
}

const OP_SYNTH: u64 = 1;
const OP_SEQ: u64 = 2;
const OP_CHANNEL: u64 = 3;

impl DnaSimMedia {
    /// `store` keeps the simulated pools (electronic storage) and must be empty or hold only pools of this
    /// simulator; `codec` is [`crate::codec::WorkerCodec`] (the VNX-DNA codec) outside tests.
    pub fn new(store: Arc<dyn MediaAdapter>, codec: Arc<dyn Codec>, cfg: DnaSimConfig) -> Result<DnaSimMedia> {
        if cfg.read_attempts == 0 || cfg.max_in_flight == 0 {
            return err(ErrorCode::InvalidArgument, "dna-sim: read_attempts and max_in_flight must be >= 1");
        }
        if cfg.seed >= 1 << 63 {
            return err(ErrorCode::InvalidArgument, "dna-sim: the seed must fit in 63 bits");
        }
        Ok(DnaSimMedia { store, codec, cfg, reads: AtomicU64::new(0), puts: AtomicU64::new(0), stats: DnaSimStats::default() })
    }

    fn model_time(&self, t: Duration, counter: &AtomicU64) {
        counter.fetch_add(u64::try_from(t.as_nanos()).unwrap_or(u64::MAX), Ordering::Relaxed);
        if self.cfg.latency.sleep && !t.is_zero() {
            std::thread::sleep(t);
        }
    }

    fn fails(&self, op: u64, at: GenerationRef, n: u64, ppm: u32) -> bool {
        ppm > 0 && SplitMix64::keyed(&[self.cfg.seed, op, at.extent.0, at.generation.get(), n]) % 1_000_000 < u64::from(ppm)
    }

    /// The simulator's counters and configuration, labelled SIMULATED.
    pub fn report(&self) -> Value {
        let n = |a: &AtomicU64| Value::Int(i64::try_from(a.load(Ordering::Relaxed)).unwrap_or(i64::MAX));
        let s = &self.stats;
        obj([
            ("evidence", Value::Str("SIMULATED".into())),
            ("adapter", Value::Str("dna-sim".into())),
            ("codec", Value::Str(self.codec.name().into())),
            ("channel", self.cfg.channel.clone().map_or(Value::Null, Value::Str)),
            ("seed", Value::Int(self.cfg.seed as i64)),
            ("synthesis_runs", n(&s.synthesis_runs)),
            ("synthesis_failures", n(&s.synthesis_failures)),
            ("sequencing_runs", n(&s.sequencing_runs)),
            ("sequencing_failures", n(&s.sequencing_failures)),
            ("decode_failures", n(&s.decode_failures)),
            ("reads_unrecoverable", n(&s.reads_unrecoverable)),
            ("modelled_synthesis_ns", n(&s.modelled_synthesis_ns)),
            ("modelled_sequencing_ns", n(&s.modelled_sequencing_ns)),
            ("note", Value::Str("simulated runs and modelled times; not measurements of any physical medium".into())),
        ])
    }

    fn present(&self, at: GenerationRef) -> Result<bool> {
        match self.store.get(at) {
            Ok(_) => Ok(true),
            Err(e) if e.code == ErrorCode::NotFound => Ok(false),
            Err(e) => Err(e),
        }
    }
}

impl MediaAdapter for DnaSimMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "dna-sim", durability: Durability::SimulatedDna, max_in_flight: self.cfg.max_in_flight }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        if self.present(at)? {
            return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored"));
        }
        let envelope = if metadata_only(at) {
            Envelope::Metadata { sha256: sha256(data), bytes: data.to_vec() }
        } else {
            let n = self.puts.fetch_add(1, Ordering::Relaxed);
            self.stats.synthesis_runs.fetch_add(1, Ordering::Relaxed);
            self.model_time(
                LatencyModel::time(self.cfg.latency.synthesis_fixed, self.cfg.latency.synthesis_per_kib, data.len()),
                &self.stats.modelled_synthesis_ns,
            );
            if self.fails(OP_SYNTH, at, n, self.cfg.failures.synthesis_failure_ppm) {
                self.stats.synthesis_failures.fetch_add(1, Ordering::Relaxed);
                return err(ErrorCode::BackendUnavailable, format!("SIMULATED synthesis run for {at:?} failed"));
            }
            let framed = frame(at, data);
            let pool = self.codec.encode(at, &framed)?;
            Envelope::Dna { frame_sha256: sha256(&framed), frame_len: framed.len() as u64, payload: pool }
        };
        let stored = envelope.to_bytes();
        let r = self.store.put(at, &stored)?;
        if r.length != stored.len() as u64 || r.sha256 != sha256(&stored) {
            return err(ErrorCode::Corruption, format!("pool store receipt for {at:?} does not describe the pool"));
        }
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let stored = self.store.get(at)?;
        match Envelope::parse(at, &stored)? {
            Envelope::Metadata { bytes, .. } if metadata_only(at) => Ok(bytes),
            Envelope::Dna { frame_sha256, frame_len, payload } if !metadata_only(at) => {
                if frame_len < FRAME_OVERHEAD as u64 {
                    return err(ErrorCode::Corruption, format!("{at:?}: pool envelope is damaged"));
                }
                let mut last = None;
                for _ in 0..self.cfg.read_attempts {
                    let n = self.reads.fetch_add(1, Ordering::Relaxed);
                    self.stats.sequencing_runs.fetch_add(1, Ordering::Relaxed);
                    self.model_time(
                        LatencyModel::time(self.cfg.latency.sequencing_fixed, self.cfg.latency.sequencing_per_kib, frame_len as usize),
                        &self.stats.modelled_sequencing_ns,
                    );
                    if self.fails(OP_SEQ, at, n, self.cfg.failures.sequencing_failure_ppm) {
                        self.stats.sequencing_failures.fetch_add(1, Ordering::Relaxed);
                        return err(ErrorCode::BackendUnavailable, format!("SIMULATED sequencing run for {at:?} failed"));
                    }
                    let channel = self.cfg.channel.as_ref().map(|m| ChannelRun {
                        model: m.clone(),
                        seed: SplitMix64::keyed(&[self.cfg.seed, OP_CHANNEL, at.extent.0, at.generation.get(), n]) >> 1,
                    });
                    match self.codec.decode(at, &payload, &frame_sha256, frame_len, channel.as_ref()) {
                        Ok(framed) => return unframe(at, &framed),
                        Err(e) if e.code == ErrorCode::Corruption => {
                            self.stats.decode_failures.fetch_add(1, Ordering::Relaxed);
                            last = Some(e);
                            if channel.is_none() {
                                break; // a perfect channel gives the same reads every time
                            }
                        }
                        Err(e) => return Err(e),
                    }
                }
                self.stats.reads_unrecoverable.fetch_add(1, Ordering::Relaxed);
                Err(last.expect("at least one attempt"))
            }
            _ => err(ErrorCode::Corruption, format!("{at:?}: stored envelope does not fit this generation")),
        }
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.store.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.store.list()
    }

    fn sync(&self) -> Result<()> {
        self.store.sync()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation};
    use crate::codec::ToyCodec;
    use crate::media::{conformance, MemoryMedia};

    fn at(e: u64, g: u64) -> GenerationRef {
        GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
    }

    fn sim(cfg: DnaSimConfig) -> (Arc<MemoryMedia>, DnaSimMedia) {
        let store = Arc::new(MemoryMedia::new());
        let m = DnaSimMedia::new(store.clone(), Arc::new(ToyCodec), cfg).unwrap();
        (store, m)
    }

    #[test]
    fn dna_sim_passes_conformance_with_and_without_a_channel() {
        conformance(&sim(DnaSimConfig::default()).1).unwrap();
        let (_, m) = sim(DnaSimConfig { channel: Some("toy-sub-1000".into()), seed: 4, ..Default::default() });
        conformance(&m).unwrap();
        assert_eq!(m.info().durability, Durability::SimulatedDna);
        assert_eq!(m.report().as_obj().unwrap()["evidence"], Value::Str("SIMULATED".into()));
    }

    #[test]
    fn an_unrecoverable_channel_is_corruption_after_every_attempt() {
        let (_, m) = sim(DnaSimConfig { channel: Some("toy-drop-950000".into()), read_attempts: 3, ..Default::default() });
        m.put(at(1, 1), &[7u8; 200]).unwrap();
        assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(m.stats.sequencing_runs.load(Ordering::Relaxed), 3);
        assert_eq!(m.stats.reads_unrecoverable.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn a_pool_moved_to_another_generation_or_damaged_is_never_returned() {
        let (store, m) = sim(DnaSimConfig::default());
        m.put(at(1, 1), b"first").unwrap();
        m.put(at(2, 1), b"second").unwrap();
        let moved = store.get(at(2, 1)).unwrap();
        store.remove(at(1, 1)).unwrap();
        store.put(at(1, 1), &moved).unwrap();
        assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::Corruption);
        let mut rotten = store.get(at(2, 1)).unwrap();
        let mid = rotten.len() - 10;
        rotten[mid] = if rotten[mid] == b'A' { b'C' } else { b'A' };
        store.remove(at(2, 1)).unwrap();
        store.put(at(2, 1), &rotten).unwrap();
        assert_eq!(m.get(at(2, 1)).unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn seeded_run_failures_are_deterministic_and_retryable() {
        let run = |seed| {
            let (_, m) = sim(DnaSimConfig {
                seed,
                failures: FailureModel { synthesis_failure_ppm: 300_000, sequencing_failure_ppm: 300_000 },
                ..Default::default()
            });
            (0..40u64)
                .map(|e| {
                    let p = m.put(at(e, 1), &[e as u8; 64]).map_err(|e| e.code);
                    let g = m.get(at(e, 1)).map(|d| d == vec![e as u8; 64]).map_err(|e| e.code);
                    (p.is_ok(), g)
                })
                .collect::<Vec<_>>()
        };
        let a = run(11);
        assert_eq!(a, run(11), "same seed, same outcomes");
        assert_ne!(a, run(12));
        assert!(a.iter().any(|(p, _)| !p), "some synthesis runs fail");
        assert!(a.iter().any(|(_, g)| *g == Err(ErrorCode::BackendUnavailable)), "some sequencing runs fail");
        for (p, g) in &a {
            match (p, g) {
                (true, Ok(ok)) => assert!(ok),
                (true, Err(c)) => assert_eq!(*c, ErrorCode::BackendUnavailable),
                (false, Err(c)) => assert_eq!(*c, ErrorCode::NotFound, "a failed synthesis stores nothing"),
                (false, Ok(_)) => panic!("a failed put must leave nothing to read"),
            }
        }
    }

    #[test]
    fn modelled_latency_is_accounted_not_measured() {
        let lat = LatencyModel { synthesis_fixed: Duration::from_secs(3600), sequencing_fixed: Duration::from_secs(60), ..Default::default() };
        let (_, m) = sim(DnaSimConfig { latency: lat, ..Default::default() });
        let t = std::time::Instant::now();
        m.put(at(0, 1), b"x").unwrap();
        m.get(at(0, 1)).unwrap();
        assert!(t.elapsed() < Duration::from_secs(5), "accounted only, not slept");
        assert_eq!(m.stats.modelled_synthesis_ns.load(Ordering::Relaxed), 3_600_000_000_000);
        assert_eq!(m.stats.modelled_sequencing_ns.load(Ordering::Relaxed), 60_000_000_000);
    }
}
