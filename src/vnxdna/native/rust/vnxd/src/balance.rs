//! VNX Load Balancer: scheduling of the service's background work and coordination of backend concurrency (master
//! directive §9; docs/V14_LOAD_BALANCER.md).
//!
//! [`Balancer`] runs tasks (encode, decode, verification, GC, maintenance — anything that is not a host request,
//! which [`crate::io::IoQueue`] orders) on a fixed pool of worker threads:
//!
//! * **classes and fairness**: every task has a [`Class`] with a weight. Among the classes that have a runnable task
//!   the scheduler starts the one with the smallest virtual time and then advances that class's virtual time by
//!   `1 / weight` (weighted fair queuing): under saturation each class gets worker starts in proportion to its
//!   weight, and no class with queued work is ever starved (a class that was idle does not bank credit: its virtual
//!   time is raised to the system virtual clock — the virtual time of the last started task — when it becomes
//!   backlogged again, and a class whose tasks all wait for a busy resource is raised likewise, so neither idle nor
//!   blocked time is banked as credit);
//! * **resources**: a task may name a resource (e.g. `media:image`, `codec`, `synthesis`) with a concurrency limit;
//!   it starts only while fewer than that many tasks hold the resource. A task whose resource is busy does not block
//!   the tasks behind it (the class's queue is scanned for the first runnable task);
//! * **backpressure**: each class queue is bounded; `submit` waits for room, `try_submit` returns RESOURCE_EXHAUSTED;
//! * **cancellation and deadlines**: a task cancelled, or past its deadline, before it starts completes with CANCELLED
//!   or TIMEOUT at once (also while it waits) and never runs; a started task runs to completion;
//! * **no nested waits**: a task must not wait for another task of the same balancer (with every worker busy waiting,
//!   nothing could run): `Ticket::wait` from a worker of the same balancer is refused with INTERNAL instead of
//!   deadlocking (a result that is already there may be taken). Tasks may submit follow-up tasks. Not guarded: waits
//!   from threads a task spawns itself, and wait cycles between two balancers — tasks must not do either;
//! * **panics**: a task that panics completes with INTERNAL; the worker keeps serving;
//! * **shutdown**: new submissions get SHUTTING_DOWN; queued tasks are drained or failed; workers are joined.
//!
//! [`LimitedMedia`] wraps a media adapter and never lets more than its limit of operations run at once (the
//! adapter's `max_in_flight` by default): a physical synthesis or sequencing backend is not as parallel as the CPU,
//! and its adapter says so. It is a leaf lock — held only around one adapter call, never while waiting for anything
//! else — so it cannot take part in a deadlock with the volume, cache or queue locks above it.

use crate::addr::GenerationRef;
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo};
use std::cell::Cell;
use std::collections::{BTreeMap, VecDeque};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Class {
    /// work a host request is waiting for (e.g. decoding an extent being read)
    Foreground,
    /// encoding and decoding not waited for by a host request (write-behind, prefetch)
    Codec,
    /// verification and scrubbing
    Verify,
    /// garbage collection and compaction
    Gc,
    /// everything else (statistics, housekeeping)
    Maintenance,
}

impl Class {
    pub const ALL: [Class; 5] = [Class::Foreground, Class::Codec, Class::Verify, Class::Gc, Class::Maintenance];

    pub fn as_str(self) -> &'static str {
        match self {
            Class::Foreground => "foreground",
            Class::Codec => "codec",
            Class::Verify => "verify",
            Class::Gc => "gc",
            Class::Maintenance => "maintenance",
        }
    }

    fn idx(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Debug)]
pub struct BalancerConfig {
    pub workers: usize,
    /// queued (not yet started) tasks per class
    pub depth: usize,
    /// weight per class, in [`Class::ALL`] order (each >= 1)
    pub weights: [u32; 5],
    /// resource name -> how many tasks may hold it at once (>= 1)
    pub resources: BTreeMap<String, u32>,
}

impl Default for BalancerConfig {
    /// The measured default (docs/V14_LOAD_BALANCER.md §5): max(1, CPUs / 4) workers — the most background workers
    /// that keep host I/O sharing the CPUs within 5% of its throughput and 20% of its p99 latency.
    fn default() -> Self {
        let workers = (std::thread::available_parallelism().map_or(1, |n| n.get()) / 4).max(1);
        BalancerConfig { workers, depth: 256, weights: [16, 8, 4, 2, 1], resources: BTreeMap::new() }
    }
}

/// Counters of one class.
#[derive(Debug, Default)]
pub struct ClassMetrics {
    pub submitted: AtomicU64,
    pub started: AtomicU64,
    pub completed: AtomicU64,
    pub failed: AtomicU64,
    pub cancelled: AtomicU64,
    pub timed_out: AtomicU64,
    pub rejected: AtomicU64,
    /// total and largest wait between submission and start, in microseconds
    pub wait_us: AtomicU64,
    pub max_wait_us: AtomicU64,
    /// total running time (wall time inside tasks, including any time a task blocks), in microseconds
    pub run_us: AtomicU64,
}

