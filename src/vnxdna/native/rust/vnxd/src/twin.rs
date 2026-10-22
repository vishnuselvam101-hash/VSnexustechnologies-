//! V15 Digital Twin (Task G): one simulated drive with six independent pools and per-pool fault injection.
//!
//! Everything this module does is SIMULATED. No DNA is synthesised, stored or sequenced: every pool is a
//! [`DnaSimMedia`] (the software simulator of the DNA channel) over an in-process [`MemoryMedia`], and every number
//! the twin reports is a count of simulated operations. Nothing here is a measurement of any physical medium.
//!
//! The drive has six pools, one per [`PoolRole`]:
//!
//! | role | what the pool holds in the twin |
//! |---|---|
//! | `storage` | the extent generations of the volume (the codec's own redundancy, not a mirror) |
//! | `ram` | the cached copies of the extents the volume read (a verifying read-through cache) |
//! | `io-journal` | the write-ahead copy of every generation, written before the storage pool |
//! | `metadata` | the map (the journal) of the volume, mirrored over its replicas |
//! | `security` | the sealed objects of a sealed volume |
//! | `bootstrap` | the binding marker of the volume and the generations of the volumes on it |
//!
//! Every pool is a set of replicas. A replica is a [`FaultMedia`] over a [`DnaSimMedia`], so a fault can be injected
//! into one replica of one pool without touching the others. A pool with more than one replica is presented to the
//! layers above as a [`MirrorMedia`]: a `put` reaches every replica, a `get` returns the bytes of the first replica
//! whose SHA-256 matches the receipt of the `put`, and the replicas whose bytes do not match are reported as
//! damaged. A pool whose replicas are all lost answers with a typed error, never with other bytes.
//!
//! The declared campaign (docs/V15_DIGITAL_TWIN.md) runs against this module with fixed seeds; the numbers come from
//! the test output, never from this file.

use crate::addr::{ExtentId, Generation, GenerationRef, Geometry};
use crate::codec::Codec;
use crate::digest::sha256;
use crate::dnasim::{DnaSimConfig, DnaSimMedia};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::fault::{FaultMedia, Rule};
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo, MemoryMedia};
use crate::record::{Record, FLAG_SEALED};
use crate::seal::{SealedMedia, Sealer};
use crate::volume::{CommitOutcome, RecoveryReport, Volume, VolumeConfig, JOURNAL_FILE};
use std::collections::BTreeMap;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// The role of a pool in the digital twin.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum PoolRole {
    /// the extent generations of the volume
    Storage,
    /// the cached copies of the extents the volume has read
    Ram,
    /// the write-ahead copy of every generation
    IoJournal,
    /// the map of the volume (its journal), mirrored over the pool's replicas
    Metadata,
    /// the sealed objects of a sealed volume
    Security,
    /// the binding marker of the volume
    Bootstrap,
}

impl PoolRole {
    /// Every role, in the order the twin builds its pools.
    pub const ALL: [PoolRole; 6] = [
        PoolRole::Storage,
        PoolRole::Ram,
        PoolRole::IoJournal,
        PoolRole::Metadata,
        PoolRole::Security,
        PoolRole::Bootstrap,
    ];

    /// The name of the role in the report and in the documentation.
    pub fn as_str(self) -> &'static str {
        match self {
            PoolRole::Storage => "storage",
            PoolRole::Ram => "ram",
            PoolRole::IoJournal => "io-journal",
            PoolRole::Metadata => "metadata",
            PoolRole::Security => "security",
            PoolRole::Bootstrap => "bootstrap",
        }
    }
}

/// The specification of one pool of the digital twin.
#[derive(Clone, Debug)]
pub struct PoolSpec {
    /// the role of the pool
    pub role: PoolRole,
    /// the pool's seed: the replica `r` runs the simulator with the seed `seed + r`, so every replica of every pool
    /// has its own deterministic stream
    pub seed: u64,
    /// the configuration of the simulator of every replica of the pool (its `seed` field is replaced by the
    /// pool's per-replica seed)
    pub sim: DnaSimConfig,
    /// how many replicas the pool has (1..=3)
    pub replicas: u8,
}

impl PoolSpec {
    /// A pool specification with the given role, seed, simulator configuration and number of replicas.
    pub fn new(role: PoolRole, seed: u64, sim: DnaSimConfig, replicas: u8) -> PoolSpec {
        PoolSpec { role, seed, sim, replicas }
    }
}

