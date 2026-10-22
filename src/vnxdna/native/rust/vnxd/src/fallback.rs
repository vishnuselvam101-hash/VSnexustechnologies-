//! Fallback: retries by error class, backend health and degraded (read-only) mode (master directive §10;
//! docs/V14_FAILURE_AND_RECOVERY_MODEL.md §3–§4).
//!
//! [`ResilientMedia`] wraps a media adapter:
//!
//! * **retry by class**: an operation failing with a *retryable* code (BACKEND_UNAVAILABLE, BACKEND_BUSY, TIMEOUT,
//!   RESOURCE_EXHAUSTED) is repeated up to `max_attempts` times with exponential backoff (`base_delay` doubling up to
//!   `max_delay`); a *permanent* code (CORRUPTION, NOT_FOUND, INVALID_ARGUMENT, ...) is returned at once, never
//!   retried. A `put` is only repeated after checking that the failed attempt left nothing behind: if the generation
//!   is present with exactly the bytes given, the earlier attempt reached the backend and the put succeeds; if it is
//!   present with other bytes the put fails with CORRUPTION;
//! * **health**: every operation's final outcome feeds a health record (consecutive failures, an exponentially
//!   weighted success score, state). Errors about the request, the data or the key (NOT_FOUND, INVALID_ARGUMENT,
//!   CORRUPTION, OUT_OF_RANGE, UNAUTHORIZED) say nothing about availability and are not counted. Consecutive
//!   failures are counted per operation kind (put, get, remove, list, sync) and the worst kind decides: `degrade_after` consecutive failures make the backend DEGRADED,
//!   `unavailable_after` make it UNAVAILABLE; a success clears its own kind only, so working reads or removes never
//!   hide failing puts;
//! * **degraded mode**: while the backend is UNAVAILABLE, `put`, `remove` and `sync` are refused at once with
//!   READ_ONLY (the device turns read-only: the host sees EPERM, nothing is half-written), while `get` and `list`
//!   are still attempted once each (reads keep working where the backend still answers). Every `probe_interval` one
//!   write-side operation is let through as a probe: when it succeeds, every write-side kind (put, remove, sync)
//!   starts afresh and, unless reads are still failing, the backend is HEALTHY again; a failed probe keeps it
//!   UNAVAILABLE for another interval (a probe that panics releases its slot);
//! * every state change and every exhausted retry is an audit event (`backend_state`, `retry_exhausted`).

use crate::addr::GenerationRef;
use crate::audit::{record_or_report, AuditSink};
use crate::error::{err, ErrorClass, ErrorCode, Result};
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RetryPolicy {
    /// attempts in total (1 = no retry)
    pub max_attempts: u32,
    pub base_delay: Duration,
    pub max_delay: Duration,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        RetryPolicy { max_attempts: 4, base_delay: Duration::from_millis(10), max_delay: Duration::from_millis(500) }
    }
}

impl RetryPolicy {
    pub fn validate(&self) -> Result<()> {
        if self.max_attempts == 0 || self.max_attempts > 32 || self.base_delay > self.max_delay {
            return err(ErrorCode::InvalidArgument, "retry policy: 1..=32 attempts and base_delay <= max_delay");
        }
        Ok(())
    }

    /// Delay before attempt `n` (n >= 2): base x 2^(n-2), capped at `max_delay`.
    pub fn delay_before(&self, attempt: u32) -> Duration {
        let shift = attempt.saturating_sub(2).min(31);
        self.base_delay.checked_mul(1u32 << shift).map_or(self.max_delay, |d| d.min(self.max_delay))
    }

