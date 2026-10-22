//! The device controller's request path (master directive §14 Stage A; docs/V14_DEVICE_INTERFACE.md §5):
//! the storage stack behind one block device and the NBD transmission phase on top of it.
//!
//! ```text
//!  NBD connection (kernel /dev/nbdN or a userspace client)
//!    reader thread ── parse + bound-check ── IoQueue (bounded, ordered, deadlines, cancellation)
//!    replier threads ── wait for completion ── one reply per request (error = the code's errno)
//!  IoQueue ─ BlockIo (VNX-RAM `io` quota) ─ [ExtentCache] ─ Volume ─ ResilientMedia ─ [FaultGate] ─ MediaAdapter
//! ```
//!
//! * every request gets exactly one reply; a failure is the errno of its [`ErrorCode`] (backend failures EIO, a
//!   read-only device EPERM, a request outside the device EINVAL/ENOSPC as the protocol asks, a full store
//!   NO_SPACE/ENOSPC), never a silent success. The one accepted no-op is TRIM (the protocol leaves trimmed data
//!   undefined): it is bound-checked, refused on a read-only device and counted, and releases no space (documented);
//! * a write with the FUA flag is answered only after the write and a following flush both succeeded;
//! * memory: the payloads of the requests in flight are bounded by `max_inflight_bytes` and their number by
//!   `max_inflight`; the reader waits (and so stops reading the socket) when either is reached: backpressure to
//!   the client. VNX-IO leases each request's working memory from the VNX-RAM `io` quota on top of that;
//! * a request that has not started before its deadline completes with TIMEOUT (EIO), well before the kernel's
//!   own timeout; when the connection dies, requests not started yet are cancelled;
//! * DISC: the reader stops, every outstanding request is answered, the store is flushed, then the connection is
//!   closed (the protocol's order).

use crate::addr::Geometry;
use crate::addr::GenerationRef;
use crate::cache::{CacheMode, ExtentCache};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::fallback::{BackendState, HealthConfig, ResilientMedia, RetryPolicy};
use crate::io::{BlockIo, IoQueue, Op, QueueConfig, Reply, Ticket};
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo};
use crate::nbd::{self, ExportInfo, Negotiated, Request};
use crate::ram::{MemoryManager, RamConfig, Subsystem};
use crate::volume::{RecoveryReport, ScrubReport, Volume, VolumeConfig};
use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// Largest request payload accepted (the Linux NBD driver sends at most 32 MiB: max_hw_sectors 65536).
pub const MAX_PAYLOAD: u32 = 32 << 20;
/// Time a client has to finish the negotiation.
pub const NEGOTIATION_TIMEOUT: Duration = Duration::from_secs(10);

// ---------------------------------------------------------------------------------------------------------------
// fault injection (tests and drills only; enabled by an explicit option)

pub const F_PUT: u32 = 1;
pub const F_GET: u32 = 2;
pub const F_SYNC: u32 = 4;
pub const F_REMOVE: u32 = 8;
pub const F_LIST: u32 = 16;

/// Parse a fault-control word list ("put get sync remove list all none"); unknown words are an error.
pub fn parse_fault_mask(text: &str) -> Result<u32> {
    let mut m = 0;
    for w in text.split_whitespace() {
        m |= match w {
            "put" => F_PUT,
            "get" => F_GET,
            "sync" => F_SYNC,
            "remove" => F_REMOVE,
            "list" => F_LIST,
            "all" => F_PUT | F_GET | F_SYNC | F_REMOVE | F_LIST,
            "none" => 0,
            _ => return err(ErrorCode::InvalidArgument, format!("unknown fault word {w:?}")),
        };
    }
    Ok(m)
}

/// Makes chosen media operations fail with BACKEND_UNAVAILABLE, switched at run time: by [`FaultGate::set`], or by
/// the contents of a control file read at every operation (absent file = no fault). For fault drills of the whole
/// device; never enabled unless asked for.
pub struct FaultGate {
    inner: Arc<dyn MediaAdapter>,
    mask: AtomicU64,
    control: Option<PathBuf>,
    pub injected: AtomicU64,
}

impl FaultGate {
    pub fn new(inner: Arc<dyn MediaAdapter>, control: Option<PathBuf>) -> FaultGate {
        FaultGate { inner, mask: AtomicU64::new(0), control, injected: AtomicU64::new(0) }
    }

    pub fn set(&self, mask: u32) {
        self.mask.store(u64::from(mask), Ordering::SeqCst);
    }

    fn check(&self, bit: u32, what: &str) -> Result<()> {
        let mut m = self.mask.load(Ordering::SeqCst) as u32;
        if let Some(p) = &self.control {
            match std::fs::read_to_string(p) {
                Ok(t) => m |= parse_fault_mask(&t).unwrap_or(F_PUT | F_GET | F_SYNC | F_REMOVE | F_LIST),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => m = F_PUT | F_GET | F_SYNC | F_REMOVE | F_LIST, // an unreadable control file fails closed
            }
        }
        if m & bit != 0 {
            self.injected.fetch_add(1, Ordering::Relaxed);
            return err(ErrorCode::BackendUnavailable, format!("injected fault: {what}"));
        }
        Ok(())
    }
}

impl MediaAdapter for FaultGate {
    fn info(&self) -> MediaInfo {
        self.inner.info()
    }
    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        self.check(F_PUT, "put")?;
        self.inner.put(at, data)
    }
    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        self.check(F_GET, "get")?;
        self.inner.get(at)
    }
    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.check(F_REMOVE, "remove")?;
        self.inner.remove(at)
    }
    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.check(F_LIST, "list")?;
        self.inner.list()
    }
    fn sync(&self) -> Result<()> {
        self.check(F_SYNC, "sync")?;
        self.inner.sync()
    }
    fn sealed(&self) -> bool {
        self.inner.sealed()
    }
}

// ---------------------------------------------------------------------------------------------------------------
// the storage stack of one device

#[derive(Clone, Debug)]
pub struct DeviceConfig {
    pub cache: Option<CacheMode>,
    /// VNX-RAM budget of the device (bytes)
    pub ram_bytes: u64,
    pub queue: Option<QueueConfig>,
    pub retry: RetryPolicy,
    pub health: HealthConfig,
    pub volume: VolumeConfig,
    pub fault_control: Option<PathBuf>,
    pub read_only: bool,
    /// upper bound on the bytes the device's store may hold (the extents that carry data); `None` = no bound, the
    /// store grows as the device is written
    pub max_stored_bytes: Option<u64>,
    /// a request not started within this time completes with TIMEOUT
    pub request_timeout: Duration,
    pub max_inflight: usize,
    pub max_inflight_bytes: u64,
    /// reply threads per connection
    pub repliers: usize,
}