struct Job {
    resource: Option<String>,
    cancel: Arc<AtomicBool>,
    deadline: Option<Instant>,
    submitted: Instant,
    /// runs the task and sends its result; true when the task succeeded
    run: Box<dyn FnOnce() -> bool + Send>,
    fail: Box<dyn FnOnce(VnxError) + Send>,
}

struct State {
    queues: [VecDeque<Job>; 5],
    /// virtual time per class (units of 1 / weight)
    vtime: [u64; 5],
    /// the system virtual clock: the virtual time of the last started task (never decreases). A class that becomes
    /// backlogged again, or that could not run because its resource was busy, is raised to it, so idle or blocked
    /// time is never banked as credit
    sys_vt: u64,
    in_use: BTreeMap<String, u32>,
    running: usize,
    shutting: bool,
    drain: bool,
}

struct Shared {
    id: u64,
    cfg: BalancerConfig,
    state: Mutex<State>,
    work: Condvar,
    room: Condvar,
    /// wakes the reaper (cancellations, shutdown); never shared with the workers' `work`
    reap: Condvar,
    metrics: [ClassMetrics; 5],
    busy_us: AtomicU64,
    started_at: Instant,
}

thread_local! {
    /// the balancer whose worker this thread is (0 = none)
    static WORKER_OF: Cell<u64> = const { Cell::new(0) };
}

/// The result of a submitted task.
pub struct Ticket<T> {
    rx: Receiver<Result<T>>,
    cancel: Arc<AtomicBool>,
    shared: Arc<Shared>,
}

impl<T> Ticket<T> {
    /// Ask for the task not to run (no effect once it started).
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
        self.shared.reap.notify_all();
    }

    fn nested(&self) -> bool {
        WORKER_OF.with(|w| w.get()) == self.shared.id
    }

    pub fn wait(self) -> Result<T> {
        // a result that is already there can always be taken, also from a worker
        if let Ok(r) = self.rx.try_recv() {
            return r;
        }
        if self.nested() {
            return err(ErrorCode::Internal, "a balancer task may not wait for another task of the same balancer (it could deadlock)");
        }
        self.rx.recv().unwrap_or_else(|_| err(ErrorCode::Internal, "the balancer dropped the task"))
    }

    pub fn wait_timeout(&self, t: Duration) -> Option<Result<T>> {
        if let Ok(r) = self.rx.try_recv() {
            return Some(r);
        }
        if self.nested() {
            return Some(err(ErrorCode::Internal, "a balancer task may not wait for another task of the same balancer (it could deadlock)"));
        }
        match self.rx.recv_timeout(t) {
            Ok(r) => Some(r),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => Some(err(ErrorCode::Internal, "the balancer dropped the task")),
        }
    }
}

pub struct Balancer {
    shared: Arc<Shared>,
    workers: Vec<JoinHandle<()>>,
    reaper: Option<JoinHandle<()>>,
}

/// Join `h` unless it is the current thread (the last handle of a balancer may be dropped by one of its own tasks).
fn join_other(h: JoinHandle<()>) -> std::thread::Result<()> {
    if h.thread().id() == std::thread::current().id() {
        return Ok(()); // this thread returns to its loop and exits by itself
    }
    h.join()
}

const VT_SCALE: u64 = 1 << 20;
const SWEEP: Duration = Duration::from_millis(10);

impl Balancer {
    pub fn start(cfg: BalancerConfig) -> Result<Balancer> {
        if cfg.workers == 0 || cfg.workers > 256 || cfg.depth == 0 || cfg.depth > 1 << 16 {
            return err(ErrorCode::InvalidArgument, "balancer: 1..=256 workers, depth 1..=65536");
        }
        if cfg.weights.iter().any(|w| *w == 0 || *w > 1 << 16) || cfg.resources.values().any(|l| *l == 0) {
            return err(ErrorCode::InvalidArgument, "balancer: weights 1..=65536, resource limits >= 1");
        }
        static NEXT_ID: AtomicU64 = AtomicU64::new(1);
        let n = cfg.workers;
        let shared = Arc::new(Shared {
            id: NEXT_ID.fetch_add(1, Ordering::Relaxed),
            cfg,
            state: Mutex::new(State {
                queues: Default::default(),
                vtime: [0; 5],
                sys_vt: 0,
                in_use: BTreeMap::new(),
                running: 0,
                shutting: false,
                drain: true,
            }),
            work: Condvar::new(),
            room: Condvar::new(),
            reap: Condvar::new(),
            metrics: Default::default(),
            busy_us: AtomicU64::new(0),
            started_at: Instant::now(),
        });
        let workers = (0..n)
            .map(|i| {
                let sh = shared.clone();
                std::thread::Builder::new()
                    .name(format!("vnxd-lb-{i}"))
                    .spawn(move || worker(&sh))
                    .map_err(|e| VnxError::new(ErrorCode::ResourceExhausted, format!("spawn balancer worker: {e}")))
            })
            .collect::<Result<Vec<_>>>()?;
        // cancellations and deadlines are resolved by their own thread: with every worker busy (or held by long
        // tasks) a cancelled or expired task still completes at once
        let sh = shared.clone();
        let reaper = std::thread::Builder::new()
            .name("vnxd-lb-reaper".into())
            .spawn(move || reaper(&sh))
            .map_err(|e| VnxError::new(ErrorCode::ResourceExhausted, format!("spawn balancer reaper: {e}")))?;
        Ok(Balancer { shared, workers, reaper: Some(reaper) })
    }