/// The mirror of one pool: an N-way replica of the same objects.
///
/// A `put` reaches every replica. A `get` returns the bytes of the first replica whose SHA-256 matches the receipt
/// of the `put`; a replica that answers with other bytes is reported as damaged and the next one is tried. When no
/// replica answers with the expected bytes the mirror answers with a typed error (CORRUPTION when a replica
/// answered with other bytes, otherwise the error of the last replica), never with those bytes.
pub struct MirrorMedia {
    replicas: Vec<Arc<dyn MediaAdapter>>,
    /// the digest each stored generation must have (the digest of the bytes of its `put`)
    digests: Mutex<BTreeMap<GenerationRef, [u8; 32]>>,
    /// the replicas that answered a `get` with bytes that do not match the receipt
    damaged: Mutex<BTreeMap<GenerationRef, Vec<usize>>>,
    /// how many damaged answers the mirror has seen
    damage_events: AtomicU64,
    info: MediaInfo,
}

impl MirrorMedia {
    /// A mirror over `replicas` (at least one; all of them must have the same durability and concurrency).
    pub fn new(replicas: Vec<Arc<dyn MediaAdapter>>) -> Result<MirrorMedia> {
        if replicas.is_empty() {
            return err(ErrorCode::InvalidArgument, "a mirror needs at least one replica");
        }
        let first = replicas[0].info();
        for r in &replicas[1..] {
            let info = r.info();
            if info.durability != first.durability || info.max_in_flight != first.max_in_flight {
                return err(ErrorCode::InvalidArgument, "the replicas of a mirror must have the same durability and concurrency");
            }
        }
        Ok(MirrorMedia {
            replicas,
            digests: Mutex::new(BTreeMap::new()),
            damaged: Mutex::new(BTreeMap::new()),
            damage_events: AtomicU64::new(0),
            info: MediaInfo { kind: "mirror", durability: first.durability, max_in_flight: first.max_in_flight },
        })
    }

    /// The replicas that answered a `get` with bytes that do not match their receipt.
    pub fn damaged_replicas(&self) -> Vec<usize> {
        let mut out: Vec<usize> = Vec::new();
        for (_, reps) in self.damaged.lock().unwrap_or_else(|p| p.into_inner()).iter() {
            for r in reps {
                if !out.contains(r) {
                    out.push(*r);
                }
            }
        }
        out.sort_unstable();
        out
    }

    /// How many damaged answers the mirror has seen.
    pub fn damage_events(&self) -> u64 {
        self.damage_events.load(Ordering::Relaxed)
    }

    fn mark_damaged(&self, at: GenerationRef, replica: usize) {
        self.damage_events.fetch_add(1, Ordering::Relaxed);
        let mut damaged = self.damaged.lock().unwrap_or_else(|p| p.into_inner());
        let entry = damaged.entry(at).or_default();
        if !entry.contains(&replica) {
            entry.push(replica);
        }
    }
}

impl MediaAdapter for MirrorMedia {
    fn info(&self) -> MediaInfo {
        self.info.clone()
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let digest = sha256(data);
        let mut last_err: Option<VnxError> = None;
        let mut stored = 0usize;
        for (i, r) in self.replicas.iter().enumerate() {
            match r.put(at, data) {
                Ok(receipt) => {
                    if receipt.length != data.len() as u64 || receipt.sha256 != digest {
                        // a lying replica: it is reported, and the put still succeeds if another replica took the bytes
                        self.mark_damaged(at, i);
                        last_err = Some(VnxError::new(ErrorCode::Corruption, format!("replica {i} of {at:?} does not describe the bytes")));
                    } else {
                        stored += 1;
                    }
                }
                Err(e) => last_err = Some(e),
            }
        }
        if stored == 0 {
            return Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable, "every replica refused the put")));
        }
        self.digests.lock().unwrap_or_else(|p| p.into_inner()).insert(at, digest);
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: digest })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let expected = self.digests.lock().unwrap_or_else(|p| p.into_inner()).get(&at).copied();
        let mut last_err: Option<VnxError> = None;
        let mut mismatched = 0usize;
        for (i, r) in self.replicas.iter().enumerate() {
            match r.get(at) {
                Ok(bytes) => match expected {
                    Some(want) if sha256(&bytes) != want => {
                        // the replica answered with other bytes: it is reported and not served
                        self.mark_damaged(at, i);
                        mismatched += 1;
                        last_err = Some(VnxError::new(ErrorCode::Corruption, format!("replica {i} of {at:?} answered with other bytes")));
                    }
                    _ => {
                        if expected.is_none() {
                            self.digests.lock().unwrap_or_else(|p| p.into_inner()).insert(at, sha256(&bytes));
                        }
                        return Ok(bytes);
                    }
                },
                Err(e) => last_err = Some(e),
            }
        }
        if mismatched > 0 {
            return err(ErrorCode::Corruption, format!("no replica of {at:?} holds the bytes of its receipt"));
        }
        Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable, "every replica refused the get")))
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        let mut last_err: Option<VnxError> = None;
        let mut removed = 0usize;
        for r in &self.replicas {
            match r.remove(at) {
                Ok(()) => removed += 1,
                Err(e) => last_err = Some(e),
            }
        }
        if removed == 0 {
            return Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable, "every replica refused the remove")));
        }
        self.digests.lock().unwrap_or_else(|p| p.into_inner()).remove(&at);
        Ok(())
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        let mut all: Vec<GenerationRef> = Vec::new();
        let mut last_err: Option<VnxError> = None;
        let mut ok = 0usize;
        for r in &self.replicas {
            match r.list() {
                Ok(l) => {
                    ok += 1;
                    all.extend(l);
                }
                Err(e) => last_err = Some(e),
            }
        }
        if ok == 0 {
            return Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable, "every replica refused the list")));
        }
        all.sort();
        all.dedup();
        Ok(all)
    }

    fn sync(&self) -> Result<()> {
        let mut last_err: Option<VnxError> = None;
        let mut ok = 0usize;
        for r in &self.replicas {
            match r.sync() {
                Ok(()) => ok += 1,
                Err(e) => last_err = Some(e),
            }
        }
        if ok == 0 {
            return Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable, "every replica refused the sync")));
        }
        Ok(())
    }
}