impl Default for DeviceConfig {
    fn default() -> Self {
        DeviceConfig {
            cache: Some(CacheMode::WriteThrough),
            ram_bytes: 256 << 20,
            queue: None,
            retry: RetryPolicy::default(),
            health: HealthConfig::default(),
            volume: VolumeConfig::default(),
            fault_control: None,
            read_only: false,
            max_stored_bytes: None,
            request_timeout: Duration::from_secs(30),
            max_inflight: 128,
            max_inflight_bytes: 64 << 20,
            repliers: 8,
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------
// the space the device's store holds

/// The space the device's store holds: the extents that carry data, the reservations of the writes in flight and
/// the bytes they add up to. The bound is a bound of the *store* (the bytes of the extents it holds), not a size of
/// the device: a write that needs no new extent (an overwrite of an extent that carries data) is always allowed, a
/// write that needs a new extent beyond the bound is refused with NO_SPACE, which the host sees as ENOSPC.
#[derive(Debug, Default)]
struct StoreSpace {
    /// the extents that carry data
    stored: HashSet<u64>,
    /// the extents the writes in flight have reserved (the number is the number of such writes)
    claims: HashMap<u64, u32>,
    /// the bytes of the extents that carry data or are reserved
    used: u64,
}

impl StoreSpace {
    /// Reserve the space a write over `touched` needs: the extents that carry no data yet. `Err` (NO_SPACE) when
    /// the bound would be exceeded; nothing is changed then.
    fn reserve(&mut self, touched: &[u64], extent_bytes: u64, bound: u64) -> Result<()> {
        let mut need = 0u64;
        for &e in touched {
            if self.stored.contains(&e) || self.claims.get(&e).copied().unwrap_or(0) > 0 {
                continue; // the extent is paid for already
            }
            need += extent_bytes;
        }
        if self.used.saturating_add(need) > bound {
            return err(
                ErrorCode::NoSpace,
                format!("the store holds {} bytes, {need} more are needed, its bound is {bound} bytes", self.used),
            );
        }
        self.used += need;
        for &e in touched {
            *self.claims.entry(e).or_insert(0) += 1;
        }
        Ok(())
    }

    /// The outcome of a write over `touched`: `written` = the data is stored (the extents carry data now), `false` =
    /// the write failed (its reservation is given back).
    fn settle(&mut self, touched: &[u64], written: bool, extent_bytes: u64) {
        for &e in touched {
            if written {
                self.stored.insert(e);
            }
            let c = self.claims.get(&e).copied().unwrap_or(0);
            if c == 0 {
                continue;
            }
            if c == 1 {
                self.claims.remove(&e);
                if !self.stored.contains(&e) {
                    self.used = self.used.saturating_sub(extent_bytes);
                }
            } else {
                self.claims.insert(e, c - 1);
            }
        }
    }
}

/// Counters of the host interface (exported by `stats`).
#[derive(Debug, Default)]
pub struct DeviceStats {
    pub connections: AtomicU64,
    pub requests: AtomicU64,
    pub reads: AtomicU64,
    pub writes: AtomicU64,
    pub fua_writes: AtomicU64,
    pub flushes: AtomicU64,
    pub trims: AtomicU64,
    pub disconnects: AtomicU64,
    pub bytes_read: AtomicU64,
    pub bytes_written: AtomicU64,
    /// requests answered with an error (any errno)
    pub errors: AtomicU64,
    pub eio: AtomicU64,
    pub protocol_errors: AtomicU64,
    pub cancelled_on_close: AtomicU64,
    pub inflight: AtomicU64,
    pub max_inflight_seen: AtomicU64,
}

pub struct Device {
    geometry: Geometry,
    cfg: DeviceConfig,
    queue: IoQueue,
    volume: Arc<Volume>,
    cache: Option<Arc<ExtentCache>>,
    ram: Arc<MemoryManager>,
    media: Arc<ResilientMedia>,
    gate: Option<Arc<FaultGate>>,
    pub stats: DeviceStats,
    last_error: Mutex<Option<String>>,
    /// the space the device's store holds (tracked only when the store has a bound)
    space: Mutex<StoreSpace>,
    /// the report of the last scrub of the volume and the time it ran (the caller gives the time: the service never
    /// reads the wall clock itself)
    last_scrub: Mutex<Option<(ScrubReport, i64)>>,
    /// set when the request path failed in a way that needs an operator (a panicked server thread)
    failed: AtomicBool,
    recovery: RecoveryReport,
}

/// The VNX-RAM budget: the default split, with the `io` quota raised to hold the working memory of one largest
/// request (a 32 MiB write needs about twice its size plus three extents).
pub fn ram_config(total: u64, geometry: Geometry) -> Result<RamConfig> {
    let ext = geometry.extent_bytes();
    let need = 2 * u64::from(MAX_PAYLOAD) + 3 * ext;
    if total < need {
        return err(ErrorCode::InvalidArgument, format!("a RAM budget of {total} bytes is below the {need} bytes one largest request needs"));
    }
    let mut cfg = RamConfig::split(total);
    let io = cfg.quotas.get(&Subsystem::Io).copied().unwrap_or(0).max(need);
    cfg.quotas.insert(Subsystem::Io, io.min(total));
    let cache = cfg.quotas.get(&Subsystem::Cache).copied().unwrap_or(0).max(ext);
    cfg.quotas.insert(Subsystem::Cache, cache.min(total));
    Ok(cfg)
}

impl Device {
    /// Open the volume in `volume_dir` over `media` (wrapped in the fault gate when asked, and in the fallback
    /// coordinator) and start its I/O queue.
    pub fn open(volume_dir: &Path, media: Arc<dyn MediaAdapter>, cfg: DeviceConfig) -> Result<Device> {
        let max_in_flight = media.info().max_in_flight;
        let gate = cfg.fault_control.as_ref().map(|p| Arc::new(FaultGate::new(media.clone(), Some(p.clone()))));
        let below: Arc<dyn MediaAdapter> = match &gate {
            Some(g) => g.clone(),
            None => media,
        };
        let resilient = Arc::new(ResilientMedia::new(below, cfg.retry.clone(), cfg.health.clone())?);
        let (vol, recovery) = Volume::open(volume_dir, resilient.clone(), cfg.volume.clone())?;
        let vol = Arc::new(vol);
        let geometry = vol.geometry();
        let ram = MemoryManager::new(ram_config(cfg.ram_bytes, geometry)?)?;
        let (io, cache) = match cfg.cache {
            Some(mode) => {
                let c = Arc::new(ExtentCache::new(vol.clone(), ram.clone(), mode));
                (BlockIo::over(c.clone(), vol.clone())?, Some(c))
            }
            None => (BlockIo::new(vol.clone()), None),
        };
        let qcfg = cfg.queue.unwrap_or_else(|| QueueConfig::for_backend(max_in_flight));
        let queue = IoQueue::start(io.with_ram(ram.clone()), qcfg)?;
        if cfg.repliers == 0 || cfg.max_inflight == 0 || cfg.max_inflight_bytes < u64::from(MAX_PAYLOAD) {
            return err(ErrorCode::InvalidArgument, "repliers >= 1, max_inflight >= 1, max_inflight_bytes >= the largest payload");
        }
        Ok(Device {
            geometry,
            cfg,
            queue,
            volume: vol,
            cache,
            ram,
            media: resilient,
            gate,
            stats: DeviceStats::default(),
            last_error: Mutex::new(None),
            space: Mutex::new(StoreSpace::default()),
            last_scrub: Mutex::new(None),
            failed: AtomicBool::new(false),
            recovery,
        })
    }

    pub fn geometry(&self) -> Geometry {
        self.geometry
    }

    pub fn recovery(&self) -> &RecoveryReport {
        &self.recovery
    }

    pub fn gate(&self) -> Option<&Arc<FaultGate>> {
        self.gate.as_ref()
    }

    /// The health of the backend the volume writes to (a snapshot; the counters move on their own).
    pub fn backend_health(&self) -> crate::fallback::Health {
        self.media.health()
    }

    /// Scrub the volume now: every extent of the map is read back and checked. The report is remembered with the
    /// time of the run (`unix_ms`, given by the caller: the service never reads the wall clock itself), so the
    /// metrics can report the last scrub.
    pub fn run_scrub(&self, unix_ms: i64) -> Result<ScrubReport> {
        let report = self.volume.scrub()?;
        *self.last_scrub.lock().unwrap_or_else(|p| p.into_inner()) = Some((report.clone(), unix_ms));
        Ok(report)
    }

    /// The report of the last scrub and the time it ran, if a scrub ran.
    pub fn last_scrub(&self) -> Option<(ScrubReport, i64)> {
        self.last_scrub.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    pub fn mark_failed(&self, why: &str) {
        self.failed.store(true, Ordering::SeqCst);
        self.note_error(why);
    }

    fn note_error(&self, msg: &str) {
        *self.last_error.lock().unwrap_or_else(|p| p.into_inner()) = Some(msg.to_string());
    }

    /// Transmission flags of this device.
    pub fn transmission_flags(&self) -> u16 {
        let mut f = nbd::TF_HAS_FLAGS | nbd::TF_SEND_FLUSH | nbd::TF_SEND_FUA | nbd::TF_SEND_TRIM;
        if self.cfg.read_only {
            f |= nbd::TF_READ_ONLY;
        }
        f
    }

    pub fn export(&self, name: &str) -> ExportInfo {
        ExportInfo {
            name: name.to_string(),
            size: self.geometry.capacity_bytes(),
            flags: self.transmission_flags(),
            block_size: self.geometry.block_size(),
            max_payload: MAX_PAYLOAD,
        }
    }

    /// The extents a write of `len` bytes at `offset` touches (the device stores whole extents). Empty when the
    /// store has no bound: then the space of the store is not tracked at all and the device behaves as before.
    fn touched_extents(&self, offset: u64, len: u64) -> Vec<u64> {
        if self.cfg.max_stored_bytes.is_none() || len == 0 {
            return Vec::new();
        }
        let eb = self.geometry.extent_bytes();
        let first = offset / eb;
        let last = (offset + len - 1) / eb; // len >= 1 and the request is inside the device
        (first..=last).collect()
    }

    /// Reserve the space a write over `touched` needs; `Err` (NO_SPACE) when the store's bound would be exceeded.
    /// Nothing is changed then: the caller refuses the write and the store keeps what it holds.
    fn reserve_space(&self, touched: &[u64]) -> Result<()> {
        let bound = match self.cfg.max_stored_bytes {
            Some(b) => b,
            None => return Ok(()),
        };
        let mut space = self.space.lock().unwrap_or_else(|p| p.into_inner());
        space.reserve(touched, self.geometry.extent_bytes(), bound)
    }

    /// The outcome of a write over `touched` extents: `written` = the data is stored, `false` = the write failed.
    fn settle_space(&self, touched: &[u64], written: bool) {
        if touched.is_empty() {
            return;
        }
        let mut space = self.space.lock().unwrap_or_else(|p| p.into_inner());
        space.settle(touched, written, self.geometry.extent_bytes());
    }

    /// Flush everything acknowledged so far to the backend's commit point (through the queue: a barrier).
    pub fn flush(&self) -> Result<()> {
        match self.queue.submit(Op::Flush, Some(Instant::now() + self.cfg.request_timeout))?.wait()? {
            Reply::Flushed => Ok(()),
            r => err(ErrorCode::Internal, format!("unexpected reply to flush: {r:?}")),
        }
    }

    /// Stop the device: drain the queue, then flush the store directly. Returns the first error.
    pub fn close(self) -> Result<()> {
        let drained = self.queue.shutdown(true);
        let flushed = match &self.cache {
            Some(c) => c.flush(),
            None => self.volume.flush(),
        };
        drained.and(flushed)
    }

    /// `ok`, `degraded` (backend degraded/unavailable, damaged extents) or `failed`.
    pub fn health_state(&self) -> &'static str {
        if self.failed.load(Ordering::SeqCst) {
            return "failed";
        }
        let damaged = self.volume.damaged().map(|d| !d.is_empty()).unwrap_or(true);
        if damaged || self.media.health().state != BackendState::Healthy {
            "degraded"
        } else {
            "ok"
        }
    }

    pub fn health_json(&self) -> Value {
        let damaged = match self.volume.damaged() {
            Ok(d) => Value::Arr(d.iter().map(|e| Value::Int(e.0 as i64)).collect()),
            Err(e) => Value::Str(e.to_string()),
        };
        let last = self.last_error.lock().unwrap_or_else(|p| p.into_inner()).clone();
        obj([
            ("state", Value::Str(self.health_state().into())),
            (
                "detail",
                obj([
                    ("backend", self.media.health().to_json()),
                    ("backend_last_error", self.media.health().last_error.map_or(Value::Null, Value::Str)),
                    ("damaged_extents", damaged),
                    ("read_only", Value::Bool(self.cfg.read_only)),
                    ("last_error", last.map_or(Value::Null, Value::Str)),
                    ("inflight", Value::Int(self.stats.inflight.load(Ordering::Relaxed) as i64)),
                ]),
            ),
        ])
    }

    pub fn capacity_json(&self) -> Value {
        let g = self.geometry;
        obj([
            ("block_size", Value::Int(i64::from(g.block_size()))),
            ("blocks_per_extent", Value::Int(i64::from(g.blocks_per_extent()))),
            ("capacity_blocks", Value::Int(g.capacity_blocks() as i64)),
            ("capacity_bytes", Value::Int(g.capacity_bytes() as i64)),
            ("extent_count", Value::Int(g.extent_count() as i64)),
        ])
    }

    pub fn stats_json(&self) -> Value {
        let l = |a: &AtomicU64| Value::Int(a.load(Ordering::Relaxed) as i64);
        let s = &self.stats;
        let q = self.queue.metrics();
        let v = &self.volume.stats;
        let mut counters = vec![
            ("nbd_connections", l(&s.connections)),
            ("nbd_requests", l(&s.requests)),
            ("nbd_reads", l(&s.reads)),
            ("nbd_writes", l(&s.writes)),
            ("nbd_fua_writes", l(&s.fua_writes)),
            ("nbd_flushes", l(&s.flushes)),
            ("nbd_trims", l(&s.trims)),
            ("nbd_disconnects", l(&s.disconnects)),
            ("nbd_bytes_read", l(&s.bytes_read)),
            ("nbd_bytes_written", l(&s.bytes_written)),
            ("nbd_errors", l(&s.errors)),
            ("nbd_eio", l(&s.eio)),
            ("nbd_protocol_errors", l(&s.protocol_errors)),
            ("nbd_cancelled_on_close", l(&s.cancelled_on_close)),
            ("nbd_max_inflight", l(&s.max_inflight_seen)),
            ("io_submitted", l(&q.submitted)),
            ("io_completed", l(&q.completed)),
            ("io_failed", l(&q.failed)),
            ("io_cancelled", l(&q.cancelled)),
            ("io_timed_out", l(&q.timed_out)),
            ("io_max_queue_depth", l(&q.max_queue_depth)),
            ("volume_reads", l(&v.reads)),
            ("volume_commits", l(&v.commits)),
            ("volume_integrity_failures", l(&v.integrity_failures)),
            ("ram_in_use", Value::Int(self.ram.total_in_use() as i64)),
            ("ram_peak", Value::Int(self.ram.total_peak() as i64)),
        ];
        if let Some(c) = &self.cache {
            counters.push(("cache_hits", l(&c.stats.hits)));
            counters.push(("cache_misses", l(&c.stats.misses)));
            counters.push(("cache_writebacks", l(&c.stats.writebacks)));
            counters.push(("cache_writeback_failures", l(&c.stats.writeback_failures)));
        }
        if let Some(g) = &self.gate {
            counters.push(("faults_injected", l(&g.injected)));
        }
        obj([("counters", Value::Obj(counters.into_iter().map(|(k, v)| (k.to_string(), v)).collect()))])
    }
}

// ---------------------------------------------------------------------------------------------------------------
// the transmission phase

/// Payload bytes and requests in flight on one connection.
struct Budget {
    used: Mutex<(u64, usize)>,
    freed: Condvar,
    max_bytes: u64,
    max_reqs: usize,
}

impl Budget {
    /// Wait for room for `bytes` (a request larger than the whole budget waits until nothing else is in flight).
    fn acquire(self: &Arc<Self>, bytes: u64) -> Grant {
        let mut u = self.used.lock().unwrap_or_else(|p| p.into_inner());
        while u.1 > 0 && (u.0 + bytes > self.max_bytes || u.1 >= self.max_reqs) {
            u = self.freed.wait(u).unwrap_or_else(|p| p.into_inner());
        }
        u.0 += bytes;
        u.1 += 1;
        Grant { b: self.clone(), bytes }
    }
}

struct Grant {
    b: Arc<Budget>,
    bytes: u64,
}

impl Drop for Grant {
    fn drop(&mut self) {
        let mut u = self.b.used.lock().unwrap_or_else(|p| p.into_inner());
        u.0 -= self.bytes;
        u.1 -= 1;
        drop(u);
        self.b.freed.notify_all();
    }
}

enum Work {
    /// `deadline`: the request's one deadline, which also bounds the flush of a FUA write.
    /// `space`: the extents a write reserved in the store; they carry data when the write succeeds.
    Io { ticket: Ticket, fua: bool, deadline: Option<Instant>, space: Option<Vec<u64>> },
    Done(u32),
}

struct Pending {
    req: Request,
    work: Work,
    _grant: Grant,
}

/// How a connection ended.
#[derive(Debug, PartialEq, Eq)]
pub enum ConnEnd {
    /// the client sent NBD_CMD_DISC; every request was answered and the store flushed
    Disconnected,
    /// the client left during negotiation
    NegotiationClosed,
    /// the socket closed without DISC (client died, or the service shut the socket down)
    Closed,
    /// the client broke the protocol; the connection was closed
    ProtocolError(String),
}

fn errno_of(e: &VnxError) -> u32 {
    e.code.errno()
}

fn read_full(r: &mut impl Read, buf: &mut [u8]) -> std::io::Result<bool> {
    // Ok(false) on a clean EOF before the first byte
    let mut got = 0;
    while got < buf.len() {
        match r.read(&mut buf[got..]) {
            Ok(0) if got == 0 => return Ok(false),
            Ok(0) => return Err(std::io::ErrorKind::UnexpectedEof.into()),
            Ok(n) => got += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e),
        }
    }
    Ok(true)
}

fn discard(r: &mut impl Read, mut n: u64) -> std::io::Result<()> {
    let mut sink = [0u8; 64 * 1024];
    while n > 0 {
        let k = n.min(sink.len() as u64) as usize;
        r.read_exact(&mut sink[..k])?;
        n -= k as u64;
    }
    Ok(())
}

impl Device {
    /// Serve one NBD connection until DISC, close or a protocol error. With `negotiate`, run the server side of
    /// the handshake first (the kernel attach path negotiates on the same socket pair before the ioctls).
    pub fn serve(&self, stream: UnixStream, export: &ExportInfo, negotiate: bool) -> Result<ConnEnd> {
        // the connection is shut down on every way out of here (return, `?`, panic): a peer never waits on a
        // connection nobody serves
        struct Shut(UnixStream);
        impl Drop for Shut {
            fn drop(&mut self) {
                let _ = self.0.shutdown(std::net::Shutdown::Both);
            }
        }
        let _shut = Shut(stream.try_clone().map_err(|e| nbd::io_err("clone socket", e))?);
        let mut rd = stream;
        if negotiate {
            // a client that never negotiates does not hold a connection slot forever
            let _ = rd.set_read_timeout(Some(NEGOTIATION_TIMEOUT));
            let negotiated = nbd::server_negotiate(&mut rd, export);
            let _ = rd.set_read_timeout(None);
            match negotiated {
                Ok(Negotiated::Transmission) => {}
                Ok(Negotiated::Closed) => return Ok(ConnEnd::NegotiationClosed),
                Err(e) => {
                    self.stats.protocol_errors.fetch_add(1, Ordering::Relaxed);
                    let _ = rd.shutdown(std::net::Shutdown::Both);
                    return Ok(ConnEnd::ProtocolError(e.to_string()));
                }
            }
        }
        self.stats.connections.fetch_add(1, Ordering::Relaxed);
        let wr = Mutex::new(rd.try_clone().map_err(|e| nbd::io_err("clone socket", e))?);
        let dead = AtomicBool::new(false);
        let budget = Arc::new(Budget {
            used: Mutex::new((0, 0)),
            freed: Condvar::new(),
            max_bytes: self.cfg.max_inflight_bytes,
            max_reqs: self.cfg.max_inflight,
        });
        let (tx, rx) = sync_channel::<Pending>(self.cfg.max_inflight);
        let rx = Mutex::new(rx);
        let end = std::thread::scope(|sc| {
            for i in 0..self.cfg.repliers {
                let (rx, wr, dead) = (&rx, &wr, &dead);
                std::thread::Builder::new()
                    .name(format!("vnxd-nbd-reply-{i}"))
                    .spawn_scoped(sc, move || self.replier(rx, wr, dead))
                    .map_err(|e| VnxError::new(ErrorCode::ResourceExhausted, format!("spawn reply thread: {e}")))?;
            }
            let end = self.read_loop(&mut rd, &tx, &budget, &dead);
            drop(tx); // repliers answer what is queued, then exit
            Ok::<ConnEnd, VnxError>(end)
        })?;
        if end == ConnEnd::Disconnected {
            self.stats.disconnects.fetch_add(1, Ordering::Relaxed);
            // every request was answered; make them durable before the connection goes away
            if let Err(e) = self.flush() {
                self.note_error(&format!("flush at disconnect: {e}"));
            }
        }
        let _ = rd.shutdown(std::net::Shutdown::Both);
        Ok(end)
    }

    fn read_loop(&self, rd: &mut UnixStream, tx: &std::sync::mpsc::SyncSender<Pending>, budget: &Arc<Budget>, dead: &AtomicBool) -> ConnEnd {
        let size = self.geometry.capacity_bytes();
        let deadline = || Some(Instant::now() + self.cfg.request_timeout);
        loop {
            let mut h = [0u8; nbd::REQUEST_BYTES];
            match read_full(rd, &mut h) {
                Ok(true) => {}
                Ok(false) => {
                    dead.store(true, Ordering::SeqCst);
                    return ConnEnd::Closed;
                }
                Err(_) => {
                    dead.store(true, Ordering::SeqCst);
                    return ConnEnd::Closed;
                }
            }
            let req = match Request::parse(&h) {
                Ok(r) => r,
                Err(e) => {
                    self.stats.protocol_errors.fetch_add(1, Ordering::Relaxed);
                    dead.store(true, Ordering::SeqCst);
                    return ConnEnd::ProtocolError(e.to_string());
                }
            };
            self.stats.requests.fetch_add(1, Ordering::Relaxed);
            let len = u64::from(req.len);
            let in_range = req.offset.checked_add(len).map_or(false, |end| end <= size);
            let fua = req.flags & nbd::CMD_FLAG_FUA != 0;
            let bad_flags = req.flags & !nbd::CMD_FLAG_FUA != 0; // no other command flag was negotiated
            let deadline = deadline();
            let work_bytes = if matches!(req.kind, nbd::CMD_READ | nbd::CMD_WRITE) { len.min(u64::from(MAX_PAYLOAD)) } else { 0 };
            let grant = budget.acquire(work_bytes);
            let n = self.stats.inflight.fetch_add(1, Ordering::Relaxed) + 1; // decremented once the reply is sent
            self.stats.max_inflight_seen.fetch_max(n, Ordering::Relaxed);
            let submit = |op: Op| match self.queue.submit(op, deadline) {
                Ok(t) => Work::Io { ticket: t, fua: false, deadline, space: None },
                Err(e) => {
                    self.note_error(&e.to_string());
                    Work::Done(errno_of(&e).max(1))
                }
            };
            let work = match req.kind {
                nbd::CMD_READ => {
                    self.stats.reads.fetch_add(1, Ordering::Relaxed);
                    if req.len == 0 || !in_range || bad_flags {
                        Work::Done(crate::error::EINVAL)
                    } else if req.len > MAX_PAYLOAD {
                        Work::Done(crate::error::EOVERFLOW)
                    } else {
                        submit(Op::Read { offset: req.offset, len })
                    }
                }
                nbd::CMD_WRITE => {
                    self.stats.writes.fetch_add(1, Ordering::Relaxed);
                    if req.len > MAX_PAYLOAD {
                        // stay in step with the stream: the payload is read and dropped, never buffered
                        if discard(rd, len).is_err() {
                            dead.store(true, Ordering::SeqCst);
                            return ConnEnd::Closed;
                        }
                        Work::Done(crate::error::EOVERFLOW)
                    } else {
                        let mut data = vec![0u8; req.len as usize];
                        if rd.read_exact(&mut data).is_err() {
                            dead.store(true, Ordering::SeqCst);
                            return ConnEnd::Closed;
                        }
                        if bad_flags {
                            Work::Done(crate::error::EINVAL)
                        } else if self.cfg.read_only {
                            Work::Done(crate::error::EPERM)
                        } else if req.len == 0 {
                            Work::Done(crate::error::EINVAL)
                        } else if !in_range {
                            Work::Done(crate::error::ENOSPC)
                        } else {
                            // the space of the store: a write that needs no new extent (an overwrite of an extent
                            // that carries data) is always allowed
                            let touched = self.touched_extents(req.offset, len);
                            if let Err(e) = self.reserve_space(&touched) {
                                // the store is full: the host sees ENOSPC, nothing is written
                                self.note_error(&e.to_string());
                                Work::Done(errno_of(&e))
                            } else {
                                if fua {
                                    self.stats.fua_writes.fetch_add(1, Ordering::Relaxed);
                                }
                                // NBD has no clock; use a fixed timestamp for deterministic retention checks
                                match submit(Op::Write { offset: req.offset, data, now_ms: 1000 }) {
                                    Work::Io { ticket, deadline, .. } => {
                                        Work::Io { ticket, fua, deadline, space: Some(touched) }
                                    }
                                    w => {
                                        self.settle_space(&touched, false); // the write never started
                                        w
                                    }
                                }
                            }
                        }
                    }
                }
                nbd::CMD_FLUSH => {
                    self.stats.flushes.fetch_add(1, Ordering::Relaxed);
                    if bad_flags {
                        Work::Done(crate::error::EINVAL)
                    } else {
                        submit(Op::Flush)
                    }
                }
                nbd::CMD_TRIM => {
                    self.stats.trims.fetch_add(1, Ordering::Relaxed);
                    if bad_flags {
                        Work::Done(crate::error::EINVAL)
                    } else if self.cfg.read_only {
                        Work::Done(crate::error::EPERM)
                    } else if !in_range {
                        Work::Done(crate::error::EINVAL)
                    } else {
                        Work::Done(0) // trimmed data is undefined by the protocol; no space is released (documented)
                    }
                }
                nbd::CMD_DISC => {
                    drop(grant);
                    self.stats.inflight.fetch_sub(1, Ordering::Relaxed);
                    return ConnEnd::Disconnected;
                }
                _ => Work::Done(crate::error::EINVAL), // not advertised: WRITE_ZEROES, CACHE, BLOCK_STATUS, ...
            };
            if tx.send(Pending { req, work, _grant: grant }).is_err() {
                dead.store(true, Ordering::SeqCst);
                return ConnEnd::Closed; // every replier is gone (the socket failed)
            }
        }
    }

    fn replier(&self, rx: &Mutex<Receiver<Pending>>, wr: &Mutex<UnixStream>, dead: &AtomicBool) {
        loop {
            let p = match rx.lock().unwrap_or_else(|p| p.into_inner()).recv() {
                Ok(p) => p,
                Err(_) => return,
            };
            let (errno, data) = self.complete(&p.work, p.req, dead);
            if errno != 0 {
                self.stats.errors.fetch_add(1, Ordering::Relaxed);
                if errno == crate::error::EIO {
                    self.stats.eio.fetch_add(1, Ordering::Relaxed);
                }
            }
            let mut msg = nbd::reply_header(errno, p.req.handle).to_vec();
            if let Some(d) = &data {
                self.stats.bytes_read.fetch_add(d.len() as u64, Ordering::Relaxed);
                msg.extend_from_slice(d);
            } else if errno == 0 && p.req.kind == nbd::CMD_WRITE {
                self.stats.bytes_written.fetch_add(u64::from(p.req.len), Ordering::Relaxed);
            }
            let mut w = wr.lock().unwrap_or_else(|p| p.into_inner());
            if !dead.load(Ordering::SeqCst) && w.write_all(&msg).and_then(|_| w.flush()).is_err() {
                dead.store(true, Ordering::SeqCst);
                let _ = w.shutdown(std::net::Shutdown::Both); // unblocks the reader
            }
            drop(w);
            drop(p);
            self.stats.inflight.fetch_sub(1, Ordering::Relaxed);
        }
    }

    /// Wait for a request's outcome: (errno, read data).
    fn complete(&self, w: &Work, req: Request, dead: &AtomicBool) -> (u32, Option<Vec<u8>>) {
        let (ticket, fua, deadline, space) = match w {
            Work::Done(e) => return (*e, None),
            Work::Io { ticket, fua, deadline, space } => (ticket, *fua, *deadline, space),
        };
        let wait = |t: &Ticket| loop {
            if dead.load(Ordering::SeqCst) {
                t.cancel(); // no effect on a request that already started
            }
            if let Some(r) = t.wait_timeout(Duration::from_millis(200)) {
                break r;
            }
        };
        let fail = |e: VnxError| {
            if e.code == ErrorCode::Cancelled && dead.load(Ordering::SeqCst) {
                self.stats.cancelled_on_close.fetch_add(1, Ordering::Relaxed);
            }
            self.note_error(&e.to_string());
            (errno_of(&e).max(1), None)
        };
        match wait(ticket) {
            // a reply's data is exactly the length asked for, or the reply stream would be out of step
            Ok(Reply::Data(d)) if d.len() == req.len as usize => (0, Some(d)),
            Ok(Reply::Data(d)) => fail(VnxError::new(ErrorCode::Internal, format!("read returned {} of {} bytes", d.len(), req.len))),
            Ok(Reply::Written(_)) => {
                // the data is stored: the extents the write touched carry data now
                if let Some(t) = space {
                    self.settle_space(t, true);
                }
                if fua {
                    let flushed = self
                        .queue
                        .submit(Op::Flush, deadline)
                        .and_then(|t| wait(&t));
                    match flushed {
                        Ok(_) => (0, None),
                        Err(e) => fail(e),
                    }
                } else {
                    (0, None)
                }
            }
            Ok(_) => (0, None),
            Err(e) => {
                // the write failed: its reservation is given back, the store keeps what it holds
                if let Some(t) = space {
                    self.settle_space(t, false);
                }
                fail(e)
            }
        }
    }
}

/// The interface the management session drives.
impl crate::authz::MgmtBackend for DeviceMgmt<'_> {
    fn capacity(&self) -> Result<Value> {
        Ok(self.dev.capacity_json())
    }
    fn health(&self) -> Result<Value> {
        Ok(self.dev.health_json())
    }
    fn stats(&self) -> Result<Value> {
        Ok(self.dev.stats_json())
    }
    /// The metrics of the device in the Prometheus text exposition format (0.0.4).
    ///
    /// Every sample is derived from the structures the device already holds: the counters of the stats view, the
    /// health view, the health of the backend, the counters of the cache, the report of the last scrub and the
    /// capacity of the volume. Nothing is counted twice and nothing is lost between two scrapes. The DNA simulator
    /// counters are not part of it: the service writes to a real backend, never to the simulator.
    fn metrics(&self) -> Result<String> {
        let dev = self.dev;
        let mut samples = crate::metrics::from_stats(&dev.stats_json());
        samples.extend(crate::metrics::from_health(&dev.health_json()));
        samples.extend(crate::metrics::from_backend(&dev.backend_health()));
        samples.extend(crate::metrics::from_capacity(&dev.capacity_json()));
        if let Some(c) = &dev.cache {
            samples.extend(crate::metrics::from_cache(&c.stats, c.cached_extents(), c.dirty_extents().len()));
        }
        // the last scrub, or zeros when no scrub ran yet (the time of the last run is 0, never the epoch of a run)
        let (report, unix_ms) = dev
            .last_scrub()
            .unwrap_or((ScrubReport { checked: 0, failed: Vec::new() }, 0));
        samples.extend(crate::metrics::from_scrub(&report, unix_ms));
        Ok(crate::metrics::render(&samples))
    }
    fn shutdown(&self) -> Result<Value> {
        (self.on_shutdown)();
        Ok(obj([]))
    }
}

/// A device plus what `shutdown` does (wake the controller).
pub struct DeviceMgmt<'a> {
    pub dev: &'a Device,
    pub on_shutdown: &'a (dyn Fn() + Sync),
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::authz::MgmtBackend;
    use crate::journal::tests::TempDir;
    use crate::media::MemoryMedia;
    use crate::nbd::*;

