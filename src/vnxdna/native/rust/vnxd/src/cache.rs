//! VNX Cache: a verified extent cache in front of the volume (master directive §8; docs/V14_RAM_AND_CACHE.md §3).
//!
//! * **read-through**: a miss reads the extent from the volume (which verifies it) and keeps a copy; a hit checks
//!   the copy's CRC-32 before returning it. A clean copy that fails the check is dropped and re-read; a dirty copy
//!   that fails it was the only copy of acknowledged-but-unflushed data: it is never written back, the extent is
//!   marked LOST, and reads of it are CORRUPTION (never other or older bytes) until a whole-extent write replaces it;
//!   the next flush reports the loss;
//! * **write-through** (default): the write goes to the volume first; the cache is updated only when the volume
//!   accepted it, and a failed write invalidates the extent's copy. The returned write state is the volume's;
//! * **write-back** (opt-in): the write is kept in the cache as dirty and acknowledged as STAGED — volatile, never
//!   durable. `flush` writes every dirty extent back (in extent order) and then flushes the volume; a writeback that
//!   fails leaves its extent and every later dirty extent dirty, so a retry is possible and nothing is lost while
//!   the process lives. Dirty data is lost if the process stops before a flush: write-back is for hosts that issue
//!   flushes (filesystems do) and is documented as such;
//! * **memory**: every cached extent is a [`Buffer`] leased from the `Cache` quota of VNX-RAM. Making room evicts the
//!   least recently used extents; a dirty victim is written back first (skipped if another request holds it). When
//!   no room can be made, the request bypasses the cache (write-back degrades to write-through for that write). An
//!   existing copy is overwritten in place, so a dirty copy is never dropped before its replacement is in the cache;
//!   every writeback checks the copy's CRC-32 first;
//! * **consistency**: every operation on an extent holds that extent's cache lock across the volume call and the
//!   cache update, so a read can never observe or install data older than a completed write.

use crate::addr::{ExtentId, Geometry};
use crate::digest::crc32;
use crate::error::{err, ErrorCode, Result};
use crate::io::ExtentStore;
use crate::ram::{Buffer, MemoryManager, Subsystem, Wait};
use crate::state::WriteState;
use crate::volume::Volume;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, TryLockError};

const LOCK_STRIPES: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CacheMode {
    WriteThrough,
    WriteBack,
}

struct Slot {
    buf: Buffer,
    crc: u32,
    dirty: bool,
    tick: u64,
}

#[derive(Default)]
struct Inner {
    slots: HashMap<ExtentId, Slot>,
    lru: BTreeMap<u64, ExtentId>,
    tick: u64,
    /// extents whose acknowledged dirty copy failed its checksum: CORRUPTION until a whole-extent write
    lost: BTreeSet<ExtentId>,
    /// lost extents not yet reported by a flush
    unreported: BTreeSet<ExtentId>,
}

impl Inner {
    fn touch(&mut self, e: ExtentId) {
        self.tick += 1;
        let t = self.tick;
        if let Some(s) = self.slots.get_mut(&e) {
            self.lru.remove(&s.tick);
            s.tick = t;
            self.lru.insert(t, e);
        }
    }

    fn remove(&mut self, e: ExtentId) -> Option<Slot> {
        let s = self.slots.remove(&e)?;
        self.lru.remove(&s.tick);
        Some(s)
    }
}

#[derive(Debug, Default)]
pub struct CacheStats {
    pub hits: AtomicU64,
    pub misses: AtomicU64,
    pub evictions: AtomicU64,
    pub writebacks: AtomicU64,
    pub writeback_failures: AtomicU64,
    pub checksum_failures: AtomicU64,
    pub bypasses: AtomicU64,
    pub lost: AtomicU64,
}

pub struct ExtentCache {
    vol: Arc<Volume>,
    ram: Arc<MemoryManager>,
    mode: CacheMode,
    inner: Mutex<Inner>,
    stripes: Vec<Mutex<()>>,
    pub stats: CacheStats,
}

