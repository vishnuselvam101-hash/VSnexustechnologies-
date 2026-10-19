//! VNX-RAM: the electronic working-memory manager of the service (master directive §5.1;
//! docs/V14_RAM_AND_CACHE.md §1–§2).
//!
//! This is ordinary host RAM under an explicit budget. It is not molecular memory (that research interface is
//! `vnxdna.device.molecular`) and it never makes data durable.
//!
//! * a total budget and a quota per [`Subsystem`]; a reservation is granted only if it fits both;
//! * [`Lease`]: an RAII reservation, returned to the budget on drop (so cancellation and error paths cannot leak);
//! * backpressure: a reservation that does not fit waits (until it fits, its deadline passes — TIMEOUT — or it is
//!   cancelled — CANCELLED) or, with [`Wait::No`], fails at once with RESOURCE_EXHAUSTED; a reservation larger than
//!   its quota can never fit and is INVALID_ARGUMENT;
//! * [`Buffer`]: a byte buffer whose whole allocation (its power-of-two size class, at least 4 KiB) is leased; buffers return to a pool on drop with their whole allocation
//!   zeroed, and a reused buffer is handed out zero-filled, so no bytes cross from one request or security context
//!   to another (safe code only: the old contents are overwritten, never read);
//! * accounting: in use and peak per subsystem and in total, grants, waits, refusals; [`MemoryManager::outstanding`]
//!   reports leases still held (leak detection at shutdown).

use crate::error::{err, ErrorCode, Result};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Subsystem {
    /// request buffers of the host interface and VNX-IO
    Io,
    /// cached extents
    Cache,
    /// extents staged for a backend or a codec worker
    Staging,
    /// codec working buffers
    Codec,
    /// maps, indexes, journal buffers
    Metadata,
}

impl Subsystem {
    pub const ALL: [Subsystem; 5] = [Subsystem::Io, Subsystem::Cache, Subsystem::Staging, Subsystem::Codec, Subsystem::Metadata];

    pub fn as_str(self) -> &'static str {
        match self {
            Subsystem::Io => "io",
            Subsystem::Cache => "cache",
            Subsystem::Staging => "staging",
            Subsystem::Codec => "codec",
            Subsystem::Metadata => "metadata",
        }
    }
}

#[derive(Clone, Debug)]
pub struct RamConfig {
    pub total: u64,
    pub quotas: BTreeMap<Subsystem, u64>,
}

impl RamConfig {
    /// A budget split as io 25 %, cache 40 %, staging 20 %, codec 10 %, metadata 5 % (quotas may sum above the
    /// total: the total is enforced separately).
    pub fn split(total: u64) -> RamConfig {
        let q = |pct: u64| total / 100 * pct;
        let quotas = [(Subsystem::Io, q(25)), (Subsystem::Cache, q(40)), (Subsystem::Staging, q(20)), (Subsystem::Codec, q(10)),
            (Subsystem::Metadata, q(5))]
        .into_iter()
        .collect();
        RamConfig { total, quotas }
    }

    fn validate(&self) -> Result<()> {
        if self.total == 0 {
            return err(ErrorCode::InvalidArgument, "memory budget must be > 0");
        }
        for s in Subsystem::ALL {
            match self.quotas.get(&s) {
                Some(q) if *q > 0 && *q <= self.total => {}
                q => return err(ErrorCode::InvalidArgument, format!("quota of {} is {q:?}: must be in 1..=total", s.as_str())),
            }
        }
        Ok(())
    }
}

/// How a reservation that does not fit behaves.
#[derive(Clone, Debug, Default)]
pub enum Wait {
    /// fail at once with RESOURCE_EXHAUSTED
    No,
    /// wait until it fits
    #[default]
    Forever,
    /// wait until it fits or the deadline passes (TIMEOUT) or the token is set (CANCELLED)
    Until { deadline: Option<Instant>, cancel: Option<Arc<AtomicBool>> },
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Usage {
    pub in_use: u64,
    pub peak: u64,
    pub grants: u64,
    pub waits: u64,
    pub refused: u64,
    pub leases: u64,
}

struct Accounts {
    total_in_use: u64,
    total_peak: u64,
    per: BTreeMap<Subsystem, Usage>,
    pools: BTreeMap<usize, Vec<Vec<u8>>>,
    /// capacity held by the pools (not leased; capped at total / POOL_SHARE)
    pooled_bytes: u64,
}

pub struct MemoryManager {
    cfg: RamConfig,
    acc: Mutex<Accounts>,
    freed: Condvar,
}

/// Capacity of pooled buffers kept for reuse, as a fraction of the total budget (pooled bytes are not leased).
const POOL_SHARE: u64 = 8;

impl MemoryManager {
    pub fn new(cfg: RamConfig) -> Result<Arc<MemoryManager>> {
        cfg.validate()?;
        let per = Subsystem::ALL.iter().map(|s| (*s, Usage::default())).collect();
        Ok(Arc::new(MemoryManager {
            cfg,
            acc: Mutex::new(Accounts { total_in_use: 0, total_peak: 0, per, pools: BTreeMap::new(), pooled_bytes: 0 }),
            freed: Condvar::new(),
        }))
    }

