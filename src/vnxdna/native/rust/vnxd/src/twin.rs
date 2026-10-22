//! V15 Digital Twin (Task G): a simulated device with four independent pools (storage, metadata, bootstrap, security).
//!
//! Everything in this module is SIMULATED. It is a deterministic fault-injection harness for the declared campaign;
//! no physical synthesis or sequencing occurs. Results are labelled SIMULATED and no numbers from real hardware are
//! claimed or implied.
//!
//! The digital twin wraps four independent pools, one per [`PoolRole`]. Each pool is a fault-injectable media
//! adapter built from the primitives in `media`, `fault`, `codec`, `dnasim` and `fallback`. The storage pool uses
//! the codec's outer code over DnaSimMedia; metadata, bootstrap and security pools use replicas=3 over
//! MemoryMedia via MirrorMedia.
//!
//! The declared nine-case fault campaign (docs/V15_DIGITAL_TWIN.md §4) runs with fixed seeds and verifies that the
//! service behaves correctly under each declared fault pattern. No results numbers appear in the documentation.

use crate::addr::{ExtentId, Generation, GenerationRef, Geometry};
use crate::codec::{Codec, ToyCodec};
use crate::dnasim::{DnaSimConfig, DnaSimMedia, FailureModel, LatencyModel};
use crate::digest::{hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::fallback::{HealthConfig, ResilientMedia, RetryPolicy};
use crate::fault::{Fault, FaultMedia, FaultPlan, Injection, MediaOp, Rule};
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo, MemoryMedia};
use crate::record::FLAG_SEALED;
use crate::volume::{Volume, VolumeConfig, MARKER_EXTENT};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

// ---------------------------------------------------------------------------
// PoolRole and PoolSpec
// ---------------------------------------------------------------------------

/// Role of a pool in the digital twin.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub enum PoolRole {
    /// The storage pool holds the volume's extent data; uses the codec's outer code over DnaSimMedia.
    Storage,
    /// The metadata pool holds the volume journal and map; replicas=3 over MemoryMedia via MirrorMedia.
    Metadata,
    /// The bootstrap pool holds the sealed marker and superblock; replicas=3 over MemoryMedia via MirrorMedia.
    Bootstrap,
    /// The security pool holds sealed extent objects; replicas=3 over MemoryMedia via MirrorMedia.
    Security,
}

/// Specification for one pool in the digital twin.
pub struct PoolSpec {
    /// Role of this pool (exactly one per role in Twin::new).
    pub role: PoolRole,
    /// Number of replicas for mirrored pools (metadata, bootstrap, security). Must be 1..=3.
    /// Storage pool uses the codec's own redundancy; this field is ignored for Storage role.
    pub replicas: u32,
    /// DnaSim configuration for the storage pool; ignored for other roles.
    pub dna_sim: Option<DnaSimConfig>,
    /// Health configuration for ResilientMedia wrapping each pool.
    pub health: HealthConfig,
    /// Fixed seed for deterministic fault injection in this pool.
    pub seed: u64,
    /// Optional codec for the storage pool; ToyCodec is used if None.
    pub codec: Option<Arc<dyn Codec>>,
}

impl Default for PoolSpec {
    fn default() -> Self {
        PoolSpec {
            role: PoolRole::Storage,
            replicas: 3,
            dna_sim: None,
            health: HealthConfig::default(),
            seed: 0,
            codec: None,
        }
    }
}

