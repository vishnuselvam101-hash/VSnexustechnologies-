//! The declared fault campaign of the V15 digital twin (docs/V15_DIGITAL_TWIN.md).
//!
//! Every case runs with a fixed seed against the twin of `vnxd::twin`; everything the twin does is SIMULATED. The
//! invariant behind every case: the device never answers a read with bytes other than the bytes of the last
//! completed write of that extent. A fault may cost a read (a typed error) or a write (the old data survives), but it
//! never changes what the device hands out.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use vnxd::addr::{ExtentId, GenerationRef, Geometry};
use vnxd::codec::ToyCodec;
use vnxd::digest::sha256;
use vnxd::dnasim::DnaSimConfig;
use vnxd::error::{err, ErrorCode, Result, VnxError};
use vnxd::fault::{Fault, MediaOp, Rule};
use vnxd::seal::Sealer;
use vnxd::twin::{PoolRole, PoolSpec, Twin};
use vnxd::volume::{Stage, Volume, VolumeConfig};

/// The clock of the campaign: a fixed instant, never the wall clock.
const NOW_MS: i64 = 1_700_000_000_000;
const EXTENT_LEN: usize = 2048;
const DEVICE: [u8; 16] = [0x54; 16];

/// A directory that is removed when the test ends.
struct Dir(PathBuf);