/// A verifying read-through cache: the RAM pool holds a copy of the bytes the storage pool served, and a copy whose
/// SHA-256 does not match the digest recorded when it was cached is not served (the read falls back to the storage
/// pool). A damaged RAM pool therefore changes no byte the volume reads.
pub struct CacheMedia {
    ram: Arc<dyn MediaAdapter>,
    storage: Arc<dyn MediaAdapter>,
    digests: Mutex<BTreeMap<GenerationRef, [u8; 32]>>,
    /// reads answered from the RAM pool
    hits: AtomicU64,
    /// reads that reached the storage pool
    misses: AtomicU64,
    /// cached copies that were rejected because their bytes did not match
    rejected: AtomicU64,
}

impl CacheMedia {
    /// A cache over `ram` in front of `storage`.
    pub fn new(ram: Arc<dyn MediaAdapter>, storage: Arc<dyn MediaAdapter>) -> CacheMedia {
        CacheMedia { ram, storage, digests: Mutex::new(BTreeMap::new()), hits: AtomicU64::new(0), misses: AtomicU64::new(0), rejected: AtomicU64::new(0) }
    }

    /// How many reads the cache answered from the RAM pool, how many reached the storage pool and how many cached
    /// copies were rejected.
    pub fn counters(&self) -> (u64, u64, u64) {
        (
            self.hits.load(Ordering::Relaxed),
            self.misses.load(Ordering::Relaxed),
            self.rejected.load(Ordering::Relaxed),
        )
    }
}

impl MediaAdapter for CacheMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "twin-cache", durability: self.storage.info().durability, max_in_flight: self.storage.info().max_in_flight }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let receipt = self.storage.put(at, data)?;
        // the cached copy of that generation is stale now
        let _ = self.ram.remove(at);
        self.digests.lock().unwrap_or_else(|p| p.into_inner()).remove(&at);
        Ok(receipt)
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let expected = self.digests.lock().unwrap_or_else(|p| p.into_inner()).get(&at).copied();
        if let Ok(bytes) = self.ram.get(at) {
            if let Some(want) = expected {
                if sha256(&bytes) == want {
                    self.hits.fetch_add(1, Ordering::Relaxed);
                    return Ok(bytes);
                }
                // the cached copy does not match the digest it was cached with: it is not served
                self.rejected.fetch_add(1, Ordering::Relaxed);
            }
        }
        self.misses.fetch_add(1, Ordering::Relaxed);
        let bytes = self.storage.get(at)?;
        let digest = sha256(&bytes);
        self.digests.lock().unwrap_or_else(|p| p.into_inner()).insert(at, digest);
        let _ = self.ram.put(at, &bytes); // the cache is a copy: a failure to cache is not a failure of the read
        Ok(bytes)
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        let cached = self.ram.remove(at);
        self.digests.lock().unwrap_or_else(|p| p.into_inner()).remove(&at);
        self.storage.remove(at).or(cached)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.storage.list()
    }

    fn sync(&self) -> Result<()> {
        self.storage.sync()
    }
}

/// The write-ahead path of the twin: every generation is written to the IO-journal pool first and read back, and
/// only then written to the storage pool. A torn write-ahead copy is detected by the read-back and aborts the write
/// before the storage pool holds the bytes, so the volume's map record is never written and the old data survives.
pub struct JournalMedia {
    journal: Arc<dyn MediaAdapter>,
    storage: Arc<dyn MediaAdapter>,
}