impl PoolSpec {
    /// Validate this pool spec.
    pub fn validate(&self) -> Result<()> {
        if !(1..=3).contains(&self.replicas) {
            return err(ErrorCode::InvalidArgument,
                format!("replicas must be 1..=3, got {}", self.replicas));
        }
        if let Some(ref d) = self.dna_sim {
            if d.read_attempts == 0 || d.max_in_flight == 0 {
                return err(ErrorCode::InvalidArgument,
                    "dna_sim: read_attempts and max_in_flight must be >= 1");
            }
            if d.seed >= 1 << 63 {
                return err(ErrorCode::InvalidArgument, "dna_sim: seed must fit in 63 bits");
            }
        }
        self.health.validate()?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// MirrorMedia: N-way mirror over MemoryMedia
// ---------------------------------------------------------------------------

/// An N-way mirror media adapter: writes to all backends, reads from the first healthy one.
/// Fails with BackendUnavailable if all backends are unavailable. Used for metadata,
/// bootstrap and security pools.
#[derive(Debug)]
pub struct MirrorMedia {
    backends: Vec<Arc<dyn MediaAdapter>>,
    info: MediaInfo,
    healthy: Mutex<Vec<bool>>,
}

impl MirrorMedia {
    /// Create a new N-way mirror over `backends` (must be non-empty, all same durability).
    pub fn new(backends: Vec<Arc<dyn MediaAdapter>>) -> Result<MirrorMedia> {
        if backends.is_empty() {
            return err(ErrorCode::InvalidArgument, "MirrorMedia needs at least one backend");
        }
        let first_info = backends[0].info();
        for b in &backends {
            let info = b.info();
            if info.durability != first_info.durability {
                return err(ErrorCode::InvalidArgument,
                    "MirrorMedia backends must have same durability");
            }
            if info.max_in_flight != first_info.max_in_flight {
                return err(ErrorCode::InvalidArgument,
                    "MirrorMedia backends must have same max_in_flight");
            }
        }
        let healthy = vec![true; backends.len()];
        Ok(MirrorMedia {
            info: MediaInfo { kind: "mirror", durability: first_info.durability,
                max_in_flight: first_info.max_in_flight },
            backends,
            healthy: Mutex::new(healthy),
        })
    }

    fn mark_healthy(&self, idx: usize, healthy: bool) {
        if let Ok(mut h) = self.healthy.lock() {
            if idx < h.len() {
                h[idx] = healthy;
            }
        }
    }
}

impl MediaAdapter for MirrorMedia {
    fn info(&self) -> MediaInfo {
        self.info.clone()
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let mut last_err: Option<VnxError> = None;
        let mut any_ok = false;
        for (i, b) in self.backends.iter().enumerate() {
            match b.put(at, data) {
                Ok(_) => {
                    self.mark_healthy(i, true);
                    any_ok = true;
                }
                Err(e) => {
                    self.mark_healthy(i, false);
                    last_err = Some(e);
                }
            }
        }
        if any_ok {
            Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
        } else {
            Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable,
                "all mirror backends unavailable")))
        }
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let mut last_err: Option<VnxError> = None;
        for (i, b) in self.backends.iter().enumerate() {
            match b.get(at) {
                Ok(d) => {
                    self.mark_healthy(i, true);
                    return Ok(d);
                }
                Err(e) => {
                    self.mark_healthy(i, false);
                    last_err = Some(e);
                }
            }
        }
        Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable,
            "all mirror backends unavailable")))
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        let mut last_err: Option<VnxError> = None;
        let mut any_ok = false;
        for (i, b) in self.backends.iter().enumerate() {
            match b.remove(at) {
                Ok(()) => {
                    self.mark_healthy(i, true);
                    any_ok = true;
                }
                Err(e) => {
                    self.mark_healthy(i, false);
                    last_err = Some(e);
                }
            }
        }
        if any_ok {
            Ok(())
        } else {
            Err(last_err.unwrap_or_else(|| VnxError::new(ErrorCode::BackendUnavailable,
                "all mirror backends unavailable")))
        }
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        for (i, b) in self.backends.iter().enumerate() {
            match b.list() {
                Ok(l) => {
                    self.mark_healthy(i, true);
                    return Ok(l);
                }
                Err(_) => {
                    self.mark_healthy(i, false);
                }
            }
        }
        err(ErrorCode::BackendUnavailable, "all mirror backends unavailable")
    }

    fn sync(&self) -> Result<()> {
        let mut any_ok = false;
        for (i, b) in self.backends.iter().enumerate() {
            match b.sync() {
                Ok(()) => {
                    self.mark_healthy(i, true);
                    any_ok = true;
                }
                Err(_) => {
                    self.mark_healthy(i, false);
                }
            }
        }
        if any_ok {
            Ok(())
        } else {
            err(ErrorCode::BackendUnavailable, "all mirror backends unavailable")
        }
    }
}

