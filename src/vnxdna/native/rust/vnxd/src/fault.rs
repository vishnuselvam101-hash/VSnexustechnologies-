//! Fault-injection media adapter (master directive §10, §13; docs/V14_FAILURE_AND_RECOVERY_MODEL.md §6).
//!
//! [`FaultMedia`] stores generations like [`MemoryMedia`] and misbehaves on command: each [`Rule`] names an
//! operation, a [`Fault`], how many matching operations to let through first (`skip`) and how many to fail (`times`).
//! It is the backend of the fault-injection matrix and of the fallback tests; it is never a device backend.
//! Unlike every real adapter it may violate the media contract on purpose (lying receipts, rotten or short reads):
//! the layers above must turn every such violation into a typed error, never into wrong data.
//!
//! Phase 7 (docs/V14_MEDIA_ADAPTERS.md §4) extends it into the fault-injection backend of the media layer:
//!
//! * [`FaultMedia::over`] injects into any adapter (the image backend, the DNA simulator), not only the in-memory
//!   store; durability is the wrapped adapter's;
//! * a seeded [`FaultPlan`] injects faults at random but deterministically: the decision for the n-th call of an
//!   operation kind is a pure function of (seed, kind, n), so the same seed and the same sequence of calls give the
//!   same faults ([`FaultMedia::trace`] records them); explicit [`Rule`]s are checked first;
//! * [`Fault::Delay`] slows an operation down and then performs it; [`FaultMedia::set_offline`] makes the whole
//!   medium unavailable (every operation BACKEND_UNAVAILABLE, nothing changes) until it is brought back, which is how
//!   recovery after an outage is tested.

use crate::addr::GenerationRef;
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo, MemoryMedia};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// SplitMix64 (Steele, Lea, Flood 2014): a small seeded generator for deterministic fault and simulator decisions.
/// Not for cryptography.
#[derive(Clone, Debug)]
pub struct SplitMix64(u64);

impl SplitMix64 {
    pub fn new(seed: u64) -> SplitMix64 {
        SplitMix64(seed)
    }

    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// true with probability `ppm` / 1,000,000
    pub fn below_ppm(&mut self, ppm: u32) -> bool {
        self.next_u64() % 1_000_000 < u64::from(ppm)
    }