impl JournalMedia {
    /// The write-ahead path over `journal` in front of `storage`.
    pub fn new(journal: Arc<dyn MediaAdapter>, storage: Arc<dyn MediaAdapter>) -> JournalMedia {
        JournalMedia { journal, storage }
    }
}

impl MediaAdapter for JournalMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "twin-io-journal", durability: self.storage.info().durability, max_in_flight: self.storage.info().max_in_flight }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let digest = sha256(data);
        let ahead = self.journal.put(at, data)?;
        if ahead.length != data.len() as u64 || ahead.sha256 != digest {
            return err(ErrorCode::Corruption, format!("the write-ahead copy of {at:?} does not describe the bytes"));
        }
        // read the write-ahead copy back: a torn copy is found here and the write is aborted
        let back = self.journal.get(at)?;
        if back != data {
            return err(ErrorCode::Corruption, format!("the write-ahead copy of {at:?} is torn"));
        }
        let receipt = self.storage.put(at, data)?;
        if receipt.length != data.len() as u64 || receipt.sha256 != digest {
            return err(ErrorCode::Corruption, format!("the receipt of the storage pool for {at:?} does not describe the bytes"));
        }
        Ok(receipt)
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        self.storage.get(at)
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        let ahead = self.journal.remove(at);
        self.storage.remove(at).or(ahead)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.storage.list()
    }

    fn sync(&self) -> Result<()> {
        self.journal.sync()?;
        self.storage.sync()
    }
}

/// One pool of the twin: its replicas, the simulator of every replica and the mirror over them.
struct Pool {
    seed: u64,
    replicas: Vec<Arc<FaultMedia>>,
    simulators: Vec<Arc<DnaSimMedia>>,
    media: Arc<MirrorMedia>,
}

/// The extent the map copy of the volume is stored under in the metadata pool (outside every device extent and
/// outside the binding marker).
const MAP_EXTENT: ExtentId = ExtentId(u64::MAX - 1);

fn map_ref(generation: Generation) -> GenerationRef {
    GenerationRef { extent: MAP_EXTENT, generation }
}

/// The digital twin: six pools, one per role, with deterministic fault injection.
pub struct Twin {
    pools: BTreeMap<PoolRole, Pool>,
    /// the cache in front of the storage pool (the RAM pool)
    ram_media: Arc<CacheMedia>,
    /// the write-ahead path in front of the storage pool (the IO-journal pool)
    io_media: Arc<JournalMedia>,
    /// the sealer of a sealed volume (the twin has none until one is set)
    sealer: Mutex<Option<Arc<dyn Sealer>>>,
    /// the replicas that were taken offline
    offline: Mutex<BTreeMap<(PoolRole, u8), bool>>,
}

impl Twin {
    /// Build the twin. There must be exactly one [`PoolSpec`] per role, and every spec must ask for 1..=3 replicas.
    pub fn new(specs: Vec<PoolSpec>, codec: Arc<dyn Codec>) -> Result<Twin> {
        if specs.len() != PoolRole::ALL.len() {
            return err(
                ErrorCode::InvalidArgument,
                format!("the digital twin needs exactly one pool spec per role ({} roles), got {}", PoolRole::ALL.len(), specs.len()),
            );
        }
        let mut by_role: BTreeMap<PoolRole, PoolSpec> = BTreeMap::new();
        for spec in specs {
            if !(1..=3).contains(&spec.replicas) {
                return err(
                    ErrorCode::InvalidArgument,
                    format!("pool {}: the number of replicas must be 1..=3, got {}", spec.role.as_str(), spec.replicas),
                );
            }
            if by_role.insert(spec.role, spec).is_some() {
                return err(ErrorCode::InvalidArgument, "two pool specs name the same role");
            }
        }
        for role in PoolRole::ALL {
            if !by_role.contains_key(&role) {
                return err(ErrorCode::InvalidArgument, format!("no pool spec for the role {}", role.as_str()));
            }
        }

        let mut pools: BTreeMap<PoolRole, Pool> = BTreeMap::new();
        for role in PoolRole::ALL {
            let spec = by_role.remove(&role).expect("every role has a spec");
            let mut replicas = Vec::new();
            let mut simulators = Vec::new();
            for r in 0..spec.replicas {
                let mut cfg = spec.sim.clone();
                cfg.seed = spec.seed.wrapping_add(u64::from(r)) & ((1u64 << 63) - 1);
                let store = Arc::new(MemoryMedia::new());
                let simulator = Arc::new(DnaSimMedia::new(store, codec.clone(), cfg)?);
                replicas.push(Arc::new(FaultMedia::over(simulator.clone() as Arc<dyn MediaAdapter>)));
                simulators.push(simulator);
            }
            let media = Arc::new(MirrorMedia::new(
                replicas.iter().map(|r| r.clone() as Arc<dyn MediaAdapter>).collect(),
            )?);
            pools.insert(role, Pool { seed: spec.seed, replicas, simulators, media });
        }

        let storage = pools[&PoolRole::Storage].media.clone();
        let ram_media = Arc::new(CacheMedia::new(pools[&PoolRole::Ram].media.clone(), storage.clone()));
        let io_media = Arc::new(JournalMedia::new(pools[&PoolRole::IoJournal].media.clone(), storage));
        Ok(Twin { pools, ram_media, io_media, sealer: Mutex::new(None), offline: Mutex::new(BTreeMap::new()) })
    }