impl Dir {
    fn new(tag: &str) -> Dir {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir()
            .join(format!("vnxd-twin-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn geometry() -> Geometry {
    Geometry::new(512, 4, 16).unwrap()
}

fn bytes(seed: u8) -> Vec<u8> {
    (0..EXTENT_LEN).map(|i| (i as u64 * 31 + u64::from(seed)) as u8).collect()
}

/// The pools of the campaign: the storage pool and the RAM and IO-journal pools carry the data, the metadata,
/// security and bootstrap pools are mirrored over three replicas.
fn specs() -> Vec<PoolSpec> {
    let sim = || DnaSimConfig { read_attempts: 3, ..Default::default() };
    vec![
        PoolSpec::new(PoolRole::Storage, 1001, sim(), 1),
        PoolSpec::new(PoolRole::Ram, 1002, sim(), 1),
        PoolSpec::new(PoolRole::IoJournal, 1003, sim(), 1),
        PoolSpec::new(PoolRole::Metadata, 1004, sim(), 3),
        PoolSpec::new(PoolRole::Security, 1005, sim(), 3),
        PoolSpec::new(PoolRole::Bootstrap, 1006, sim(), 3),
    ]
}

fn twin() -> Twin {
    Twin::new(specs(), Arc::new(ToyCodec)).unwrap()
}

/// A volume of the campaign, on the given pool.
fn create(twin: &Twin, dir: &Dir, role: PoolRole) -> Volume {
    twin.create_volume(&dir.0, role, geometry(), DEVICE, 0, VolumeConfig::default()).unwrap()
}

/// How many bytes of `got` are neither the old nor the new content of the extent.
fn wrong_bytes(got: &[u8], old: &[u8], new: &[u8]) -> usize {
    (0..got.len().min(old.len()).min(new.len()))
        .filter(|i| got[*i] != old[*i] && got[*i] != new[*i])
        .count()
}

/// The error of an operation that must fail (the volume does not implement `Debug`, so `unwrap_err` is not usable).
fn expect_err<T>(r: Result<T>) -> VnxError {
    match r {
        Ok(_) => panic!("the operation was expected to fail"),
        Err(e) => e,
    }
}

/// The sealer of the campaign: it seals with a tag of the device, the extent and the bytes, so a sealed object that
/// was moved or damaged is refused. It is a TEST-ONLY stand-in, never a cipher.
struct TestSealer {
    kcv: [u8; 32],
}

impl Sealer for TestSealer {
    fn seal(&self, device_id: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
        let mut out = Vec::with_capacity(plain.len() + 48);
        out.extend_from_slice(b"VNXTWIN1");
        out.extend_from_slice(&sha256(&[device_id.as_slice(), &[at.extent.0.to_le_bytes(), at.generation.get().to_le_bytes()].concat(), plain].concat()));
        out.extend_from_slice(plain);
        Ok(out)
    }

    fn open(&self, device_id: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
        if sealed.len() < 40 || &sealed[..8] != b"VNXTWIN1" {
            return Err(VnxError::new(ErrorCode::Corruption, "not a sealed object of this twin"));
        }
        let (tag, plain) = sealed.split_at(40);
        let want = sha256(&[device_id.as_slice(), &[at.extent.0.to_le_bytes(), at.generation.get().to_le_bytes()].concat(), plain].concat());
        if tag[8..40] != want[..] {
            return Err(VnxError::new(ErrorCode::Corruption, "the sealed object does not authenticate"));
        }
        Ok(plain.to_vec())
    }

    fn key_check(&self, _device_id: &[u8; 16]) -> Result<[u8; 32]> {
        Ok(self.kcv)
    }
}

/// (1) A bit rot in the storage pool: the read is either right after a retry or a typed error, never other bytes,
/// and the scrub reports the damage.
#[test]
fn a_bit_rot_in_the_storage_pool_is_never_served() {
    // the rot hits one read: the read fails with a typed error and the retry reads the bytes of the last write
    let t = twin();
    let d = Dir::new("rot-once");
    let v = create(&t, &d, PoolRole::Storage);
    let data = bytes(7);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    t.inject(PoolRole::Storage, 0, Rule::once(MediaOp::Get, Fault::BitRot)).unwrap();
    match v.read_extent(ExtentId(0)) {
        Ok(got) => assert_eq!(got, data, "a read that answers must answer with the bytes of the last write"),
        Err(e) => assert!(
            matches!(e.code, ErrorCode::Corruption | ErrorCode::BackendUnavailable),
            "a damaged read must be a typed error, got {e}"
        ),
    }
    assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data, "the retry reads the bytes of the last write");

    // the rot lasts: every read fails with a typed error and the scrub reports the damaged extent
    let t = twin();
    let d = Dir::new("rot-always");
    let v = create(&t, &d, PoolRole::Storage);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    t.inject(PoolRole::Storage, 0, Rule::always(MediaOp::Get, Fault::BitRot)).unwrap();
    let e = v.read_extent(ExtentId(0)).unwrap_err();
    assert!(matches!(e.code, ErrorCode::Corruption | ErrorCode::BackendUnavailable), "got {e}");
    let scrub = v.scrub().unwrap();
    assert_eq!(scrub.checked, 1);
    assert_eq!(scrub.failed.len(), 1, "the scrub reports the damaged extent");
    assert_eq!(scrub.failed[0].0, ExtentId(0));
}

/// (2) One lost metadata replica: everything still reads.
#[test]
fn losing_one_metadata_replica_keeps_everything_readable() {
    let t = twin();
    let d = Dir::new("meta-one");
    let v = create(&t, &d, PoolRole::Storage);
    let data = bytes(11);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    t.write_extent(&v, &d.0, ExtentId(1), &data, NOW_MS).unwrap();
    drop(v);

    t.lose_pool(PoolRole::Metadata, 0).unwrap();
    let (v, _report) = t.open_volume(&d.0, PoolRole::Storage, VolumeConfig::default()).unwrap();
    assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data);
    assert_eq!(v.read_extent(ExtentId(1)).unwrap(), data);
    // the map is still readable from the replicas that are left
    let (device, _bytes) = t.read_map().unwrap();
    assert_eq!(device, DEVICE);
}

/// (3) Every metadata replica lost: typed errors, never other bytes.
#[test]
fn losing_every_metadata_replica_gives_a_typed_error() {
    let t = twin();
    let d = Dir::new("meta-all");
    let v = create(&t, &d, PoolRole::Storage);
    let data = bytes(13);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    drop(v);

    for replica in 0..t.replicas(PoolRole::Metadata) {
        t.lose_pool(PoolRole::Metadata, replica as u8).unwrap();
    }
    let e = expect_err(t.open_volume(&d.0, PoolRole::Storage, VolumeConfig::default()));
    assert!(matches!(e.code, ErrorCode::BackendUnavailable | ErrorCode::NotFound), "got {e}");
    // the map itself is not readable any more either, and it never answers with other bytes
    assert!(t.read_map().is_err());
}

/// (4) Damage in the RAM pool changes no byte the storage pool serves.
#[test]
fn damage_in_the_ram_pool_does_not_change_a_storage_read() {
    let t = twin();
    let d = Dir::new("ram");
    let v = create(&t, &d, PoolRole::Ram);
    let data = bytes(17);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    // the write's read-back filled the RAM pool
    assert_eq!(t.ram_counters(), (0, 1, 0), "the read-back of the write reached the storage pool");
    assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data);
    assert_eq!(t.ram_counters(), (1, 1, 0), "the read was answered from the RAM pool");