    fn make(t: &TempDir, cfg: DeviceConfig, media: Arc<dyn MediaAdapter>) -> Device {
        let geo = Geometry::new(4096, 4, 64).unwrap(); // 256 KiB
        Volume::create(&t.0.join("vol"), geo, [7; 16], 0, media.clone(), VolumeConfig::default()).unwrap();
        Device::open(&t.0.join("vol"), media, cfg).unwrap()
    }

    struct Client {
        s: UnixStream,
        next: u64,
    }

    impl Client {
        fn send(&mut self, kind: u16, flags: u16, offset: u64, len: u32, data: &[u8]) -> u64 {
            self.next += 1;
            let r = Request { flags, kind, handle: self.next, offset, len };
            self.s.write_all(&r.encode()).unwrap();
            self.s.write_all(data).unwrap();
            self.next
        }
        fn reply(&mut self, data_len: usize) -> (u64, u32, Vec<u8>) {
            let mut h = [0u8; REPLY_BYTES];
            self.s.read_exact(&mut h).unwrap();
            assert_eq!(u32::from_be_bytes(h[0..4].try_into().unwrap()), SIMPLE_REPLY_MAGIC);
            let e = u32::from_be_bytes(h[4..8].try_into().unwrap());
            let handle = u64::from_be_bytes(h[8..16].try_into().unwrap());
            let mut d = vec![0u8; if e == 0 { data_len } else { 0 }];
            self.s.read_exact(&mut d).unwrap();
            (handle, e, d)
        }
        fn call(&mut self, kind: u16, flags: u16, offset: u64, len: u32, data: &[u8], data_len: usize) -> (u32, Vec<u8>) {
            let h = self.send(kind, flags, offset, len, data);
            let (rh, e, d) = self.reply(data_len);
            assert_eq!(rh, h);
            (e, d)
        }
    }