    /// The media adapter a volume or a subsystem uses for a pool: the cache for the RAM pool, the write-ahead path
    /// for the IO-journal pool and the mirror of the pool for every other role.
    pub fn media(&self, role: PoolRole) -> Arc<dyn MediaAdapter> {
        match role {
            PoolRole::Ram => self.ram_media.clone(),
            PoolRole::IoJournal => self.io_media.clone(),
            _ => self.pools[&role].media.clone(),
        }
    }

    /// The mirror of a pool itself, without the cache or the write-ahead path in front of it.
    pub fn pool_media(&self, role: PoolRole) -> Arc<dyn MediaAdapter> {
        self.pools[&role].media.clone()
    }

    /// How many replicas a pool has.
    pub fn replicas(&self, role: PoolRole) -> usize {
        self.pools.get(&role).map(|p| p.replicas.len()).unwrap_or(0)
    }

    /// Inject a fault into one replica of one pool. The other replicas of the pool are untouched.
    pub fn inject(&self, role: PoolRole, replica: u8, rule: Rule) -> Result<()> {
        let pool = self.pools.get(&role).ok_or_else(|| VnxError::new(ErrorCode::InvalidArgument, "no such pool"))?;
        let target = pool.replicas.get(usize::from(replica)).ok_or_else(|| {
            VnxError::new(
                ErrorCode::InvalidArgument,
                format!("pool {} has {} replicas: no replica {}", role.as_str(), pool.replicas.len(), replica),
            )
        })?;
        target.add(rule);
        Ok(())
    }

    /// Take one replica of one pool offline: every operation of that replica fails with BACKEND_UNAVAILABLE and
    /// changes nothing.
    pub fn lose_pool(&self, role: PoolRole, replica: u8) -> Result<()> {
        let pool = self.pools.get(&role).ok_or_else(|| VnxError::new(ErrorCode::InvalidArgument, "no such pool"))?;
        let target = pool.replicas.get(usize::from(replica)).ok_or_else(|| {
            VnxError::new(
                ErrorCode::InvalidArgument,
                format!("pool {} has {} replicas: no replica {}", role.as_str(), pool.replicas.len(), replica),
            )
        })?;
        target.set_offline(true);
        self.offline.lock().unwrap_or_else(|p| p.into_inner()).insert((role, replica), true);
        Ok(())
    }

    /// Bring one replica of one pool back.
    pub fn restore_pool(&self, role: PoolRole, replica: u8) -> Result<()> {
        let pool = self.pools.get(&role).ok_or_else(|| VnxError::new(ErrorCode::InvalidArgument, "no such pool"))?;
        let target = pool.replicas.get(usize::from(replica)).ok_or_else(|| {
            VnxError::new(
                ErrorCode::InvalidArgument,
                format!("pool {} has {} replicas: no replica {}", role.as_str(), pool.replicas.len(), replica),
            )
        })?;
        target.set_offline(false);
        self.offline.lock().unwrap_or_else(|p| p.into_inner()).insert((role, replica), false);
        Ok(())
    }

    /// The replicas of a pool that answered a `get` with bytes that do not match their receipt.
    pub fn damaged_replicas(&self, role: PoolRole) -> Vec<usize> {
        self.pools.get(&role).map(|p| p.media.damaged_replicas()).unwrap_or_default()
    }

    /// The counters of the cache in front of the storage pool: the reads answered from the RAM pool, the reads that
    /// reached the storage pool and the cached copies that were rejected because their bytes did not match.
    pub fn ram_counters(&self) -> (u64, u64, u64) {
        self.ram_media.counters()
    }

    /// The sealer a sealed volume of this twin is opened with.
    pub fn set_sealer(&self, sealer: Arc<dyn Sealer>) {
        *self.sealer.lock().unwrap_or_else(|p| p.into_inner()) = Some(sealer);
    }

