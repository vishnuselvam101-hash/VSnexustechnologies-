//! VNX Volume (block): extent map, generations, transactions, journal, crash recovery and garbage collection
//! (master directive §12; docs/V14_VOLUME_AND_IO.md §2–§4).
//!
//! A volume is a directory holding `journal` (the superblock followed by map and release records) over a media
//! adapter holding the extents' generations. Every extent is written whole as a NEW generation; the map record is
//! the commit point of the remap:
//!
//! ```text
//! 1 allocate generation g (> every generation ever used for the extent), reserve backend space   [STAGED]
//! 2 media.put(extent, g, bytes); check the receipt's SHA-256                              [BACKEND_COMMITTED]
//! 3 optional readback: media.get == bytes
//! 4 journal: map_update(txid, extent, g, previous, length, sha256), fsynced          <- commit point
//!                                                     [VERIFIED if the readback ran, else BACKEND_COMMITTED]
//! 5 media.remove(previous); journal: generation_released(previous)                 (garbage collection)
//! ```
//!
//! A crash before step 4 leaves the old mapping and an orphan generation; a crash after step 4 leaves the new mapping
//! and possibly an orphan old generation. `open` replays the journal (cutting a torn tail), removes every generation
//! the map does not reference (and journals its release) and marks a mapped extent whose generation is missing
//! from the backend as damaged: reading it is CORRUPTION, never zeros or other bytes, until a whole-extent write
//! replaces it. An extent that was never written reads as zeros (it holds no data). Reads verify length and SHA-256
//! against the map before returning.
//!
//! The backend is bound to the volume by a marker object (extent [`MARKER_EXTENT`], generation 1, holding the device
//! id) written at create and checked at every open BEFORE any garbage collection: opening a volume against another
//! volume's backend, or an unbound backend, is refused and deletes nothing. Backend objects of extents outside the
//! device are never touched; they are reported as foreign.

use crate::addr::{ExtentId, Generation, GenerationRef, Geometry};
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::journal::Journal;
use crate::media::MediaAdapter;
use crate::record::{GenerationReleased, MapUpdate, Record, Superblock};
use crate::state::WriteState;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

pub const JOURNAL_FILE: &str = "journal";
/// Reserved extent id of the backend's binding marker (never a device extent: extents are < 2^64 - 1).
pub const MARKER_EXTENT: ExtentId = ExtentId(u64::MAX);
const MARKER_MAGIC: &[u8; 8] = b"VNXDEV01";

fn marker_ref() -> GenerationRef {
    GenerationRef { extent: MARKER_EXTENT, generation: Generation::FIRST }
}

fn marker_bytes(device_id: &[u8; 16]) -> Vec<u8> {
    [&MARKER_MAGIC[..], &device_id[..]].concat()
}
const LOCK_STRIPES: usize = 64;

#[derive(Clone, Debug)]
pub struct VolumeConfig {
    /// read every new generation back before the map record (VERIFIED instead of BACKEND_COMMITTED)
    pub verify_readback: bool,
    /// upper bound on the bytes of all generations held by the backend (live + in flight); 0 = no bound
    pub max_stored_bytes: u64,
}

impl Default for VolumeConfig {
    fn default() -> Self {
        VolumeConfig { verify_readback: true, max_stored_bytes: 0 }
    }
}

/// Points of the commit procedure where the fault-injection tests stop the volume as if the process crashed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    AfterPut,
    BeforeMapRecord,
    /// the map record is written only partly (a torn append), then the process stops
    TornMapRecord,
    AfterMapRecord,
    AfterRemove,
}

/// Returns `Err` to stop the operation at that stage (a simulated crash: the volume is unusable afterwards).
pub type FaultHook = Arc<dyn Fn(Stage, ExtentId) -> Result<()> + Send + Sync>;

#[derive(Clone, Debug, PartialEq, Eq)]
struct Entry {
    generation: Generation,
    length: u64,
    sha256: [u8; 32],
}