    pub fn metrics(&self, class: Class) -> &ClassMetrics {
        &self.shared.metrics[class.idx()]
    }

    /// Share of worker time spent running tasks since the start (0..=1).
    pub fn utilisation(&self) -> f64 {
        let wall = self.shared.started_at.elapsed().as_micros() as f64 * self.shared.cfg.workers as f64;
        if wall == 0.0 {
            0.0
        } else {
            (self.shared.busy_us.load(Ordering::Relaxed) as f64 / wall).min(1.0)
        }
    }

    /// Queued (not started) tasks per class.
    pub fn queued(&self) -> [usize; 5] {
        let st = lock(&self.shared.state);
        [0, 1, 2, 3, 4].map(|i| st.queues[i].len())
    }

    /// Submit a task, waiting while its class queue is full.
    pub fn submit<T: Send + 'static>(
        &self,
        class: Class,
        resource: Option<&str>,
        deadline: Option<Instant>,
        f: impl FnOnce() -> Result<T> + Send + 'static,
    ) -> Result<Ticket<T>> {
        self.enqueue(class, resource, deadline, f, true)
    }

    /// Submit a task, or fail with RESOURCE_EXHAUSTED (retryable) when its class queue is full.
    pub fn try_submit<T: Send + 'static>(
        &self,
        class: Class,
        resource: Option<&str>,
        deadline: Option<Instant>,
        f: impl FnOnce() -> Result<T> + Send + 'static,
    ) -> Result<Ticket<T>> {
        self.enqueue(class, resource, deadline, f, false)
    }

    fn enqueue<T: Send + 'static>(
        &self,
        class: Class,
        resource: Option<&str>,
        deadline: Option<Instant>,
        f: impl FnOnce() -> Result<T> + Send + 'static,
        block: bool,
    ) -> Result<Ticket<T>> {
        let sh = &self.shared;
        if let Some(r) = resource {
            if !sh.cfg.resources.contains_key(r) {
                return err(ErrorCode::InvalidArgument, format!("unknown balancer resource {r:?}"));
            }
        }
        let m = &sh.metrics[class.idx()];
        let mut st = lock(&sh.state);
        loop {
            if st.shutting {
                return err(ErrorCode::ShuttingDown, "the balancer is shutting down");
            }
            if st.queues[class.idx()].len() < sh.cfg.depth {
                break;
            }
            if !block || WORKER_OF.with(|w| w.get()) == sh.id {
                // a worker waiting for room in its own balancer could wait forever
                m.rejected.fetch_add(1, Ordering::Relaxed);
                return err(ErrorCode::ResourceExhausted, format!("balancer queue {} is full", class.as_str()));
            }
            st = sh.room.wait(st).unwrap_or_else(|p| p.into_inner());
        }
        let (tx, rx) = sync_channel(1);
        let tx2 = tx.clone();
        let cancel = Arc::new(AtomicBool::new(false));
        let job = Job {
            resource: resource.map(str::to_string),
            cancel: cancel.clone(),
            deadline,
            submitted: Instant::now(),
            run: Box::new(move || {
                let r = catch_unwind(AssertUnwindSafe(f)).unwrap_or_else(|_| err(ErrorCode::Internal, "the task panicked"));
                let ok = r.is_ok();
                let _ = tx.send(r);
                ok
            }),
            fail: Box::new(move |e| {
                let _ = tx2.send(Err(e));
            }),
        };
        // a class becoming active does not bring banked credit from its idle time
        let ci = class.idx();
        if st.queues[ci].is_empty() {
            st.vtime[ci] = st.vtime[ci].max(st.sys_vt);
        }
        st.queues[ci].push_back(job);
        m.submitted.fetch_add(1, Ordering::Relaxed);
        drop(st);
        sh.work.notify_one();
        Ok(Ticket { rx, cancel, shared: sh.clone() })
    }

    /// Stop: refuse new tasks; run the queued ones (`drain`) or fail them with SHUTTING_DOWN; join the workers.
    pub fn shutdown(mut self, drain: bool) -> Result<()> {
        self.stop(drain);
        for w in self.workers.drain(..) {
            join_other(w).or_else(|_| err(ErrorCode::Internal, "a balancer worker panicked"))?;
        }
        if let Some(r) = self.reaper.take() {
            join_other(r).or_else(|_| err(ErrorCode::Internal, "the balancer reaper panicked"))?;
        }
        Ok(())
    }

    fn stop(&self, drain: bool) {
        let mut st = lock(&self.shared.state);
        st.shutting = true;
        st.drain = drain;
        drop(st);
        self.shared.work.notify_all();
        self.shared.room.notify_all();
        self.shared.reap.notify_all();
    }
}