impl ExtentCache {
    pub fn new(vol: Arc<Volume>, ram: Arc<MemoryManager>, mode: CacheMode) -> ExtentCache {
        ExtentCache {
            vol,
            ram,
            mode,
            inner: Mutex::new(Inner::default()),
            stripes: (0..LOCK_STRIPES).map(|_| Mutex::new(())).collect(),
            stats: CacheStats::default(),
        }
    }

    pub fn volume(&self) -> &Arc<Volume> {
        &self.vol
    }

    pub fn mode(&self) -> CacheMode {
        self.mode
    }

    fn inner(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn stripe_of(e: ExtentId) -> usize {
        (e.0 % LOCK_STRIPES as u64) as usize
    }

    fn lock(&self, e: ExtentId) -> Result<MutexGuard<'_, ()>> {
        self.stripes[Self::stripe_of(e)].lock().or_else(|_| err(ErrorCode::Internal, "cache lock poisoned"))
    }

    pub fn cached_extents(&self) -> usize {
        self.inner().slots.len()
    }

    pub fn dirty_extents(&self) -> Vec<ExtentId> {
        let mut v: Vec<ExtentId> = self.inner().slots.iter().filter(|(_, s)| s.dirty).map(|(e, _)| *e).collect();
        v.sort();
        v
    }

    /// A leased buffer holding `data`, evicting least recently used extents to make room; None when no room can be
    /// made (the caller then bypasses the cache). `holder` is the extent whose stripe the caller holds.
    fn make_room(&self, data: &[u8], holder: ExtentId) -> Option<Buffer> {
        loop {
            match self.ram.buffer(Subsystem::Cache, data.len(), Wait::No) {
                Ok(mut b) => {
                    b.as_mut_slice().copy_from_slice(data);
                    return Some(b);
                }
                Err(e) if e.code == ErrorCode::ResourceExhausted => {
                    if !self.evict_one(holder) {
                        return None;
                    }
                }
                Err(_) => return None, // larger than the whole cache quota
            }
        }
    }

    /// Evict the least recently used extent that can be evicted now; false if none can.
    fn evict_one(&self, holder: ExtentId) -> bool {
        let candidates: Vec<ExtentId> = self.inner().lru.values().copied().collect();
        let holding = Self::stripe_of(holder);
        for victim in candidates {
            if victim == holder {
                continue;
            }
            let si = Self::stripe_of(victim);
            // the caller's own stripe is already held; any other is only try-locked, so no thread ever waits for one
            // extent while holding another (no lock-order cycles)
            let _g = if si == holding {
                None
            } else {
                match self.stripes[si].try_lock() {
                    Ok(g) => Some(g),
                    Err(TryLockError::WouldBlock) | Err(TryLockError::Poisoned(_)) => continue,
                }
            };
            let dirty_copy = {
                let inner = self.inner();
                match inner.slots.get(&victim) {
                    None => continue,
                    // a dirty copy failing its checksum is never written back; the next flush handles it
                    Some(s) if s.dirty && crc32(s.buf.as_slice()) != s.crc => continue,
                    Some(s) if s.dirty => Some(s.buf.as_slice().to_vec()),
                    Some(_) => None,
                }
            };
            if let Some(d) = dirty_copy {
                if self.vol.write_extent(victim, &d).is_err() {
                    self.stats.writeback_failures.fetch_add(1, Ordering::Relaxed);
                    continue; // keep it dirty; try another victim
                }
                self.stats.writebacks.fetch_add(1, Ordering::Relaxed);
            }
            self.inner().remove(victim);
            self.stats.evictions.fetch_add(1, Ordering::Relaxed);
            return true;
        }
        false
    }