    /// The state of the twin: every pool, every replica, the counters of the simulator and the faults that were
    /// injected. Everything in the report is SIMULATED.
    pub fn report(&self) -> Value {
        let offline = self.offline.lock().unwrap_or_else(|p| p.into_inner()).clone();
        let mut pools = BTreeMap::new();
        for (role, pool) in &self.pools {
            let mut entry = BTreeMap::new();
            entry.insert("role".into(), Value::Str(role.as_str().into()));
            entry.insert("seed".into(), Value::Int(pool.seed as i64));
            entry.insert("replicas".into(), Value::Int(pool.replicas.len() as i64));
            let mut replicas = Vec::new();
            for (i, simulator) in pool.simulators.iter().enumerate() {
                let faulty = &pool.replicas[i];
                let trace: Vec<Value> = faulty
                    .trace()
                    .iter()
                    .map(|inj| {
                        obj([
                            ("op", Value::Str(format!("{:?}", inj.op))),
                            ("call", Value::Int(inj.call as i64)),
                            ("fault", Value::Str(format!("{:?}", inj.fault))),
                        ])
                    })
                    .collect();
                let mut r = BTreeMap::new();
                r.insert("simulator".into(), simulator.report());
                r.insert("faults_injected".into(), Value::Int(faulty.injected.load(Ordering::Relaxed) as i64));
                r.insert("offline".into(), Value::Bool(offline.get(&(*role, i as u8)).copied().unwrap_or(false)));
                r.insert("fault_trace".into(), Value::Arr(trace));
                r.insert("evidence".into(), Value::Str("SIMULATED".into()));
                replicas.push(Value::Obj(r));
            }
            entry.insert("replica".into(), Value::Arr(replicas));
            let damaged: Vec<Value> = pool.media.damaged_replicas().into_iter().map(|i| Value::Int(i as i64)).collect();
            entry.insert("damaged_replicas".into(), Value::Arr(damaged));
            entry.insert("damage_events".into(), Value::Int(pool.media.damage_events() as i64));
            entry.insert("evidence".into(), Value::Str("SIMULATED".into()));
            pools.insert(role.as_str().to_string(), Value::Obj(entry));
        }
        obj([
            ("evidence", Value::Str("SIMULATED".into())),
            ("adapter", Value::Str("digital-twin/1".into())),
            ("pools", Value::Obj(pools)),
            (
                "note",
                Value::Str(
                    "simulated pools and injected faults; no DNA was synthesised, stored or sequenced and no number here is a measurement"
                        .into(),
                ),
            ),
        ])
    }

    /// The media a volume on `role` runs over: the sealing adapter when the volume is sealed, the pool otherwise.
    fn volume_media(
        &self,
        role: PoolRole,
        device_id: [u8; 16],
        geometry: Geometry,
        sealed: bool,
    ) -> Result<Arc<dyn MediaAdapter>> {
        if !sealed {
            return Ok(self.media(role));
        }
        let sealer = self
            .sealer
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
            .ok_or_else(|| VnxError::new(ErrorCode::InvalidArgument, "the twin has no sealer: a sealed volume cannot be opened"))?;
        Ok(Arc::new(SealedMedia::new(self.pool_media(role), sealer, device_id, geometry.extent_bytes())))
    }

    /// Copy the map of the volume (its journal) into the metadata pool. Every copy gets its own generation, so a
    /// copy that was published before a crash is never overwritten.
    pub fn publish_map(&self, dir: &Path, device_id: [u8; 16]) -> Result<()> {
        let bytes = std::fs::read(dir.join(JOURNAL_FILE)).map_err(|e| {
            VnxError::new(ErrorCode::BackendUnavailable, format!("read the journal of {}: {e}", dir.display()))
        })?;
        let mut payload = Vec::with_capacity(16 + bytes.len());
        payload.extend_from_slice(&device_id);
        payload.extend_from_slice(&bytes);
        let generation = self.next_map_generation()?;
        self.pool_media(PoolRole::Metadata).put(map_ref(generation), &payload)?;
        Ok(())
    }

    /// The generation the next map copy gets: one above the highest one the metadata pool already holds.
    fn next_map_generation(&self) -> Result<Generation> {
        let media = self.pool_media(PoolRole::Metadata);
        let highest = media
            .list()?
            .iter()
            .filter(|at| at.extent == MAP_EXTENT)
            .map(|at| at.generation.get())
            .max()
            .unwrap_or(0);
        Generation::new(highest + 1)
    }

