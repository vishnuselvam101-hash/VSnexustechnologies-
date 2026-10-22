//! VNX-IO: logical block requests over a [`Volume`] (master directive §7; docs/V14_VOLUME_AND_IO.md §5–§6).
//!
//! [`BlockIo`] turns byte-range requests into extent operations: a request is split into extent spans; a span that
//! covers its whole extent replaces it, a partial span is a read-modify-write under the extent's lock. A write is
//! ONE transaction across all its extents (`ExtentStore::write_extents`, a single `map_batch` journal record): after
//! a crash or an error either all of it is applied or none of it, as long as it spans at most
//! [`MAX_BATCH_EXTENTS`] extents (every request of up to 64 MiB does when extents are larger than 64 KiB; a request
//! spanning more extents is applied in transactions of that many extents, in order). Reads of unwritten ranges return zeros; every other
//! byte returned was verified by the volume. With a VNX-RAM manager ([`BlockIo::with_ram`]) every request leases
//! its transient working memory from the `io` quota first.
//!
//! [`IoQueue`] runs requests on a fixed set of worker threads behind a bounded queue:
//! * **backpressure**: `submit` blocks while the queue is full; `try_submit` returns RESOURCE_EXHAUSTED instead;
//! * **ordering**: a request never starts before an earlier-submitted request it conflicts with has completed
//!   (two requests conflict when their extent ranges overlap and at least one writes); non-conflicting requests run
//!   in parallel. Overlapping writes are therefore applied in submission order;
//! * **barriers**: `flush` waits for every earlier request, then syncs the backend; later requests wait for it;
//! * **cancellation / timeouts**: a request cancelled or past its deadline before it starts completes with
//!   CANCELLED / TIMEOUT at once (even while it waits behind a conflicting request) and has no effect; once started,
//!   a request runs to completion (each extent commit is atomic);
//! * **size**: a request larger than [`MAX_REQUEST_BYTES`] is INVALID_ARGUMENT (no unbounded allocation);
//! * **panics**: a request whose execution panics completes with INTERNAL and frees its place; the queue keeps going;
//! * **shutdown**: new submissions fail with SHUTTING_DOWN; queued requests are drained (or failed with
//!   SHUTTING_DOWN when not draining); workers are joined.
//!
//! The scheduler keeps one lock over the queue and the set of pending requests; the earliest pending request never
//! has an earlier conflict, so the queue always makes progress.

use crate::addr::{ExtentId, Geometry};
use crate::error::{err, ErrorCode, Result};
use crate::ram::{Lease, MemoryManager, Subsystem, Wait};
use crate::record::MAX_BATCH_EXTENTS;
use crate::state::WriteState;
use crate::volume::{Patch, Volume};
use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, SyncSender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Largest single read or write (the host block layer splits larger I/O; NBD clients use at most 32 MiB).
pub const MAX_REQUEST_BYTES: u64 = 64 << 20;
pub const MAX_QUEUE_DEPTH: usize = 4096;

fn check_size(len: u64) -> Result<()> {
    if len > MAX_REQUEST_BYTES {
        return err(ErrorCode::InvalidArgument, format!("request of {len} bytes exceeds {MAX_REQUEST_BYTES}"));
    }
    Ok(())
}