    // the RAM pool rots every copy it hands out
    t.inject(PoolRole::Ram, 0, Rule::always(MediaOp::Get, Fault::BitRot)).unwrap();
    let got = v.read_extent(ExtentId(0)).unwrap();
    assert_eq!(got, data, "the damaged cache copy must not change what the volume reads");
    let (hits, misses, _rejected) = t.ram_counters();
    assert_eq!(misses, 2, "the read fell back to the storage pool");
    assert_eq!(hits, 1, "no damaged copy was ever served");
    assert_eq!(t.damaged_replicas(PoolRole::Ram), vec![0], "the RAM pool reported its damaged replica");
    // the damage really happened: the RAM pool served damaged bytes
    let report = t.report();
    let pools = report.as_obj().unwrap()["pools"].as_obj().unwrap();
    let ram = pools["ram"].as_obj().unwrap();
    let injected = match &ram["replica"] {
        vnxd::json::Value::Arr(reps) => reps[0].as_obj().unwrap()["faults_injected"].as_int().unwrap(),
        _ => panic!("the replica list is not a list"),
    };
    assert!(injected > 0, "the RAM pool served a damaged copy");
}

/// (5) A torn write-ahead copy in the IO-journal pool: the reopen gives the old or the new data.
#[test]
fn a_torn_write_ahead_copy_leaves_the_old_or_the_new_data() {
    // the write-ahead copy is torn: the write fails and the old data survives the reopen
    let t = twin();
    let d = Dir::new("torn");
    let v = create(&t, &d, PoolRole::IoJournal);
    let old = bytes(19);
    let new = bytes(23);
    t.write_extent(&v, &d.0, ExtentId(0), &old, NOW_MS).unwrap();
    t.inject(PoolRole::IoJournal, 0, Rule::once(MediaOp::Put, Fault::TornPut)).unwrap();
    let e = t.write_extent(&v, &d.0, ExtentId(0), &new, NOW_MS).unwrap_err();
    assert!(matches!(e.code, ErrorCode::Corruption | ErrorCode::BackendUnavailable), "got {e}");
    drop(v);
    let (v, _report) = t.open_volume(&d.0, PoolRole::IoJournal, VolumeConfig::default()).unwrap();
    let got = v.read_extent(ExtentId(0)).unwrap();
    assert_eq!(wrong_bytes(&got, &old, &new), 0, "the reopen gave bytes that are neither the old nor the new ones");
    assert!(got == old || got == new);

    // the crash after the map record: the new data is committed and survives the reopen
    let t = twin();
    let d = Dir::new("torn-committed");
    let v = create(&t, &d, PoolRole::IoJournal);
    t.write_extent(&v, &d.0, ExtentId(0), &old, NOW_MS).unwrap();
    let hook: vnxd::volume::FaultHook =
        Arc::new(|stage, _| if stage == Stage::AfterMapRecord { err(ErrorCode::Internal, "crash") } else { Ok(()) });
    let v = v.with_fault_hook(hook);
    assert!(v.write_extent(ExtentId(0), &new, NOW_MS).is_err());
    drop(v);
    let (v, _report) = t.open_volume(&d.0, PoolRole::IoJournal, VolumeConfig::default()).unwrap();
    let got = v.read_extent(ExtentId(0)).unwrap();
    assert_eq!(wrong_bytes(&got, &old, &new), 0);
    assert!(got == old || got == new);
}