    pub fn config(&self) -> &RamConfig {
        &self.cfg
    }

    fn lock(&self) -> MutexGuard<'_, Accounts> {
        // accounting must survive a panic elsewhere: a poisoned lock still holds consistent counters
        self.acc.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn fits(&self, a: &Accounts, s: Subsystem, bytes: u64) -> bool {
        a.total_in_use + bytes <= self.cfg.total && a.per[&s].in_use + bytes <= self.cfg.quotas[&s]
    }

    /// Reserve `bytes` for subsystem `s`.
    pub fn reserve(self: &Arc<Self>, s: Subsystem, bytes: u64, wait: Wait) -> Result<Lease> {
        let quota = self.cfg.quotas[&s];
        if bytes > quota {
            return err(ErrorCode::InvalidArgument, format!("{bytes} bytes can never fit the {} quota of {quota}", s.as_str()));
        }
        let mut a = self.lock();
        let mut waited = false;
        while !self.fits(&a, s, bytes) {
            let (deadline, cancel) = match &wait {
                Wait::No => {
                    a.per.get_mut(&s).expect("all subsystems").refused += 1;
                    return err(
                        ErrorCode::ResourceExhausted,
                        format!("{}: {bytes} bytes do not fit ({} of {} in use, total {} of {})", s.as_str(),
                            a.per[&s].in_use, quota, a.total_in_use, self.cfg.total),
                    );
                }
                Wait::Forever => (None, None),
                Wait::Until { deadline, cancel } => (*deadline, cancel.clone()),
            };
            if !waited {
                a.per.get_mut(&s).expect("all subsystems").waits += 1;
                waited = true;
            }
            if cancel.as_ref().map_or(false, |c| c.load(Ordering::SeqCst)) {
                a.per.get_mut(&s).expect("all subsystems").refused += 1;
                return err(ErrorCode::Cancelled, format!("{} reservation of {bytes} bytes cancelled", s.as_str()));
            }
            // waiters re-check at least every 10 ms so a cancel token is seen without a notification
            let mut slice = Duration::from_millis(10);
            if let Some(d) = deadline {
                let now = Instant::now();
                if now >= d {
                    a.per.get_mut(&s).expect("all subsystems").refused += 1;
                    return err(ErrorCode::Timeout, format!("{} reservation of {bytes} bytes timed out", s.as_str()));
                }
                slice = slice.min(d - now);
            }
            a = match &wait {
                Wait::Forever => self.freed.wait(a).unwrap_or_else(|p| p.into_inner()),
                _ => self.freed.wait_timeout(a, slice).unwrap_or_else(|p| p.into_inner()).0,
            };
        }
        a.total_in_use += bytes;
        a.total_peak = a.total_peak.max(a.total_in_use);
        let u = a.per.get_mut(&s).expect("all subsystems");
        u.in_use += bytes;
        u.peak = u.peak.max(u.in_use);
        u.grants += 1;
        u.leases += 1;
        Ok(Lease { mm: self.clone(), s, bytes })
    }

    fn release(&self, s: Subsystem, bytes: u64) {
        let mut a = self.lock();
        a.total_in_use -= bytes;
        let u = a.per.get_mut(&s).expect("all subsystems");
        u.in_use -= bytes;
        u.leases -= 1;
        drop(a);
        self.freed.notify_all();
    }

    /// The allocation size of a buffer of `len` bytes (what a buffer leases).
    pub fn size_class(len: usize) -> usize {
        len.next_power_of_two().max(4096)
    }

    /// A zeroed buffer of `len` bytes leased from subsystem `s` (reusing a pooled allocation when one fits). The lease
    /// covers the whole allocation, so accounted memory is the memory actually held.
    pub fn buffer(self: &Arc<Self>, s: Subsystem, len: usize, wait: Wait) -> Result<Buffer> {
        let class = Self::size_class(len);
        let lease = self.reserve(s, class as u64, wait)?;
        let pooled = {
            let mut a = self.lock();
            let v = a.pools.get_mut(&class).and_then(Vec::pop);
            if let Some(v) = &v {
                a.pooled_bytes -= v.capacity() as u64;
            }
            v
        };
        let data = match pooled {
            Some(mut v) => {
                v.resize(len, 0); // pooled vectors are stored empty and were zeroed when returned
                v
            }
            None => {
                let mut v = Vec::with_capacity(class);
                v.resize(len, 0);
                v
            }
        };
        Ok(Buffer { data, lease: Some(lease) })
    }