impl Drop for Balancer {
    fn drop(&mut self) {
        self.stop(true);
        for w in self.workers.drain(..) {
            let _ = join_other(w);
        }
        if let Some(r) = self.reaper.take() {
            let _ = join_other(r);
        }
    }
}

fn lock(m: &Mutex<State>) -> MutexGuard<'_, State> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

/// Resolve every queued job that was cancelled or whose deadline passed; returns them for completion outside the lock.
fn sweep(st: &mut State, sh: &Shared, out: &mut Vec<(Job, VnxError, usize)>) {
    let now = Instant::now();
    for (ci, q) in st.queues.iter_mut().enumerate() {
        let mut i = 0;
        while i < q.len() {
            let j = &q[i];
            let e = if j.cancel.load(Ordering::SeqCst) {
                Some(VnxError::new(ErrorCode::Cancelled, "cancelled before it started"))
            } else if j.deadline.map_or(false, |d| now >= d) {
                Some(VnxError::new(ErrorCode::Timeout, "deadline passed before it started"))
            } else {
                None
            };
            match e {
                Some(e) => {
                    let job = q.remove(i).expect("index checked");
                    out.push((job, e, ci));
                }
                None => i += 1,
            }
        }
    }
    if !out.is_empty() {
        sh.room.notify_all();
    }
}

/// The next job to start: the class with the smallest virtual time among those with a runnable job.
fn pick(st: &mut State, sh: &Shared) -> Option<(Job, usize)> {
    let mut best: Option<(u64, usize, usize)> = None; // (vtime, class, position)
    let mut blocked = [false; 5];
    for ci in 0..5 {
        let pos = st.queues[ci].iter().position(|j| match &j.resource {
            None => true,
            Some(r) => st.in_use.get(r).copied().unwrap_or(0) < sh.cfg.resources[r],
        });
        match pos {
            Some(p) => {
                if best.map_or(true, |(v, _, _)| st.vtime[ci] < v) {
                    best = Some((st.vtime[ci], ci, p));
                }
            }
            None => blocked[ci] = !st.queues[ci].is_empty(),
        }
    }
    let (_, ci, p) = best?;
    let job = st.queues[ci].remove(p).expect("position found");
    st.sys_vt = st.sys_vt.max(st.vtime[ci]);
    st.vtime[ci] = st.vtime[ci].saturating_add(VT_SCALE / u64::from(sh.cfg.weights[ci]));
    // a class that waits for a busy resource is not backlogged: it does not bank credit while it waits
    for (b, is_blocked) in blocked.iter().enumerate() {
        if *is_blocked {
            st.vtime[b] = st.vtime[b].max(st.sys_vt);
        }
    }
    if let Some(r) = &job.resource {
        *st.in_use.entry(r.clone()).or_insert(0) += 1;
    }
    st.running += 1;
    Some((job, ci))
}

fn complete_expired(sh: &Shared, expired: Vec<(Job, VnxError, usize)>) {
    for (job, e, ci) in expired {
        let m = &sh.metrics[ci];
        match e.code {
            ErrorCode::Timeout => m.timed_out.fetch_add(1, Ordering::Relaxed),
            ErrorCode::Cancelled => m.cancelled.fetch_add(1, Ordering::Relaxed),
            _ => m.failed.fetch_add(1, Ordering::Relaxed),
        };
        (job.fail)(e);
    }
}

fn reaper(sh: &Arc<Shared>) {
    loop {
        let mut expired = Vec::new();
        {
            let mut st = lock(&sh.state);
            if st.shutting && st.queues.iter().all(|q| q.is_empty()) {
                return;
            }
            if st.shutting && !st.drain {
                // fail the queued tasks now, not when a worker becomes free
                for ci in 0..5 {
                    while let Some(j) = st.queues[ci].pop_front() {
                        expired.push((j, VnxError::new(ErrorCode::ShuttingDown, "the balancer shut down before the task started"), ci));
                    }
                }
                sh.room.notify_all();
            }
            sweep(&mut st, sh, &mut expired);
            if expired.is_empty() {
                drop(sh.reap.wait_timeout(st, SWEEP).unwrap_or_else(|p| p.into_inner()));
            }
        }
        complete_expired(sh, expired);
    }
}