    /// Read the map copy back from the metadata pool: the device id and the bytes of the journal.
    pub fn read_map(&self) -> Result<([u8; 16], Vec<u8>)> {
        let media = self.pool_media(PoolRole::Metadata);
        let generation = self.next_map_generation()?.get().checked_sub(1).unwrap_or(0);
        if generation == 0 {
            return err(ErrorCode::NotFound, "the metadata pool holds no map of a volume");
        }
        let bytes = media.get(map_ref(Generation::new(generation)?))?;
        if bytes.len() < 16 {
            return err(ErrorCode::Corruption, "the map copy in the metadata pool is too short to hold a device id");
        }
        let (id, rest) = bytes.split_at(16);
        let id: [u8; 16] = id.try_into().expect("16 bytes");
        Ok((id, rest.to_vec()))
    }

    /// Create a volume on one of the pools and publish its map into the metadata pool.
    pub fn create_volume(
        &self,
        dir: &Path,
        role: PoolRole,
        geometry: Geometry,
        device_id: [u8; 16],
        flags: u32,
        cfg: VolumeConfig,
    ) -> Result<Volume> {
        let media = self.volume_media(role, device_id, geometry, flags & FLAG_SEALED != 0)?;
        let volume = Volume::create(dir, geometry, device_id, flags, media, cfg)?;
        self.publish_map(dir, device_id)?;
        Ok(volume)
    }

    /// Open a volume: the metadata pool must be reachable (the map is confirmed there) and the map must belong to
    /// the device the journal names. A volume whose map cannot be confirmed is not opened, and no byte of it is
    /// served.
    pub fn open_volume(&self, dir: &Path, role: PoolRole, cfg: VolumeConfig) -> Result<(Volume, RecoveryReport)> {
        let (map_device, _bytes) = self.read_map()?;
        let raw = std::fs::read(dir.join(JOURNAL_FILE)).map_err(|e| {
            VnxError::new(ErrorCode::BackendUnavailable, format!("read the journal of {}: {e}", dir.display()))
        })?;
        let parsed = crate::journal::parse(&raw)?;
        let superblock = parsed
            .records
            .iter()
            .find_map(|r| match r {
                Record::Superblock(sb) => Some(sb),
                _ => None,
            })
            .ok_or_else(|| VnxError::new(ErrorCode::Corruption, "the journal of the volume has no superblock"))?;
        if superblock.device_id != map_device {
            return err(ErrorCode::InvalidArgument, "the metadata pool holds the map of another device");
        }
        let media = self.volume_media(role, superblock.device_id, superblock.geometry, superblock.flags & FLAG_SEALED != 0)?;
        Volume::open(dir, media, cfg)
    }