    fn recycle(&self, mut v: Vec<u8>) {
        let cap = v.capacity();
        if !cap.is_power_of_two() || cap < 4096 {
            return; // freed normally
        }
        // zero the whole allocation, not only the used length, before it can serve another request
        v.clear();
        v.resize(cap, 0);
        v.clear();
        let mut a = self.lock();
        // the cap is checked and updated under the accounts lock (concurrent drops cannot overshoot it)
        if a.pooled_bytes + cap as u64 <= self.cfg.total / POOL_SHARE {
            a.pooled_bytes += cap as u64;
            a.pools.entry(cap).or_default().push(v);
        }
    }

    /// Capacity held by the reuse pools (not leased; at most total / 8).
    pub fn pooled_bytes(&self) -> u64 {
        self.lock().pooled_bytes
    }

    pub fn usage(&self, s: Subsystem) -> Usage {
        self.lock().per[&s].clone()
    }

    pub fn total_in_use(&self) -> u64 {
        self.lock().total_in_use
    }

    pub fn total_peak(&self) -> u64 {
        self.lock().total_peak
    }

    /// Leases still held, per subsystem (all zero after an orderly shutdown; anything else is a leak).
    pub fn outstanding(&self) -> Vec<(Subsystem, u64, u64)> {
        self.lock().per.iter().filter(|(_, u)| u.leases > 0).map(|(s, u)| (*s, u.leases, u.in_use)).collect()
    }
}

/// A granted reservation; returned to the budget when dropped.
pub struct Lease {
    mm: Arc<MemoryManager>,
    s: Subsystem,
    bytes: u64,
}

impl Lease {
    pub fn bytes(&self) -> u64 {
        self.bytes
    }

    pub fn subsystem(&self) -> Subsystem {
        self.s
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.mm.release(self.s, self.bytes);
    }
}

/// A leased byte buffer. Dropping it zeroes and pools the allocation and returns the lease.
pub struct Buffer {
    data: Vec<u8>,
    lease: Option<Lease>,
}

impl Buffer {
    pub fn len(&self) -> usize {
        self.data.len()
    }

    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    pub fn as_slice(&self) -> &[u8] {
        &self.data
    }