/// (6) The security pool is unavailable: a sealed volume refuses to open, and it never opens unsealed.
#[test]
fn a_sealed_volume_refuses_to_open_without_the_security_pool() {
    let t = twin();
    t.set_sealer(Arc::new(TestSealer { kcv: [0x11; 32] }));
    let d = Dir::new("sealed");
    let v = t
        .create_volume(&d.0, PoolRole::Security, geometry(), DEVICE, vnxd::record::FLAG_SEALED, VolumeConfig::default())
        .unwrap();
    let data = bytes(29);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data, "a sealed volume reads its plaintext back");
    drop(v);

    // the security pool is gone: the volume does not open
    for replica in 0..t.replicas(PoolRole::Security) {
        t.lose_pool(PoolRole::Security, replica as u8).unwrap();
    }
    let e = expect_err(t.open_volume(&d.0, PoolRole::Security, VolumeConfig::default()));
    assert!(matches!(e.code, ErrorCode::BackendUnavailable | ErrorCode::Unauthorized), "got {e}");

    // another key: the volume does not open either
    let t = twin();
    t.set_sealer(Arc::new(TestSealer { kcv: [0x11; 32] }));
    let d = Dir::new("sealed-key");
    let v = t
        .create_volume(&d.0, PoolRole::Security, geometry(), DEVICE, vnxd::record::FLAG_SEALED, VolumeConfig::default())
        .unwrap();
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    drop(v);
    t.set_sealer(Arc::new(TestSealer { kcv: [0x22; 32] }));
    let e = expect_err(t.open_volume(&d.0, PoolRole::Security, VolumeConfig::default()));
    assert_eq!(e.code, ErrorCode::Unauthorized, "got {e}");
}

/// (7) A bit rot in one bootstrap replica: the other replicas carry the volume.
#[test]
fn a_bit_rot_in_one_bootstrap_replica_is_recovered_from_the_others() {
    let t = twin();
    let d = Dir::new("bootstrap");
    let v = create(&t, &d, PoolRole::Bootstrap);
    let data = bytes(31);
    t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
    drop(v);

    t.inject(PoolRole::Bootstrap, 0, Rule::always(MediaOp::Get, Fault::BitRot)).unwrap();
    let (v, _report) = t.open_volume(&d.0, PoolRole::Bootstrap, VolumeConfig::default()).unwrap();
    assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data, "the read is served by another replica");
    assert_eq!(t.damaged_replicas(PoolRole::Bootstrap), vec![0], "the damaged replica is reported");
}

/// (8) A crash at every stage of the commit: the reopen gives no wrong bytes.
#[test]
fn a_crash_at_every_stage_reopens_with_no_wrong_bytes() {
    for stage in [
        Stage::AfterPut,
        Stage::BeforeMapRecord,
        Stage::TornMapRecord,
        Stage::AfterMapRecord,
        Stage::AfterRemove,
    ] {
        let t = twin();
        let d = Dir::new("crash");
        let v = create(&t, &d, PoolRole::Storage);
        let old = bytes(37);
        let new = bytes(41);
        t.write_extent(&v, &d.0, ExtentId(0), &old, NOW_MS).unwrap();
        let hook: vnxd::volume::FaultHook =
            Arc::new(move |s, _| if s == stage { err(ErrorCode::Internal, "crash") } else { Ok(()) });
        let v = v.with_fault_hook(hook);
        assert!(v.write_extent(ExtentId(0), &new, NOW_MS).is_err(), "{stage:?}");
        drop(v);

        let (v, _report) = t.open_volume(&d.0, PoolRole::Storage, VolumeConfig::default()).unwrap();
        let got = v.read_extent(ExtentId(0)).unwrap();
        assert_eq!(wrong_bytes(&got, &old, &new), 0, "{stage:?}: the reopen gave bytes of neither write");
        assert!(got == old || got == new, "{stage:?}");
    }
}

/// (9) The whole campaign is deterministic: the same seeds give the same report.
#[test]
fn the_campaign_is_deterministic() {
    let run = || {
        let t = twin();
        let d = Dir::new("deterministic");
        let v = create(&t, &d, PoolRole::Storage);
        let data = bytes(43);
        t.write_extent(&v, &d.0, ExtentId(0), &data, NOW_MS).unwrap();
        assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data);
        t.inject(PoolRole::Storage, 0, Rule::once(MediaOp::Get, Fault::BitRot)).unwrap();
        let _ = v.read_extent(ExtentId(0));
        t.lose_pool(PoolRole::Metadata, 1).unwrap();
        let _ = t.read_map();
        t.report()
    };
    let first = run();
    let second = run();
    assert_eq!(first, second, "the same seeds must give the same report");
}
