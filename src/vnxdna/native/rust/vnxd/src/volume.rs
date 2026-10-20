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
use crate::record::{BatchEntry, GenerationReleased, MapBatch, MapUpdate, Record, Superblock, FLAG_SEALED, MAX_BATCH_EXTENTS};
use crate::state::WriteState;
use std::borrow::Cow;
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
    /// journal records read from their intact copy (the journal was then rewritten to restore the mirror)
    pub journal_repaired: usize,
    /// why restoring the mirror after a repair failed (the volume is open and correct, the journal still holds the
    /// single intact copies; the next open or compaction retries)
    pub mirror_restore_error: Option<VnxError>,
}

/// One extent of an atomic multi-extent write: `data` replaces the extent's bytes from `offset` on (a whole-extent
/// update has offset 0 and the extent's length; anything else is a read-modify-write inside the transaction).
#[derive(Clone, Copy, Debug)]
pub struct Patch<'a> {
    pub extent: ExtentId,
    pub offset: u64,
    pub data: &'a [u8],
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScrubReport {
    pub checked: u64,
    pub failed: Vec<(ExtentId, VnxError)>,
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

/// A sealed volume (superblock flag) only runs over a sealing adapter, and an unsealed one never does: plaintext
/// never reaches the backend of a sealed device, and sealed objects are never served as plaintext.
fn check_sealing(flags: u32, media: &dyn MediaAdapter) -> Result<()> {
    match (flags & FLAG_SEALED != 0, media.sealed()) {
        (true, false) => err(ErrorCode::InvalidArgument, "the volume is sealed: it needs the sealing media adapter"),
        (false, true) => err(ErrorCode::InvalidArgument, "the volume is not sealed: it cannot run over the sealing adapter"),
        _ => Ok(()),
    }
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
        check_sealing(flags, media.as_ref())?;
        if !media.list()?.is_empty() {
            // also refuses another volume's backend (its marker is listed)
            return err(ErrorCode::InvalidArgument, "a new volume needs an empty backend");
        }
        std::fs::create_dir_all(dir)
            .map_err(|e| VnxError::new(ErrorCode::BackendUnavailable, format!("create {}: {e}", dir.display())))?;
        let sb = Record::Superblock(Superblock { device_id, geometry, flags });
        let jpath = dir.join(JOURNAL_FILE);
        let journal = Journal::create(&jpath, &sb)?;
        if let Err(e) = media.put(marker_ref(), &marker_bytes(&device_id)).and_then(|_| media.sync()) {
            // undo, so a failed create leaves no half-made device and can simply be retried: the backend was empty
            // (checked above), so a marker it may hold (acknowledgement lost) is this attempt's own
            drop(journal);
            let _ = media.remove(marker_ref());
            let _ = std::fs::remove_file(&jpath);
            let _ = std::fs::File::open(dir).and_then(|d| d.sync_all());
            return Err(e);
        }
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
        check_sealing(flags, media.as_ref())?;
        let mut map: BTreeMap<ExtentId, Entry> = BTreeMap::new();
        let mut last_gen: BTreeMap<ExtentId, Generation> = BTreeMap::new();
        let mut last_txid = 0u64;
        for r in recs {
            let (txid, entries): (u64, Vec<BatchEntry>) = match r {
                Record::MapUpdate(m) => {
                    (m.txid, vec![BatchEntry { at: m.at, previous: m.previous, length: m.length, sha256: m.sha256 }])
                }
                Record::MapBatch(b) => (b.txid, b.entries),
                Record::GenerationReleased(g) => {
                    if g.txid <= last_txid {
                        return corrupt(format!("journal txid {} not after {last_txid}", g.txid));
                    }
                    if g.at.extent.0 >= geometry.extent_count() {
                        return corrupt(format!("journal names extent {} beyond the device", g.at.extent.0));
                    }
                    if map.get(&g.at.extent).map(|e| e.generation) == Some(g.at.generation) {
                        return corrupt(format!("journal releases the mapped generation {} of extent {}", g.at.generation.get(), g.at.extent.0));
                    }
                    last_txid = g.txid;
                    let lg = last_gen.entry(g.at.extent).or_insert(g.at.generation);
                    *lg = (*lg).max(g.at.generation);
                    continue;
                }
                Record::Superblock(_) => return corrupt("second superblock in the journal"),
                Record::Snapshot(s) => {
                    if s.txid <= last_txid {
                        return corrupt(format!("journal txid {} not after {last_txid}", s.txid));
                    }
                    last_txid = s.txid;
                    continue;
                }
                Record::Retention(r) => {
                    if r.txid <= last_txid {
                        return corrupt(format!("journal txid {} not after {last_txid}", r.txid));
                    }
                    last_txid = r.txid;
                    continue;
                }
            };
            if txid <= last_txid {
                return corrupt(format!("journal txid {txid} not after {last_txid}"));
            }
            last_txid = txid;
            // a map update or batch is checked whole, then applied whole (one transaction)
            for m in &entries {
                if m.at.extent.0 >= geometry.extent_count() {
                    return corrupt(format!("journal names extent {} beyond the device", m.at.extent.0));
                }
                if last_gen.get(&m.at.extent).map_or(false, |g| m.at.generation <= *g) {
                    return corrupt(format!("journal reuses generation {} of extent {}", m.at.generation.get(), m.at.extent.0));
                }
                let current = map.get(&m.at.extent).map(|e| e.generation);
                if current != m.previous {
                    return corrupt(format!("map update for extent {} expects previous {:?}, map has {:?}",
                        m.at.extent.0, m.previous, current));
                }
                if m.length != geometry.extent_len(m.at.extent)? {
                    return corrupt(format!("map update for extent {} with length {}", m.at.extent.0, m.length));
                }
            }
            for m in entries {
                last_gen.insert(m.at.extent, m.at.generation);
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
        report.journal_repaired = opened.repaired.len();
        if !opened.repaired.is_empty() {
            // rewrite both copies of every record from the replayed state; a failure leaves a readable journal
            report.mirror_restore_error = vol.compact().err();
        }
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
        let data = match self.media.get(GenerationRef { extent, generation: e.generation }) {
            Ok(d) => d,
            // the map says the generation exists: its absence is damage, reported like any other (never zeros)
            Err(x) if x.code == ErrorCode::NotFound => {
                self.stats.integrity_failures.fetch_add(1, Ordering::Relaxed);
                return corrupt(format!("extent {} generation {}: missing from the backend ({})", extent.0, e.generation.get(), x.message));
            }
            Err(x) => return Err(x),
        };
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

    /// Replace several extents in ONE transaction: after a crash or an error either every extent of `patches` has its
    /// new content or none has (one `map_batch` record is the commit point). Extents must be strictly increasing,
    /// 1..=[`MAX_BATCH_EXTENTS`] of them; partial patches are read-modify-writes inside the transaction. Returns the
    /// write state reached (the same for every extent).
    pub fn write_extents(&self, patches: &[Patch<'_>]) -> Result<WriteState> {
        if patches.is_empty() || patches.len() > MAX_BATCH_EXTENTS {
            return err(ErrorCode::InvalidArgument, format!("an atomic write covers 1..={MAX_BATCH_EXTENTS} extents, got {}", patches.len()));
        }
        for (i, p) in patches.iter().enumerate() {
            let len = self.geometry.extent_len(p.extent)?;
            if p.data.is_empty() || p.offset.checked_add(p.data.len() as u64).map_or(true, |end| end > len) {
                return err(ErrorCode::InvalidArgument, format!("patch of extent {} outside its {len} bytes", p.extent.0));
            }
            if i > 0 && patches[i - 1].extent >= p.extent {
                return err(ErrorCode::InvalidArgument, "patched extents must be strictly increasing");
            }
        }
        // every stripe involved, locked in ascending order (single-extent operations hold one stripe and never wait
        // for a second, so no lock-order cycle exists)
        let mut idx: Vec<usize> = patches.iter().map(|p| (p.extent.0 % LOCK_STRIPES as u64) as usize).collect();
        idx.sort_unstable();
        idx.dedup();
        let mut _guards = Vec::with_capacity(idx.len());
        for i in idx {
            _guards.push(self.stripes[i].lock().or_else(|_| err(ErrorCode::Internal, "extent lock poisoned"))?);
        }
        let mut contents: Vec<(ExtentId, Cow<'_, [u8]>)> = Vec::with_capacity(patches.len());
        for p in patches {
            let len = self.geometry.extent_len(p.extent)?;
            if p.offset == 0 && p.data.len() as u64 == len {
                contents.push((p.extent, Cow::Borrowed(p.data)));
            } else {
                let mut cur = self.read_locked(p.extent)?;
                let at = p.offset as usize;
                cur[at..at + p.data.len()].copy_from_slice(p.data);
                contents.push((p.extent, Cow::Owned(cur)));
            }
        }
        let items: Vec<(ExtentId, &[u8])> = contents.iter().map(|(e, c)| (*e, c.as_ref())).collect();
        self.commit_many_locked(&items).map(|(_, ws)| ws)
    }

    fn commit_locked(&self, extent: ExtentId, data: &[u8]) -> Result<CommitOutcome> {
        let (gens, state) = self.commit_many_locked(&[(extent, data)])?;
        Ok(CommitOutcome { generation: gens[0], state })
    }

    /// The commit procedure for one extent (a `map_update` record) or several (a `map_batch` record); the caller
    /// holds every extent's stripe.
    fn commit_many_locked(&self, items: &[(ExtentId, &[u8])]) -> Result<(Vec<Generation>, WriteState)> {
        let mut lens = Vec::with_capacity(items.len());
        for (extent, data) in items {
            let len = self.geometry.extent_len(*extent)?;
            if data.len() as u64 != len {
                return err(ErrorCode::InvalidArgument, format!("extent {} holds {len} bytes, got {}", extent.0, data.len()));
            }
            lens.push(len);
        }
        let first = items[0].0;
        let total: u64 = lens.iter().sum();
        let mut ws = WriteState::Received;
        let gens: Vec<Generation> = {
            let mut st = self.lock_state()?;
            let max = self.cfg.max_stored_bytes;
            if max > 0 && st.stored_bytes.saturating_add(total) > max {
                return err(ErrorCode::NoSpace, format!("backend budget {max} bytes: {} stored, {total} more needed", st.stored_bytes));
            }
            let mut gens = Vec::with_capacity(items.len());
            for (extent, _) in items {
                let g = match st.last_gen.get(extent) {
                    Some(g) => g.next()?,
                    None => Generation::FIRST,
                };
                st.last_gen.insert(*extent, g);
                gens.push(g);
            }
            st.stored_bytes += total;
            gens
        };
        ws = ws.advance(WriteState::Staged)?;
        let at = |i: usize| GenerationRef { extent: items[i].0, generation: gens[i] };
        let digests: Vec<[u8; 32]> = items.iter().map(|(_, d)| sha256(d)).collect();
        // undo the generations that will not be mapped: the first `put` of them reached the backend; space is given
        // back for every generation the backend no longer holds
        let discard = |v: &Volume, put: usize| {
            let mut freed = 0u64;
            for i in 0..items.len() {
                let gone = i >= put || !matches!(v.media.remove(at(i)), Err(e) if e.code != ErrorCode::NotFound);
                if gone {
                    freed += lens[i]; // a generation still held stays counted; the next open collects it
                }
            }
            if let Ok(mut st) = v.lock_state() {
                st.stored_bytes = st.stored_bytes.saturating_sub(freed);
            }
        };
        for (i, (extent, data)) in items.iter().enumerate() {
            let receipt = match self.media.put(at(i), data) {
                Ok(r) => r,
                Err(e) => {
                    // a failed put may have left something behind; a refused one (READ_ONLY) never reached the backend
                    discard(self, if e.code == ErrorCode::ReadOnly { i } else { i + 1 });
                    return Err(e);
                }
            };
            if receipt.sha256 != digests[i] || receipt.length != lens[i] {
                discard(self, i + 1);
                return corrupt(format!("backend receipt for extent {} does not describe the bytes written", extent.0));
            }
        }
        ws = ws.advance(WriteState::BackendCommitted)?;
        self.fault(Stage::AfterPut, first)?;
        let mut verified = false;
        if self.cfg.verify_readback {
            for (i, (extent, data)) in items.iter().enumerate() {
                match self.media.get(at(i)) {
                    Ok(back) if back == *data => {}
                    other => {
                        discard(self, items.len());
                        self.stats.integrity_failures.fetch_add(1, Ordering::Relaxed);
                        return match other {
                            Err(e) => Err(e),
                            Ok(_) => corrupt(format!("readback of extent {} generation {} differs", extent.0, gens[i].get())),
                        };
                    }
                }
            }
            verified = true;
        }
        self.fault(Stage::BeforeMapRecord, first)?;
        let previous: Vec<Option<Entry>> = {
            let mut st = self.lock_state()?;
            let previous: Vec<Option<Entry>> = items.iter().map(|(e, _)| st.map.get(e).cloned()).collect();
            let txid = st.next_txid;
            let entries: Vec<BatchEntry> = (0..items.len())
                .map(|i| BatchEntry {
                    at: at(i),
                    previous: previous[i].as_ref().map(|e| e.generation),
                    length: lens[i],
                    sha256: digests[i],
                })
                .collect();
            let rec = if entries.len() == 1 {
                let e = &entries[0];
                Record::MapUpdate(MapUpdate { txid, at: e.at, previous: e.previous, length: e.length, sha256: e.sha256 })
            } else {
                Record::MapBatch(MapBatch { txid, entries })
            };
            if self.fault.is_some() {
                if let Err(e) = self.fault(Stage::TornMapRecord, first) {
                    let bytes = crate::journal::encode_pair(&rec)?;
                    let _ = st.journal.append_raw(&bytes[..bytes.len() / 4]);
                    return Err(e);
                }
            }
            if let Err(e) = st.journal.append(&rec) {
                if st.journal.is_poisoned() {
                    // the map record may still reach the disk: keep the generations and stop the volume
                    self.crashed.store(true, Ordering::SeqCst);
                    return Err(e);
                }
                drop(st);
                discard(self, items.len());
                return Err(e);
            }
            st.next_txid += 1;
            for i in 0..items.len() {
                st.map.insert(items[i].0, Entry { generation: gens[i], length: lens[i], sha256: digests[i] });
                st.damaged.remove(&items[i].0);
            }
            previous
        };
        self.stats.commits.fetch_add(1, Ordering::Relaxed);
        if verified {
            ws = ws.advance(WriteState::Verified)?;
        }
        self.fault(Stage::AfterMapRecord, first)?;
        for (i, old) in previous.into_iter().enumerate() {
            if let Some(old) = old {
                self.release(GenerationRef { extent: items[i].0, generation: old.generation }, old.length)?;
            }
        }
        self.fault(Stage::AfterRemove, first)?;
        Ok((gens, ws))
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

    /// Read and verify every mapped extent (length and SHA-256 against the map); returns the extents that fail, with
    /// the error each one gave. Changes nothing: damage is reported, the extents keep failing their reads until a
    /// whole-extent write replaces them.
    pub fn scrub(&self) -> Result<ScrubReport> {
        let extents: Vec<ExtentId> = self.lock_state()?.map.keys().copied().collect();
        let mut report = ScrubReport { checked: 0, failed: Vec::new() };
        for e in extents {
            report.checked += 1;
            match self.read_extent(e) {
                Ok(_) => {}
                // the backend or the sealing service is unreachable: no verdict on the data, stop
                Err(x) if x.code.class() == crate::error::ErrorClass::Retryable || x.code == ErrorCode::ShuttingDown => {
                    return Err(x)
                }
                Err(x) => report.failed.push((e, x)),
            }
        }
        Ok(report)
    }

    /// Make every completed write durable at the backend's commit point (map records are fsynced on append).
    pub fn flush(&self) -> Result<()> {
        let _st = self.lock_state()?; // no commit can append while the backend syncs
        self.media.sync()
    }

    /// Rewrite the journal as the superblock, one map record per live extent (previous = none) and, for every extent
    /// whose highest used generation is above its live one, a release record of that generation: replay then knows
    /// every generation number ever used, so none is reused after a compaction.
    pub fn compact(&self) -> Result<u64> {
        // no commit may be in flight: its generation is already counted in `last_gen` but not yet mapped, and a
        // release record of it would make its map record look like a reuse. Every stripe, in ascending order.
        let mut _guards = Vec::with_capacity(LOCK_STRIPES);
        for m in &self.stripes {
            _guards.push(m.lock().or_else(|_| err(ErrorCode::Internal, "extent lock poisoned"))?);
        }
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
        let mut txid = live.len() as u64;
        for (extent, hw) in st.last_gen.iter() {
            if st.map.get(extent).map_or(true, |e| e.generation < *hw) {
                txid += 1;
                recs.push(Record::GenerationReleased(GenerationReleased { txid, at: GenerationRef { extent: *extent, generation: *hw } }));
            }
        }
        let before = st.journal.len();
        st.journal.rewrite(&recs)?;
        st.next_txid = txid + 1;
        Ok(before.saturating_sub(st.journal.len()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::tests::TempDir;
    use crate::media::MemoryMedia;
    use std::sync::atomic::AtomicU64;

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
    fn a_failed_create_leaves_nothing_behind_and_can_be_retried() {
        // V14-P9-D2: when the device-marker put failed, create left its journal behind: every retry failed with
        // BACKEND_UNAVAILABLE ("File exists", a retryable code for a permanent state) and open refused the directory
        let d = TempDir::new("vol");
        let m = Arc::new(FailNth::new());
        m.fail_in(1);
        let e = Volume::create(&d.0, geo(), [1; 16], 0, m.clone(), VolumeConfig::default()).err().unwrap();
        assert_eq!(e.code, ErrorCode::BackendUnavailable);
        assert!(!d.0.join(JOURNAL_FILE).exists(), "no journal of a device that was never created");
        assert!(m.list().unwrap().is_empty());
        let v = Volume::create(&d.0, geo(), [1; 16], 0, m.clone(), VolumeConfig::default()).unwrap();
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        drop(v);
        assert_eq!(Volume::open(&d.0, m, VolumeConfig::default()).unwrap().0.read_extent(ExtentId(0)).unwrap(), fill(0, 1));
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
        let clean = std::fs::read(&p).unwrap();
        // the journal is mirrored: superblock pair 2 x 52 bytes, then map record pairs of 2 x 88 bytes
        let mut bytes = clean.clone();
        bytes[104 + 20] ^= 1; // copy 1 of the first map record
        bytes[104 + 88 + 20] ^= 1; // and copy 2
        std::fs::write(&p, &bytes).unwrap();
        let before = m.list().unwrap();
        assert_eq!(Volume::open(&d.0, m.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
        assert_eq!(m.list().unwrap(), before, "no generation was garbage-collected");
        assert_eq!(std::fs::read(&p).unwrap(), bytes, "the journal is untouched");
        // one damaged copy, in the first or in the last record, is repaired and the mirror restored
        for at in [104 + 20, clean.len() - 3] {
            let mut bytes = clean.clone();
            bytes[at] ^= 1;
            std::fs::write(&p, &bytes).unwrap();
            let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            assert_eq!(r.journal_repaired, 1, "flip at {at}");
            assert!(r.orphans_removed.is_empty() && r.damaged.is_empty() && r.torn_tail.is_none());
            for e in 0..3 {
                assert_eq!(v.read_extent(ExtentId(e)).unwrap(), fill(e, 4), "flip at {at}");
            }
            drop(v);
            let (_, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            assert_eq!(r.journal_repaired, 0, "the repair rewrote both copies");
            std::fs::write(&p, &clean).unwrap();
        }
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

    /// Media whose `fail_put`-th put from now on fails (0 = never).
    struct FailNth {
        inner: MemoryMedia,
        puts: AtomicU64,
        fail_put: AtomicU64,
    }
    impl FailNth {
        fn new() -> FailNth {
            FailNth { inner: MemoryMedia::new(), puts: AtomicU64::new(0), fail_put: AtomicU64::new(0) }
        }
        fn fail_in(&self, n: u64) {
            self.fail_put.store(self.puts.load(Ordering::SeqCst) + n, Ordering::SeqCst);
        }
    }
    impl MediaAdapter for FailNth {
        fn info(&self) -> crate::media::MediaInfo {
            self.inner.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<crate::media::CommitReceipt> {
            let n = self.puts.fetch_add(1, Ordering::SeqCst) + 1;
            if n == self.fail_put.load(Ordering::SeqCst) {
                return err(ErrorCode::BackendUnavailable, "injected put failure");
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

    fn patches(v: u8) -> (Vec<u8>, Vec<u8>, Vec<u8>) {
        (vec![v; 100], fill(1, v), vec![v; 7])
    }

    fn expect_all(v: &Volume, base: u8, new: Option<u8>) {
        let (mut e0, mut e1, mut e2) = (fill(0, base), fill(1, base), fill(2, base));
        if let Some(n) = new {
            e0[1948..].fill(n);
            e1 = fill(1, n);
            e2[..7].fill(n);
        }
        assert_eq!(v.read_extent(ExtentId(0)).unwrap(), e0);
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), e1);
        assert_eq!(v.read_extent(ExtentId(2)).unwrap(), e2);
    }

    #[test]
    fn a_multi_extent_write_is_one_transaction() {
        let d = TempDir::new("batch");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        for e in 0..3 {
            v.write_extent(ExtentId(e), &fill(e, 1)).unwrap();
        }
        let (a, b, c) = patches(9);
        let ps = [
            Patch { extent: ExtentId(0), offset: 1948, data: &a },
            Patch { extent: ExtentId(1), offset: 0, data: &b },
            Patch { extent: ExtentId(2), offset: 0, data: &c },
        ];
        assert_eq!(v.write_extents(&ps).unwrap(), WriteState::Verified);
        expect_all(&v, 1, Some(9));
        assert_eq!(m.list().unwrap().len(), 3 + 1, "the three old generations were released");
        drop(v);
        let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
        assert!(r.orphans_removed.is_empty() && r.torn_tail.is_none());
        expect_all(&v, 1, Some(9));
        // invalid batches fail before any effect
        for bad in [
            vec![],
            vec![ps[1], ps[0]],
            vec![ps[0], ps[0]],
            vec![Patch { extent: ExtentId(2), offset: 1020, data: &a }],
            vec![Patch { extent: ExtentId(5), offset: 0, data: &c }],
        ] {
            assert!(v.write_extents(&bad).is_err());
        }
        expect_all(&v, 1, Some(9));
    }

    #[test]
    fn a_crash_at_any_stage_of_a_multi_extent_write_leaves_all_old_or_all_new() {
        for (stage, expect_new) in [
            (Stage::AfterPut, false),
            (Stage::BeforeMapRecord, false),
            (Stage::TornMapRecord, false),
            (Stage::AfterMapRecord, true),
            (Stage::AfterRemove, true),
        ] {
            let d = TempDir::new("batch-crash");
            let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
            let v = new_vol(&d, m.clone());
            for e in 0..3 {
                v.write_extent(ExtentId(e), &fill(e, 1)).unwrap();
            }
            let v = v.with_fault_hook(crash_at(stage));
            let (a, b, c) = patches(9);
            let ps = [
                Patch { extent: ExtentId(0), offset: 1948, data: &a },
                Patch { extent: ExtentId(1), offset: 0, data: &b },
                Patch { extent: ExtentId(2), offset: 0, data: &c },
            ];
            assert!(v.write_extents(&ps).is_err(), "{stage:?}");
            drop(v);
            let (v, r) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
            expect_all(&v, 1, expect_new.then_some(9));
            assert_eq!(r.torn_tail.is_some(), stage == Stage::TornMapRecord, "{stage:?}");
            assert_eq!(m.list().unwrap().len(), 3 + 1, "{stage:?}: only the live generations remain");
            assert_eq!(v.stored_bytes().unwrap(), 2048 + 2048 + 1024, "{stage:?}");
        }
    }

    #[test]
    fn a_failed_put_inside_a_multi_extent_write_changes_nothing() {
        for nth in 1..=3 {
            let d = TempDir::new("batch-fail");
            let fm = Arc::new(FailNth::new());
            let m: Arc<dyn MediaAdapter> = fm.clone();
            let v = new_vol(&d, m.clone());
            for e in 0..3 {
                v.write_extent(ExtentId(e), &fill(e, 1)).unwrap();
            }
            let before = (m.list().unwrap(), v.stored_bytes().unwrap());
            fm.fail_in(nth);
            let (a, b, c) = patches(9);
            let ps = [
                Patch { extent: ExtentId(0), offset: 1948, data: &a },
                Patch { extent: ExtentId(1), offset: 0, data: &b },
                Patch { extent: ExtentId(2), offset: 0, data: &c },
            ];
            assert_eq!(v.write_extents(&ps).unwrap_err().code, ErrorCode::BackendUnavailable, "put {nth}");
            expect_all(&v, 1, None);
            assert_eq!((m.list().unwrap(), v.stored_bytes().unwrap()), before, "put {nth}: no trace");
            assert_eq!(v.write_extents(&ps).unwrap(), WriteState::Verified, "the volume keeps working");
            expect_all(&v, 1, Some(9));
        }
    }

    #[test]
    fn a_multi_extent_write_that_does_not_fit_is_refused_whole() {
        let d = TempDir::new("batch-space");
        let cfg = VolumeConfig { max_stored_bytes: 2048 + 2048 + 1024 + 2048, ..Default::default() };
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = Volume::create(&d.0, geo(), [1; 16], 0, m.clone(), cfg).unwrap();
        for e in 0..3 {
            v.write_extent(ExtentId(e), &fill(e, 1)).unwrap();
        }
        let (a, b, c) = patches(9);
        let ps = [
            Patch { extent: ExtentId(0), offset: 1948, data: &a },
            Patch { extent: ExtentId(1), offset: 0, data: &b },
            Patch { extent: ExtentId(2), offset: 0, data: &c },
        ];
        assert_eq!(v.write_extents(&ps).unwrap_err().code, ErrorCode::NoSpace);
        expect_all(&v, 1, None);
        assert_eq!(m.list().unwrap().len(), 3 + 1);
    }

    #[test]
    fn replay_checks_a_batch_whole() {
        let d = TempDir::new("batch-replay");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        v.write_extent(ExtentId(0), &fill(0, 1)).unwrap();
        drop(v);
        // the second entry claims a previous generation extent 1 never had: the whole journal is refused
        let e = |x: u64, g: u64, p: u64| BatchEntry {
            at: GenerationRef { extent: ExtentId(x), generation: Generation::new(g).unwrap() },
            previous: Generation::new(p).ok(),
            length: 2048,
            sha256: [0; 32],
        };
        append(&d, Record::MapBatch(MapBatch { txid: 50, entries: vec![e(0, 2, 1), e(1, 3, 2)] }));
        assert_eq!(Volume::open(&d.0, m, VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
    }

    #[test]
    fn generations_are_never_reused_across_compaction() {
        let d = TempDir::new("vol-hw");
        let fm = Arc::new(FailNth::new());
        let m: Arc<dyn MediaAdapter> = fm.clone();
        let v = new_vol(&d, m.clone());
        for i in 0..5u8 {
            v.write_extent(ExtentId(0), &fill(0, i)).unwrap(); // generations 1..=5, 5 live
        }
        // extent 1: a write that never committed (generation 1 used, nothing mapped)
        fm.fail_in(1);
        assert!(v.write_extent(ExtentId(1), &fill(1, 1)).is_err());
        v.compact().unwrap();
        // a later write between compaction and reopen, and one after
        assert_eq!(v.write_extent(ExtentId(0), &fill(0, 6)).unwrap().generation.get(), 6);
        drop(v);
        let (v, _) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
        v.compact().unwrap();
        drop(v);
        let (v, _) = Volume::open(&d.0, m.clone(), VolumeConfig::default()).unwrap();
        assert_eq!(v.write_extent(ExtentId(0), &fill(0, 7)).unwrap().generation.get(), 7);
        assert_eq!(v.write_extent(ExtentId(1), &fill(1, 2)).unwrap().generation.get(), 2, "generation 1 of extent 1 was used");
        assert_eq!(v.read_extent(ExtentId(0)).unwrap(), fill(0, 7));
    }

    #[test]
    fn a_p3_unmirrored_journal_is_refused_and_its_backend_untouched() {
        let d = TempDir::new("vol-p3");
        let m: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
        let v = new_vol(&d, m.clone());
        v.write_extent(ExtentId(1), &fill(1, 5)).unwrap();
        drop(v);
        // rewrite the journal in the old layout: every record once
        let p = d.0.join(JOURNAL_FILE);
        let recs = Journal::open(&p).unwrap().records;
        for keep in [1, recs.len()] {
            let plain: Vec<u8> = recs[..keep].iter().flat_map(|r| r.encode().unwrap()).collect();
            std::fs::write(&p, &plain).unwrap();
            let before = m.list().unwrap();
            assert_eq!(Volume::open(&d.0, m.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::VersionMismatch);
            assert_eq!(m.list().unwrap(), before, "nothing garbage-collected");
            assert_eq!(std::fs::read(&p).unwrap(), plain, "journal untouched");
        }
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