    pub fn as_mut_slice(&mut self) -> &mut [u8] {
        &mut self.data
    }
}

impl Drop for Buffer {
    fn drop(&mut self) {
        if let Some(lease) = self.lease.take() {
            let mm = lease.mm.clone();
            mm.recycle(std::mem::take(&mut self.data));
            drop(lease);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mm(total: u64) -> Arc<MemoryManager> {
        MemoryManager::new(RamConfig::split(total)).unwrap()
    }

    #[test]
    fn config_validation() {
        assert!(MemoryManager::new(RamConfig { total: 0, quotas: BTreeMap::new() }).is_err());
        let mut c = RamConfig::split(1000);
        c.quotas.insert(Subsystem::Io, 2000);
        assert_eq!(MemoryManager::new(c).err().unwrap().code, ErrorCode::InvalidArgument);
        let mut c = RamConfig::split(1000);
        c.quotas.remove(&Subsystem::Codec);
        assert!(MemoryManager::new(c).is_err());
    }

    #[test]
    fn quotas_and_total_are_enforced() {
        let m = mm(1000); // io 250, cache 400, staging 200, codec 100, metadata 50
        let a = m.reserve(Subsystem::Io, 200, Wait::No).unwrap();
        assert_eq!(m.reserve(Subsystem::Io, 51, Wait::No).err().unwrap().code, ErrorCode::ResourceExhausted);
        assert_eq!(m.reserve(Subsystem::Io, 251, Wait::No).err().unwrap().code, ErrorCode::InvalidArgument);
        let b = m.reserve(Subsystem::Cache, 400, Wait::No).unwrap();
        let c = m.reserve(Subsystem::Staging, 200, Wait::No).unwrap();
        let d = m.reserve(Subsystem::Codec, 100, Wait::No).unwrap();
        // 900 of 1000 in use: metadata's 50 fits, but nothing beyond the total
        let e = m.reserve(Subsystem::Metadata, 50, Wait::No).unwrap();
        assert_eq!(m.total_in_use(), 950);
        drop((a, b, c, d, e));
        // quotas that over-commit the total: the total binds on its own
        let over = RamConfig { total: 300, quotas: Subsystem::ALL.iter().map(|s| (*s, 200)).collect() };
        let o = MemoryManager::new(over).unwrap();
        let x = o.reserve(Subsystem::Io, 200, Wait::No).unwrap();
        assert_eq!(o.reserve(Subsystem::Cache, 101, Wait::No).err().unwrap().code, ErrorCode::ResourceExhausted);
        let y = o.reserve(Subsystem::Cache, 100, Wait::No).unwrap();
        drop((x, y));
        assert_eq!(o.total_peak(), 300);
        assert_eq!(m.total_in_use(), 0);
        assert!(m.outstanding().is_empty());
        let u = m.usage(Subsystem::Io);
        assert_eq!((u.peak, u.grants, u.refused), (200, 1, 1)); // the oversized request is INVALID_ARGUMENT, not a refusal
    }

    #[test]
    fn backpressure_waits_then_proceeds() {
        let m = mm(1000);
        let held = m.reserve(Subsystem::Io, 250, Wait::No).unwrap();
        let m2 = m.clone();
        let t = std::thread::spawn(move || {
            let start = Instant::now();
            let l = m2.reserve(Subsystem::Io, 100, Wait::Forever).unwrap();
            (start.elapsed(), l.bytes())
        });
        std::thread::sleep(Duration::from_millis(60));
        drop(held);
        let (waited, bytes) = t.join().unwrap();
        assert!(waited >= Duration::from_millis(50) && bytes == 100);
        assert_eq!(m.usage(Subsystem::Io).waits, 1);
        assert_eq!(m.total_in_use(), 0);
    }

    #[test]
    fn deadline_and_cancellation() {
        let m = mm(1000);
        let _held = m.reserve(Subsystem::Codec, 100, Wait::No).unwrap();
        let w = Wait::Until { deadline: Some(Instant::now() + Duration::from_millis(30)), cancel: None };
        assert_eq!(m.reserve(Subsystem::Codec, 1, w).err().unwrap().code, ErrorCode::Timeout);
        let cancel = Arc::new(AtomicBool::new(false));
        let (m2, c2) = (m.clone(), cancel.clone());
        let t = std::thread::spawn(move || m2.reserve(Subsystem::Codec, 1, Wait::Until { deadline: None, cancel: Some(c2) }).err().unwrap().code);
        std::thread::sleep(Duration::from_millis(30));
        cancel.store(true, Ordering::SeqCst);
        assert_eq!(t.join().unwrap(), ErrorCode::Cancelled);
        assert_eq!(m.usage(Subsystem::Codec).in_use, 100, "a cancelled wait reserves nothing");
    }

    #[test]
    fn buffers_are_zeroed_before_reuse_and_return_their_lease() {
        let m = mm(1 << 20);
        let mut b = m.buffer(Subsystem::Staging, 5000, Wait::No).unwrap();
        b.as_mut_slice().fill(0xAB);
        let ptr = b.as_slice().as_ptr();
        assert_eq!(m.usage(Subsystem::Staging).in_use, 8192, "the whole allocation is leased");
        drop(b);
        assert_eq!(m.usage(Subsystem::Staging).in_use, 0);
        // the next buffer of the same size class reuses the allocation, and sees only zeros, even past its length
        let b = m.buffer(Subsystem::Staging, 6000, Wait::No).unwrap();
        assert_eq!(b.as_slice().as_ptr(), ptr, "pooled allocation reused");
        assert!(b.as_slice().iter().all(|&x| x == 0), "no byte of the previous user is visible");
    }

    #[test]
    fn concurrent_stress_never_exceeds_the_budget() {
        let m = mm(1 << 20); // every quota holds at least one 4 KiB allocation class
        std::thread::scope(|sc| {
            for t in 0..8u64 {
                let m = m.clone();
                sc.spawn(move || {
                    let s = Subsystem::ALL[(t % 5) as usize];
                    for i in 0..300u64 {
                        let n = 1 + (t * 977 + i * 131) % (m.config().quotas[&s] / 2);
                        let mut b = m.buffer(s, n as usize, Wait::Forever).unwrap();
                        b.as_mut_slice()[0] = t as u8;
                        assert!(m.total_in_use() <= 1 << 20);
                    }
                });
            }
        });
        assert!(m.total_peak() <= 1 << 20);
        assert!(m.pooled_bytes() <= (1 << 20) / POOL_SHARE);
        assert_eq!(m.total_in_use(), 0);
        assert!(m.outstanding().is_empty(), "no lease leaked");
        for s in Subsystem::ALL {
            let u = m.usage(s);
            assert!(u.peak <= m.config().quotas[&s], "{s:?}");
        }
    }
}