    fn with_conn<R>(dev: &Device, f: impl FnOnce(&mut Client) -> R) -> (R, ConnEnd) {
        let (a, b) = UnixStream::pair().unwrap();
        let export = dev.export("vnx");
        std::thread::scope(|sc| {
            let srv = sc.spawn(|| dev.serve(a, &export, true).unwrap());
            let mut b = b;
            let info = client_negotiate(&mut b, "vnx").unwrap();
            assert_eq!(info.size, dev.geometry().capacity_bytes());
            let mut c = Client { s: b, next: 0 };
            let r = f(&mut c);
            drop(c);
            (r, srv.join().unwrap())
        })
    }

    #[test]
    fn read_write_flush_trim_fua_and_disc() {
        let t = TempDir::new("nbd-rw");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let ((), end) = with_conn(&dev, |c| {
            let pat: Vec<u8> = (0..12288u32).map(|i| (i * 7 % 251) as u8).collect();
            assert_eq!(c.call(CMD_WRITE, 0, 4096, 12288, &pat, 0).0, 0);
            assert_eq!(c.call(CMD_READ, 0, 4096, 12288, &[], 12288), (0, pat.clone()));
            // unaligned read across extents, unwritten bytes are zeros
            let (e, d) = c.call(CMD_READ, 0, 4000, 200, &[], 200);
            assert_eq!(e, 0);
            assert_eq!(&d[..96], &[0u8; 96][..]);
            assert_eq!(&d[96..], &pat[..104]);
            assert_eq!(c.call(CMD_WRITE, CMD_FLAG_FUA, 0, 512, &[9u8; 512], 0).0, 0);
            assert_eq!(c.call(CMD_FLUSH, 0, 0, 0, &[], 0).0, 0);
            assert_eq!(c.call(CMD_TRIM, 0, 0, 4096, &[], 0).0, 0);
            assert_eq!(c.call(CMD_TRIM, 0, 1 << 20, 4096, &[], 0).0, crate::error::EINVAL);
            // out of range, zero length, unknown commands: errors, never success
            assert_eq!(c.call(CMD_READ, 0, 256 * 1024 - 512, 1024, &[], 0).0, crate::error::EINVAL);
            assert_eq!(c.call(CMD_WRITE, 0, 256 * 1024 - 512, 1024, &[1u8; 1024], 0).0, crate::error::ENOSPC);
            assert_eq!(c.call(CMD_READ, 0, 0, 0, &[], 0).0, crate::error::EINVAL);
            assert_eq!(c.call(CMD_READ, 0, u64::MAX - 10, 512, &[], 0).0, crate::error::EINVAL);
            assert_eq!(c.call(6, 0, 0, 4096, &[], 0).0, crate::error::EINVAL);
            assert_eq!(c.call(CMD_READ, 0, 0, 512, &[], 512), (0, vec![9u8; 512]));
            c.send(CMD_DISC, 0, 0, 0, &[]);
        });
        assert_eq!(end, ConnEnd::Disconnected);
        assert_eq!(dev.stats.fua_writes.load(Ordering::Relaxed), 1);
        assert_eq!(dev.stats.trims.load(Ordering::Relaxed), 2);
        assert!(dev.stats.errors.load(Ordering::Relaxed) >= 6);
        dev.close().unwrap();
    }