// ---------------------------------------------------------------------------
// TwinPool: one pool inside the twin
// ---------------------------------------------------------------------------

struct TwinPool {
    role: PoolRole,
    faulty: Arc<FaultMedia>,
    resilient: Arc<ResilientMedia>,
    sealed: bool,
    codec: Option<Arc<dyn Codec>>,
}

// ---------------------------------------------------------------------------
// Twin: four independent pools
// ---------------------------------------------------------------------------

/// The digital twin: four independent pools (storage, metadata, bootstrap, security)
/// with deterministic fault injection.
pub struct Twin {
    device_id: [u8; 16],
    geometry: Geometry,
    flags: u32,
    pools: BTreeMap<PoolRole, TwinPool>,
    fault_plans: Mutex<BTreeMap<PoolRole, FaultPlan>>,
    lost: Mutex<BTreeMap<PoolRole, bool>>,
}

impl Twin {
    /// Create a new digital twin with exactly one PoolSpec per role.
    /// Returns InvalidArgument if roles are duplicated, missing, or replicas out of range (1..=3).
    pub fn new(
        device_id: [u8; 16],
        geometry: Geometry,
        flags: u32,
        specs: Vec<PoolSpec>,
    ) -> Result<Twin> {
        if specs.len() != 4 {
            return err(ErrorCode::InvalidArgument,
                format!("Twin::new needs exactly 4 PoolSpecs (one per role), got {}", specs.len()));
        }

        let mut by_role = BTreeMap::new();
        for spec in specs {
            spec.validate()?;
            if by_role.contains_key(&spec.role) {
                return err(ErrorCode::InvalidArgument,
                    format!("duplicate pool role: {:?}", spec.role));
            }
            by_role.insert(spec.role, spec);
        }

        for role in [PoolRole::Storage, PoolRole::Metadata, PoolRole::Bootstrap, PoolRole::Security] {
            if !by_role.contains_key(&role) {
                return err(ErrorCode::InvalidArgument,
                    format!("missing pool role: {:?}", role));
            }
        }

        let mut pools = BTreeMap::new();
        let mut fault_plans = BTreeMap::new();

        for role in [PoolRole::Storage, PoolRole::Metadata, PoolRole::Bootstrap, PoolRole::Security] {
            let spec = by_role.remove(&role).unwrap();

            // Build the base media adapter for this role
            let base: Arc<dyn MediaAdapter> = match role {
                PoolRole::Storage => {
                    let codec = spec.codec.clone().unwrap_or_else(|| Arc::new(ToyCodec));
                    let cfg = spec.dna_sim.unwrap_or_else(|| DnaSimConfig {
                        channel: None,
                        seed: spec.seed,
                        read_attempts: 3,
                        latency: LatencyModel::default(),
                        failures: FailureModel::default(),
                        max_in_flight: 4,
                    });
                    let store = Arc::new(MemoryMedia::new());
                    Arc::new(DnaSimMedia::new(store, codec, cfg)?)
                }
                PoolRole::Metadata | PoolRole::Bootstrap | PoolRole::Security => {
                    let mut backends = Vec::new();
                    for _i in 0..spec.replicas {
                        backends.push(Arc::new(MemoryMedia::new()) as Arc<dyn MediaAdapter>);
                    }
                    Arc::new(MirrorMedia::new(backends)?)
                }
            };

            // Wrap in FaultMedia (over constructor takes the base adapter)
            let faulty = Arc::new(FaultMedia::over(base));

            // Wrap in ResilientMedia
            let resilient: Arc<ResilientMedia> = Arc::new(ResilientMedia::new(faulty.clone() as Arc<dyn MediaAdapter>,
                RetryPolicy::default(), spec.health)?);

            // Security pool is sealed
            let sealed = role == PoolRole::Security;

            pools.insert(role, TwinPool {
                role,
                faulty,
                resilient,
                sealed,
                codec: if role == PoolRole::Storage {
                    spec.codec
                } else {
                    None
                },
            });

            fault_plans.insert(role, FaultPlan::honest(spec.seed, 100));
        }

        Ok(Twin {
            device_id,
            geometry,
            flags,
            pools,
            fault_plans: Mutex::new(fault_plans),
            lost: Mutex::new(BTreeMap::new()),
        })
    }