/// Whole-extent storage under VNX-IO: the volume itself, or the cache in front of it.
pub trait ExtentStore: Send + Sync {
    fn geometry(&self) -> Geometry;
    fn read_extent(&self, extent: ExtentId) -> Result<Vec<u8>>;
    /// Replace one whole extent; returns the write state reached.
    /// `now_ms` is the caller-supplied current time for retention checks (deterministic clock).
    fn write_extent(&self, extent: ExtentId, data: &[u8], now_ms: i64) -> Result<WriteState>;
    /// Read-modify-write of one extent, atomic with respect to other operations on it.
    /// `now_ms` is the caller-supplied current time for retention checks (deterministic clock).
    fn modify_extent(&self, extent: ExtentId, patch: &mut dyn FnMut(&mut [u8]), now_ms: i64) -> Result<WriteState>;
    /// Apply several extent patches as ONE transaction (all or none); extents strictly increasing, at most
    /// [`MAX_BATCH_EXTENTS`].
    /// `now_ms` is the caller-supplied current time for retention checks (deterministic clock).
    fn write_extents(&self, patches: &[Patch<'_>], now_ms: i64) -> Result<WriteState>;
    fn flush(&self) -> Result<()>;
}

impl ExtentStore for Volume {
    fn geometry(&self) -> Geometry {
        Volume::geometry(self)
    }
    fn read_extent(&self, extent: ExtentId) -> Result<Vec<u8>> {
        Volume::read_extent(self, extent)
    }
    fn write_extent(&self, extent: ExtentId, data: &[u8], now_ms: i64) -> Result<WriteState> {
        Volume::write_extent(self, extent, data, now_ms).map(|o| o.state)
    }
    fn modify_extent(&self, extent: ExtentId, patch: &mut dyn FnMut(&mut [u8]), now_ms: i64) -> Result<WriteState> {
        Volume::modify_extent(self, extent, |b| patch(b), now_ms).map(|o| o.state)
    }
    fn write_extents(&self, patches: &[Patch<'_>], now_ms: i64) -> Result<WriteState> {
        Volume::write_extents(self, patches, now_ms)
    }
    fn flush(&self) -> Result<()> {
        Volume::flush(self)
    }
}

/// Synchronous byte-range I/O over an extent store.
#[derive(Clone)]
pub struct BlockIo {
    store: Arc<dyn ExtentStore>,
    vol: Arc<Volume>,
    ram: Option<Arc<MemoryManager>>,
}

impl BlockIo {
    /// I/O directly on the volume.
    pub fn new(vol: Arc<Volume>) -> BlockIo {
        BlockIo { store: vol.clone(), vol, ram: None }
    }

    /// I/O through `store` (e.g. the cache) in front of `vol`; both must have the same geometry.
    pub fn over(store: Arc<dyn ExtentStore>, vol: Arc<Volume>) -> Result<BlockIo> {
        if store.geometry() != vol.geometry() {
            return err(ErrorCode::InvalidArgument, "the extent store and the volume have different geometries");
        }
        Ok(BlockIo { store, vol, ram: None })
    }

    /// Lease every request's transient working memory from the `io` quota of `ram` (waiting while it is in use by
    /// other requests). A request whose working memory exceeds the whole quota is INVALID_ARGUMENT.
    pub fn with_ram(mut self, ram: Arc<MemoryManager>) -> BlockIo {
        self.ram = Some(ram);
        self
    }

    pub fn volume(&self) -> &Arc<Volume> {
        &self.vol
    }

    /// Upper bound of the memory a request of `len` bytes allocates while it runs: its own buffer, plus for a read
    /// one extent copy at a time; for a write one more copy of the request (the new extent contents a store may
    /// build), two read-modify-write copies (the first and last extents) and one readback copy at a time.
    pub fn working_bytes(&self, len: u64, write: bool) -> u64 {
        let ext = self.vol.geometry().extent_bytes();
        if write {
            len.saturating_mul(2).saturating_add(ext.saturating_mul(3))
        } else {
            len.saturating_add(ext)
        }
    }

    fn lease(&self, len: u64, write: bool) -> Result<Option<Lease>> {
        match &self.ram {
            None => Ok(None),
            Some(ram) => ram.reserve(Subsystem::Io, self.working_bytes(len, write), Wait::Forever).map(Some),
        }
    }

    pub fn read(&self, offset: u64, len: u64) -> Result<Vec<u8>> {
        check_size(len)?;
        let spans = self.vol.geometry().spans(offset, len)?;
        let _lease = self.lease(len, false)?;
        let len = usize::try_from(len).or_else(|_| err(ErrorCode::Overflow, "read length exceeds the address space"))?;
        let mut out = vec![0u8; len];
        for s in spans {
            let data = self.store.read_extent(s.extent)?;
            let (a, b) = (s.offset as usize, (s.offset + s.len) as usize);
            out[s.buf_offset as usize..(s.buf_offset + s.len) as usize].copy_from_slice(&data[a..b]);
        }
        Ok(out)
    }

    /// Write `data` at `offset` as one transaction (see the module documentation); returns the weakest write state
    /// reached. `now_ms` is the caller-supplied current time for retention checks (deterministic clock).
    pub fn write(&self, offset: u64, data: &[u8], now_ms: i64) -> Result<WriteState> {
        check_size(data.len() as u64)?;
        let spans = self.vol.geometry().spans(offset, data.len() as u64)?;
        let _lease = self.lease(data.len() as u64, true)?;
        let patches: Vec<Patch<'_>> = spans
            .iter()
            .map(|s| Patch { extent: s.extent, offset: s.offset, data: &data[s.buf_offset as usize..(s.buf_offset + s.len) as usize] })
            .collect();
        let mut weakest = WriteState::Verified;
        for chunk in patches.chunks(MAX_BATCH_EXTENTS) {
            weakest = weakest.min(self.store.write_extents(chunk, now_ms)?);
        }
        Ok(weakest)
    }

    pub fn flush(&self) -> Result<()> {
        self.store.flush()
    }
}

/// A request for the queue.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Op {
    Read { offset: u64, len: u64 },
    Write { offset: u64, data: Vec<u8>, now_ms: i64 },
    Flush,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Reply {
    Data(Vec<u8>),
    Written(WriteState),
    Flushed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Footprint {
    Barrier,
    Extents { first: u64, last: u64, write: bool },
}

impl Footprint {
    fn conflicts(&self, other: &Footprint) -> bool {
        match (self, other) {
            (Footprint::Barrier, _) | (_, Footprint::Barrier) => true,
            (Footprint::Extents { first: a, last: b, write: w1 }, Footprint::Extents { first: c, last: d, write: w2 }) => {
                (*w1 || *w2) && a <= d && c <= b
            }
        }
    }
}

struct Job {
    seq: u64,
    op: Op,
    cancel: Arc<AtomicBool>,
    deadline: Option<Instant>,
    reply: SyncSender<Result<Reply>>,
    submitted: Instant,
}

struct Sched {
    queue: VecDeque<Job>,
    /// queued and running requests by sequence number
    pending: BTreeMap<u64, Footprint>,
    next_seq: u64,
    shutting: bool,
    drain: bool,
}

#[derive(Debug, Default)]
pub struct IoMetrics {
    pub submitted: AtomicU64,
    pub completed: AtomicU64,
    pub failed: AtomicU64,
    pub cancelled: AtomicU64,
    pub timed_out: AtomicU64,
    pub rejected_full: AtomicU64,
    pub max_queue_depth: AtomicU64,
    /// sum of the time requests waited before a worker started them, in microseconds
    pub queue_wait_us: AtomicU64,
}

struct Shared {
    io: BlockIo,
    depth: usize,
    sched: Mutex<Sched>,
    /// signalled when a request is queued or completes, and on shutdown
    work: Condvar,
    /// signalled when the queue has room
    room: Condvar,
    metrics: IoMetrics,
}

/// Handle of a submitted request.
pub struct Ticket {
    pub seq: u64,
    cancel: Arc<AtomicBool>,
    rx: Receiver<Result<Reply>>,
    shared: Arc<Shared>,
}

impl Ticket {
    /// Ask for the request not to run; it has no effect on a request that already started.
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
        self.shared.work.notify_all(); // a waiting worker resolves it now
    }

    pub fn wait(self) -> Result<Reply> {
        self.rx.recv().unwrap_or_else(|_| err(ErrorCode::Internal, "the I/O worker dropped the request"))
    }

    pub fn wait_timeout(&self, t: Duration) -> Option<Result<Reply>> {
        match self.rx.recv_timeout(t) {
            Ok(r) => Some(r),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => Some(err(ErrorCode::Internal, "the I/O worker dropped the request")),
        }
    }
}

pub struct IoQueue {
    shared: Arc<Shared>,
    workers: Vec<JoinHandle<()>>,
}

#[derive(Clone, Copy, Debug)]
pub struct QueueConfig {
    pub workers: usize,
    /// maximum queued (not yet started) requests
    pub depth: usize,
}

fn cpus() -> usize {
    std::thread::available_parallelism().map_or(1, |n| n.get())
}

impl QueueConfig {
    /// The measured default (docs/V14_LOAD_BALANCER.md §5): min(CPUs, 2 x the backend's `max_in_flight`) workers.
    /// More workers than that only wait for backend slots (higher p99, no more throughput); fewer leave CPUs idle
    /// on a fast backend.
    pub fn for_backend(max_in_flight: u32) -> QueueConfig {
        let w = cpus().min(2 * max_in_flight.max(1) as usize).clamp(1, 64);
        QueueConfig { workers: w, depth: 64 }
    }
}

impl Default for QueueConfig {
    /// `for_backend(32)`: a fast electronic backend.
    fn default() -> Self {
        QueueConfig::for_backend(32)
    }
}

impl IoQueue {
    pub fn start(io: BlockIo, cfg: QueueConfig) -> Result<IoQueue> {
        if cfg.workers == 0 || cfg.workers > 256 || cfg.depth == 0 || cfg.depth > MAX_QUEUE_DEPTH {
            return err(ErrorCode::InvalidArgument, format!("queue config {cfg:?}: workers 1..=256, depth 1..={MAX_QUEUE_DEPTH}"));
        }
        let shared = Arc::new(Shared {
            io,
            depth: cfg.depth,
            sched: Mutex::new(Sched { queue: VecDeque::new(), pending: BTreeMap::new(), next_seq: 0, shutting: false, drain: true }),
            work: Condvar::new(),
            room: Condvar::new(),
            metrics: IoMetrics::default(),
        });
        let workers = (0..cfg.workers)
            .map(|i| {
                let s = shared.clone();
                std::thread::Builder::new()
                    .name(format!("vnxd-io-{i}"))
                    .spawn(move || worker(&s))
                    .or_else(|e| err(ErrorCode::ResourceExhausted, format!("spawn I/O worker: {e}")))
            })
            .collect::<Result<Vec<_>>>()?;
        let mut workers = workers;
        // cancellations and deadlines are resolved by their own thread, so they complete at once even while every
        // worker is busy with a long request
        let s = shared.clone();
        workers.push(
            std::thread::Builder::new()
                .name("vnxd-io-reaper".into())
                .spawn(move || reaper(&s))
                .or_else(|e| err(ErrorCode::ResourceExhausted, format!("spawn I/O reaper: {e}")))?,
        );
        Ok(IoQueue { shared, workers })
    }

    pub fn metrics(&self) -> &IoMetrics {
        &self.shared.metrics
    }

    fn footprint(&self, op: &Op) -> Result<Footprint> {
        let g = self.shared.io.vol.geometry();
        let (offset, len, write) = match op {
            Op::Flush => return Ok(Footprint::Barrier),
            Op::Read { offset, len } => (*offset, *len, false),
            Op::Write { offset, data, now_ms: _ } => (*offset, data.len() as u64, true),
        };
        check_size(len)?;
        g.check_range(offset, len)?;
        let eb = g.extent_bytes();
        Ok(Footprint::Extents { first: offset / eb, last: (offset + len - 1) / eb, write })
    }

    /// Submit a request; blocks while the queue is full.
    pub fn submit(&self, op: Op, deadline: Option<Instant>) -> Result<Ticket> {
        self.enqueue(op, deadline, true)
    }

    /// Submit a request; RESOURCE_EXHAUSTED (retryable) when the queue is full.
    pub fn try_submit(&self, op: Op, deadline: Option<Instant>) -> Result<Ticket> {
        self.enqueue(op, deadline, false)
    }

    fn enqueue(&self, op: Op, deadline: Option<Instant>, block: bool) -> Result<Ticket> {
        let fp = self.footprint(&op)?; // invalid requests fail at submission, never enter the queue
        let sh = &self.shared;
        let mut s = sh.sched.lock().or_else(|_| err(ErrorCode::Internal, "I/O scheduler lock poisoned"))?;
        loop {
            if s.shutting {
                return err(ErrorCode::ShuttingDown, "the I/O queue is shutting down");
            }
            if s.queue.len() < sh.depth {
                break;
            }
            if !block {
                sh.metrics.rejected_full.fetch_add(1, Ordering::Relaxed);
                return err(ErrorCode::ResourceExhausted, format!("I/O queue full ({} requests)", sh.depth));
            }
            s = sh.room.wait(s).or_else(|_| err(ErrorCode::Internal, "I/O scheduler lock poisoned"))?;
        }
        let seq = s.next_seq;
        s.next_seq += 1;
        let cancel = Arc::new(AtomicBool::new(false));
        let (tx, rx) = sync_channel(1);
        s.pending.insert(seq, fp);
        s.queue.push_back(Job { seq, op, cancel: cancel.clone(), deadline, reply: tx, submitted: Instant::now() });
        sh.metrics.submitted.fetch_add(1, Ordering::Relaxed);
        sh.metrics.max_queue_depth.fetch_max(s.queue.len() as u64, Ordering::Relaxed);
        drop(s);
        sh.work.notify_all();
        Ok(Ticket { seq, cancel, rx, shared: sh.clone() })
    }

    /// Stop accepting requests; with `drain`, run every queued request first, otherwise fail them with
    /// SHUTTING_DOWN. Returns after every worker has exited.
    pub fn shutdown(mut self, drain: bool) -> Result<()> {
        self.stop(drain);
        let mut panicked = 0;
        for w in self.workers.drain(..) {
            if w.join().is_err() {
                panicked += 1;
            }
        }
        if panicked > 0 {
            return err(ErrorCode::Internal, format!("{panicked} I/O workers panicked"));
        }
        Ok(())
    }

    fn stop(&self, drain: bool) {
        let mut s = self.shared.sched.lock().unwrap_or_else(|p| p.into_inner());
        s.shutting = true;
        s.drain = drain;
        drop(s);
        self.shared.work.notify_all();
        self.shared.room.notify_all();
    }
}

impl Drop for IoQueue {
    fn drop(&mut self) {
        if !self.workers.is_empty() {
            self.stop(true);
            for w in self.workers.drain(..) {
                let _ = w.join();
            }
        }
    }
}

/// Index in the queue of the first request whose earlier pending requests do not conflict with it.
fn runnable(s: &Sched) -> Option<usize> {
    s.queue.iter().position(|job| {
        let fp = s.pending[&job.seq];
        !s.pending.range(..job.seq).any(|(_, earlier)| earlier.conflicts(&fp))
    })
}

fn reaper(sh: &Shared) {
    let Ok(mut s) = sh.sched.lock() else { return };
    loop {
        if s.shutting && !s.drain {
            // fail the queued requests now, not when a worker becomes free
            while let Some(job) = s.queue.pop_front() {
                s.pending.remove(&job.seq);
                let _ = job.reply.send(err(ErrorCode::ShuttingDown, "the I/O queue shut down before the request ran"));
                sh.metrics.failed.fetch_add(1, Ordering::Relaxed);
            }
            sh.room.notify_all();
        }
        let before = s.queue.len();
        sweep(&mut s, sh);
        if s.queue.len() != before {
            sh.work.notify_all(); // a request that conflicted only with a resolved one may run now
        }
        if s.shutting && s.queue.is_empty() {
            return;
        }
        s = match sh.work.wait_timeout(s, Duration::from_millis(10)) {
            Ok((s, _)) => s,
            Err(_) => return,
        };
    }
}

fn worker(sh: &Shared) {
    loop {
        let job = {
            let mut s = match sh.sched.lock() {
                Ok(s) => s,
                Err(_) => return,
            };
            loop {
                sweep(&mut s, sh);
                if s.shutting && !s.drain {
                    // fail everything still queued
                    while let Some(job) = s.queue.pop_front() {
                        s.pending.remove(&job.seq);
                        let _ = job.reply.send(err(ErrorCode::ShuttingDown, "the I/O queue shut down before the request ran"));
                        sh.metrics.failed.fetch_add(1, Ordering::Relaxed);
                    }
                }
                if let Some(i) = runnable(&s) {
                    let job = s.queue.remove(i).expect("index from position");
                    sh.room.notify_one();
                    break job;
                }
                if s.shutting && s.queue.is_empty() {
                    sh.work.notify_all();
                    return;
                }
                // sleep until new work, a completion, a cancel, or the nearest deadline of a queued request
                let next = s.queue.iter().filter_map(|j| j.deadline).min();
                s = match next {
                    Some(d) => match sh.work.wait_timeout(s, d.saturating_duration_since(Instant::now()) + Duration::from_millis(1)) {
                        Ok((s, _)) => s,
                        Err(_) => return,
                    },
                    None => match sh.work.wait(s) {
                        Ok(s) => s,
                        Err(_) => return,
                    },
                };
            }
        };
        let waited = job.submitted.elapsed();
        sh.metrics.queue_wait_us.fetch_add(waited.as_micros().min(u64::MAX as u128) as u64, Ordering::Relaxed);
        let result = if let Some(r) = expired(&job, sh) {
            r
        } else {
            let run = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match job.op {
                Op::Read { offset, len } => sh.io.read(offset, len).map(Reply::Data),
                Op::Write { offset, ref data, now_ms } => sh.io.write(offset, data, now_ms).map(Reply::Written),
                Op::Flush => sh.io.flush().map(|_| Reply::Flushed),
            }));
            let r = run.unwrap_or_else(|_| err(ErrorCode::Internal, format!("request {} panicked", job.seq)));
            if r.is_ok() {
                sh.metrics.completed.fetch_add(1, Ordering::Relaxed);
            } else {
                sh.metrics.failed.fetch_add(1, Ordering::Relaxed);
            }
            r
        };
        let _ = job.reply.send(result);
        sh.sched.lock().unwrap_or_else(|p| p.into_inner()).pending.remove(&job.seq);
        sh.work.notify_all();
    }
}

/// The CANCELLED / TIMEOUT completion of a request that must not run, if it must not.
fn expired(job: &Job, sh: &Shared) -> Option<Result<Reply>> {
    if job.cancel.load(Ordering::SeqCst) {
        sh.metrics.cancelled.fetch_add(1, Ordering::Relaxed);
        return Some(err(ErrorCode::Cancelled, format!("request {} cancelled before it started", job.seq)));
    }
    if job.deadline.map_or(false, |d| Instant::now() > d) {
        sh.metrics.timed_out.fetch_add(1, Ordering::Relaxed);
        return Some(err(ErrorCode::Timeout, format!("request {} passed its deadline before it started", job.seq)));
    }
    None
}

/// Resolve every queued request that was cancelled or expired, wherever it waits in the queue.
fn sweep(s: &mut Sched, sh: &Shared) {
    let mut i = 0;
    while i < s.queue.len() {
        if let Some(r) = expired(&s.queue[i], sh) {
            let job = s.queue.remove(i).expect("index in range");
            s.pending.remove(&job.seq);
            let _ = job.reply.send(r);
            sh.room.notify_one();
        } else {
            i += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{Geometry, GenerationRef};
    use crate::journal::tests::TempDir;
    use crate::media::{CommitReceipt, MediaAdapter, MediaInfo, MemoryMedia};
    use crate::volume::VolumeConfig;

    /// Deterministic xorshift generator (no external crates).
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
        fn below(&mut self, n: u64) -> u64 {
            self.next() % n
        }
    }

    fn setup(d: &TempDir, media: Arc<dyn MediaAdapter>) -> BlockIo {
        // 512-byte blocks, 4 blocks per extent, 23 blocks: 6 extents, the last one 1536 bytes
        let g = Geometry::new(512, 4, 23).unwrap();
        let v = Volume::create(&d.0, g, [2; 16], 0, media, VolumeConfig::default()).unwrap();
        BlockIo::new(Arc::new(v))
    }

    #[test]
    fn random_unaligned_workload_matches_a_byte_model_across_reopen() {
        let d = TempDir::new("io");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let io = setup(&d, m.clone());
        let cap = io.volume().geometry().capacity_bytes();
        let mut model = vec![0u8; cap as usize];
        let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
        for step in 0..400u64 {
            let off = rng.below(cap);
            let len = 1 + rng.below((cap - off).min(5000));
            if rng.below(3) == 0 {
                assert_eq!(io.read(off, len).unwrap(), &model[off as usize..(off + len) as usize], "step {step}");
            } else {
                let data: Vec<u8> = (0..len).map(|i| (step * 31 + i * 7) as u8).collect();
                assert_eq!(io.write(off, &data, 1000).unwrap(), WriteState::Verified);
                model[off as usize..(off + len) as usize].copy_from_slice(&data);
            }
        }
        assert_eq!(io.read(0, cap).unwrap(), model);
        drop(io);
        let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
        assert!(r.orphans_removed.is_empty() && r.damaged.is_empty());
        assert_eq!(BlockIo::new(Arc::new(v)).read(0, cap).unwrap(), model);
        assert!(m.list().unwrap().len() <= 6 + 1, "one live generation per extent at most (+ the marker)");
    }

    #[test]
    fn random_crashes_never_lose_committed_extents_or_mix_contents() {
        use crate::volume::{FaultHook, Stage};
        let stages = [Stage::AfterPut, Stage::BeforeMapRecord, Stage::TornMapRecord, Stage::AfterMapRecord, Stage::AfterRemove];
        let d = TempDir::new("io-crash");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let g = setup(&d, m.clone()).volume().geometry();
        let eb = g.extent_bytes();
        let cap = g.capacity_bytes();
        let mut model = vec![0u8; cap as usize];
        let mut rng = Rng(0xDEAD_BEEF);
        let mut crashes = 0;
        for round in 0..60u64 {
            // crash at a random stage of the k-th extent commit of this round (or not at all)
            let crash_after = rng.below(6);
            let stage = stages[rng.below(5) as usize];
            let counter = Arc::new(AtomicU64::new(0));
            let c = counter.clone();
            let hook: FaultHook = Arc::new(move |s, _| {
                if s == stage && c.fetch_add(1, Ordering::SeqCst) == crash_after {
                    return err(ErrorCode::Internal, "injected crash");
                }
                Ok(())
            });
            let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            assert!(r.damaged.is_empty(), "round {round}");
            let io = BlockIo::new(Arc::new(v.with_fault_hook(hook)));
            assert_eq!(io.read(0, cap).unwrap(), model, "round {round}: state after recovery");
            for _ in 0..4 {
                let off = rng.below(cap);
                let len = 1 + rng.below((cap - off).min(3 * eb));
                let data = vec![(round as u8).wrapping_mul(7).wrapping_add(1); len as usize];
                match io.write(off, &data, 1000) {
                    Ok(_) => model[off as usize..(off + len) as usize].copy_from_slice(&data),
                    Err(_) => {
                        crashes += 1;
                        drop(io);
                        let (v, _) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
                        let after = BlockIo::new(Arc::new(v)).read(0, cap).unwrap();
                        let mut new_model = model.clone();
                        new_model[off as usize..(off + len) as usize].copy_from_slice(&data);
                        // the request is one transaction: all of it or none of it, across every extent it spans
                        assert!(after == model || after == new_model, "round {round}: a multi-extent write was split");
                        for e in 0..g.extent_count() {
                            let a = (e * eb) as usize;
                            let b = ((e + 1) * eb).min(cap) as usize;
                            let got = &after[a..b];
                            assert!(got == &model[a..b] || got == &new_model[a..b], "round {round} extent {e}: mixed content");
                            model[a..b].copy_from_slice(got);
                        }
                        break;
                    }
                }
            }
        }
        assert!(crashes >= 15, "only {crashes} crashes were injected");
    }

    #[test]
    fn a_failed_multi_extent_write_leaves_every_extent_unchanged() {
        // the backend refuses the third put: the extents already put are rolled back with the rest
        struct NthPutFails(MemoryMedia, AtomicU64);
        impl MediaAdapter for NthPutFails {
            fn info(&self) -> MediaInfo {
                self.0.info()
            }
            fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
                if self.1.fetch_add(1, Ordering::SeqCst) == 2 {
                    return err(ErrorCode::BackendUnavailable, "third put refused");
                }
                self.0.put(at, d)
            }
            fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
                self.0.get(at)
            }
            fn remove(&self, at: GenerationRef) -> Result<()> {
                self.0.remove(at)
            }
            fn list(&self) -> Result<Vec<GenerationRef>> {
                self.0.list()
            }
            fn sync(&self) -> Result<()> {
                Ok(())
            }
        }
        let d = TempDir::new("io-batch");
        let m = Arc::new(NthPutFails(MemoryMedia::new(), AtomicU64::new(u64::MAX / 2)));
        let io = setup(&d, m.clone());
        let cap = io.volume().geometry().capacity_bytes();
        io.write(0, &vec![1u8; cap as usize], 1000).unwrap();
        let before = (io.read(0, cap).unwrap(), m.list().unwrap());
        m.1.store(0, Ordering::SeqCst);
        // 100 bytes into extent 0 .. 100 bytes into extent 4: five extents, two of them partial
        assert_eq!(io.write(100, &vec![2u8; 4 * 2048], 1000).unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!((io.read(0, cap).unwrap(), m.list().unwrap()), before);
        io.write(100, &vec![2u8; 4 * 2048], 1000).unwrap();
        let mut want = vec![1u8; cap as usize];
        want[100..100 + 4 * 2048].fill(2);
        assert_eq!(io.read(0, cap).unwrap(), want);
    }

    #[test]
    fn requests_lease_their_working_memory_from_the_io_quota() {
        use crate::ram::{RamConfig, Subsystem};
        let d = TempDir::new("io-ram");
        let io = setup(&d, Arc::new(MemoryMedia::new()));
        let cap = io.volume().geometry().capacity_bytes();
        let mut cfg = RamConfig::split(1 << 20);
        cfg.quotas.insert(Subsystem::Io, 3 * io.working_bytes(4096, true));
        let ram = MemoryManager::new(cfg).unwrap();
        let io = io.with_ram(ram.clone());
        std::thread::scope(|sc| {
            for t in 0..8u64 {
                let io = io.clone();
                sc.spawn(move || {
                    for i in 0..40u64 {
                        let off = (t * 977 + i * 131) % (cap - 4096);
                        io.write(off, &vec![t as u8; 1 + (i * 37 % 4096) as usize], 1000).unwrap();
                        io.read(off, 1 + (i * 53 % 4096)).unwrap();
                    }
                });
            }
        });
        let u = ram.usage(Subsystem::Io);
        assert!(u.peak <= 3 * io.working_bytes(4096, true), "io quota respected: {u:?}");
        assert!(u.peak >= io.working_bytes(1, false), "requests did lease");
        assert!(ram.outstanding().is_empty(), "every lease returned");
        // a request that could never fit is refused before any effect
        let mut small = RamConfig::split(1 << 20);
        small.quotas.insert(Subsystem::Io, 4096);
        let d2 = TempDir::new("io-ram2");
        let io2 = setup(&d2, Arc::new(MemoryMedia::new())).with_ram(MemoryManager::new(small).unwrap());
        assert_eq!(io2.write(0, &[1u8; 10], 1000).unwrap_err().code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn invalid_requests_fail_before_any_effect() {
        let d = TempDir::new("io");
        let io = setup(&d, Arc::new(MemoryMedia::new()));
        let cap = io.volume().geometry().capacity_bytes();
        assert_eq!(io.read(cap - 1, 2).unwrap_err().code, ErrorCode::OutOfRange);
        assert_eq!(io.write(cap, &[1], 1000).unwrap_err().code, ErrorCode::OutOfRange);
        assert_eq!(io.write(0, &[], 1000).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(io.read(u64::MAX, 1).unwrap_err().code, ErrorCode::OutOfRange);
        assert_eq!(io.volume().stats.commits.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn concurrent_writers_on_disjoint_ranges_and_readers() {
        let d = TempDir::new("io");
        let io = setup(&d, Arc::new(MemoryMedia::new()));
        let cap = io.volume().geometry().capacity_bytes();
        // 6 writers, each owning a 1000-byte range that straddles extent boundaries
        std::thread::scope(|sc| {
            for w in 0..6u64 {
                let io = io.clone();
                sc.spawn(move || {
                    for round in 0..30u64 {
                        let data = vec![(w * 40 + round) as u8; 1000];
                        io.write(w * 1900 + 300, &data, 1000).unwrap();
                        assert_eq!(io.read(w * 1900 + 300, 1000).unwrap(), data);
                    }
                });
            }
            let io = io.clone();
            sc.spawn(move || {
                for _ in 0..50 {
                    assert_eq!(io.read(0, cap).unwrap().len() as u64, cap);
                }
            });
        });
        for w in 0..6u64 {
            assert_eq!(io.read(w * 1900 + 300, 1000).unwrap(), vec![(w * 40 + 29) as u8; 1000]);
        }
    }

    #[test]
    fn queued_overlapping_writes_apply_in_submission_order() {
        let d = TempDir::new("io");
        let io = setup(&d, Arc::new(MemoryMedia::new()));
        let cap = io.volume().geometry().capacity_bytes();
        let q = IoQueue::start(io.clone(), QueueConfig { workers: 8, depth: 16 }).unwrap();
        let mut model = vec![0u8; cap as usize];
        let mut rng = Rng(7);
        let mut tickets = Vec::new();
        for i in 0..300u64 {
            let off = rng.below(cap - 1);
            let len = 1 + rng.below((cap - off).min(3000));
            let data = vec![(i % 251) as u8 + 1; len as usize];
            model[off as usize..(off + len) as usize].copy_from_slice(&data);
            tickets.push(q.submit(Op::Write { offset: off, data, now_ms: 1000 }, None).unwrap());
            if i % 50 == 49 {
                // a read in the stream sees exactly the writes submitted before it
                let snapshot = model.clone();
                let t = q.submit(Op::Read { offset: 0, len: cap }, None).unwrap();
                tickets.push(t);
                let r = tickets.pop().unwrap().wait().unwrap();
                assert_eq!(r, Reply::Data(snapshot), "read after write {i}");
            }
        }
        for t in tickets {
            assert!(matches!(t.wait().unwrap(), Reply::Written(_)));
        }
        assert_eq!(q.submit(Op::Flush, None).unwrap().wait().unwrap(), Reply::Flushed);
        q.shutdown(true).unwrap();
        assert_eq!(io.read(0, cap).unwrap(), model);
    }

    /// Media whose put blocks until released (to hold requests in flight).
    struct Gate {
        inner: MemoryMedia,
        open: Mutex<bool>,
        cv: Condvar,
        /// puts currently blocked at the gate
        blocked: AtomicU64,
    }
    impl Gate {
        fn new() -> Gate {
            Gate { inner: MemoryMedia::new(), open: Mutex::new(false), cv: Condvar::new(), blocked: AtomicU64::new(0) }
        }
        fn release(&self) {
            *self.open.lock().unwrap() = true;
            self.cv.notify_all();
        }
        /// Wait until `n` puts are blocked at the gate (an event, not a fixed sleep: the tests also run under
        /// qemu-user emulation, where a thread can take far longer than a few milliseconds to start).
        fn wait_blocked(&self, n: u64) {
            let end = Instant::now() + Duration::from_secs(60);
            while self.blocked.load(Ordering::SeqCst) < n {
                assert!(Instant::now() < end, "{n} writes did not reach the backend");
                std::thread::sleep(Duration::from_millis(1));
            }
        }
    }
    impl MediaAdapter for Gate {
        fn info(&self) -> MediaInfo {
            self.inner.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
            let mut o = self.open.lock().unwrap();
            if !*o && at.extent != crate::volume::MARKER_EXTENT {
                self.blocked.fetch_add(1, Ordering::SeqCst);
                while !*o {
                    o = self.cv.wait(o).unwrap();
                }
                self.blocked.fetch_sub(1, Ordering::SeqCst);
            }
            drop(o);
            self.inner.put(at, d)
        }
        fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
            self.inner.get(at)
        }
        fn remove(&self, at: GenerationRef) -> Result<()> {
            self.inner.remove(at)
        }
        fn list(&self) -> Result<Vec<GenerationRef>> {
            self.inner.list()
        }
        fn sync(&self) -> Result<()> {
            Ok(())
        }
    }

    /// Opens the gate when dropped. Declared after the queue, so it drops first: a failing assertion then unwinds into
    /// a queue whose workers can finish, instead of hanging in the queue's drop (it joins workers stuck in the gate).
    struct OpenOnDrop(Arc<Gate>);
    impl Drop for OpenOnDrop {
        fn drop(&mut self) {
            self.0.release();
        }
    }

    #[test]
    fn backpressure_cancellation_timeout_and_barrier() {
        let d = TempDir::new("io");
        let gate = Arc::new(Gate::new());
        let io = setup(&d, gate.clone());
        let q = IoQueue::start(io.clone(), QueueConfig { workers: 2, depth: 3 }).unwrap();
        let _open = OpenOnDrop(gate.clone());
        // two writes to extent 0 and 1 start and block in the backend
        let w0 = q.submit(Op::Write { offset: 0, data: vec![1; 2048], now_ms: 1000 }, None).unwrap();
        let w1 = q.submit(Op::Write { offset: 2048, data: vec![2; 2048], now_ms: 1000 }, None).unwrap();
        gate.wait_blocked(2);
        // a flush (barrier), a write that will be cancelled and a plain write fill the queue. (No request with an
        // expired deadline is used to fill it: the reaper may resolve that one before the next submission, which
        // made the fullness check depend on thread timing; it failed under qemu-user emulation.)
        let fl = q.submit(Op::Flush, None).unwrap();
        let wc = q.submit(Op::Write { offset: 4096, data: vec![3; 10], now_ms: 1000 }, None).unwrap();
        let wx = q.submit(Op::Write { offset: 8192, data: vec![5; 10], now_ms: 1000 }, None).unwrap();
        assert_eq!(q.try_submit(Op::Read { offset: 0, len: 1 }, None).err().unwrap().code, ErrorCode::ResourceExhausted);
        assert!(fl.wait_timeout(Duration::from_millis(50)).is_none(), "the barrier waits for earlier writes");
        // cancelling frees a slot (resolved by the reaper while every worker is blocked); a write whose deadline has
        // already passed takes it and times out without running
        wc.cancel();
        let wt = q.submit(Op::Write { offset: 6144, data: vec![4; 10], now_ms: 1000 }, Some(Instant::now())).unwrap();
        assert_eq!(wc.wait_timeout(Duration::from_secs(30)).unwrap().unwrap_err().code, ErrorCode::Cancelled);
        assert_eq!(wt.wait_timeout(Duration::from_secs(30)).unwrap().unwrap_err().code, ErrorCode::Timeout);
        gate.release();
        assert!(matches!(w0.wait().unwrap(), Reply::Written(WriteState::Verified)));
        assert!(matches!(w1.wait().unwrap(), Reply::Written(_)));
        assert_eq!(fl.wait_timeout(Duration::from_secs(30)).unwrap().unwrap(), Reply::Flushed);
        assert!(matches!(wx.wait().unwrap(), Reply::Written(_)));
        assert_eq!(io.read(4096, 10).unwrap(), vec![0; 10], "a cancelled write has no effect");
        assert_eq!(io.read(6144, 10).unwrap(), vec![0; 10], "a timed-out write has no effect");
        assert_eq!(io.read(8192, 10).unwrap(), vec![5; 10]);
        let m = q.metrics();
        assert_eq!((m.cancelled.load(Ordering::Relaxed), m.timed_out.load(Ordering::Relaxed)), (1, 1));
        assert_eq!(m.rejected_full.load(Ordering::Relaxed), 1);
        assert!(m.max_queue_depth.load(Ordering::Relaxed) <= 3);
        q.shutdown(true).unwrap();
    }

    #[test]
    fn shutdown_without_drain_fails_queued_requests_and_refuses_new_ones() {
        let d = TempDir::new("io");
        let gate = Arc::new(Gate::new());
        let io = setup(&d, gate.clone());
        let q = IoQueue::start(io.clone(), QueueConfig { workers: 1, depth: 8 }).unwrap();
        let _open = OpenOnDrop(gate.clone());
        let running = q.submit(Op::Write { offset: 0, data: vec![5; 100], now_ms: 1000 }, None).unwrap();
        gate.wait_blocked(1);
        let queued: Vec<Ticket> =
            (0..4).map(|i| q.submit(Op::Write { offset: 2048 * (i + 1), data: vec![6; 100], now_ms: 1000 }, None).unwrap()).collect();
        let shared = q.shared.clone();
        let stopper = std::thread::spawn(move || q.shutdown(false));
        let end = Instant::now() + Duration::from_secs(60);
        while !shared.sched.lock().unwrap().shutting {
            assert!(Instant::now() < end, "shutdown did not start");
            std::thread::sleep(Duration::from_millis(1));
        }
        gate.release();
        stopper.join().unwrap().unwrap();
        assert!(matches!(running.wait().unwrap(), Reply::Written(_)), "a started request completes");
        for t in queued {
            assert_eq!(t.wait().unwrap_err().code, ErrorCode::ShuttingDown);
        }
        assert!(shared.sched.lock().unwrap().pending.is_empty());
        assert_eq!(io.read(2048, 100).unwrap(), vec![0; 100]);
        assert_eq!(io.read(0, 100).unwrap(), vec![5; 100]);
    }

    #[test]
    fn cancel_and_deadline_resolve_while_blocked_behind_a_stuck_write() {
        let d = TempDir::new("io");
        let gate = Arc::new(Gate::new());
        let io = setup(&d, gate.clone());
        let q = IoQueue::start(io.clone(), QueueConfig { workers: 1, depth: 8 }).unwrap(); // the only worker is stuck
        let _open = OpenOnDrop(gate.clone());
        let stuck = q.submit(Op::Write { offset: 0, data: vec![1; 100], now_ms: 1000 }, None).unwrap();
        gate.wait_blocked(1);
        let c = q.submit(Op::Write { offset: 10, data: vec![2; 10], now_ms: 1000 }, None).unwrap();
        let t = q.submit(Op::Read { offset: 0, len: 10 }, Some(Instant::now() + Duration::from_millis(40))).unwrap();
        c.cancel();
        assert_eq!(c.wait_timeout(Duration::from_secs(2)).unwrap().unwrap_err().code, ErrorCode::Cancelled);
        assert_eq!(t.wait_timeout(Duration::from_secs(2)).unwrap().unwrap_err().code, ErrorCode::Timeout);
        gate.release();
        assert!(matches!(stuck.wait().unwrap(), Reply::Written(_)));
        assert_eq!(io.read(10, 10).unwrap(), vec![1; 10], "the cancelled write had no effect");
        q.shutdown(true).unwrap();
    }

    /// Media that panics on get of extent 1.
    struct Bomb(MemoryMedia);
    impl MediaAdapter for Bomb {
        fn info(&self) -> MediaInfo {
            self.0.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
            self.0.put(at, d)
        }
        fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
            if at.extent.0 == 1 {
                panic!("adapter bug");
            }
            self.0.get(at)
        }
        fn remove(&self, at: GenerationRef) -> Result<()> {
            self.0.remove(at)
        }
        fn list(&self) -> Result<Vec<GenerationRef>> {
            self.0.list()
        }
        fn sync(&self) -> Result<()> {
            Ok(())
        }
    }

    #[test]
    fn a_panicking_request_fails_alone_and_the_queue_keeps_going() {
        let d = TempDir::new("io");
        let g = Geometry::new(512, 4, 23).unwrap();
        let cfg = VolumeConfig { verify_readback: false, ..Default::default() };
        let v = Volume::create(&d.0, g, [2; 16], 0, Arc::new(Bomb(MemoryMedia::new())), cfg).unwrap();
        let io = BlockIo::new(Arc::new(v));
        let q = IoQueue::start(io, QueueConfig { workers: 1, depth: 8 }).unwrap();
        q.submit(Op::Write { offset: 2048, data: vec![1; 2048], now_ms: 1000 }, None).unwrap().wait().unwrap();
        let boom = q.submit(Op::Read { offset: 2048, len: 10 }, None).unwrap();
        let after = q.submit(Op::Write { offset: 2048, data: vec![2; 10], now_ms: 1000 }, None).unwrap(); // conflicts with the read
        assert_eq!(boom.wait().unwrap_err().code, ErrorCode::Internal);
        assert_eq!(after.wait().unwrap_err().code, ErrorCode::Internal, "RMW of extent 1 reads it: it panics too");
        assert_eq!(q.submit(Op::Read { offset: 0, len: 10 }, None).unwrap().wait().unwrap(), Reply::Data(vec![0; 10]));
        q.submit(Op::Flush, None).unwrap().wait().unwrap();
        q.shutdown(true).unwrap();
    }

    #[test]
    fn oversized_requests_are_refused() {
        let d = TempDir::new("io");
        let g = Geometry::new(4096, 256, 1 << 20).unwrap(); // 4 GiB device (sparse: nothing is written)
        let v = Volume::create(&d.0, g, [3; 16], 0, Arc::new(MemoryMedia::new()), VolumeConfig::default()).unwrap();
        let io = BlockIo::new(Arc::new(v));
        assert_eq!(io.read(0, MAX_REQUEST_BYTES + 1).unwrap_err().code, ErrorCode::InvalidArgument);
        let q = IoQueue::start(io, QueueConfig::default()).unwrap();
        assert_eq!(q.submit(Op::Read { offset: 0, len: 1 << 32 }, None).err().unwrap().code, ErrorCode::InvalidArgument);
        assert!(IoQueue::start(q.shared.io.clone(), QueueConfig { workers: 1, depth: MAX_QUEUE_DEPTH + 1 }).is_err());
        q.shutdown(true).unwrap();
    }

    #[test]
    fn the_default_worker_count_follows_cpus_and_the_backend_limit() {
        let c = cpus();
        assert_eq!(QueueConfig::for_backend(2).workers, c.min(4));
        assert_eq!(QueueConfig::for_backend(32).workers, c.min(64).max(1));
        assert_eq!(QueueConfig::for_backend(0).workers, c.min(2));
        assert_eq!(QueueConfig::default().workers, QueueConfig::for_backend(32).workers);
    }

    #[test]
    fn submit_after_shutdown_and_bad_config() {
        let d = TempDir::new("io");
        let io = setup(&d, Arc::new(MemoryMedia::new()));
        assert!(IoQueue::start(io.clone(), QueueConfig { workers: 0, depth: 1 }).is_err());
        assert!(IoQueue::start(io.clone(), QueueConfig { workers: 1, depth: 0 }).is_err());
        let q = IoQueue::start(io.clone(), QueueConfig::default()).unwrap();
        q.stop(true);
        assert_eq!(q.submit(Op::Flush, None).err().unwrap().code, ErrorCode::ShuttingDown);
        let cap = io.volume().geometry().capacity_bytes();
        assert_eq!(q.submit(Op::Read { offset: cap, len: 1 }, None).err().unwrap().code, ErrorCode::OutOfRange);
        q.shutdown(true).unwrap();
    }
}