    #[test]
    fn many_requests_in_flight_complete_out_of_order_with_correct_handles() {
        let t = TempDir::new("nbd-pipe");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let ((), _) = with_conn(&dev, |c| {
            let mut sent = std::collections::BTreeMap::new();
            for i in 0..64u64 {
                let off = (i % 32) * 8192;
                let h = c.send(CMD_WRITE, 0, off, 4096, &[i as u8; 4096]);
                sent.insert(h, off);
            }
            for _ in 0..64 {
                let (h, e, _) = c.reply(0);
                assert_eq!(e, 0);
                assert!(sent.remove(&h).is_some(), "unknown or duplicate handle {h}");
            }
            // overlapping writes were applied in submission order: the last one wins
            for i in 0..32u64 {
                let (e, d) = c.call(CMD_READ, 0, i * 8192, 4096, &[], 4096);
                assert_eq!(e, 0);
                assert_eq!(d, vec![(i + 32) as u8; 4096]);
            }
            c.send(CMD_DISC, 0, 0, 0, &[]);
        });
        dev.close().unwrap();
    }

    #[test]
    fn backend_failures_are_eio_and_never_success() {
        let t = TempDir::new("nbd-fault");
        let cfg = DeviceConfig {
            retry: RetryPolicy { max_attempts: 2, base_delay: Duration::from_millis(1), max_delay: Duration::from_millis(2) },
            health: HealthConfig { probe_interval: Duration::from_millis(50), ..HealthConfig::default() },
            cache: None,
            fault_control: Some(t.0.join("faults")),
            ..DeviceConfig::default()
        };
        let dev = make(&t, cfg, Arc::new(MemoryMedia::new()));
        let ((), _) = with_conn(&dev, |c| {
            assert_eq!(c.call(CMD_WRITE, 0, 0, 4096, &[5u8; 4096], 0).0, 0);
            std::fs::write(t.0.join("faults"), "get").unwrap();
            assert_eq!(c.call(CMD_READ, 0, 0, 4096, &[], 0).0, crate::error::EIO);
            std::fs::write(t.0.join("faults"), "put").unwrap();
            let e = c.call(CMD_WRITE, 0, 0, 4096, &[6u8; 4096], 0).0;
            assert!(e == crate::error::EIO || e == crate::error::EPERM, "write under a failing backend: errno {e}");
            assert_eq!(dev.health_state(), "degraded");
            std::fs::write(t.0.join("faults"), "sync").unwrap();
            assert_eq!(c.call(CMD_FLUSH, 0, 0, 0, &[], 0).0 != 0, true, "a failed flush is reported");
            std::fs::remove_file(t.0.join("faults")).unwrap();
            std::thread::sleep(Duration::from_millis(120)); // past the probe interval
            // the old data is intact; writes work again once a probe succeeds
            assert_eq!(c.call(CMD_READ, 0, 0, 4096, &[], 4096), (0, vec![5u8; 4096]));
            let mut ok = false;
            for _ in 0..20 {
                if c.call(CMD_WRITE, 0, 0, 4096, &[7u8; 4096], 0).0 == 0 {
                    ok = true;
                    break;
                }
                std::thread::sleep(Duration::from_millis(60));
            }
            assert!(ok, "writes resume after the backend recovers");
            assert_eq!(c.call(CMD_READ, 0, 0, 4096, &[], 4096), (0, vec![7u8; 4096]));
            c.send(CMD_DISC, 0, 0, 0, &[]);
        });
        assert!(dev.stats.eio.load(Ordering::Relaxed) >= 1);
        assert!(dev.gate().unwrap().injected.load(Ordering::Relaxed) >= 1);
        dev.close().unwrap();
    }