    /// Write one extent of a volume and publish the new map into the metadata pool.
    pub fn write_extent(
        &self,
        volume: &Volume,
        dir: &Path,
        extent: ExtentId,
        data: &[u8],
        now_ms: i64,
    ) -> Result<CommitOutcome> {
        let outcome = volume.write_extent(extent, data, now_ms)?;
        self.publish_map(dir, volume.device_id())?;
        Ok(outcome)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::codec::ToyCodec;
    use crate::fault::{Fault, MediaOp};

    /// The error of a call that must fail (the twin does not implement `Debug`).
    fn expect_err<T>(r: Result<T>) -> VnxError {
        match r {
            Ok(_) => panic!("the call was expected to fail"),
            Err(e) => e,
        }
    }

    fn spec(role: PoolRole, seed: u64, replicas: u8) -> PoolSpec {
        PoolSpec::new(role, seed, DnaSimConfig::default(), replicas)
    }

    fn specs() -> Vec<PoolSpec> {
        PoolRole::ALL
            .iter()
            .enumerate()
            .map(|(i, role)| spec(*role, 1000 + i as u64, 3))
            .collect()
    }

    fn twin() -> Twin {
        Twin::new(specs(), Arc::new(ToyCodec)).unwrap()
    }

    #[test]
    fn a_twin_needs_exactly_one_spec_per_role() {
        let mut wrong = specs();
        wrong.pop();
        let e = expect_err(Twin::new(wrong, Arc::new(ToyCodec)));
        assert_eq!(e.code, ErrorCode::InvalidArgument);
        assert!(e.message.contains("one pool spec per role"), "{e}");
    }

    #[test]
    fn a_twin_refuses_two_specs_for_the_same_role() {
        let mut wrong = specs();
        wrong[1].role = PoolRole::Storage;
        let e = expect_err(Twin::new(wrong, Arc::new(ToyCodec)));
        assert_eq!(e.code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn a_pool_refuses_more_than_three_replicas() {
        let mut wrong = specs();
        wrong[0].replicas = 4;
        let e = expect_err(Twin::new(wrong, Arc::new(ToyCodec)));
        assert_eq!(e.code, ErrorCode::InvalidArgument);
        assert!(e.message.contains("1..=3"), "{e}");
    }

    #[test]
    fn every_role_has_a_pool_and_a_media_adapter() {
        let t = twin();
        for role in PoolRole::ALL {
            assert_eq!(t.replicas(role), 3);
            assert_eq!(t.media(role).list().unwrap().len(), 0);
        }
        assert_eq!(PoolRole::Storage.as_str(), "storage");
        assert_eq!(PoolRole::Ram.as_str(), "ram");
        assert_eq!(PoolRole::IoJournal.as_str(), "io-journal");
    }

    #[test]
    fn a_fault_reaches_one_replica_only() {
        let t = twin();
        t.inject(PoolRole::Storage, 1, Rule::always(MediaOp::Get, Fault::Unavailable)).unwrap();
        let at = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
        assert!(t.pool_media(PoolRole::Storage).get(at).is_err());
        let e = t.inject(PoolRole::Storage, 7, Rule::always(MediaOp::Get, Fault::Unavailable)).unwrap_err();
        assert_eq!(e.code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn a_lost_replica_is_offline_and_can_come_back() {
        let t = twin();
        let at = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
        // one replica of three is lost: the pool still takes the put
        t.lose_pool(PoolRole::Metadata, 0).unwrap();
        assert!(t.pool_media(PoolRole::Metadata).put(at, b"x").is_ok());
        // every replica is lost: the pool is unavailable
        t.lose_pool(PoolRole::Metadata, 1).unwrap();
        t.lose_pool(PoolRole::Metadata, 2).unwrap();
        let e = expect_err(t.pool_media(PoolRole::Metadata).put(at, b"y"));
        assert_eq!(e.code, ErrorCode::BackendUnavailable);
        // and it comes back
        t.restore_pool(PoolRole::Metadata, 0).unwrap();
        t.restore_pool(PoolRole::Metadata, 1).unwrap();
        t.restore_pool(PoolRole::Metadata, 2).unwrap();
        assert!(t.pool_media(PoolRole::Metadata).put(at, b"z").is_ok());
    }

    #[test]
    fn a_mirror_puts_to_every_replica_and_gets_from_a_matching_one() {
        let t = twin();
        let at = GenerationRef { extent: ExtentId(1), generation: Generation::FIRST };
        t.pool_media(PoolRole::Metadata).put(at, b"payload").unwrap();
        // one replica rots its answer: the mirror serves the bytes of another one and reports the damage
        t.inject(PoolRole::Metadata, 0, Rule::always(MediaOp::Get, Fault::BitRot)).unwrap();
        assert_eq!(t.pool_media(PoolRole::Metadata).get(at).unwrap(), b"payload");
        assert_eq!(t.damaged_replicas(PoolRole::Metadata), vec![0]);
    }

    #[test]
    fn a_mirror_with_one_damaged_replica_never_serves_wrong_bytes() {
        let mut one = specs();
        // the metadata pool with a single replica: with no other copy the mirror has nothing to fall back on
        let idx = one.iter().position(|s| s.role == PoolRole::Metadata).unwrap();
        one[idx].replicas = 1;
        let single = Twin::new(one, Arc::new(ToyCodec)).unwrap();
        let at = GenerationRef { extent: ExtentId(1), generation: Generation::FIRST };
        single.pool_media(PoolRole::Metadata).put(at, b"payload").unwrap();
        single.inject(PoolRole::Metadata, 0, Rule::always(MediaOp::Get, Fault::BitRot)).unwrap();
        let e = expect_err(single.pool_media(PoolRole::Metadata).get(at));
        assert_eq!(e.code, ErrorCode::Corruption);

        // with a second replica the mirror serves the bytes of the last write and reports the damaged one
        let t = twin();
        t.pool_media(PoolRole::Metadata).put(at, b"payload").unwrap();
        t.inject(PoolRole::Metadata, 0, Rule::always(MediaOp::Get, Fault::BitRot)).unwrap();
        assert_eq!(t.pool_media(PoolRole::Metadata).get(at).unwrap(), b"payload");
        assert_eq!(t.damaged_replicas(PoolRole::Metadata), vec![0]);
    }

    #[test]
    fn the_report_is_labelled_simulated() {
        let report = twin().report();
        assert_eq!(report.as_obj().unwrap()["evidence"], Value::Str("SIMULATED".into()));
        let pools = report.as_obj().unwrap()["pools"].as_obj().unwrap();
        assert_eq!(pools.len(), PoolRole::ALL.len());
        for (name, pool) in pools {
            assert_eq!(pool.as_obj().unwrap()["evidence"], Value::Str("SIMULATED".into()));
            match &pool.as_obj().unwrap()["replica"] {
                Value::Arr(replicas) => assert_eq!(replicas.len(), 3, "{name}"),
                other => panic!("{name}: the replicas are not a list: {other:?}"),
            }
        }
    }
}