fn worker(sh: &Arc<Shared>) {
    WORKER_OF.with(|w| w.set(sh.id));
    loop {
        let mut expired = Vec::new();
        let next = {
            let mut st = lock(&sh.state);
            loop {
                sweep(&mut st, sh, &mut expired);
                if !expired.is_empty() {
                    break None;
                }
                if st.shutting && !st.drain {
                    for ci in 0..5 {
                        while let Some(j) = st.queues[ci].pop_front() {
                            expired.push((j, VnxError::new(ErrorCode::ShuttingDown, "the balancer shut down before the task started"), ci));
                        }
                    }
                    sh.room.notify_all();
                    if expired.is_empty() {
                        return;
                    }
                    break None;
                }
                if let Some(x) = pick(&mut st, sh) {
                    break Some(x);
                }
                if st.shutting && st.queues.iter().all(|q| q.is_empty()) {
                    return;
                }
                st = sh.work.wait_timeout(st, SWEEP).map(|(g, _)| g).unwrap_or_else(|p| p.into_inner().0);
            }
        };
        complete_expired(sh, expired);
        let Some((job, ci)) = next else { continue };
        sh.room.notify_all(); // submitters of several classes may wait for room: each re-checks its own queue
        let m = &sh.metrics[ci];
        let waited = job.submitted.elapsed().as_micros() as u64;
        m.started.fetch_add(1, Ordering::Relaxed);
        m.wait_us.fetch_add(waited, Ordering::Relaxed);
        m.max_wait_us.fetch_max(waited, Ordering::Relaxed);
        let t0 = Instant::now();
        let resource = job.resource.clone();
        let ok = (job.run)(); // never unwinds: the task runs inside catch_unwind
        let ran = t0.elapsed().as_micros() as u64;
        m.run_us.fetch_add(ran, Ordering::Relaxed);
        if ok {
            m.completed.fetch_add(1, Ordering::Relaxed)
        } else {
            m.failed.fetch_add(1, Ordering::Relaxed)
        };
        sh.busy_us.fetch_add(ran, Ordering::Relaxed);
        let mut st = lock(&sh.state);
        st.running -= 1;
        if let Some(r) = resource {
            if let Some(n) = st.in_use.get_mut(&r) {
                *n -= 1;
            }
        }
        drop(st);
        sh.work.notify_all(); // a task waiting for the resource may start now
    }
}

/// Bounds the operations in flight on one media adapter.
pub struct LimitedMedia {
    inner: Arc<dyn MediaAdapter>,
    limit: u32,
    in_flight: Mutex<u32>,
    freed: Condvar,
    /// most operations ever in flight at once, and the total time operations waited for a slot (microseconds)
    pub max_seen: AtomicU64,
    pub wait_us: AtomicU64,
}

impl LimitedMedia {
    /// `limit`: None = the adapter's own `max_in_flight`.
    pub fn new(inner: Arc<dyn MediaAdapter>, limit: Option<u32>) -> Result<LimitedMedia> {
        let limit = limit.unwrap_or(inner.info().max_in_flight);
        if limit == 0 {
            return err(ErrorCode::InvalidArgument, "a media concurrency limit must be >= 1");
        }
        Ok(LimitedMedia { inner, limit, in_flight: Mutex::new(0), freed: Condvar::new(), max_seen: AtomicU64::new(0), wait_us: AtomicU64::new(0) })
    }

    fn with<T>(&self, f: impl FnOnce() -> Result<T>) -> Result<T> {
        let t0 = Instant::now();
        {
            let mut n = self.in_flight.lock().unwrap_or_else(|p| p.into_inner());
            while *n >= self.limit {
                n = self.freed.wait(n).unwrap_or_else(|p| p.into_inner());
            }
            *n += 1;
            self.max_seen.fetch_max(u64::from(*n), Ordering::Relaxed);
        }
        self.wait_us.fetch_add(t0.elapsed().as_micros() as u64, Ordering::Relaxed);
        struct Slot<'a>(&'a LimitedMedia);
        impl Drop for Slot<'_> {
            fn drop(&mut self) {
                *self.0.in_flight.lock().unwrap_or_else(|p| p.into_inner()) -= 1;
                self.0.freed.notify_one();
            }
        }
        let _slot = Slot(self); // released even if the adapter panics
        f()
    }
}