    #[test]
    fn read_only_device_refuses_writes_and_trims() {
        let t = TempDir::new("nbd-ro");
        let dev = make(&t, DeviceConfig { read_only: true, ..DeviceConfig::default() }, Arc::new(MemoryMedia::new()));
        assert_ne!(dev.transmission_flags() & TF_READ_ONLY, 0);
        let ((), _) = with_conn(&dev, |c| {
            assert_eq!(c.call(CMD_WRITE, 0, 0, 512, &[1u8; 512], 0).0, crate::error::EPERM);
            assert_eq!(c.call(CMD_TRIM, 0, 0, 512, &[], 0).0, crate::error::EPERM);
            assert_eq!(c.call(CMD_READ, 0, 0, 512, &[], 512), (0, vec![0u8; 512]));
            // the payload of a refused write was consumed: the stream stays in step
            assert_eq!(c.call(CMD_FLUSH, 0, 0, 0, &[], 0).0, 0);
        });
        dev.close().unwrap();
    }

    #[test]
    fn oversized_write_is_refused_without_buffering() {
        let t = TempDir::new("nbd-big");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let ((), _) = with_conn(&dev, |c| {
            let n = MAX_PAYLOAD + 4096;
            let h = c.send(CMD_WRITE, 0, 0, n, &[]);
            let chunk = vec![0u8; 1 << 20];
            let mut left = n as usize;
            let s = c.s.try_clone().unwrap();
            let w = std::thread::spawn(move || {
                let mut s = s;
                while left > 0 {
                    let k = left.min(chunk.len());
                    s.write_all(&chunk[..k]).unwrap();
                    left -= k;
                }
            });
            let (rh, e, _) = c.reply(0);
            w.join().unwrap();
            assert_eq!((rh, e), (h, crate::error::EOVERFLOW));
            assert_eq!(c.call(CMD_READ, 0, 0, 512, &[], 512).0, 0);
        });
        dev.close().unwrap();
    }