    /// Run `op` until it succeeds, fails permanently or runs out of attempts. Returns the last result and the
    /// number of attempts made.
    pub fn run<T>(&self, mut op: impl FnMut(u32) -> Result<T>, sleep: &dyn Fn(Duration)) -> (Result<T>, u32) {
        let mut attempt = 1;
        loop {
            let r = op(attempt);
            match &r {
                Err(e) if e.code.class() == ErrorClass::Retryable && attempt < self.max_attempts => {
                    attempt += 1;
                    sleep(self.delay_before(attempt));
                }
                _ => return (r, attempt),
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BackendState {
    Healthy,
    Degraded,
    Unavailable,
}

impl BackendState {
    pub fn as_str(self) -> &'static str {
        match self {
            BackendState::Healthy => "healthy",
            BackendState::Degraded => "degraded",
            BackendState::Unavailable => "unavailable",
        }
    }

    /// The device health state of the management protocol (`ok`, `degraded`; `failed` is decided above the media).
    pub fn device_state(self) -> &'static str {
        match self {
            BackendState::Healthy => "ok",
            BackendState::Degraded | BackendState::Unavailable => "degraded",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct HealthConfig {
    pub degrade_after: u32,
    pub unavailable_after: u32,
    pub probe_interval: Duration,
    /// weight of the newest outcome in the success score (0 < alpha <= 1)
    pub score_alpha: f64,
}

impl Default for HealthConfig {
    fn default() -> Self {
        HealthConfig { degrade_after: 1, unavailable_after: 3, probe_interval: Duration::from_secs(5), score_alpha: 0.2 }
    }
}

impl HealthConfig {
    pub fn validate(&self) -> Result<()> {
        if self.degrade_after == 0 || self.unavailable_after < self.degrade_after || !(self.score_alpha > 0.0 && self.score_alpha <= 1.0) {
            return err(ErrorCode::InvalidArgument, "health: 1 <= degrade_after <= unavailable_after, 0 < score_alpha <= 1");
        }
        Ok(())
    }
}

/// A snapshot of the backend's health.
#[derive(Clone, Debug, PartialEq)]
pub struct Health {
    pub state: BackendState,
    pub consecutive_failures: u32,
    /// exponentially weighted share of successful operations, 1.0 = all recent operations succeeded
    pub score: f64,
    pub operations: u64,
    pub failures: u64,
    pub retries: u64,
    pub refused_read_only: u64,
    pub transitions: u64,
    pub last_error: Option<String>,
}

impl Health {
    pub fn to_json(&self) -> Value {
        obj([
            ("backend", Value::Str(self.state.as_str().into())),
            ("consecutive_failures", Value::Int(i64::from(self.consecutive_failures))),
            ("score_milli", Value::Int((self.score * 1000.0).round() as i64)),
            ("operations", Value::Int(self.operations as i64)),
            ("failures", Value::Int(self.failures as i64)),
            ("retries", Value::Int(self.retries as i64)),
            ("refused_read_only", Value::Int(self.refused_read_only as i64)),
            ("writes", Value::Str(if self.state == BackendState::Unavailable { "read_only" } else { "accepted" }.into())),
        ])
    }
}

struct Tracker {
    h: Health,
    /// consecutive failed operations per operation kind: a backend whose removes work but whose puts fail cannot
    /// store anything, and must not look healthy because the removes succeed
    streak: std::collections::BTreeMap<&'static str, u32>,
    /// when the backend became (or was last confirmed) unavailable
    since: Option<Instant>,
    probing: bool,
}

/// Does this failure say something about the backend's availability?
fn counts_against_backend(code: ErrorCode) -> bool {
    !matches!(
        code,
        ErrorCode::NotFound | ErrorCode::InvalidArgument | ErrorCode::Corruption | ErrorCode::OutOfRange | ErrorCode::Unauthorized
    )
}

pub type Sleeper = Arc<dyn Fn(Duration) + Send + Sync>;

pub struct ResilientMedia {
    inner: Arc<dyn MediaAdapter>,
    retry: RetryPolicy,
    cfg: HealthConfig,
    t: Mutex<Tracker>,
    audit: Option<Arc<dyn AuditSink>>,
    sleep: Sleeper,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Side {
    Read,
    Write,
}

impl ResilientMedia {
    pub fn new(inner: Arc<dyn MediaAdapter>, retry: RetryPolicy, cfg: HealthConfig) -> Result<ResilientMedia> {
        retry.validate()?;
        cfg.validate()?;
        let h = Health {
            state: BackendState::Healthy,
            consecutive_failures: 0,
            score: 1.0,
            operations: 0,
            failures: 0,
            retries: 0,
            refused_read_only: 0,
            transitions: 0,
            last_error: None,
        };
        Ok(ResilientMedia {
            inner,
            retry,
            cfg,
            t: Mutex::new(Tracker { h, streak: Default::default(), since: None, probing: false }),
            audit: None,
            sleep: Arc::new(std::thread::sleep),
        })
    }

    pub fn with_audit(mut self, audit: Arc<dyn AuditSink>) -> ResilientMedia {
        self.audit = Some(audit);
        self
    }

    /// Replace the sleep between attempts (tests count delays instead of sleeping).
    pub fn with_sleeper(mut self, sleep: Sleeper) -> ResilientMedia {
        self.sleep = sleep;
        self
    }

    pub fn health(&self) -> Health {
        self.tracker().h.clone()
    }

    fn tracker(&self) -> std::sync::MutexGuard<'_, Tracker> {
        self.t.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn set_state(&self, t: &mut Tracker, to: BackendState, why: &str) {
        if t.h.state == to {
            return;
        }
        let from = t.h.state;
        t.h.state = to;
        t.h.transitions += 1;
        t.since = (to == BackendState::Unavailable).then(Instant::now);
        let detail = obj([
            ("from", Value::Str(from.as_str().into())),
            ("to", Value::Str(to.as_str().into())),
            ("adapter", Value::Str(self.inner.info().kind.into())),
            ("reason", Value::Str(why.into())),
        ]);
        record_or_report(self.audit.as_deref(), "backend_state", detail);
    }

    /// Admission of an operation: Ok(probe?) or READ_ONLY in degraded mode.
    fn admit(&self, side: Side, what: &str) -> Result<bool> {
        let mut t = self.tracker();
        if t.h.state != BackendState::Unavailable || side == Side::Read {
            return Ok(false);
        }
        let due = t.since.map_or(true, |s| s.elapsed() >= self.cfg.probe_interval);
        if due && !t.probing {
            t.probing = true;
            return Ok(true);
        }
        t.h.refused_read_only += 1;
        drop(t);
        err(ErrorCode::ReadOnly, format!("{what} refused: the backend is unavailable, the device is read-only until a probe succeeds"))
    }

    fn outcome<T>(&self, r: &Result<T>, attempts: u32, probe: bool, what: &'static str) {
        let mut t = self.tracker();
        t.h.operations += 1;
        t.h.retries += u64::from(attempts.saturating_sub(1));
        if probe {
            t.probing = false;
        }
        let a = self.cfg.score_alpha;
        // only a failure that counts against the backend proves it is still down; successful (or request-level
        // failing) operations served while it is UNAVAILABLE never postpone the next write probe
        let counted_failure = matches!(r, Err(e) if counts_against_backend(e.code));
        let failed_probe = match r {
            Ok(_) => {
                t.h.score = t.h.score * (1.0 - a) + a;
                t.streak.insert(what, 0);
                if probe {
                    // the backend accepted a write again: every write-side kind gets a fresh start (a sync or remove
                    // streak must not keep the device read-only when only puts are ever admitted as probes)
                    for k in ["put", "remove", "sync"] {
                        t.streak.insert(k, 0);
                    }
                }
                false
            }
            Err(e) if counts_against_backend(e.code) => {
                t.h.failures += 1;
                t.h.score *= 1.0 - a;
                t.h.last_error = Some(e.to_string());
                *t.streak.entry(what).or_insert(0) += 1;
                if attempts > 1 && e.code.class() == ErrorClass::Retryable {
                    let detail = obj([
                        ("operation", Value::Str(what.into())),
                        ("attempts", Value::Int(i64::from(attempts))),
                        ("code", Value::Str(e.code.as_str().into())),
                    ]);
                    record_or_report(self.audit.as_deref(), "retry_exhausted", detail);
                }
                probe
            }
            Err(_) => false,
        };
        // the state follows the worst operation kind: a successful read never clears failing writes
        let (worst, n) = t.streak.iter().max_by_key(|(_, n)| **n).map_or(("", 0), |(k, n)| (*k, *n));
        t.h.consecutive_failures = n;
        if failed_probe || n >= self.cfg.unavailable_after {
            if t.h.state == BackendState::Unavailable && counted_failure {
                t.since = Some(Instant::now()); // still down: the next probe waits another interval
            }
            self.set_state(&mut t, BackendState::Unavailable, &format!("{n} consecutive failed {worst} operations"));
        } else if n >= self.cfg.degrade_after {
            self.set_state(&mut t, BackendState::Degraded, &format!("{n} consecutive failed {worst} operations"));
        } else {
            self.set_state(&mut t, BackendState::Healthy, if probe { "probe succeeded" } else { "operations succeed" });
        }
    }

    fn run<T>(&self, side: Side, what: &'static str, mut op: impl FnMut(u32) -> Result<T>) -> Result<T> {
        let probe = self.admit(side, what)?;
        // a probe that never reaches `outcome` (the operation panicked) must not keep every later write refused
        struct ProbeGuard<'a>(&'a ResilientMedia, bool);
        impl Drop for ProbeGuard<'_> {
            fn drop(&mut self) {
                if self.1 {
                    self.0.tracker().probing = false;
                }
            }
        }
        let guard = ProbeGuard(self, probe);
        // degraded reads and probes get one attempt: a dead backend is not waited for twice
        let unavailable = self.tracker().h.state == BackendState::Unavailable;
        let policy = if probe || unavailable { RetryPolicy { max_attempts: 1, ..self.retry.clone() } } else { self.retry.clone() };
        let (r, attempts) = policy.run(&mut op, &*self.sleep);
        drop(guard);
        self.outcome(&r, attempts, probe, what);
        r
    }
}

impl MediaAdapter for ResilientMedia {
    fn info(&self) -> MediaInfo {
        self.inner.info()
    }

    fn sealed(&self) -> bool {
        self.inner.sealed()
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        self.run(Side::Write, "put", |attempt| {
            if attempt > 1 {
                // the failed attempt may have reached the backend: never put twice, never accept other bytes
                match self.inner.get(at) {
                    Ok(stored) if stored == data => {
                        return Ok(CommitReceipt { at, length: data.len() as u64, sha256: crate::digest::sha256(data) })
                    }
                    Ok(_) => {
                        return err(ErrorCode::Corruption, format!("generation {at:?} holds other bytes after a failed put"))
                    }
                    Err(e) if e.code == ErrorCode::NotFound => {}
                    Err(e) => return Err(e),
                }
            }
            self.inner.put(at, data)
        })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        self.run(Side::Read, "get", |_| self.inner.get(at))
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.run(Side::Write, "remove", |attempt| match self.inner.remove(at) {
            // a retried remove whose first attempt reached the backend
            Err(e) if attempt > 1 && e.code == ErrorCode::NotFound => Ok(()),
            r => r,
        })
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.run(Side::Read, "list", |_| self.inner.list())
    }

    fn sync(&self) -> Result<()> {
        self.run(Side::Write, "sync", |_| self.inner.sync())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation, Geometry};
    use crate::audit::MemorySink;
    use crate::fault::{Fault, FaultMedia, MediaOp, Rule};
    use crate::journal::tests::TempDir;
    use crate::media::MediaInfo;
    use crate::volume::{Volume, VolumeConfig};
    use std::sync::atomic::Ordering;

    fn at(e: u64, g: u64) -> GenerationRef {
        GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
    }

    struct Rig {
        fm: Arc<FaultMedia>,
        rm: Arc<ResilientMedia>,
        audit: Arc<MemorySink>,
        slept: Arc<Mutex<Vec<Duration>>>,
    }

    fn rig(health: HealthConfig) -> Rig {
        let fm = Arc::new(FaultMedia::new());
        let audit = Arc::new(MemorySink::new());
        let slept = Arc::new(Mutex::new(Vec::new()));
        let s = slept.clone();
        let rm = ResilientMedia::new(fm.clone(), RetryPolicy::default(), health)
            .unwrap()
            .with_audit(audit.clone())
            .with_sleeper(Arc::new(move |d| s.lock().unwrap().push(d)));
        Rig { fm, rm: Arc::new(rm), audit, slept }
    }

    #[test]
    fn retryable_errors_are_retried_with_backoff_permanent_ones_never() {
        let r = rig(HealthConfig::default());
        r.fm.add(Rule { op: MediaOp::Put, fault: Fault::Busy, skip: 0, times: 2 });
        r.rm.put(at(0, 1), b"abc").unwrap();
        assert_eq!(*r.slept.lock().unwrap(), vec![Duration::from_millis(10), Duration::from_millis(20)]);
        assert_eq!(r.rm.health().retries, 2);
        // a permanent error: one attempt
        let before = r.fm.calls[1].load(Ordering::Relaxed);
        assert_eq!(r.rm.get(at(9, 1)).unwrap_err().code, ErrorCode::NotFound);
        assert_eq!(r.fm.calls[1].load(Ordering::Relaxed), before + 1);
        r.fm.add(Rule::once(MediaOp::Get, Fault::BitRot));
        assert_eq!(r.rm.get(at(0, 1)).unwrap()[1], b'b' ^ 0x04, "the media layer passes bytes through; the volume verifies");
        // retries run out: the last error, an audit event
        r.fm.add(Rule::always(MediaOp::Sync, Fault::Timeout));
        assert_eq!(r.rm.sync().unwrap_err().code, ErrorCode::Timeout);
        assert!(r.audit.names().contains(&"retry_exhausted".to_string()));
        let p = RetryPolicy { max_attempts: 10, base_delay: Duration::from_millis(100), max_delay: Duration::from_millis(250) };
        assert_eq!((2..=5).map(|a| p.delay_before(a).as_millis()).collect::<Vec<_>>(), vec![100, 200, 250, 250]);
        assert!(RetryPolicy { max_attempts: 0, ..p.clone() }.validate().is_err());
        assert!(HealthConfig { degrade_after: 3, unavailable_after: 2, ..Default::default() }.validate().is_err());
    }

    #[test]
    fn a_put_whose_acknowledgement_was_lost_is_never_stored_twice() {
        let r = rig(HealthConfig::default());
        r.fm.add(Rule::once(MediaOp::Put, Fault::AckLost));
        let rc = r.rm.put(at(1, 1), b"payload").unwrap();
        assert_eq!(rc.sha256, crate::digest::sha256(b"payload"));
        assert_eq!(r.fm.raw().get(at(1, 1)).unwrap(), b"payload");
        // the lost attempt stored other bytes (a lying backend): CORRUPTION, not success
        r.fm.add(Rule::once(MediaOp::Put, Fault::TornPut));
        r.fm.add(Rule::once(MediaOp::Put, Fault::Unavailable));
        let rc = r.rm.put(at(2, 1), b"0123456789").unwrap();
        assert_eq!(rc.length, 10, "the torn put returned a (lying) receipt; the volume's readback catches it");
        r.fm.add(Rule { op: MediaOp::Put, fault: Fault::AckLost, skip: 0, times: 1 });
        r.fm.raw().put(at(3, 1), b"other").unwrap();
        r.fm.raw().remove(at(3, 1)).unwrap();
        assert!(r.rm.put(at(3, 1), b"mine").is_ok());
    }

    #[test]
    fn an_unavailable_backend_makes_the_device_read_only_until_a_probe_succeeds() {
        let r = rig(HealthConfig { degrade_after: 1, unavailable_after: 2, probe_interval: Duration::from_millis(40), score_alpha: 0.5 });
        r.rm.put(at(0, 1), b"kept").unwrap();
        r.fm.add(Rule::always(MediaOp::Put, Fault::Unavailable));
        assert_eq!(r.rm.put(at(1, 1), b"x").unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.rm.health().state, BackendState::Degraded);
        assert_eq!(r.rm.put(at(1, 1), b"x").unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.rm.health().state, BackendState::Unavailable);
        // degraded mode: writes refused at once (no backend call), reads still served
        let puts = r.fm.calls[0].load(Ordering::Relaxed);
        for _ in 0..5 {
            assert_eq!(r.rm.put(at(2, 1), b"y").unwrap_err().code, ErrorCode::ReadOnly);
            assert_eq!(r.rm.remove(at(0, 1)).unwrap_err().code, ErrorCode::ReadOnly);
        }
        assert_eq!(r.fm.calls[0].load(Ordering::Relaxed), puts, "no put reached the backend");
        assert_eq!(r.rm.health().refused_read_only, 10);
        assert_eq!(r.rm.health().to_json().as_obj().unwrap()["writes"], Value::Str("read_only".into()));
        // a probe after the interval: still down -> stays read-only for another interval
        std::thread::sleep(Duration::from_millis(50));
        assert_eq!(r.rm.put(at(2, 1), b"y").unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.rm.put(at(2, 1), b"y").unwrap_err().code, ErrorCode::ReadOnly);
        // the backend comes back: the next probe restores writes
        r.fm.clear();
        std::thread::sleep(Duration::from_millis(50));
        r.rm.put(at(2, 1), b"y").unwrap();
        assert_eq!(r.rm.health().state, BackendState::Healthy);
        r.rm.put(at(3, 1), b"z").unwrap();
        let states: Vec<String> = r.audit.events().into_iter().filter(|(n, _)| n == "backend_state")
            .map(|(_, d)| d.as_obj().unwrap()["to"].as_str().unwrap().to_string()).collect();
        assert_eq!(states, ["degraded", "unavailable", "healthy"]);
    }

    #[test]
    fn steady_reads_never_postpone_the_write_probe() {
        // regression (P8): a block device reads all the time; a successful read while the backend is UNAVAILABLE
        // used to restart the probe interval, so writes were never probed again and the device stayed read-only
        let r = rig(HealthConfig { degrade_after: 1, unavailable_after: 1, probe_interval: Duration::from_millis(200), score_alpha: 0.5 });
        r.rm.put(at(0, 1), b"kept").unwrap();
        r.fm.add(Rule::always(MediaOp::Put, Fault::Unavailable));
        assert_eq!(r.rm.put(at(1, 1), b"x").unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.rm.health().state, BackendState::Unavailable);
        r.fm.clear();
        let t0 = std::time::Instant::now();
        while t0.elapsed() < Duration::from_millis(260) {
            assert_eq!(r.rm.get(at(0, 1)).unwrap(), b"kept"); // reads are served throughout
            std::thread::sleep(Duration::from_millis(5));
        }
        r.rm.put(at(1, 1), b"x").expect("the write probe is due one interval after the last FAILURE, whatever the reads did");
        assert_eq!(r.rm.health().state, BackendState::Healthy);
        // a FAILED read while unavailable still postpones the probe (the backend is still misbehaving)
        r.fm.add(Rule::always(MediaOp::Put, Fault::Unavailable));
        assert_eq!(r.rm.put(at(2, 1), b"y").unwrap_err().code, ErrorCode::BackendUnavailable);
        r.fm.clear();
        std::thread::sleep(Duration::from_millis(120));
        r.fm.add(Rule::once(MediaOp::Get, Fault::Unavailable));
        assert_eq!(r.rm.get(at(0, 1)).unwrap_err().code, ErrorCode::BackendUnavailable);
        std::thread::sleep(Duration::from_millis(100)); // 220 ms after the put failure, 100 ms after the read failure
        assert_eq!(r.rm.put(at(2, 1), b"y").unwrap_err().code, ErrorCode::ReadOnly);
    }

    #[test]
    fn a_recovered_backend_leaves_read_only_mode_whatever_kind_failed() {
        for kind in [MediaOp::Sync, MediaOp::Remove, MediaOp::Put] {
            let r = rig(HealthConfig { degrade_after: 1, unavailable_after: 3, probe_interval: Duration::from_millis(20), score_alpha: 0.5 });
            r.fm.add(Rule::always(kind, Fault::Unavailable));
            for i in 0..3 {
                let _ = match kind {
                    MediaOp::Sync => r.rm.sync(),
                    MediaOp::Remove => r.rm.remove(at(i, 1)),
                    _ => r.rm.put(at(i, 1), b"x").map(|_| ()),
                };
            }
            assert_eq!(r.rm.health().state, BackendState::Unavailable, "{kind:?}");
            r.fm.clear();
            std::thread::sleep(Duration::from_millis(30));
            // the host's usual pattern: write, then flush
            r.rm.put(at(10, 1), b"y").unwrap();
            r.rm.sync().unwrap();
            assert_eq!(r.rm.health().state, BackendState::Healthy, "{kind:?}");
        }
    }

    #[test]
    fn a_panicking_probe_does_not_keep_the_device_read_only() {
        struct Boom(FaultMedia, std::sync::atomic::AtomicBool);
        impl MediaAdapter for Boom {
            fn info(&self) -> MediaInfo {
                self.0.info()
            }
            fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
                if self.1.swap(false, Ordering::SeqCst) {
                    panic!("adapter bug");
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
                self.0.sync()
            }
        }
        let b = Arc::new(Boom(FaultMedia::new(), std::sync::atomic::AtomicBool::new(false)));
        let h = HealthConfig { degrade_after: 1, unavailable_after: 1, probe_interval: Duration::from_millis(10), score_alpha: 0.5 };
        let rm = Arc::new(ResilientMedia::new(b.clone(), RetryPolicy::default(), h).unwrap().with_sleeper(Arc::new(|_| {})));
        b.0.add(Rule::once(MediaOp::Put, Fault::Unavailable));
        b.0.add(Rule::once(MediaOp::Put, Fault::Unavailable));
        b.0.add(Rule::once(MediaOp::Put, Fault::Unavailable));
        b.0.add(Rule::once(MediaOp::Put, Fault::Unavailable));
        assert!(rm.put(at(0, 1), b"x").is_err());
        assert_eq!(rm.health().state, BackendState::Unavailable);
        std::thread::sleep(Duration::from_millis(20));
        b.1.store(true, Ordering::SeqCst);
        let rm2 = rm.clone();
        assert!(std::thread::spawn(move || rm2.put(at(1, 1), b"x")).join().is_err(), "the probe panicked");
        std::thread::sleep(Duration::from_millis(20));
        rm.put(at(2, 1), b"x").unwrap();
        assert_eq!(rm.health().state, BackendState::Healthy);
    }

    #[test]
    fn a_retried_put_finding_other_bytes_is_corruption() {
        let r = rig(HealthConfig::default());
        r.fm.add(Rule::once(MediaOp::Put, Fault::CorruptAckLost));
        assert_eq!(r.rm.put(at(4, 1), b"the real bytes").unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn a_volume_over_an_unavailable_backend_refuses_writes_and_keeps_serving_reads() {
        let d = TempDir::new("fallback-vol");
        let r = rig(HealthConfig { degrade_after: 1, unavailable_after: 1, probe_interval: Duration::from_secs(3600), score_alpha: 0.5 });
        let g = Geometry::new(512, 4, 16).unwrap();
        let v = Volume::create(&d.0, g, [3; 16], 0, r.rm.clone(), VolumeConfig::default()).unwrap();
        v.write_extent(ExtentId(0), &[7u8; 2048], 1000).unwrap();
        r.fm.add(Rule::always(MediaOp::Put, Fault::Unavailable));
        assert_eq!(v.write_extent(ExtentId(1), &[8u8; 2048], 1000).unwrap_err().code, ErrorCode::BackendUnavailable);
        let stored = v.stored_bytes().unwrap();
        for _ in 0..3 {
            assert_eq!(v.write_extent(ExtentId(1), &[8u8; 2048], 1000).unwrap_err().code, ErrorCode::ReadOnly);
            assert_eq!(v.read_extent(ExtentId(0)).unwrap(), vec![7u8; 2048]);
        }
        assert_eq!(v.stored_bytes().unwrap(), stored, "refused writes leave no trace");
        assert_eq!(v.mapping(ExtentId(1)).unwrap(), None);
        assert_eq!(r.rm.health().state, BackendState::Unavailable, "successful reads do not end the read-only mode");
    }
}