struct State {
    journal: Journal,
    map: BTreeMap<ExtentId, Entry>,
    /// highest generation ever allocated per extent (never reused)
    last_gen: BTreeMap<ExtentId, Generation>,
    next_txid: u64,
    stored_bytes: u64,
    damaged: BTreeSet<ExtentId>,
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct RecoveryReport {
    pub records_replayed: usize,
    pub torn_tail: Option<VnxError>,
    pub orphans_removed: Vec<GenerationRef>,
    pub orphans_left: Vec<GenerationRef>,
    /// backend objects of extents outside the device (left untouched)
    pub foreign: Vec<GenerationRef>,
    pub damaged: Vec<ExtentId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommitOutcome {
    pub generation: Generation,
    pub state: WriteState,
}

#[derive(Debug, Default)]
pub struct VolumeStats {
    pub reads: AtomicU64,
    pub unwritten_reads: AtomicU64,
    pub commits: AtomicU64,
    pub released: AtomicU64,
    pub release_failures: AtomicU64,
    pub integrity_failures: AtomicU64,
}

pub struct Volume {
    dir: PathBuf,
    geometry: Geometry,
    device_id: [u8; 16],
    flags: u32,
    media: Arc<dyn MediaAdapter>,
    cfg: VolumeConfig,
    state: Mutex<State>,
    stripes: Vec<Mutex<()>>,
    fault: Option<FaultHook>,
    crashed: AtomicBool,
    pub stats: VolumeStats,
}

fn corrupt<T>(msg: impl Into<String>) -> Result<T> {
    err(ErrorCode::Corruption, msg)
}

impl Volume {
    /// Create a volume in `dir` (which must not hold a journal) over an empty media adapter.
    pub fn create(
        dir: &Path,
        geometry: Geometry,
        device_id: [u8; 16],
        flags: u32,
        media: Arc<dyn MediaAdapter>,
        cfg: VolumeConfig,
    ) -> Result<Volume> {
        if !media.list()?.is_empty() {
            // also refuses another volume's backend (its marker is listed)
            return err(ErrorCode::InvalidArgument, "a new volume needs an empty backend");
        }
        std::fs::create_dir_all(dir)
            .map_err(|e| VnxError::new(ErrorCode::BackendUnavailable, format!("create {}: {e}", dir.display())))?;
        let sb = Record::Superblock(Superblock { device_id, geometry, flags });
        let journal = Journal::create(&dir.join(JOURNAL_FILE), &sb)?;
        media.put(marker_ref(), &marker_bytes(&device_id))?;
        media.sync()?;
        let state = State {
            journal,
            map: BTreeMap::new(),
            last_gen: BTreeMap::new(),
            next_txid: 1,
            stored_bytes: 0,
            damaged: BTreeSet::new(),
        };
        Ok(Volume::assemble(dir, geometry, device_id, flags, media, cfg, state))
    }

    fn assemble(
        dir: &Path,
        geometry: Geometry,
        device_id: [u8; 16],
        flags: u32,
        media: Arc<dyn MediaAdapter>,
        cfg: VolumeConfig,
        state: State,
    ) -> Volume {
        Volume {
            dir: dir.to_path_buf(),
            geometry,
            device_id,
            flags,
            media,
            cfg,
            state: Mutex::new(state),
            stripes: (0..LOCK_STRIPES).map(|_| Mutex::new(())).collect(),
            fault: None,
            crashed: AtomicBool::new(false),
            stats: VolumeStats::default(),
        }
    }

    /// Open an existing volume: replay the journal, garbage-collect orphan generations, find damaged extents.
    pub fn open(dir: &Path, media: Arc<dyn MediaAdapter>, cfg: VolumeConfig) -> Result<(Volume, RecoveryReport)> {
        let opened = Journal::open(&dir.join(JOURNAL_FILE))?;
        let mut report = RecoveryReport { records_replayed: opened.records.len(), torn_tail: opened.torn_tail, ..Default::default() };
        let mut recs = opened.records.into_iter();
        let (device_id, geometry, flags) = match recs.next() {
            Some(Record::Superblock(s)) => (s.device_id, s.geometry, s.flags),
            _ => return corrupt("journal does not start with a superblock"),
        };
        let mut map: BTreeMap<ExtentId, Entry> = BTreeMap::new();
        let mut last_gen: BTreeMap<ExtentId, Generation> = BTreeMap::new();
        let mut last_txid = 0u64;
        for r in recs {
            let (txid, at) = match &r {
                Record::MapUpdate(m) => (m.txid, m.at),
                Record::GenerationReleased(g) => (g.txid, g.at),
                Record::Superblock(_) => return corrupt("second superblock in the journal"),
            };
            if txid <= last_txid {
                return corrupt(format!("journal txid {txid} not after {last_txid}"));
            }
            if at.extent.0 >= geometry.extent_count() {
                return corrupt(format!("journal names extent {} beyond the device", at.extent.0));
            }
            last_txid = txid;
            if let Record::GenerationReleased(g) = &r {
                if map.get(&g.at.extent).map(|e| e.generation) == Some(g.at.generation) {
                    return corrupt(format!("journal releases the mapped generation {} of extent {}", g.at.generation.get(), g.at.extent.0));
                }
            }
            if let Record::MapUpdate(m) = &r {
                if last_gen.get(&m.at.extent).map_or(false, |g| m.at.generation <= *g) {
                    return corrupt(format!("journal reuses generation {} of extent {}", m.at.generation.get(), m.at.extent.0));
                }
            }
            let lg = last_gen.entry(at.extent).or_insert(at.generation);
            *lg = (*lg).max(at.generation);
            if let Record::MapUpdate(m) = r {
                let current = map.get(&m.at.extent).map(|e| e.generation);
                if current != m.previous {
                    return corrupt(format!("map update for extent {} expects previous {:?}, map has {:?}",
                        m.at.extent.0, m.previous, current));
                }
                if m.length != geometry.extent_len(m.at.extent)? {
                    return corrupt(format!("map update for extent {} with length {}", m.at.extent.0, m.length));
                }
                map.insert(m.at.extent, Entry { generation: m.at.generation, length: m.length, sha256: m.sha256 });
            }
        }
        let state = State {
            journal: opened.journal,
            map,
            last_gen,
            next_txid: last_txid + 1,
            stored_bytes: 0,
            damaged: BTreeSet::new(),
        };
        let vol = Volume::assemble(dir, geometry, device_id, flags, media, cfg, state);
        vol.recover(&mut report)?;
        Ok((vol, report))
    }

    fn recover(&self, report: &mut RecoveryReport) -> Result<()> {
        // bind first: nothing is deleted from a backend that is not this volume's
        match self.media.get(marker_ref()) {
            Ok(m) if m == marker_bytes(&self.device_id) => {}
            Ok(_) => return err(ErrorCode::InvalidArgument, "the backend belongs to another device (marker mismatch)"),
            Err(e) if e.code == ErrorCode::NotFound => {
                return err(ErrorCode::InvalidArgument, "the backend holds no device marker: not this volume's backend")
            }
            Err(e) => return Err(e),
        }
        let stored = self.media.list()?;
        let mut st = self.lock_state()?;
        let present: BTreeSet<GenerationRef> = stored.iter().copied().collect();
        let mut orphan_bytes = 0u64;
        for at in stored {
            if at.extent == MARKER_EXTENT {
                continue;
            }
            if at.extent.0 >= self.geometry.extent_count() {
                report.foreign.push(at);
                continue;
            }
            let lg = st.last_gen.entry(at.extent).or_insert(at.generation);
            *lg = (*lg).max(at.generation);
            let live = st.map.get(&at.extent).map(|e| e.generation) == Some(at.generation);
            if live {
                continue;
            }
            match self.media.remove(at) {
                Ok(()) => {
                    let txid = st.next_txid;
                    st.journal.append(&Record::GenerationReleased(GenerationReleased { txid, at }))?;
                    st.next_txid += 1;
                    report.orphans_removed.push(at);
                }
                Err(_) => {
                    // still held by the backend: it counts against the space budget
                    orphan_bytes += self.media.get(at).map_or(0, |d| d.len() as u64);
                    report.orphans_left.push(at);
                }
            }
        }
        let mut stored_bytes = 0;
        let mapped: Vec<(ExtentId, Entry)> = st.map.iter().map(|(k, v)| (*k, v.clone())).collect();
        for (extent, e) in mapped {
            if present.contains(&GenerationRef { extent, generation: e.generation }) {
                stored_bytes += e.length;
            } else {
                st.damaged.insert(extent);
                report.damaged.push(extent);
            }
        }
        st.stored_bytes = stored_bytes + orphan_bytes;
        Ok(())
    }

    pub fn with_fault_hook(mut self, hook: FaultHook) -> Volume {
        self.fault = Some(hook);
        self
    }

    pub fn geometry(&self) -> Geometry {
        self.geometry
    }

    pub fn device_id(&self) -> [u8; 16] {
        self.device_id
    }

    pub fn flags(&self) -> u32 {
        self.flags
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn media(&self) -> &Arc<dyn MediaAdapter> {
        &self.media
    }

    pub fn stored_bytes(&self) -> Result<u64> {
        Ok(self.lock_state()?.stored_bytes)
    }

    pub fn damaged(&self) -> Result<Vec<ExtentId>> {
        Ok(self.lock_state()?.damaged.iter().copied().collect())
    }

    /// The generation currently serving `extent`, if any.
    pub fn mapping(&self, extent: ExtentId) -> Result<Option<Generation>> {
        Ok(self.lock_state()?.map.get(&extent).map(|e| e.generation))
    }

    fn lock_state(&self) -> Result<MutexGuard<'_, State>> {
        if self.crashed.load(Ordering::SeqCst) {
            return err(ErrorCode::ShuttingDown, "volume stopped by an injected crash; reopen it");
        }
        self.state.lock().or_else(|_| err(ErrorCode::Internal, "volume state lock poisoned"))
    }

    fn stripe(&self, extent: ExtentId) -> Result<MutexGuard<'_, ()>> {
        self.stripes[(extent.0 % LOCK_STRIPES as u64) as usize]
            .lock()
            .or_else(|_| err(ErrorCode::Internal, "extent lock poisoned"))
    }

    fn fault(&self, stage: Stage, extent: ExtentId) -> Result<()> {
        if let Some(h) = &self.fault {
            if let Err(e) = h(stage, extent) {
                self.crashed.store(true, Ordering::SeqCst);
                return Err(e);
            }
        }
        Ok(())
    }

    /// Read one whole extent (verified against the map).
    pub fn read_extent(&self, extent: ExtentId) -> Result<Vec<u8>> {
        let _g = self.stripe(extent)?;
        self.read_locked(extent)
    }

    fn read_locked(&self, extent: ExtentId) -> Result<Vec<u8>> {
        let len = self.geometry.extent_len(extent)?;
        let entry = {
            let st = self.lock_state()?;
            if st.damaged.contains(&extent) {
                return corrupt(format!("extent {}: its generation is missing from the backend", extent.0));
            }
            st.map.get(&extent).cloned()
        };
        self.stats.reads.fetch_add(1, Ordering::Relaxed);
        let e = match entry {
            None => {
                self.stats.unwritten_reads.fetch_add(1, Ordering::Relaxed);
                return Ok(vec![0u8; len as usize]);
            }
            Some(e) => e,
        };
        let data = self.media.get(GenerationRef { extent, generation: e.generation })?;
        if data.len() as u64 != e.length || sha256(&data) != e.sha256 {
            self.stats.integrity_failures.fetch_add(1, Ordering::Relaxed);
            return corrupt(format!("extent {} generation {}: stored bytes do not match the map", extent.0, e.generation.get()));
        }
        Ok(data)
    }

    /// Replace one whole extent with `data` (its exact length).
    pub fn write_extent(&self, extent: ExtentId, data: &[u8]) -> Result<CommitOutcome> {
        let _g = self.stripe(extent)?;
        self.commit_locked(extent, data)
    }

    /// Read-modify-write of one extent under its lock: `patch` edits the current content (zeros if unwritten).
    pub fn modify_extent(&self, extent: ExtentId, patch: impl FnOnce(&mut [u8])) -> Result<CommitOutcome> {
        let _g = self.stripe(extent)?;
        let mut data = self.read_locked(extent)?;
        patch(&mut data);
        self.commit_locked(extent, &data)
    }

    fn commit_locked(&self, extent: ExtentId, data: &[u8]) -> Result<CommitOutcome> {
        let len = self.geometry.extent_len(extent)?;
        if data.len() as u64 != len {
            return err(ErrorCode::InvalidArgument, format!("extent {} holds {len} bytes, got {}", extent.0, data.len()));
        }
        let mut ws = WriteState::Received;
        let generation = {
            let mut st = self.lock_state()?;
            let max = self.cfg.max_stored_bytes;
            if max > 0 && st.stored_bytes.saturating_add(len) > max {
                return err(ErrorCode::NoSpace, format!("backend budget {max} bytes: {} stored, {len} more needed", st.stored_bytes));
            }
            let g = match st.last_gen.get(&extent) {
                Some(g) => g.next()?,
                None => Generation::FIRST,
            };
            st.last_gen.insert(extent, g);
            st.stored_bytes += len;
            g
        };
        ws = ws.advance(WriteState::Staged)?;
        let at = GenerationRef { extent, generation };
        let digest = sha256(data);
        let unreserve = |v: &Volume| {
            if let Ok(mut st) = v.lock_state() {
                st.stored_bytes = st.stored_bytes.saturating_sub(len);
            }
        };
        // undo a generation that will not be mapped; its space is given back only when the backend dropped it
        let discard = |v: &Volume| {
            match v.media.remove(at) {
                Err(e) if e.code != ErrorCode::NotFound => {} // still held: it stays counted, the next open collects it
                _ => unreserve(v),
            }
        };
        let receipt = match self.media.put(at, data) {
            Ok(r) => r,
            Err(e) => {
                discard(self);
                return Err(e);
            }
        };
        if receipt.sha256 != digest || receipt.length != len {
            discard(self);
            return corrupt(format!("backend receipt for extent {} does not describe the bytes written", extent.0));
        }
        ws = ws.advance(WriteState::BackendCommitted)?;
        self.fault(Stage::AfterPut, extent)?;
        let mut verified = false;
        if self.cfg.verify_readback {
            match self.media.get(at) {
                Ok(back) if back == data => verified = true,
                other => {
                    discard(self);
                    self.stats.integrity_failures.fetch_add(1, Ordering::Relaxed);
                    return match other {
                        Err(e) => Err(e),
                        Ok(_) => corrupt(format!("readback of extent {} generation {} differs", extent.0, generation.get())),
                    };
                }
            }
        }
        self.fault(Stage::BeforeMapRecord, extent)?;
        let previous = {
            let mut st = self.lock_state()?;
            let previous = st.map.get(&extent).cloned();
            let txid = st.next_txid;
            let rec = Record::MapUpdate(MapUpdate {
                txid,
                at,
                previous: previous.as_ref().map(|e| e.generation),
                length: len,
                sha256: digest,
            });
            if self.fault.is_some() {
                if let Err(e) = self.fault(Stage::TornMapRecord, extent) {
                    let bytes = rec.encode()?;
                    let _ = st.journal.append_raw(&bytes[..bytes.len() / 2]);
                    return Err(e);
                }
            }
            if let Err(e) = st.journal.append(&rec) {
                if st.journal.is_poisoned() {
                    // the map record may still reach the disk: keep generation g and stop the volume
                    self.crashed.store(true, Ordering::SeqCst);
                    return Err(e);
                }
                drop(st);
                discard(self);
                return Err(e);
            }
            st.next_txid += 1;
            st.map.insert(extent, Entry { generation, length: len, sha256: digest });
            st.damaged.remove(&extent);
            previous
        };
        self.stats.commits.fetch_add(1, Ordering::Relaxed);
        if verified {
            ws = ws.advance(WriteState::Verified)?;
        }
        self.fault(Stage::AfterMapRecord, extent)?;
        if let Some(old) = previous {
            self.release(GenerationRef { extent, generation: old.generation }, old.length)?;
        }
        self.fault(Stage::AfterRemove, extent)?;
        Ok(CommitOutcome { generation, state: ws })
    }

    /// Remove an unreferenced generation and journal its release. A failed remove leaves an orphan that the next
    /// `open` collects; it does not fail the write that replaced it.
    fn release(&self, at: GenerationRef, length: u64) -> Result<()> {
        if self.media.remove(at).is_err() {
            self.stats.release_failures.fetch_add(1, Ordering::Relaxed);
            return Ok(());
        }
        let mut st = self.lock_state()?;
        // saturating: a generation that was missing at open (damaged) and reappeared was never counted
        st.stored_bytes = st.stored_bytes.saturating_sub(length);
        let txid = st.next_txid;
        // the remap is already durable; a missing release record only costs a no-op orphan check at the next open
        if st.journal.append(&Record::GenerationReleased(GenerationReleased { txid, at })).is_ok() {
            st.next_txid += 1;
            self.stats.released.fetch_add(1, Ordering::Relaxed);
        } else {
            self.stats.release_failures.fetch_add(1, Ordering::Relaxed);
        }
        Ok(())
    }

    /// Make every completed write durable at the backend's commit point (map records are fsynced on append).
    pub fn flush(&self) -> Result<()> {
        let _st = self.lock_state()?; // no commit can append while the backend syncs
        self.media.sync()
    }

    /// Rewrite the journal as the superblock plus one map record per live extent (same txids, previous = none).
    pub fn compact(&self) -> Result<u64> {
        let mut st = self.lock_state()?;
        let mut recs = vec![Record::Superblock(Superblock { device_id: self.device_id, geometry: self.geometry, flags: self.flags })];
        // one map record per extent, in txid order, re-numbered densely so replay keeps txids increasing
        let mut live: Vec<(ExtentId, Entry)> = st.map.iter().map(|(k, v)| (*k, v.clone())).collect();
        live.sort_by_key(|(k, _)| *k);
        for (i, (extent, e)) in live.iter().enumerate() {
            recs.push(Record::MapUpdate(MapUpdate {
                txid: i as u64 + 1,
                at: GenerationRef { extent: *extent, generation: e.generation },
                previous: None,
                length: e.length,
                sha256: e.sha256,
            }));
        }
        let before = st.journal.len();
        st.journal.rewrite(&recs)?;
        st.next_txid = live.len() as u64 + 1;
        Ok(before - st.journal.len())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::tests::TempDir;
    use crate::media::MemoryMedia;

    fn geo() -> Geometry {
        Geometry::new(512, 4, 10).unwrap() // extents of 2048, 2048, 1024 bytes
    }

    fn new_vol(d: &TempDir, media: Arc<dyn MediaAdapter>) -> Volume {
        Volume::create(&d.0, geo(), [1; 16], 0, media, VolumeConfig::default()).unwrap()
    }

    fn fill(extent: u64, v: u8) -> Vec<u8> {
        vec![v; geo().extent_len(ExtentId(extent)).unwrap() as usize]
    }

    #[test]
    fn write_read_reopen() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), vec![0; 2048], "unwritten extents read as zeros");
        let o = v.write_extent(ExtentId(1), &fill(1, 7)).unwrap();
        assert_eq!(o, CommitOutcome { generation: Generation::FIRST, state: WriteState::Verified });
        let o = v.write_extent(ExtentId(1), &fill(1, 8)).unwrap();
        assert_eq!(o.generation.get(), 2);
        v.write_extent(ExtentId(2), &fill(2, 9)).unwrap();
        assert_eq!(m.list().unwrap().len(), 2 + 1, "the replaced generation was released (+ the marker)");
        assert_eq!(v.stored_bytes().unwrap(), 2048 + 1024);
        drop(v);
        let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
        assert!(r.orphans_removed.is_empty() && r.damaged.is_empty() && r.torn_tail.is_none());
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), fill(1, 8));
        assert_eq!(v.read_extent(ExtentId(2)).unwrap(), fill(2, 9));
        assert_eq!(v.write_extent(ExtentId(1), &fill(1, 1)).unwrap().generation.get(), 3, "generations never reused");
        assert_eq!(v.write_extent(ExtentId(1), &[0; 5]).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(v.read_extent(ExtentId(3)).unwrap_err().code, ErrorCode::OutOfRange);
    }

    #[test]
    fn without_readback_the_state_is_backend_committed() {
        let d = TempDir::new("vol");
        let cfg = VolumeConfig { verify_readback: false, ..Default::default() };
        let v = Volume::create(&d.0, geo(), [1; 16], 0, Arc::new(MemoryMedia::new()), cfg).unwrap();
        assert_eq!(v.write_extent(ExtentId(0), &fill(0, 1)).unwrap().state, WriteState::BackendCommitted);
    }

    #[test]
    fn no_space_is_typed_and_leaves_no_trace() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let cfg = VolumeConfig { max_stored_bytes: 4096, ..Default::default() };
        let v = Volume::create(&d.0, geo(), [1; 16], 0, m.clone(), cfg).unwrap();
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        v.write_extent(ExtentId(1), &fill(1, 2)).unwrap();
        // overwriting needs room for the new generation before the old one is released
        assert_eq!(v.write_extent(ExtentId(0), &fill(0, 3)).unwrap_err().code, ErrorCode::NoSpace);
        assert_eq!(v.read_extent(ExtentId(0)).unwrap(), fill(0, 1));
        assert_eq!(m.list().unwrap().len(), 2 + 1);
        assert_eq!(v.stored_bytes().unwrap(), 4096);
    }

    /// Media that corrupts every stored byte string after the put.
    struct Rot(MemoryMedia);
    impl MediaAdapter for Rot {
        fn info(&self) -> crate::media::MediaInfo {
            self.0.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<crate::media::CommitReceipt> {
            self.0.put(at, d)
        }
        fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
            self.0.get(at).map(|mut d| {
                if let Some(b) = d.last_mut() {
                    *b ^= 0x40;
                }
                d
            })
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
    fn corrupted_media_is_never_returned() {
        let d = TempDir::new("vol");
        let rot: Arc<dyn MediaAdapter> = Arc::new(Rot(MemoryMedia::new()));
        let v = new_vol(&d, rot.clone());
        // readback catches it at write time: the write fails and the mapping is unchanged
        assert_eq!(v.write_extent(ExtentId(0), &fill(0, 5)).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(v.mapping(ExtentId(0)).unwrap(), None);
        assert_eq!(rot.list().unwrap(), vec![marker_ref()], "the bad generation was removed");
        drop(v);
        // without readback the write commits, and the read refuses the rotten bytes
        let d2 = TempDir::new("vol");
        let cfg = VolumeConfig { verify_readback: false, ..Default::default() };
        let v = Volume::create(&d2.0, geo(), [1; 16], 0, Arc::new(Rot(MemoryMedia::new())), cfg).unwrap();
        v.write_extent(ExtentId(0), &fill(0, 5)).unwrap();
        assert_eq!(v.read_extent(ExtentId(0)).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(v.stats.integrity_failures.load(Ordering::Relaxed), 1);
    }

    fn crash_at(stage: Stage) -> FaultHook {
        Arc::new(move |s, _| if s == stage { err(ErrorCode::Internal, format!("injected crash at {s:?}")) } else { Ok(()) })
    }

    #[test]
    fn crash_at_every_stage_recovers_to_old_or_new() {
        for (stage, expect_new) in [
            (Stage::AfterPut, false),
            (Stage::BeforeMapRecord, false),
            (Stage::TornMapRecord, false),
            (Stage::AfterMapRecord, true),
            (Stage::AfterRemove, true),
        ] {
            let d = TempDir::new("crash");
            let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
            let v = new_vol(&d, m.clone());
            v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
            v.write_extent(ExtentId(2), &fill(2, 2)).unwrap();
            let v = v.with_fault_hook(crash_at(stage));
            assert!(v.write_extent(ExtentId(0), &fill(0, 3)).is_err(), "{stage:?}");
            assert_eq!(v.read_extent(ExtentId(2)).unwrap_err().code, ErrorCode::ShuttingDown, "a crashed volume is dead");
            drop(v);
            let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            let want = if expect_new { fill(0, 3) } else { fill(0, 1) };
            assert_eq!(v.read_extent(ExtentId(0)).unwrap(), want, "{stage:?}");
            assert_eq!(v.read_extent(ExtentId(2)).unwrap(), fill(2, 2), "{stage:?}");
            assert_eq!(r.torn_tail.is_some(), stage == Stage::TornMapRecord, "{stage:?}");
            let orphans = usize::from(stage != Stage::AfterRemove);
            assert_eq!(r.orphans_removed.len(), orphans, "{stage:?}: {r:?}");
            assert_eq!(m.list().unwrap().len(), 2 + 1, "{stage:?}: exactly the live generations (+ the marker) remain");
            assert_eq!(v.stored_bytes().unwrap(), 2048 + 1024);
            // the volume keeps working after recovery, and generation numbers are not reused
            let o = v.write_extent(ExtentId(0), &fill(0, 4)).unwrap();
            assert!(o.generation.get() >= 3, "{stage:?}");
            drop(v);
            let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            assert!(r.orphans_removed.is_empty() && r.torn_tail.is_none(), "{stage:?}: second open is clean");
            assert_eq!(v.read_extent(ExtentId(0)).unwrap(), fill(0, 4));
        }
    }

    #[test]
    fn missing_generation_is_damage_not_zeros() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        let g = v.write_extent(ExtentId(1), &fill(1, 6)).unwrap().generation;
        drop(v);
        m.remove(GenerationRef { extent: ExtentId(1), generation: g }).unwrap();
        let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
        assert_eq!(r.damaged, vec![ExtentId(1)]);
        assert_eq!(v.read_extent(ExtentId(1)).unwrap_err().code, ErrorCode::Corruption);
        // rewriting the extent repairs it
        v.write_extent(ExtentId(1), &fill(1, 7)).unwrap();
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), fill(1, 7));
        assert!(v.damaged().unwrap().is_empty());
    }

    #[test]
    fn inconsistent_journal_is_refused() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        drop(v);
        // a map update claiming a previous generation the map does not have
        let mut j = Journal::open(&d.0.join(JOURNAL_FILE)).unwrap().journal;
        let at = GenerationRef { extent: ExtentId(0), generation: Generation::new(9).unwrap() };
        j.append(&Record::MapUpdate(MapUpdate { txid: 99, at, previous: Some(Generation::new(5).unwrap()), length: 2048, sha256: [0; 32] }))
            .unwrap();
        drop(j);
        assert_eq!(Volume::open(&d.0, m, VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
    }

    #[test]
    fn compaction_keeps_the_mapping() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        for i in 0..20u8 {
            v.write_extent(ExtentId(u64::from(i % 3)), &fill(u64::from(i % 3), i)).unwrap();
        }
        let saved = v.compact().unwrap();
        assert!(saved > 0);
        v.write_extent(ExtentId(2), &fill(2, 99)).unwrap();
        drop(v);
        let (v, r) = Volume::open(&d.0, m, VolumeConfig::default()).unwrap();
        assert_eq!(r.records_replayed, 1 + 3 + 2);
        assert_eq!(v.read_extent(ExtentId(0)).unwrap(), fill(0, 18));
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), fill(1, 19));
        assert_eq!(v.read_extent(ExtentId(2)).unwrap(), fill(2, 99));
    }

    #[test]
    fn another_volumes_backend_is_refused_and_untouched() {
        let (da, db) = (TempDir::new("vol"), TempDir::new("vol"));
        let (ma, mb): (Arc<dyn MediaAdapter>, Arc<dyn MediaAdapter>) = (Arc::new(MemoryMedia::new()), Arc::new(MemoryMedia::new()));
        let a = new_vol(&da, ma.clone());
        a.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        let b = Volume::create(&db.0, geo(), [9; 16], 0, mb.clone(), VolumeConfig::default()).unwrap();
        b.write_extent(ExtentId(1), &fill(1, 2)).unwrap();
        drop((a, b));
        let before = mb.list().unwrap();
        assert_eq!(Volume::open(&da.0, mb.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::InvalidArgument);
        assert_eq!(mb.list().unwrap(), before, "nothing was deleted from the other backend");
        let unbound: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        assert_eq!(Volume::open(&da.0, unbound, VolumeConfig::default()).err().unwrap().code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn foreign_backend_objects_are_left_alone() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        drop(v);
        let stray = GenerationRef { extent: ExtentId(50), generation: Generation::FIRST };
        m.put(stray, b"not ours").unwrap();
        for _ in 0..2 {
            let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            assert_eq!(r.foreign, vec![stray]);
            assert!(r.orphans_removed.is_empty());
            assert_eq!(v.read_extent(ExtentId(0)).unwrap(), fill(0, 1));
        }
        assert_eq!(m.get(stray).unwrap(), b"not ours");
    }

    #[test]
    fn damage_inside_the_journal_never_turns_committed_data_into_zeros() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        for e in 0..3 {
            v.write_extent(ExtentId(e), &fill(e, 4)).unwrap();
        }
        drop(v);
        let p = d.0.join(JOURNAL_FILE);
        let mut bytes = std::fs::read(&p).unwrap();
        bytes[52 + 20] ^= 1; // inside the first map record (after the 52-byte superblock)
        std::fs::write(&p, &bytes).unwrap();
        let before = m.list().unwrap();
        assert_eq!(Volume::open(&d.0, m.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
        assert_eq!(m.list().unwrap(), before, "no generation was garbage-collected");
    }

    fn append(d: &TempDir, rec: Record) {
        let mut j = Journal::open(&d.0.join(JOURNAL_FILE)).unwrap().journal;
        j.append(&rec).unwrap();
    }

    #[test]
    fn replay_refuses_a_released_live_generation_or_a_reused_generation() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        v.write_extent(ExtentId(0), &fill(0, 2)).unwrap(); // generation 2 live, 1 released
        drop(v);
        let live = GenerationRef { extent: ExtentId(0), generation: Generation::new(2).unwrap() };
        append(&d, Record::GenerationReleased(GenerationReleased { txid: 100, at: live }));
        assert_eq!(Volume::open(&d.0, m.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);

        let d2 = TempDir::new("vol");
        let m2: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d2, m2.clone());
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        v.write_extent(ExtentId(0), &fill(0, 2)).unwrap();
        drop(v);
        // remap from 2 back to the already-used generation 1... expressed as previous=2 -> generation 1 is invalid
        // at the record level, so reuse is a map update of a NEW extent-local number not above the last one
        let at = GenerationRef { extent: ExtentId(1), generation: Generation::new(3).unwrap() };
        append(&d2, Record::MapUpdate(MapUpdate { txid: 100, at, previous: None, length: 2048, sha256: [0; 32] }));
        let at = GenerationRef { extent: ExtentId(1), generation: Generation::new(5).unwrap() };
        append(&d2, Record::GenerationReleased(GenerationReleased { txid: 101, at }));
        let at = GenerationRef { extent: ExtentId(1), generation: Generation::new(4).unwrap() };
        append(&d2, Record::MapUpdate(MapUpdate { txid: 102, at, previous: Some(Generation::new(3).unwrap()), length: 2048, sha256: [0; 32] }));
        assert_eq!(Volume::open(&d2.0, m2, VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
    }

    #[test]
    fn create_refuses_a_used_backend_or_directory() {
        let d = TempDir::new("vol");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        drop(new_vol(&d, m.clone()));
        assert!(Volume::create(&d.0, geo(), [1; 16], 0, Arc::new(MemoryMedia::new()), VolumeConfig::default()).is_err());
        let used: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        used.put(GenerationRef { extent: ExtentId(0), generation: Generation::FIRST }, b"x").unwrap();
        let d2 = TempDir::new("vol");
        assert_eq!(
            Volume::create(&d2.0, geo(), [1; 16], 0, used, VolumeConfig::default()).err().unwrap().code,
            ErrorCode::InvalidArgument
        );
    }
}