    /// Put `data` in the cache as the copy of `e`. An existing copy is overwritten in place (same length, no
    /// allocation), so a dirty copy is never dropped before its replacement is installed. False = not cached.
    fn install(&self, e: ExtentId, data: &[u8], dirty: bool) -> bool {
        {
            let mut inner = self.inner();
            if let Some(s) = inner.slots.get_mut(&e) {
                s.buf.as_mut_slice().copy_from_slice(data);
                s.crc = crc32(data);
                s.dirty = dirty;
                inner.touch(e);
                return true;
            }
        }
        match self.make_room(data, e) {
            Some(buf) => {
                let mut inner = self.inner();
                inner.slots.insert(e, Slot { buf, crc: crc32(data), dirty, tick: 0 });
                inner.touch(e);
                true
            }
            None => {
                self.stats.bypasses.fetch_add(1, Ordering::Relaxed);
                false
            }
        }
    }

    /// Mark `e` lost: its acknowledged dirty copy failed its checksum and is dropped (never written back).
    fn lose(&self, inner: &mut Inner, e: ExtentId) {
        inner.remove(e);
        inner.lost.insert(e);
        inner.unreported.insert(e);
        self.stats.lost.fetch_add(1, Ordering::Relaxed);
    }

    fn lost_error<T>(e: ExtentId) -> Result<T> {
        err(ErrorCode::Corruption, format!("extent {}: its acknowledged write-back copy failed its checksum and was lost", e.0))
    }

    fn read_locked(&self, e: ExtentId) -> Result<Vec<u8>> {
        {
            let mut inner = self.inner();
            if inner.lost.contains(&e) {
                return Self::lost_error(e);
            }
            let hit = inner.slots.get(&e).map(|s| (s.buf.as_slice().to_vec(), s.crc, s.dirty));
            if let Some((data, crc, dirty)) = hit {
                if crc32(&data) == crc {
                    inner.touch(e);
                    self.stats.hits.fetch_add(1, Ordering::Relaxed);
                    return Ok(data);
                }
                self.stats.checksum_failures.fetch_add(1, Ordering::Relaxed);
                if dirty {
                    self.lose(&mut inner, e);
                    return Self::lost_error(e);
                }
                inner.remove(e);
            }
        }
        self.stats.misses.fetch_add(1, Ordering::Relaxed);
        let data = self.vol.read_extent(e)?;
        self.install(e, &data, false);
        Ok(data)
    }

    fn write_locked(&self, e: ExtentId, data: &[u8]) -> Result<WriteState> {
        let len = self.vol.geometry().extent_len(e)?;
        if data.len() as u64 != len {
            return err(ErrorCode::InvalidArgument, format!("extent {} holds {len} bytes, got {}", e.0, data.len()));
        }
        if self.mode == CacheMode::WriteBack && self.install(e, data, true) {
            self.inner().lost.remove(&e); // a whole-extent write replaces lost content
            return Ok(WriteState::Staged);
        }
        // write-through, or write-back without room (then no copy of `e` exists: install overwrites in place)
        match self.vol.write_extent(e, data) {
            Ok(o) => {
                self.inner().lost.remove(&e);
                self.install(e, data, false);
                Ok(o.state)
            }
            Err(x) => {
                // the volume keeps its previous content: drop a clean copy so the next read refetches it; a dirty
                // copy (acknowledged, newer than the volume) is kept
                let mut inner = self.inner();
                if inner.slots.get(&e).map_or(false, |s| !s.dirty) {
                    inner.remove(e);
                }
                Err(x)
            }
        }
    }

    /// Write every dirty extent back (in extent order), then flush the volume. Stops at the first backend failure.
    /// A dirty copy failing its checksum is never written: its extent becomes lost and the flush, after finishing
    /// the other extents, reports CORRUPTION once for every extent lost since the previous flush.
    pub fn flush(&self) -> Result<()> {
        for e in self.dirty_extents() {
            let _g = self.lock(e)?;
            let copy = {
                let mut inner = self.inner();
                match inner.slots.get(&e) {
                    Some(s) if s.dirty && crc32(s.buf.as_slice()) != s.crc => {
                        self.stats.checksum_failures.fetch_add(1, Ordering::Relaxed);
                        self.lose(&mut inner, e);
                        continue;
                    }
                    Some(s) if s.dirty => s.buf.as_slice().to_vec(),
                    _ => continue,
                }
            };
            if let Err(x) = self.vol.write_extent(e, &copy) {
                self.stats.writeback_failures.fetch_add(1, Ordering::Relaxed);
                return Err(x);
            }
            self.stats.writebacks.fetch_add(1, Ordering::Relaxed);
            if let Some(s) = self.inner().slots.get_mut(&e) {
                s.dirty = false;
            }
        }
        self.vol.flush()?;
        let lost: Vec<u64> = std::mem::take(&mut self.inner().unreported).into_iter().map(|e| e.0).collect();
        if !lost.is_empty() {
            return err(ErrorCode::Corruption, format!("write-back copies of extents {lost:?} failed their checksum and were lost"));
        }
        Ok(())
    }