    #[test]
    fn protocol_violation_closes_the_connection() {
        let t = TempDir::new("nbd-proto");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let ((), end) = with_conn(&dev, |c| {
            c.s.write_all(&[0xffu8; REQUEST_BYTES]).unwrap();
            let mut b = [0u8; 1];
            assert_eq!(c.s.read(&mut b).unwrap(), 0, "the server closes the connection");
        });
        assert!(matches!(end, ConnEnd::ProtocolError(_)));
        assert_eq!(dev.stats.protocol_errors.load(Ordering::Relaxed), 1);
        dev.close().unwrap();
    }

    #[test]
    fn writes_survive_a_reopen_and_close_flushes_write_back() {
        let t = TempDir::new("nbd-persist");
        let back = t.0.join("backend");
        let media = Arc::new(crate::dirmedia::DirMedia::open(&back).unwrap());
        let dev = make(&t, DeviceConfig { cache: Some(CacheMode::WriteBack), ..DeviceConfig::default() }, media);
        let ((), _) = with_conn(&dev, |c| {
            assert_eq!(c.call(CMD_WRITE, 0, 8192, 4096, &[0x5a; 4096], 0).0, 0);
            // the client leaves without DISC: nothing is lost because close() flushes the write-back cache
        });
        dev.close().unwrap();
        let media = Arc::new(crate::dirmedia::DirMedia::open(&back).unwrap());
        let dev = Device::open(&t.0.join("vol"), media, DeviceConfig::default()).unwrap();
        let ((), _) = with_conn(&dev, |c| {
            assert_eq!(c.call(CMD_READ, 0, 8192, 4096, &[], 4096), (0, vec![0x5a; 4096]));
        });
        dev.close().unwrap();
    }