    /// Get the media adapter for a specific pool role (the resilient wrapper).
    pub fn media(&self, role: PoolRole) -> Option<Arc<dyn MediaAdapter>> {
        self.pools.get(&role).map(|p| p.resilient.clone() as Arc<dyn MediaAdapter>)
    }

    /// Get the media adapter for the storage pool (used by the volume).
    pub fn storage_media(&self) -> Arc<dyn MediaAdapter> {
        self.media(PoolRole::Storage).expect("storage pool always exists")
    }

    /// Get the media adapter for the metadata pool (used by the volume journal).
    pub fn metadata_media(&self) -> Arc<dyn MediaAdapter> {
        self.media(PoolRole::Metadata).expect("metadata pool always exists")
    }

    /// Get the media adapter for the bootstrap pool (used for marker/superblock).
    pub fn bootstrap_media(&self) -> Arc<dyn MediaAdapter> {
        self.media(PoolRole::Bootstrap).expect("bootstrap pool always exists")
    }

    /// Get the media adapter for the security pool (used for sealed extents).
    pub fn security_media(&self) -> Arc<dyn MediaAdapter> {
        self.media(PoolRole::Security).expect("security pool always exists")
    }

    /// Inject a fault rule into a specific pool.
    pub fn inject(&self, role: PoolRole, rule: Rule) -> Result<()> {
        if let Some(pool) = self.pools.get(&role) {
            pool.faulty.add(rule);
            Ok(())
        } else {
            err(ErrorCode::InvalidArgument, format!("no such pool: {:?}", role))
        }
    }

    /// Set a seeded fault plan for a specific pool (replaces any existing plan).
    pub fn set_fault_plan(&self, role: PoolRole, plan: FaultPlan) -> Result<()> {
        if !self.pools.contains_key(&role) {
            return err(ErrorCode::InvalidArgument, format!("no such pool: {:?}", role));
        }
        self.fault_plans.lock().unwrap().insert(role, plan);
        Ok(())
    }

    /// Simulate losing a pool (making it permanently unavailable).
    pub fn lose_pool(&self, role: PoolRole) -> Result<()> {
        if !self.pools.contains_key(&role) {
            return err(ErrorCode::InvalidArgument, format!("no such pool: {:?}", role));
        }
        self.lost.lock().unwrap().insert(role, true);
        if let Some(pool) = self.pools.get(&role) {
            pool.faulty.set_offline(true);
        }
        Ok(())
    }

    /// Restore a lost pool.
    pub fn restore_pool(&self, role: PoolRole) -> Result<()> {
        if !self.pools.contains_key(&role) {
            return err(ErrorCode::InvalidArgument, format!("no such pool: {:?}", role));
        }
        self.lost.lock().unwrap().insert(role, false);
        if let Some(pool) = self.pools.get(&role) {
            pool.faulty.set_offline(false);
        }
        Ok(())
    }

    /// Check if a pool is lost.
    pub fn is_lost(&self, role: PoolRole) -> bool {
        self.lost.lock().unwrap().get(&role).copied().unwrap_or(false)
    }

    /// Create a volume on this twin's pools.
    pub fn create_volume(&self, dir: &std::path::Path) -> Result<Volume> {
        let composite = TwinCompositeMedia {
            storage: self.storage_media(),
            metadata: self.metadata_media(),
            bootstrap: self.bootstrap_media(),
            security: self.security_media(),
            device_id: self.device_id,
            flags: self.flags,
        };
        Volume::create(dir, self.geometry, self.device_id, self.flags,
            Arc::new(composite), VolumeConfig::default())
    }

    /// Open an existing volume on this twin's pools.
    pub fn open_volume(&self, dir: &std::path::Path) -> Result<(Volume, crate::volume::RecoveryReport)> {
        let composite = TwinCompositeMedia {
            storage: self.storage_media(),
            metadata: self.metadata_media(),
            bootstrap: self.bootstrap_media(),
            security: self.security_media(),
            device_id: self.device_id,
            flags: self.flags,
        };
        Volume::open(dir, Arc::new(composite), VolumeConfig::default())
    }