    /// Drop the copy of one extent (a dirty copy is written back first).
    pub fn invalidate(&self, e: ExtentId) -> Result<()> {
        let _g = self.lock(e)?;
        let dirty = {
            let mut inner = self.inner();
            match inner.slots.get(&e) {
                Some(s) if s.dirty && crc32(s.buf.as_slice()) != s.crc => {
                    self.lose(&mut inner, e);
                    return Self::lost_error(e);
                }
                Some(s) if s.dirty => Some(s.buf.as_slice().to_vec()),
                _ => None,
            }
        };
        if let Some(d) = dirty {
            self.vol.write_extent(e, &d)?;
        }
        self.inner().remove(e);
        Ok(())
    }

    #[cfg(test)]
    fn corrupt_copy(&self, e: ExtentId) {
        if let Some(s) = self.inner().slots.get_mut(&e) {
            s.buf.as_mut_slice()[0] ^= 1;
        }
    }
}

impl Drop for ExtentCache {
    fn drop(&mut self) {
        let dirty = self.dirty_extents();
        if !dirty.is_empty() {
            // never silent: acknowledged write-back data that was not flushed is lost with the process
            eprintln!("vnxd cache: dropped with {} unflushed write-back extents {:?}", dirty.len(), dirty);
        }
    }
}

impl ExtentStore for ExtentCache {
    fn geometry(&self) -> Geometry {
        self.vol.geometry()
    }

    fn read_extent(&self, extent: ExtentId) -> Result<Vec<u8>> {
        let _g = self.lock(extent)?;
        self.read_locked(extent)
    }

    fn write_extent(&self, extent: ExtentId, data: &[u8]) -> Result<WriteState> {
        let _g = self.lock(extent)?;
        self.write_locked(extent, data)
    }

    fn modify_extent(&self, extent: ExtentId, patch: &mut dyn FnMut(&mut [u8])) -> Result<WriteState> {
        let _g = self.lock(extent)?;
        let mut data = self.read_locked(extent)?;
        patch(&mut data);
        self.write_locked(extent, &data)
    }