impl MediaAdapter for LimitedMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { max_in_flight: self.limit, ..self.inner.info() }
    }
    fn sealed(&self) -> bool {
        self.inner.sealed()
    }
    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        self.with(|| self.inner.put(at, data))
    }
    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        self.with(|| self.inner.get(at))
    }
    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.with(|| self.inner.remove(at))
    }
    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.with(|| self.inner.list())
    }
    fn sync(&self) -> Result<()> {
        self.with(|| self.inner.sync())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation, Geometry};
    use crate::media::MemoryMedia;
    use std::sync::atomic::AtomicUsize;

    fn cfg(workers: usize) -> BalancerConfig {
        let mut c = BalancerConfig { workers, depth: 1024, ..Default::default() };
        c.resources.insert("synthesis".into(), 1);
        c.resources.insert("media".into(), 3);
        c
    }

    /// Holds every worker until released.
    fn block_workers(b: &Balancer, n: usize) -> (Arc<(Mutex<bool>, Condvar)>, Vec<Ticket<()>>) {
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let started = Arc::new(AtomicUsize::new(0));
        let ts = (0..n)
            .map(|_| {
                let (g, s) = (gate.clone(), started.clone());
                b.submit(Class::Foreground, None, None, move || {
                    s.fetch_add(1, Ordering::SeqCst);
                    let mut open = g.0.lock().unwrap();
                    while !*open {
                        open = g.1.wait(open).unwrap();
                    }
                    Ok(())
                })
                .unwrap()
            })
            .collect();
        while started.load(Ordering::SeqCst) < n {
            std::thread::yield_now();
        }
        (gate, ts)
    }

    fn open(g: &Arc<(Mutex<bool>, Condvar)>) {
        *g.0.lock().unwrap() = true;
        g.1.notify_all();
    }

    #[test]
    fn under_saturation_classes_share_starts_by_weight_and_none_starves() {
        let b = Balancer::start(cfg(1)).unwrap();
        let (gate, held) = block_workers(&b, 1);
        let order = Arc::new(Mutex::new(Vec::new()));
        let mut ts = Vec::new();
        for c in Class::ALL {
            for _ in 0..200 {
                let o = order.clone();
                ts.push(b.submit(c, None, None, move || {
                    o.lock().unwrap().push(c);
                    Ok(())
                }).unwrap());
            }
        }
        open(&gate);
        for t in ts.into_iter().chain(held) {
            t.wait().unwrap();
        }
        let o = order.lock().unwrap();
        // the first 310 starts (10 rounds of 16+8+4+2+1) follow the weights exactly while every class has work
        let first = &o[..310];
        let n = |c: Class| first.iter().filter(|x| **x == c).count();
        assert_eq!([n(Class::Foreground), n(Class::Codec), n(Class::Verify), n(Class::Gc), n(Class::Maintenance)], [160, 80, 40, 20, 10]);
        // and the lowest class started within the first round
        assert!(o.iter().position(|c| *c == Class::Maintenance).unwrap() < 31);
        assert_eq!(o.len(), 1000);
    }

    #[test]
    fn resource_limits_hold_and_do_not_block_other_tasks() {
        let b = Balancer::start(cfg(8)).unwrap();
        let (now, max) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let mut ts = Vec::new();
        for _ in 0..20 {
            let (n, m) = (now.clone(), max.clone());
            ts.push(b.submit(Class::Codec, Some("synthesis"), None, move || {
                let c = n.fetch_add(1, Ordering::SeqCst) + 1;
                m.fetch_max(c, Ordering::SeqCst);
                std::thread::sleep(Duration::from_millis(3));
                n.fetch_sub(1, Ordering::SeqCst);
                Ok(())
            }).unwrap());
        }
        // tasks without the resource run meanwhile, behind the synthesis queue in the same class
        let t0 = Instant::now();
        let quick = b.submit(Class::Codec, None, None, || Ok(7)).unwrap();
        assert_eq!(quick.wait().unwrap(), 7);
        assert!(t0.elapsed() < Duration::from_millis(40), "not stuck behind the serialised resource");
        for t in ts {
            t.wait().unwrap();
        }
        assert_eq!(max.load(Ordering::SeqCst), 1, "synthesis never ran twice at once");
        assert_eq!(b.submit(Class::Codec, Some("nope"), None, || Ok(())).err().unwrap().code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn backpressure_cancellation_and_deadlines() {
        let b = Balancer::start(BalancerConfig { workers: 1, depth: 2, ..Default::default() }).unwrap();
        let (gate, held) = block_workers(&b, 1);
        let a = b.try_submit(Class::Gc, None, None, || Ok(1)).unwrap();
        let c = b.try_submit(Class::Gc, None, Some(Instant::now() + Duration::from_millis(20)), || Ok(2)).unwrap();
        assert_eq!(b.try_submit(Class::Gc, None, None, || Ok(3)).err().unwrap().code, ErrorCode::ResourceExhausted);
        assert!(b.try_submit(Class::Verify, None, None, || Ok(4)).is_ok(), "queues are per class");
        a.cancel();
        assert_eq!(a.wait().unwrap_err().code, ErrorCode::Cancelled, "resolved while every worker is busy");
        assert_eq!(c.wait().unwrap_err().code, ErrorCode::Timeout);
        assert_eq!(b.metrics(Class::Gc).rejected.load(Ordering::Relaxed), 1);
        open(&gate);
        for t in held {
            t.wait().unwrap();
        }
    }

    #[test]
    fn tasks_cannot_wait_for_tasks_but_may_submit_them_and_panics_are_contained() {
        let b = Arc::new(Balancer::start(cfg(2)).unwrap());
        let b2 = b.clone();
        let t = b.submit(Class::Maintenance, None, None, move || {
            // the inner task is held until the outer one has tried to wait: otherwise the second worker may finish it
            // first and the wait returns its result, which is harmless but leaves nothing to refuse (seen under
            // qemu-user emulation, where the outer task is slower to reach the wait)
            let (go, held) = std::sync::mpsc::channel::<()>();
            let inner = b2.submit(Class::Maintenance, None, None, move || {
                let _ = held.recv();
                Ok(5)
            })?;
            let refused = inner.wait_timeout(Duration::from_secs(1)).unwrap();
            let _ = go.send(());
            Ok(refused.unwrap_err().code)
        }).unwrap();
        assert_eq!(t.wait().unwrap(), ErrorCode::Internal);
        let p = b.submit(Class::Verify, None, None, || -> Result<()> { panic!("task bug") }).unwrap();
        assert_eq!(p.wait().unwrap_err().code, ErrorCode::Internal);
        assert_eq!(b.submit(Class::Verify, None, None, || Ok(9)).unwrap().wait().unwrap(), 9, "the pool keeps serving");
    }

    #[test]
    fn shutdown_drains_or_fails_queued_tasks() {
        for drain in [true, false] {
            let b = Balancer::start(cfg(1)).unwrap();
            let (gate, held) = block_workers(&b, 1);
            let queued: Vec<_> = (0..5).map(|i| b.submit(Class::Codec, None, None, move || Ok(i)).unwrap()).collect();
            let g = gate.clone();
            let opener = std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(30));
                open(&g);
            });
            b.shutdown(drain).unwrap();
            opener.join().unwrap();
            for t in held {
                t.wait().unwrap();
            }
            for (i, t) in queued.into_iter().enumerate() {
                match t.wait() {
                    Ok(v) => assert!(drain && v == i),
                    Err(e) => assert!(!drain && e.code == ErrorCode::ShuttingDown),
                }
            }
        }
    }

    #[test]
    fn an_idle_class_banks_no_credit() {
        let b = Balancer::start(cfg(1)).unwrap();
        // foreground alone for a long time
        let ts: Vec<_> = (0..5000).map(|_| b.submit(Class::Foreground, None, None, || Ok(())).unwrap()).collect();
        for t in ts {
            t.wait().unwrap();
        }
        let (gate, held) = block_workers(&b, 1);
        let order = Arc::new(Mutex::new(Vec::new()));
        let mut ts = Vec::new();
        for (c, n) in [(Class::Maintenance, 500), (Class::Foreground, 10)] {
            for _ in 0..n {
                let o = order.clone();
                ts.push(b.submit(c, None, None, move || {
                    o.lock().unwrap().push(c);
                    Ok(())
                }).unwrap());
            }
        }
        open(&gate);
        for t in ts.into_iter().chain(held) {
            t.wait().unwrap();
        }
        let o = order.lock().unwrap();
        let first_fg = o.iter().position(|c| *c == Class::Foreground).unwrap();
        assert!(first_fg <= 2, "foreground waited for {first_fg} maintenance starts (idle maintenance banked credit)");
        let fg_done = o.iter().rposition(|c| *c == Class::Foreground).unwrap();
        assert!(fg_done < 20, "the 10 foreground tasks finish within the first round: last at {fg_done}");
    }

    #[test]
    fn a_class_blocked_on_a_resource_banks_no_credit() {
        let b = Balancer::start(cfg(2)).unwrap();
        // gc holds "synthesis" for a while with more gc tasks queued behind it, codec runs meanwhile
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let g = gate.clone();
        let hold = b.submit(Class::Gc, Some("synthesis"), None, move || {
            let mut o = g.0.lock().unwrap();
            while !*o {
                o = g.1.wait(o).unwrap();
            }
            Ok(())
        }).unwrap();
        std::thread::sleep(Duration::from_millis(20));
        let order = Arc::new(Mutex::new(Vec::new()));
        let mut ts = Vec::new();
        for _ in 0..20 {
            let o = order.clone();
            ts.push(b.submit(Class::Gc, Some("synthesis"), None, move || {
                o.lock().unwrap().push(Class::Gc);
                std::thread::sleep(Duration::from_millis(1));
                Ok(())
            }).unwrap());
        }
        let cs: Vec<_> = (0..300).map(|_| {
            let o = order.clone();
            b.submit(Class::Codec, None, None, move || {
                o.lock().unwrap().push(Class::Codec);
                Ok(())
            }).unwrap()
        }).collect();
        for t in cs {
            t.wait().unwrap();
        }
        open(&gate);
        hold.wait().unwrap();
        for t in ts {
            t.wait().unwrap();
        }
        assert_eq!(order.lock().unwrap().iter().filter(|c| **c == Class::Gc).count(), 20);
    }

    #[test]
    fn a_blocked_submitter_is_woken_when_its_own_queue_has_room() {
        let mut c = cfg(2);
        c.depth = 1;
        let b = Arc::new(Balancer::start(c).unwrap());
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let g = gate.clone();
        let hold = b.submit(Class::Foreground, Some("synthesis"), None, move || {
            let mut o = g.0.lock().unwrap();
            while !*o {
                o = g.1.wait(o).unwrap();
            }
            Ok(())
        }).unwrap();
        std::thread::sleep(Duration::from_millis(20));
        let gc_queued = b.submit(Class::Gc, Some("synthesis"), None, || Ok(())).unwrap(); // stays queued (resource)
        let g2 = gate.clone();
        // the second worker is kept busy by a codec task until the submitters are waiting
        let codec_busy = b.submit(Class::Codec, None, None, move || {
            std::thread::sleep(Duration::from_millis(60));
            let _ = &g2;
            Ok(())
        }).unwrap();
        std::thread::sleep(Duration::from_millis(10));
        let codec_queued = b.submit(Class::Codec, None, None, || Ok(())).unwrap();
        let (b1, b2) = (b.clone(), b.clone());
        let sb = std::thread::spawn(move || b1.submit(Class::Gc, None, None, || Ok(())).map(|_| ()));
        std::thread::sleep(Duration::from_millis(5));
        let t0 = Instant::now();
        let sa = std::thread::spawn(move || b2.submit(Class::Codec, None, None, || Ok(())).map(|t| t.wait()));
        sa.join().unwrap().unwrap().unwrap();
        assert!(t0.elapsed() < Duration::from_millis(400), "the codec submitter was not left waiting: {:?}", t0.elapsed());
        open(&gate);
        hold.wait().unwrap();
        codec_busy.wait().unwrap();
        codec_queued.wait().unwrap();
        gc_queued.wait().unwrap();
        sb.join().unwrap().unwrap();
    }

    #[test]
    fn failures_are_counted_as_failures_and_ready_results_can_be_taken_by_workers() {
        let b = Arc::new(Balancer::start(cfg(2)).unwrap());
        b.submit(Class::Verify, None, None, || -> Result<()> { err(ErrorCode::Corruption, "x") }).unwrap().wait().unwrap_err();
        b.submit(Class::Verify, None, None, || -> Result<()> { panic!("y") }).unwrap().wait().unwrap_err();
        b.submit(Class::Verify, None, None, || Ok(())).unwrap().wait().unwrap();
        let m = b.metrics(Class::Verify);
        assert_eq!((m.completed.load(Ordering::Relaxed), m.failed.load(Ordering::Relaxed)), (1, 2));
        let b2 = b.clone();
        let t = b.submit(Class::Maintenance, None, None, move || {
            let inner = b2.submit(Class::Maintenance, None, None, || Ok(5))?;
            std::thread::sleep(Duration::from_millis(50)); // the other worker finishes it meanwhile
            inner.wait()
        }).unwrap();
        assert_eq!(t.wait().unwrap(), 5, "a finished result is not refused");
    }

    #[test]
    fn a_task_may_drop_the_last_handle_and_no_drain_shutdown_does_not_wait_for_workers() {
        let b = Arc::new(Balancer::start(cfg(1)).unwrap());
        let (tx, rx) = std::sync::mpsc::channel();
        let b2 = b.clone();
        drop(b);
        let t = std::thread::spawn(move || {
            // submit from outside, then hand the last Arc to the task itself
            let holder = Arc::new(Mutex::new(Some(b2.clone())));
            let h = holder.clone();
            let tk = b2.submit(Class::Codec, None, None, move || {
                drop(h.lock().unwrap().take());
                Ok(1)
            }).unwrap();
            drop(b2);
            drop(holder);
            tx.send(tk.wait()).unwrap();
        });
        assert_eq!(rx.recv_timeout(Duration::from_secs(5)).unwrap().unwrap(), 1);
        t.join().unwrap();
        // every worker busy: the queued tasks fail at shutdown at once
        let b = Balancer::start(cfg(1)).unwrap();
        let (gate, held) = block_workers(&b, 1);
        let queued: Vec<_> = (0..3).map(|_| b.submit(Class::Gc, None, None, || Ok(())).unwrap()).collect();
        let stopper = std::thread::spawn(move || b.shutdown(false));
        for q in queued {
            assert_eq!(q.wait_timeout(Duration::from_secs(2)).unwrap().unwrap_err().code, ErrorCode::ShuttingDown);
        }
        open(&gate);
        for h in held {
            h.wait().unwrap();
        }
        stopper.join().unwrap().unwrap();
    }

    #[test]
    fn limited_media_never_exceeds_its_limit() {
        let lm = Arc::new(LimitedMedia::new(Arc::new(crate::fault::LatencyMedia::new(
            Arc::new(MemoryMedia::new()), Duration::from_millis(2), Duration::from_millis(1), 3)), None).unwrap());
        std::thread::scope(|s| {
            for t in 0..12u64 {
                let lm = lm.clone();
                s.spawn(move || {
                    for i in 0..10u64 {
                        let at = GenerationRef { extent: ExtentId(t * 100 + i), generation: Generation::FIRST };
                        lm.put(at, b"x").unwrap();
                        lm.get(at).unwrap();
                    }
                });
            }
        });
        assert_eq!(lm.max_seen.load(Ordering::Relaxed), 3);
        assert_eq!(lm.info().max_in_flight, 3);
        let _ = Geometry::new(512, 1, 1).unwrap();
    }
}