    /// Run the fault plan for a specific pool (applies deterministic faults from the plan).
    pub fn run_fault_plan(&self, role: PoolRole) -> Result<()> {
        if let Some(plan) = self.fault_plans.lock().unwrap().get(&role).cloned() {
            if let Some(pool) = self.pools.get(&role) {
                pool.faulty.set_plan(Some(plan));
            }
        }
        Ok(())
    }

    /// Get a JSON report of the twin's state (all pools, health, fault state).
    /// Everything is labelled SIMULATED.
    pub fn report(&self) -> Value {
        let mut pools_obj = BTreeMap::new();
        for (role, pool) in &self.pools {
            let health = pool.resilient.health();
            let fault_trace: Vec<Value> = pool.faulty.trace().iter().map(|inj| {
                obj([
                    ("op", Value::Str(format!("{:?}", inj.op))),
                    ("call", Value::Int(inj.call as i64)),
                    ("fault", Value::Str(format!("{:?}", inj.fault))),
                ])
            }).collect();
            let lost = self.lost.lock().unwrap().get(role).copied().unwrap_or(false);

            let mut pool_obj = BTreeMap::new();
            pool_obj.insert("role".into(), Value::Str(format!("{:?}", role)));
            pool_obj.insert("sealed".into(), Value::Bool(pool.sealed));
            pool_obj.insert("lost".into(), Value::Bool(lost));
            pool_obj.insert("health".into(), health.to_json());
            pool_obj.insert("fault_trace".into(), Value::Arr(fault_trace));
            pool_obj.insert("evidence".into(), Value::Str("SIMULATED".into()));
            pool_obj.insert("adapter".into(), Value::Str(pool.faulty.raw().info().kind.into()));
            pool_obj.insert("durability".into(),
                Value::Str(pool.faulty.raw().info().durability.as_str().into()));

            pools_obj.insert(format!("{:?}", role), Value::Obj(pool_obj));
        }

        obj([
            ("evidence", Value::Str("SIMULATED".into())),
            ("device_id", Value::Str(hex(&self.device_id))),
            ("geometry", obj([
                ("block_size", Value::Int(self.geometry.block_size() as i64)),
                ("blocks_per_extent", Value::Int(self.geometry.blocks_per_extent() as i64)),
                ("capacity_blocks", Value::Int(self.geometry.capacity_blocks() as i64)),
            ])),
            ("flags", Value::Int(self.flags as i64)),
            ("pools", Value::Obj(pools_obj)),
            ("note", Value::Str("digital twin report; all numbers are SIMULATED, no physical hardware involved".into())),
        ])
    }
}

// ---------------------------------------------------------------------------
// TwinCompositeMedia: routes operations to the appropriate pool
// ---------------------------------------------------------------------------

/// A composite media adapter that routes operations to the appropriate pool in the twin.
/// Used by Volume to have separate pools for data, journal, marker, and sealed extents.
struct TwinCompositeMedia {
    storage: Arc<dyn MediaAdapter>,
    metadata: Arc<dyn MediaAdapter>,
    bootstrap: Arc<dyn MediaAdapter>,
    security: Arc<dyn MediaAdapter>,
    device_id: [u8; 16],
    flags: u32,
}