    /// One draw keyed by several values (a pure function of the key; no state).
    pub fn keyed(key: &[u64]) -> u64 {
        let mut h = SplitMix64::new(0x5658_4E44_5F46_4C54); // "VXND_FLT"
        let mut v = h.next_u64();
        for k in key {
            h = SplitMix64::new(v ^ k);
            v = h.next_u64();
        }
        v
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MediaOp {
    Put,
    Get,
    Remove,
    List,
    Sync,
    /// any of the above
    Any,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Fault {
    /// fail with BACKEND_UNAVAILABLE, no effect
    Unavailable,
    /// fail with BACKEND_BUSY, no effect
    Busy,
    /// fail with TIMEOUT, no effect
    Timeout,
    /// put: store the bytes, then fail with BACKEND_UNAVAILABLE (the acknowledgement is lost)
    AckLost,
    /// put: store only the first half, return a receipt claiming all of it (a lying backend)
    TornPut,
    /// put: store the bytes, return a receipt with a wrong SHA-256
    WrongReceipt,
    /// get: return the bytes with one bit flipped
    BitRot,
    /// get: return the bytes without their last byte
    ShortRead,
    /// get: NOT_FOUND although the generation is stored (a lost object)
    Lost,
    /// put: store other bytes than given (silent corruption on the write path), honest receipt of the given bytes
    SilentCorruptWrite,
    /// put: store other bytes than given, then fail with BACKEND_UNAVAILABLE (a retry finds the wrong bytes)
    CorruptAckLost,
    /// any operation: wait this long, then perform it normally (a slow medium)
    Delay { millis: u32 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rule {
    pub op: MediaOp,
    pub fault: Fault,
    /// matching operations let through before the fault applies
    pub skip: u64,
    /// matching operations that fail (u64::MAX = all)
    pub times: u64,
}

impl Rule {
    pub fn always(op: MediaOp, fault: Fault) -> Rule {
        Rule { op, fault, skip: 0, times: u64::MAX }
    }

    pub fn once(op: MediaOp, fault: Fault) -> Rule {
        Rule { op, fault, skip: 0, times: 1 }
    }
}

/// A seeded random fault plan: each entry injects `fault` into a call of `op` with probability `ppm` / 1,000,000.
/// Entries are tried in order with independent draws; the first that fires applies.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FaultPlan {
    pub seed: u64,
    pub entries: Vec<(MediaOp, Fault, u32)>,
}

impl FaultPlan {
    /// Faults that are honest about failing (the adapter reports them): unavailable, busy, timeout, lost
    /// acknowledgements, slow reads, at `ppm` each (or a fraction of it).
    pub fn honest(seed: u64, ppm: u32) -> FaultPlan {
        let mut entries = Vec::new();
        for op in [MediaOp::Put, MediaOp::Get, MediaOp::Remove, MediaOp::List, MediaOp::Sync] {
            entries.push((op, Fault::Unavailable, ppm));
            entries.push((op, Fault::Busy, ppm / 2));
            entries.push((op, Fault::Timeout, ppm / 2));
        }
        entries.push((MediaOp::Put, Fault::AckLost, ppm / 2));
        entries.push((MediaOp::Get, Fault::Delay { millis: 1 }, ppm));
        FaultPlan { seed, entries }
    }

    /// Faults where the medium lies or rots (wrong receipts, torn or corrupted writes, bit rot, short reads, objects
    /// reported missing although stored): the layers above must detect every one of them.
    pub fn lying(seed: u64, ppm: u32) -> FaultPlan {
        let mut p = FaultPlan::honest(seed, ppm);
        for f in [Fault::TornPut, Fault::WrongReceipt, Fault::SilentCorruptWrite, Fault::CorruptAckLost] {
            p.entries.push((MediaOp::Put, f, ppm / 2));
        }
        for f in [Fault::BitRot, Fault::ShortRead, Fault::Lost] {
            p.entries.push((MediaOp::Get, f, ppm));
        }
        p
    }
}

/// One injected fault: the operation kind, its call number (from 0, per kind) and the fault.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Injection {
    pub op: MediaOp,
    pub call: u64,
    pub fault: Fault,
}

/// Longest fault trace kept (older entries are dropped).
const MAX_TRACE: usize = 1 << 16;

pub struct FaultMedia {
    inner: Arc<dyn MediaAdapter>,
    rules: Mutex<Vec<Rule>>,
    plan: Mutex<Option<FaultPlan>>,
    offline: AtomicBool,
    trace: Mutex<Vec<Injection>>,
    /// operations per kind (put, get, remove, list, sync)
    pub calls: [AtomicU64; 5],
    pub injected: AtomicU64,
}

impl Default for FaultMedia {
    fn default() -> FaultMedia {
        FaultMedia::over(Arc::new(MemoryMedia::new()))
    }
}

fn idx(op: MediaOp) -> usize {
    match op {
        MediaOp::Put => 0,
        MediaOp::Get => 1,
        MediaOp::Remove => 2,
        MediaOp::List => 3,
        MediaOp::Sync | MediaOp::Any => 4,
    }
}

impl FaultMedia {
    pub fn new() -> FaultMedia {
        FaultMedia::default()
    }

    /// Inject faults into `inner` (any adapter).
    pub fn over(inner: Arc<dyn MediaAdapter>) -> FaultMedia {
        FaultMedia {
            inner,
            rules: Mutex::new(Vec::new()),
            plan: Mutex::new(None),
            offline: AtomicBool::new(false),
            trace: Mutex::new(Vec::new()),
            calls: Default::default(),
            injected: AtomicU64::new(0),
        }
    }

    /// Inject faults into `inner` according to a seeded plan.
    pub fn seeded(inner: Arc<dyn MediaAdapter>, plan: FaultPlan) -> FaultMedia {
        let m = FaultMedia::over(inner);
        m.set_plan(Some(plan));
        m
    }

    pub fn set_plan(&self, plan: Option<FaultPlan>) {
        *self.plan.lock().unwrap_or_else(|p| p.into_inner()) = plan;
    }

    /// Take the whole medium offline (every operation fails BACKEND_UNAVAILABLE without effect) or bring it back.
    pub fn set_offline(&self, offline: bool) {
        self.offline.store(offline, Ordering::SeqCst);
    }

    /// Every fault injected so far, in order (the last 65,536).
    pub fn trace(&self) -> Vec<Injection> {
        self.trace.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    pub fn add(&self, rule: Rule) {
        self.rules.lock().unwrap_or_else(|p| p.into_inner()).push(rule);
    }

    pub fn clear(&self) {
        self.rules.lock().unwrap_or_else(|p| p.into_inner()).clear();
    }

    /// Direct access to the stored objects, bypassing every rule (test setup and inspection).
    pub fn raw(&self) -> &dyn MediaAdapter {
        self.inner.as_ref()
    }

    /// The fault that applies to this call of `op`, if any (consuming one of its `times`). A delay is served here
    /// (the call then proceeds normally); an offline medium fails every call.
    fn take(&self, op: MediaOp) -> Option<Fault> {
        let call = self.calls[idx(op)].fetch_add(1, Ordering::Relaxed);
        let fault = if self.offline.load(Ordering::SeqCst) { Some(Fault::Unavailable) } else { self.decide(op, call) };
        if let Some(f) = fault {
            self.injected.fetch_add(1, Ordering::Relaxed);
            let mut t = self.trace.lock().unwrap_or_else(|p| p.into_inner());
            if t.len() == MAX_TRACE {
                t.remove(0);
            }
            t.push(Injection { op, call, fault: f });
        }
        if let Some(Fault::Delay { millis }) = fault {
            std::thread::sleep(Duration::from_millis(u64::from(millis)));
            return None;
        }
        fault
    }

    fn decide(&self, op: MediaOp, call: u64) -> Option<Fault> {
        let mut rules = self.rules.lock().unwrap_or_else(|p| p.into_inner());
        for r in rules.iter_mut() {
            if r.op != op && r.op != MediaOp::Any {
                continue;
            }
            if r.skip > 0 {
                r.skip -= 1;
                continue;
            }
            if r.times == 0 {
                continue;
            }
            if r.times != u64::MAX {
                r.times -= 1;
            }
            return Some(r.fault);
        }
        drop(rules);
        let plan = self.plan.lock().unwrap_or_else(|p| p.into_inner());
        let plan = plan.as_ref()?;
        for (i, (pop, fault, ppm)) in plan.entries.iter().enumerate() {
            if (*pop == op || *pop == MediaOp::Any)
                && SplitMix64::keyed(&[plan.seed, idx(op) as u64, call, i as u64]) % 1_000_000 < u64::from(*ppm)
            {
                return Some(*fault);
            }
        }
        None
    }
}

fn plain_failure(f: Fault) -> Option<ErrorCode> {
    match f {
        Fault::Unavailable => Some(ErrorCode::BackendUnavailable),
        Fault::Busy => Some(ErrorCode::BackendBusy),
        Fault::Timeout => Some(ErrorCode::Timeout),
        _ => None,
    }
}

impl MediaAdapter for FaultMedia {
    fn info(&self) -> MediaInfo {
        let inner = self.inner.info();
        MediaInfo { kind: "faulty", durability: inner.durability, max_in_flight: inner.max_in_flight }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let fault = self.take(MediaOp::Put);
        if let Some(code) = fault.and_then(plain_failure) {
            return err(code, "injected put failure");
        }
        match fault {
            Some(Fault::AckLost) => {
                self.inner.put(at, data)?;
                err(ErrorCode::BackendUnavailable, "injected: put stored, acknowledgement lost")
            }
            Some(Fault::TornPut) => {
                self.inner.put(at, &data[..data.len() / 2])?;
                Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
            }
            Some(Fault::WrongReceipt) => {
                let mut r = self.inner.put(at, data)?;
                r.sha256[0] ^= 1;
                Ok(r)
            }
            Some(Fault::CorruptAckLost) => {
                let mut bad = data.to_vec();
                if let Some(b) = bad.last_mut() {
                    *b ^= 0x01;
                }
                self.inner.put(at, &bad)?;
                err(ErrorCode::BackendUnavailable, "injected: other bytes stored, acknowledgement lost")
            }
            Some(Fault::SilentCorruptWrite) => {
                let mut bad = data.to_vec();
                if let Some(b) = bad.first_mut() {
                    *b ^= 0x80;
                }
                self.inner.put(at, &bad)?;
                Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
            }
            _ => self.inner.put(at, data),
        }
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let fault = self.take(MediaOp::Get);
        if let Some(code) = fault.and_then(plain_failure) {
            return err(code, "injected get failure");
        }
        match fault {
            Some(Fault::Lost) => err(ErrorCode::NotFound, format!("injected: generation {at:?} lost")),
            Some(Fault::BitRot) => self.inner.get(at).map(|mut d| {
                let mid = d.len() / 2;
                if let Some(b) = d.get_mut(mid) {
                    *b ^= 0x04;
                }
                d
            }),
            Some(Fault::ShortRead) => self.inner.get(at).map(|mut d| {
                d.pop();
                d
            }),
            _ => self.inner.get(at),
        }
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        if let Some(code) = self.take(MediaOp::Remove).and_then(plain_failure) {
            return err(code, "injected remove failure");
        }
        self.inner.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        if let Some(code) = self.take(MediaOp::List).and_then(plain_failure) {
            return err(code, "injected list failure");
        }
        self.inner.list()
    }

    fn sync(&self) -> Result<()> {
        if let Some(code) = self.take(MediaOp::Sync).and_then(plain_failure) {
            return err(code, "injected sync failure");
        }
        self.inner.sync()
    }
}

/// Adds a fixed MODELLED latency to `put` and `get` of an adapter (benchmarks of scheduling and concurrency; the
/// numbers are model inputs, never measurements of any physical medium). `max_in_flight` is what the modelled
/// backend accepts at once; durability and sealing are the wrapped adapter's.
pub struct LatencyMedia {
    inner: std::sync::Arc<dyn MediaAdapter>,
    pub put: std::time::Duration,
    pub get: std::time::Duration,
    pub max_in_flight: u32,
}

impl LatencyMedia {
    pub fn new(inner: std::sync::Arc<dyn MediaAdapter>, put: std::time::Duration, get: std::time::Duration, max_in_flight: u32) -> LatencyMedia {
        LatencyMedia { inner, put, get, max_in_flight }
    }
}

impl MediaAdapter for LatencyMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "modelled-latency", max_in_flight: self.max_in_flight, ..self.inner.info() }
    }
    fn sealed(&self) -> bool {
        self.inner.sealed()
    }
    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        std::thread::sleep(self.put);
        self.inner.put(at, data)
    }
    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        std::thread::sleep(self.get);
        self.inner.get(at)
    }
    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.inner.remove(at)
    }
    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.inner.list()
    }
    fn sync(&self) -> Result<()> {
        self.inner.sync()
    }
}