    #[test]
    fn the_low_memory_preset_runs_a_device_round_trip() {
        // the `vnxd nbd --low-memory` budgets (docs/V14_RASPBERRY_PI_PROFILE.md §3) are accepted by the device with
        // the default geometry (4 KiB blocks, 16-block extents), and requests round-trip through every layer
        let t = TempDir::new("nbd-lowmem");
        let media: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let geo = Geometry::new(4096, 16, 256).unwrap(); // 1 MiB
        Volume::create(&t.0.join("vol"), geo, [7; 16], 0, media.clone(), VolumeConfig::default()).unwrap();
        let mut cfg = DeviceConfig::default();
        crate::nbdctl::apply_low_memory(&mut cfg);
        let dev = Device::open(&t.0.join("vol"), media, cfg).unwrap();
        let ((), _) = with_conn(&dev, |c| {
            let data: Vec<u8> = (0..3 * 65536u32).map(|i| (i * 7 + 3) as u8).collect();
            assert_eq!(c.call(CMD_WRITE, 0, 4096, data.len() as u32, &data, 0).0, 0);
            assert_eq!(c.call(CMD_FLUSH, 0, 0, 0, &[], 0).0, 0);
            assert_eq!(c.call(CMD_READ, 0, 4096, data.len() as u32, &[], data.len()), (0, data));
            c.send(CMD_DISC, 0, 0, 0, &[]);
        });
        assert!(dev.stats.max_inflight_seen.load(Ordering::Relaxed) <= 16);
        dev.close().unwrap();
        // the floor: one largest request must fit, so a budget below it is refused rather than run unbounded
        let mut low = DeviceConfig::default();
        crate::nbdctl::apply_low_memory(&mut low);
        assert!(ram_config(low.ram_bytes, geo).is_ok());
        assert!(ram_config(2 * u64::from(MAX_PAYLOAD), geo).is_err());
    }

    #[test]
    fn the_device_exposes_its_metrics_in_the_prometheus_text_format() {
        let t = TempDir::new("nbd-metrics");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let backend = DeviceMgmt { dev: &dev, on_shutdown: &|| {} };
        let text = backend.metrics().unwrap();
        // the health of the device, the capacity of the volume and the state of the backend
        assert!(text.contains("vnxd_health_state{state=\"ok\"} 1\n"), "{text}");
        assert!(text.contains("vnxd_health_state{state=\"degraded\"} 0\n"), "{text}");
        assert!(text.contains("vnxd_backend_state{state=\"healthy\"} 1\n"), "{text}");
        assert!(text.contains("vnxd_read_only 0\n"), "{text}");
        assert!(text.contains("vnxd_capacity_bytes "), "{text}");
        assert!(text.contains("vnxd_extent_count "), "{text}");
        // no scrub ran yet: the time of the last run is 0, not the time of a run that never happened
        assert!(text.contains("vnxd_scrub_last_run_unix_ms 0\n"), "{text}");
        // the exposition is longer than a `Ty::Str` field of the protocol (1024 bytes): the result of the `metrics`
        // operation is a `Ty::Text` field for that reason
        assert!(text.len() > 1024, "the exposition is {} bytes", text.len());
        // every line is a comment or a series: a name, optionally labels, and an integer
        for line in text.lines() {
            if line.starts_with('#') {
                continue;
            }
            let (series, value) = line.rsplit_once(' ').expect("a series and a value");
            assert!(value.parse::<i64>().is_ok(), "{line}");
            let name = series.split('{').next().unwrap();
            assert!(crate::metrics::is_valid_name(name), "{line}");
        }
        // a scrub of the volume is reported by the metrics
        dev.run_scrub(1_700_000_000_000).unwrap();
        let text = backend.metrics().unwrap();
        assert!(text.contains("vnxd_scrub_last_run_unix_ms 1700000000000\n"), "{text}");
        assert!(text.contains("vnxd_scrub_checked_extents "), "{text}");
    }

    #[test]
    fn unknown_flags_are_refused_and_the_inflight_gauge_returns_to_zero() {
        let t = TempDir::new("nbd-flags");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let ((), _) = with_conn(&dev, |c| {
            assert_eq!(c.call(CMD_WRITE, 1 << 1, 0, 512, &[1u8; 512], 0).0, crate::error::EINVAL, "NO_HOLE not negotiated");
            assert_eq!(c.call(CMD_READ, 1 << 2, 0, 512, &[], 0).0, crate::error::EINVAL);
            assert_eq!(c.call(CMD_FLUSH, 1 << 3, 0, 0, &[], 0).0, crate::error::EINVAL);
            // the refused write's payload was consumed: the stream is still in step
            assert_eq!(c.call(CMD_READ, 0, 0, 512, &[], 512), (0, vec![0u8; 512]));
            c.send(CMD_DISC, 0, 0, 0, &[]);
        });
        assert_eq!(dev.stats.inflight.load(Ordering::Relaxed), 0);
        assert!(dev.stats.max_inflight_seen.load(Ordering::Relaxed) >= 1);
        dev.close().unwrap();
    }

    #[test]
    fn a_client_that_never_negotiates_is_dropped() {
        let t = TempDir::new("nbd-idle");
        let dev = make(&t, DeviceConfig::default(), Arc::new(MemoryMedia::new()));
        let (a, b) = UnixStream::pair().unwrap();
        let t0 = Instant::now();
        let end = dev.serve(a, &dev.export("vnx"), true).unwrap();
        assert!(matches!(end, ConnEnd::ProtocolError(_) | ConnEnd::NegotiationClosed), "{end:?}");
        assert!(t0.elapsed() >= NEGOTIATION_TIMEOUT && t0.elapsed() < NEGOTIATION_TIMEOUT * 3);
        drop(b);
        dev.close().unwrap();
    }

    #[test]
    fn fault_words_parse() {
        assert_eq!(parse_fault_mask("put get\n").unwrap(), F_PUT | F_GET);
        assert_eq!(parse_fault_mask("").unwrap(), 0);
        assert_eq!(parse_fault_mask("all").unwrap(), 31);
        assert!(parse_fault_mask("explode").is_err());
    }

    #[test]
    fn ram_budget_holds_one_largest_request() {
        let geo = Geometry::new(4096, 16, 1024).unwrap();
        assert!(ram_config(64 << 20, geo).is_err());
        let c = ram_config(256 << 20, geo).unwrap();
        assert!(c.quotas[&Subsystem::Io] >= 2 * u64::from(MAX_PAYLOAD) + 3 * geo.extent_bytes());
    }
}