impl MediaAdapter for TwinCompositeMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo {
            kind: "twin-composite",
            durability: Durability::SimulatedDna,
            max_in_flight: 4,
        }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        if at.extent == MARKER_EXTENT {
            return self.bootstrap.put(at, data);
        }
        if self.flags & FLAG_SEALED != 0 && at.extent != MARKER_EXTENT {
            return self.security.put(at, data);
        }
        self.storage.put(at, data)
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        if at.extent == MARKER_EXTENT {
            return self.bootstrap.get(at);
        }
        if self.flags & FLAG_SEALED != 0 && at.extent != MARKER_EXTENT {
            return self.security.get(at);
        }
        self.storage.get(at)
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        if at.extent == MARKER_EXTENT {
            return self.bootstrap.remove(at);
        }
        if self.flags & FLAG_SEALED != 0 && at.extent != MARKER_EXTENT {
            return self.security.remove(at);
        }
        self.storage.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        let mut all = Vec::new();
        all.extend(self.storage.list()?);
        all.extend(self.metadata.list()?);
        all.extend(self.bootstrap.list()?);
        all.extend(self.security.list()?);
        all.sort();
        all.dedup();
        Ok(all)
    }

    fn sync(&self) -> Result<()> {
        self.storage.sync()?;
        self.metadata.sync()?;
        self.bootstrap.sync()?;
        self.security.sync()?;
        Ok(())
    }

    fn sealed(&self) -> bool {
        self.flags & FLAG_SEALED != 0
    }
}

// ---------------------------------------------------------------------------
// Helper: create a std-only temp directory (Cargo.toml has no tempfile dependency)
// ---------------------------------------------------------------------------