    fn flush(&self) -> Result<()> {
        ExtentCache::flush(self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::GenerationRef;
    use crate::io::BlockIo;
    use crate::journal::tests::TempDir;
    use crate::media::{CommitReceipt, MediaAdapter, MediaInfo, MemoryMedia};
    use crate::ram::RamConfig;
    use crate::volume::VolumeConfig;
    use std::sync::atomic::AtomicBool;

    /// Media whose puts fail while `down` is set (a backend outage).
    struct Flaky {
        inner: MemoryMedia,
        down: AtomicBool,
    }
    impl MediaAdapter for Flaky {
        fn info(&self) -> MediaInfo {
            self.inner.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
            if self.down.load(Ordering::SeqCst) {
                return err(ErrorCode::BackendUnavailable, "backend down");
            }
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

    struct Rig {
        _d: TempDir,
        media: Arc<Flaky>,
        cache: Arc<ExtentCache>,
        ram: Arc<MemoryManager>,
    }

    /// 6 extents of 2 KiB (the last 1.5 KiB); a cache quota of `cache_kib` KiB.
    fn rig(mode: CacheMode, cache_kib: u64) -> Rig {
        let d = TempDir::new("cache");
        let media = Arc::new(Flaky { inner: MemoryMedia::new(), down: AtomicBool::new(false) });
        let g = Geometry::new(512, 4, 23).unwrap();
        let vol = Arc::new(Volume::create(&d.0, g, [4; 16], 0, media.clone(), VolumeConfig::default()).unwrap());
        let mut cfg = RamConfig::split(1 << 20);
        cfg.quotas.insert(Subsystem::Cache, cache_kib << 10);
        let ram = MemoryManager::new(cfg).unwrap();
        let cache = Arc::new(ExtentCache::new(vol, ram.clone(), mode));
        Rig { _d: d, media, cache, ram }
    }

    fn ext(n: u64, v: u8) -> Vec<u8> {
        vec![v; if n == 5 { 1536 } else { 2048 }]
    }

    #[test]
    fn read_through_hits_and_lru_eviction_within_the_quota() {
        let r = rig(CacheMode::WriteThrough, 8); // room for two extents (each leases a 4 KiB allocation)
        for e in 0..3 {
            r.cache.write_extent(ExtentId(e), &ext(e, e as u8 + 1)).unwrap();
        }
        assert_eq!(r.cache.cached_extents(), 2, "the quota holds two extents");
        assert!(r.ram.usage(Subsystem::Cache).peak <= 8 << 10);
        let h0 = r.cache.stats.hits.load(Ordering::Relaxed);
        assert_eq!(r.cache.read_extent(ExtentId(2)).unwrap(), ext(2, 3));
        assert_eq!(r.cache.stats.hits.load(Ordering::Relaxed), h0 + 1);
        assert_eq!(r.cache.read_extent(ExtentId(0)).unwrap(), ext(0, 1), "evicted extent is read through");
        assert!(r.cache.stats.evictions.load(Ordering::Relaxed) >= 2);
        drop(r.cache);
        assert!(r.ram.outstanding().is_empty(), "every cache lease returned");
    }

    #[test]
    fn write_through_is_durable_and_a_failed_write_never_leaves_a_stale_copy() {
        let r = rig(CacheMode::WriteThrough, 64);
        assert_eq!(r.cache.write_extent(ExtentId(1), &ext(1, 7)).unwrap(), WriteState::Verified);
        r.media.down.store(true, Ordering::SeqCst);
        assert_eq!(r.cache.write_extent(ExtentId(1), &ext(1, 8)).unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.cache.cached_extents(), 0, "the copy was invalidated");
        assert_eq!(r.cache.read_extent(ExtentId(1)).unwrap(), ext(1, 7), "the volume's content, not the failed write");
    }

    #[test]
    fn checksum_failure_refetches_clean_and_refuses_dirty() {
        let r = rig(CacheMode::WriteBack, 64);
        r.cache.volume().write_extent(ExtentId(0), &ext(0, 1)).unwrap();
        assert_eq!(r.cache.read_extent(ExtentId(0)).unwrap(), ext(0, 1));
        r.cache.corrupt_copy(ExtentId(0));
        assert_eq!(r.cache.read_extent(ExtentId(0)).unwrap(), ext(0, 1), "a clean copy is re-read from the volume");
        r.cache.write_extent(ExtentId(2), &ext(2, 9)).unwrap();
        r.cache.corrupt_copy(ExtentId(2));
        assert_eq!(r.cache.read_extent(ExtentId(2)).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(r.cache.stats.checksum_failures.load(Ordering::Relaxed), 2);
    }

    #[test]
    fn write_back_acks_staged_and_flush_makes_it_durable() {
        let r = rig(CacheMode::WriteBack, 64);
        assert_eq!(r.cache.write_extent(ExtentId(3), &ext(3, 5)).unwrap(), WriteState::Staged);
        assert_eq!(r.cache.volume().read_extent(ExtentId(3)).unwrap(), ext(3, 0), "not on the volume yet");
        assert_eq!(r.cache.read_extent(ExtentId(3)).unwrap(), ext(3, 5), "but read back from the cache");
        assert_eq!(r.cache.dirty_extents(), vec![ExtentId(3)]);
        r.cache.flush().unwrap();
        assert!(r.cache.dirty_extents().is_empty());
        assert_eq!(r.cache.volume().read_extent(ExtentId(3)).unwrap(), ext(3, 5));
    }

    #[test]
    fn interrupted_writeback_keeps_dirty_data_and_a_retry_completes_it() {
        let r = rig(CacheMode::WriteBack, 64);
        for e in 0..4 {
            r.cache.write_extent(ExtentId(e), &ext(e, 10 + e as u8)).unwrap();
        }
        r.media.down.store(true, Ordering::SeqCst);
        assert_eq!(r.cache.flush().unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.cache.dirty_extents().len(), 4, "nothing was marked clean");
        for e in 0..4 {
            assert_eq!(r.cache.read_extent(ExtentId(e)).unwrap(), ext(e, 10 + e as u8));
        }
        r.media.down.store(false, Ordering::SeqCst);
        r.cache.flush().unwrap();
        for e in 0..4 {
            assert_eq!(r.cache.volume().read_extent(ExtentId(e)).unwrap(), ext(e, 10 + e as u8));
        }
    }

    #[test]
    fn dirty_eviction_writes_back_first_and_full_cache_degrades_to_write_through() {
        let r = rig(CacheMode::WriteBack, 8); // two extents
        r.cache.write_extent(ExtentId(0), &ext(0, 1)).unwrap();
        r.cache.write_extent(ExtentId(1), &ext(1, 2)).unwrap();
        r.cache.write_extent(ExtentId(2), &ext(2, 3)).unwrap(); // evicts extent 0 after writing it back
        assert_eq!(r.cache.volume().read_extent(ExtentId(0)).unwrap(), ext(0, 1));
        assert!(r.cache.stats.writebacks.load(Ordering::Relaxed) >= 1);
        // backend down: dirty victims cannot be written back, so the new write cannot be cached -> write-through,
        // which fails visibly instead of silently dropping data
        r.media.down.store(true, Ordering::SeqCst);
        assert_eq!(r.cache.write_extent(ExtentId(4), &ext(4, 4)).unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(r.cache.dirty_extents(), vec![ExtentId(1), ExtentId(2)], "dirty data kept");
        r.media.down.store(false, Ordering::SeqCst);
        r.cache.flush().unwrap();
        assert_eq!(r.cache.volume().read_extent(ExtentId(2)).unwrap(), ext(2, 3));
    }

    /// A cache whose RAM total binds (two 4 KiB allocations), over a volume of `extents` 2 KiB extents.
    fn tight(mode: CacheMode) -> Rig {
        let d = TempDir::new("cache");
        let media = Arc::new(Flaky { inner: MemoryMedia::new(), down: AtomicBool::new(false) });
        let g = Geometry::new(512, 4, 23).unwrap();
        let vol = Arc::new(Volume::create(&d.0, g, [4; 16], 0, media.clone(), VolumeConfig::default()).unwrap());
        let ram = MemoryManager::new(RamConfig { total: 8192, quotas: Subsystem::ALL.iter().map(|s| (*s, 8192)).collect() }).unwrap();
        let cache = Arc::new(ExtentCache::new(vol, ram.clone(), mode));
        Rig { _d: d, media, cache, ram }
    }

    #[test]
    fn acknowledged_write_back_data_survives_an_outage_under_memory_pressure() {
        let r = tight(CacheMode::WriteBack);
        r.cache.write_extent(ExtentId(0), &ext(0, 1)).unwrap();
        r.cache.write_extent(ExtentId(1), &ext(1, 2)).unwrap();
        r.media.down.store(true, Ordering::SeqCst);
        // another subsystem cannot take the copies' memory: rewriting e0 updates its copy in place
        assert_eq!(r.cache.write_extent(ExtentId(0), &ext(0, 3)).unwrap(), WriteState::Staged);
        assert_eq!(r.ram.reserve(Subsystem::Io, 1, Wait::No).err().unwrap().code, ErrorCode::ResourceExhausted,
            "the total is fully used by the two copies");
        // a new extent finds no room (both victims dirty, backend down): it fails visibly and drops nothing
        assert_eq!(r.cache.write_extent(ExtentId(2), &ext(2, 4)).unwrap_err().code, ErrorCode::BackendUnavailable);
        let mut patch = |b: &mut [u8]| b[0] = 9;
        assert_eq!(r.cache.modify_extent(ExtentId(1), &mut patch).unwrap(), WriteState::Staged);
        assert_eq!(r.cache.read_extent(ExtentId(0)).unwrap(), ext(0, 3));
        assert_eq!(r.cache.read_extent(ExtentId(1)).unwrap()[..2], [9, 2]);
        assert_eq!(r.cache.dirty_extents(), vec![ExtentId(0), ExtentId(1)]);
        r.media.down.store(false, Ordering::SeqCst);
        r.cache.flush().unwrap();
        assert_eq!(r.cache.volume().read_extent(ExtentId(0)).unwrap(), ext(0, 3));
        assert_eq!(r.cache.volume().read_extent(ExtentId(1)).unwrap()[..2], [9, 2]);
    }

    #[test]
    fn a_corrupt_dirty_copy_is_never_written_back() {
        // flush
        let r = rig(CacheMode::WriteBack, 64);
        r.cache.write_extent(ExtentId(2), &ext(2, 9)).unwrap();
        r.cache.write_extent(ExtentId(3), &ext(3, 7)).unwrap();
        r.cache.corrupt_copy(ExtentId(2));
        assert_eq!(r.cache.flush().unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(r.cache.volume().read_extent(ExtentId(2)).unwrap(), ext(2, 0), "the corrupt bytes never reached the volume");
        assert_eq!(r.cache.volume().read_extent(ExtentId(3)).unwrap(), ext(3, 7), "the other extents were flushed");
        assert_eq!(r.cache.read_extent(ExtentId(2)).unwrap_err().code, ErrorCode::Corruption, "lost, not older data");
        r.cache.flush().unwrap(); // reported once
        assert_eq!(r.cache.read_extent(ExtentId(2)).unwrap_err().code, ErrorCode::Corruption);
        let mut patch = |b: &mut [u8]| b[0] = 1;
        assert_eq!(r.cache.modify_extent(ExtentId(2), &mut patch).unwrap_err().code, ErrorCode::Corruption);
        r.cache.write_extent(ExtentId(2), &ext(2, 5)).unwrap(); // a whole-extent write replaces it
        assert_eq!(r.cache.read_extent(ExtentId(2)).unwrap(), ext(2, 5));
        // eviction skips it, invalidate refuses it
        let r = tight(CacheMode::WriteBack);
        r.cache.write_extent(ExtentId(0), &ext(0, 1)).unwrap();
        r.cache.write_extent(ExtentId(1), &ext(1, 2)).unwrap();
        r.cache.corrupt_copy(ExtentId(0));
        r.cache.write_extent(ExtentId(2), &ext(2, 3)).unwrap(); // evicts e1 (written back), never e0
        assert_eq!(r.cache.volume().read_extent(ExtentId(0)).unwrap(), ext(0, 0));
        assert_eq!(r.cache.volume().read_extent(ExtentId(1)).unwrap(), ext(1, 2));
        assert_eq!(r.cache.invalidate(ExtentId(0)).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(r.cache.volume().read_extent(ExtentId(0)).unwrap(), ext(0, 0));
        assert_eq!(r.cache.stats.lost.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn extents_sharing_the_callers_lock_stripe_can_be_evicted() {
        let d = TempDir::new("cache");
        let g = Geometry::new(512, 1, 130).unwrap(); // extents 0, 64 and 128 share lock stripe 0
        let vol = Arc::new(Volume::create(&d.0, g, [4; 16], 0, Arc::new(MemoryMedia::new()), VolumeConfig::default()).unwrap());
        let mut cfg = RamConfig::split(1 << 20);
        cfg.quotas.insert(Subsystem::Cache, 8192);
        let cache = ExtentCache::new(vol, MemoryManager::new(cfg).unwrap(), CacheMode::WriteThrough);
        for e in [0u64, 64, 128] {
            cache.write_extent(ExtentId(e), &[e as u8; 512]).unwrap();
        }
        assert_eq!(cache.cached_extents(), 2);
        assert_eq!(cache.stats.bypasses.load(Ordering::Relaxed), 0);
        assert_eq!(cache.read_extent(ExtentId(0)).unwrap(), [0u8; 512]);
    }

    #[test]
    fn block_io_refuses_a_store_with_another_geometry() {
        let a = rig(CacheMode::WriteThrough, 64);
        let d = TempDir::new("cache");
        let other = Arc::new(Volume::create(&d.0, Geometry::new(512, 8, 23).unwrap(), [5; 16], 0, Arc::new(MemoryMedia::new()),
            VolumeConfig::default()).unwrap());
        assert_eq!(BlockIo::over(a.cache.clone(), other).err().unwrap().code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn concurrent_readers_never_see_older_data_than_a_completed_write() {
        for mode in [CacheMode::WriteThrough, CacheMode::WriteBack] {
            let r = rig(mode, 12); // three of six extents: constant eviction
            let done: Vec<AtomicU64> = (0..6).map(|_| AtomicU64::new(0)).collect();
            std::thread::scope(|sc| {
                for e in 0..6u64 {
                    let (c, done) = (r.cache.clone(), &done);
                    sc.spawn(move || {
                        for v in 1..=40u8 {
                            c.write_extent(ExtentId(e), &ext(e, v)).unwrap();
                            done[e as usize].store(v as u64, Ordering::SeqCst);
                        }
                    });
                }
                for t in 0..3u64 {
                    let (c, done) = (r.cache.clone(), &done);
                    sc.spawn(move || {
                        for i in 0..400u64 {
                            let e = (i * 7 + t) % 6;
                            let floor = done[e as usize].load(Ordering::SeqCst);
                            let got = c.read_extent(ExtentId(e)).unwrap();
                            assert!(got.iter().all(|&b| b == got[0]), "{mode:?}: torn extent");
                            assert!(u64::from(got[0]) >= floor, "{mode:?}: stale read of extent {e}");
                        }
                    });
                }
            });
            r.cache.flush().unwrap();
            for e in 0..6 {
                assert_eq!(r.cache.volume().read_extent(ExtentId(e)).unwrap(), ext(e, 40), "{mode:?}");
            }
            assert!(r.ram.usage(Subsystem::Cache).peak <= 12 << 10);
        }
    }

    #[test]
    fn block_io_through_the_cache_matches_a_byte_model() {
        for mode in [CacheMode::WriteThrough, CacheMode::WriteBack] {
            let r = rig(mode, 10);
            let io = BlockIo::over(r.cache.clone(), r.cache.volume().clone()).unwrap();
            let cap = r.cache.geometry().capacity_bytes();
            let mut model = vec![0u8; cap as usize];
            let mut x = 0x1234_5678u64;
            for step in 0..300u64 {
                x ^= x << 13;
                x ^= x >> 7;
                x ^= x << 17;
                let off = x % cap;
                let len = 1 + (x >> 20) % (cap - off).min(4000);
                if step % 3 == 0 {
                    assert_eq!(io.read(off, len).unwrap(), &model[off as usize..(off + len) as usize], "{mode:?} {step}");
                } else {
                    let data: Vec<u8> = (0..len).map(|i| (step + i) as u8).collect();
                    io.write(off, &data).unwrap();
                    model[off as usize..(off + len) as usize].copy_from_slice(&data);
                }
            }
            io.flush().unwrap();
            assert!(r.cache.dirty_extents().is_empty());
            assert_eq!(BlockIo::new(r.cache.volume().clone()).read(0, cap).unwrap(), model, "{mode:?}: volume after flush");
        }
    }
}