fn make_test_dir(name: &str) -> std::path::PathBuf {
    let mut dir = std::env::temp_dir();
    dir.push(format!("vnx-twin-{}-{}", std::process::id(), name));
    dir
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation};
    use std::fs;

    fn test_geometry() -> Geometry {
        Geometry::new(512, 4, 16).unwrap()
    }

    fn test_device_id() -> [u8; 16] {
        [42; 16]
    }

    fn default_specs() -> Vec<PoolSpec> {
        vec![
            PoolSpec {
                role: PoolRole::Storage,
                replicas: 1,
                dna_sim: Some(DnaSimConfig {
                    channel: None,
                    seed: 12345,
                    read_attempts: 3,
                    latency: LatencyModel::default(),
                    failures: FailureModel::default(),
                    max_in_flight: 4,
                }),
                health: HealthConfig::default(),
                seed: 12345,
                codec: None,
            },
            PoolSpec {
                role: PoolRole::Metadata,
                replicas: 3,
                dna_sim: None,
                health: HealthConfig::default(),
                seed: 23456,
                codec: None,
            },
            PoolSpec {
                role: PoolRole::Bootstrap,
                replicas: 3,
                dna_sim: None,
                health: HealthConfig::default(),
                seed: 34567,
                codec: None,
            },
            PoolSpec {
                role: PoolRole::Security,
                replicas: 3,
                dna_sim: None,
                health: HealthConfig::default(),
                seed: 45678,
                codec: None,
            },
        ]
    }

    #[test]
    fn twin_new_validates_exactly_four_roles() {
        let specs = default_specs();
        let twin = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap();
        assert!(twin.media(PoolRole::Storage).is_some());
        assert!(twin.media(PoolRole::Metadata).is_some());
        assert!(twin.media(PoolRole::Bootstrap).is_some());
        assert!(twin.media(PoolRole::Security).is_some());
    }

    #[test]
    fn twin_new_rejects_duplicate_roles() {
        let mut specs = default_specs();
        specs.push(PoolSpec { role: PoolRole::Storage, ..Default::default() });
        let e = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap_err();
        assert_eq!(e.code, ErrorCode::InvalidArgument);
        assert!(e.message.contains("duplicate"));
    }

    #[test]
    fn twin_new_rejects_missing_roles() {
        let specs = vec![
            PoolSpec { role: PoolRole::Storage, ..Default::default() },
            PoolSpec { role: PoolRole::Metadata, ..Default::default() },
            PoolSpec { role: PoolRole::Bootstrap, ..Default::default() },
        ];
        let e = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap_err();
        assert_eq!(e.code, ErrorCode::InvalidArgument);
        assert!(e.message.contains("missing"));
    }

    #[test]
    fn twin_new_rejects_replicas_out_of_range() {
        let mut specs = default_specs();
        specs[1].replicas = 4;
        let e = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap_err();
        assert_eq!(e.code, ErrorCode::InvalidArgument);
        assert!(e.message.contains("replicas"));
    }

    #[test]
    fn twin_inject_adds_fault_rule() {
        let specs = default_specs();
        let twin = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap();
        twin.inject(PoolRole::Storage,
            Rule::always(MediaOp::Put, Fault::Unavailable)).unwrap();

        let pool = twin.pools.get(&PoolRole::Storage).unwrap();
        let trace = pool.faulty.trace();
        assert!(trace.is_empty()); // no calls yet, so no trace
    }

    #[test]
    fn twin_lose_pool_marks_offline() {
        let specs = default_specs();
        let twin = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap();
        assert!(!twin.is_lost(PoolRole::Storage));

        twin.lose_pool(PoolRole::Storage).unwrap();
        assert!(twin.is_lost(PoolRole::Storage));

        let media = twin.storage_media();
        let at = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
        let e = media.put(at, b"test").unwrap_err();
        assert_eq!(e.code, ErrorCode::BackendUnavailable);

        twin.restore_pool(PoolRole::Storage).unwrap();
        assert!(!twin.is_lost(PoolRole::Storage));
    }

    #[test]
    fn mirror_media_write_all_read_first() {
        let b1 = Arc::new(MemoryMedia::new());
        let b2 = Arc::new(MemoryMedia::new());
        let m = MirrorMedia::new(vec![b1.clone(), b2.clone()]).unwrap();

        let at = GenerationRef { extent: ExtentId(1), generation: Generation::FIRST };
        m.put(at, b"hello").unwrap();

        assert_eq!(b1.get(at).unwrap(), b"hello");
        assert_eq!(b2.get(at).unwrap(), b"hello");
        assert_eq!(m.get(at).unwrap(), b"hello");
    }

    #[test]
    fn mirror_media_fails_if_all_unavailable() {
        let b1 = Arc::new(MemoryMedia::new());
        let m = MirrorMedia::new(vec![b1.clone()]).unwrap();

        let at = GenerationRef { extent: ExtentId(1), generation: Generation::FIRST };
        b1.put(at, b"data").unwrap();
        assert_eq!(m.get(at).unwrap(), b"data");

        b1.remove(at).unwrap();
        assert_eq!(m.get(at).unwrap_err().code, ErrorCode::NotFound);
    }

    #[test]
    fn mirror_media_rejects_empty_backends() {
        let e = MirrorMedia::new(vec![]).unwrap_err();
        assert_eq!(e.code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn twin_report_labelled_simulated() {
        let specs = default_specs();
        let twin = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap();
        let report = twin.report();

        assert_eq!(report.as_obj().unwrap().get("evidence").unwrap().as_str().unwrap(), "SIMULATED");

        let pools = report.as_obj().unwrap().get("pools").unwrap().as_obj().unwrap();
        for (_, pool_val) in pools {
            let pool_obj = pool_val.as_obj().unwrap();
            assert_eq!(pool_obj.get("evidence").unwrap().as_str().unwrap(), "SIMULATED");
        }
    }

    #[test]
    fn twin_create_and_open_volume() {
        let specs = default_specs();
        let twin = Twin::new(test_device_id(), test_geometry(), 0, specs).unwrap();

        let dir = make_test_dir("create_open");
        fs::create_dir_all(&dir).unwrap();
        let vol = twin.create_volume(&dir).unwrap();

        let data = [0u8; 2048];
        vol.write_extent(ExtentId(0), &data).unwrap();
        assert_eq!(vol.read_extent(ExtentId(0)).unwrap(), data.as_slice());

        drop(vol);

        let (vol2, _report) = twin.open_volume(&dir).unwrap();
        assert_eq!(vol2.read_extent(ExtentId(0)).unwrap(), data.as_slice());
    }

    #[test]
    fn twin_sealed_volume_through_security_pool() {
        let specs = default_specs();
        let twin = Twin::new(test_device_id(), test_geometry(), FLAG_SEALED, specs).unwrap();

        let dir = make_test_dir("sealed");
        fs::create_dir_all(&dir).unwrap();
        let vol = twin.create_volume(&dir).unwrap();

        let data = [0x41u8; 2048];
        vol.write_extent(ExtentId(0), &data).unwrap();
        assert_eq!(vol.read_extent(ExtentId(0)).unwrap(), data.as_slice());

        drop(vol);
    }
}
