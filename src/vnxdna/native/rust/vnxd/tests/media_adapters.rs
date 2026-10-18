//! Phase 7 media adapters (docs/V14_MEDIA_ADAPTERS.md) through the layers above them: restart persistence of the
//! image backend, seeded fault determinism, and the volume / block I/O / fallback layers surviving or reporting
//! injected faults. The invariant of every test: a read returns the bytes of the last completed write of that range,
//! or a typed error; never other bytes. The DNA adapters here run on the TEST-ONLY `ToyCodec`; the same adapters over
//! the real VNX-DNA codec are exercised by `tests/media_worker.rs` (through pytest).

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use vnxd::addr::{ExtentId, Generation, GenerationRef, Geometry};
use vnxd::codec::ToyCodec;
use vnxd::dnasim::{DnaSimConfig, DnaSimMedia, FailureModel};
use vnxd::error::ErrorCode;
use vnxd::fallback::{BackendState, HealthConfig, ResilientMedia, RetryPolicy};
use vnxd::fault::{Fault, FaultMedia, FaultPlan, Injection, SplitMix64};
use vnxd::image::ImageMedia;
use vnxd::io::BlockIo;
use vnxd::lab::{LabMedia, LabMediaConfig, SimLabConfig, SimulatedLab};
use vnxd::media::{conformance, MediaAdapter, MemoryMedia};
use vnxd::volume::{Volume, VolumeConfig};

struct Dir(std::path::PathBuf);
impl Dir {
    fn new(tag: &str) -> Dir {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!("vnxd-ma-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
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

fn at(e: u64, g: u64) -> GenerationRef {
    GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
}

fn geo() -> Geometry {
    Geometry::new(512, 4, 32).unwrap() // 8 extents of 2 KiB
}

fn data(seed: u64, n: usize) -> Vec<u8> {
    let mut r = SplitMix64::new(seed);
    (0..n).map(|_| r.next_u64() as u8).collect()
}

// ------------------------------------------------------------------------------------------- B: image backend

#[test]
fn image_backend_volume_survives_restarts() {
    let d = Dir::new("img");
    let (img, vol) = (d.0.join("image"), d.0.join("vol"));
    let dev = [7u8; 16];
    {
        let m: Arc<dyn MediaAdapter> = Arc::new(ImageMedia::create(&img).unwrap());
        let v = Volume::create(&vol, geo(), dev, 0, m, VolumeConfig::default()).unwrap();
        for e in 0..8u64 {
            v.write_extent(ExtentId(e), &data(e, 2048)).unwrap();
        }
        v.write_extent(ExtentId(3), &data(103, 2048)).unwrap(); // an overwrite: a new generation
    } // process "exits": volume and image dropped
    for round in 0..3u64 {
        let m: Arc<dyn MediaAdapter> = Arc::new(ImageMedia::open(&img).unwrap());
        let (v, rep) = Volume::open(&vol, m.clone(), VolumeConfig::default()).unwrap();
        assert!(rep.torn_tail.is_none());
        for e in 0..8u64 {
            let want = if e == 3 { data(103 + round * 1000, 2048) } else { data(e, 2048) };
            assert_eq!(v.read_extent(ExtentId(e)).unwrap(), want, "round {round} extent {e}");
        }
        v.write_extent(ExtentId(3), &data(103 + (round + 1) * 1000, 2048)).unwrap();
        // one live generation per extent + the marker: the overwritten generations were collected
        assert_eq!(m.list().unwrap().len(), 9);
    }
}

#[test]
fn image_backend_sweeps_unfinished_writes_and_refuses_damaged_objects() {
    let d = Dir::new("img");
    let root = d.0.join("image");
    let m = ImageMedia::create(&root).unwrap();
    m.put(at(1, 1), b"one").unwrap();
    m.put(at(2, 1), b"two").unwrap();
    drop(m);
    // a crash during a put leaves a temporary file; it never becomes an object and is swept at open
    std::fs::write(root.join("objects/.tmp-dead-0"), b"half written").unwrap();
    let m = ImageMedia::open(&root).unwrap();
    assert!(!root.join("objects/.tmp-dead-0").exists());
    assert_eq!(m.list().unwrap(), vec![at(1, 1), at(2, 1)]);
    // a generation stays immutable across restarts
    assert_eq!(m.put(at(1, 1), b"other").unwrap_err().code, ErrorCode::InvalidArgument);
    // bit rot, truncation and an object swapped under another name: CORRUPTION, never other bytes
    let p1 = root.join(format!("objects/{:016x}-{:016x}.obj", 1, 1));
    let p2 = root.join(format!("objects/{:016x}-{:016x}.obj", 2, 1));
    let mut b = std::fs::read(&p1).unwrap();
    *b.last_mut().unwrap() ^= 0x20;
    std::fs::write(&p1, &b).unwrap();
    assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::Corruption);
    let b2 = std::fs::read(&p2).unwrap();
    std::fs::write(&p2, &b2[..b2.len() - 1]).unwrap();
    assert_eq!(m.get(at(2, 1)).unwrap_err().code, ErrorCode::Corruption);
    m.put(at(3, 1), b"three").unwrap();
    std::fs::copy(root.join(format!("objects/{:016x}-{:016x}.obj", 3, 1)), &p1).unwrap();
    assert_eq!(m.get(at(1, 1)).unwrap_err().code, ErrorCode::Corruption, "an object under another generation's name");
    let found: Vec<GenerationRef> = m.scrub().unwrap().into_iter().map(|f| f.at).collect();
    assert_eq!(found, vec![at(1, 1), at(2, 1)]);
}

#[test]
fn a_volume_over_a_damaged_image_reports_the_damage() {
    let d = Dir::new("img");
    let (img, vol) = (d.0.join("image"), d.0.join("vol"));
    {
        let m: Arc<dyn MediaAdapter> = Arc::new(ImageMedia::create(&img).unwrap());
        let v = Volume::create(&vol, geo(), [1; 16], 0, m, VolumeConfig::default()).unwrap();
        v.write_extent(ExtentId(0), &data(1, 2048)).unwrap();
        v.write_extent(ExtentId(1), &data(2, 2048)).unwrap();
    }
    let obj = std::fs::read_dir(img.join("objects")).unwrap().flatten().map(|e| e.path()).find(|p| p.to_string_lossy().contains("0000000000000000-")).unwrap();
    let mut b = std::fs::read(&obj).unwrap();
    b[100] ^= 1;
    std::fs::write(&obj, &b).unwrap();
    let m: Arc<dyn MediaAdapter> = Arc::new(ImageMedia::open(&img).unwrap());
    let (v, _) = Volume::open(&vol, m, VolumeConfig::default()).unwrap();
    assert_eq!(v.read_extent(ExtentId(0)).unwrap_err().code, ErrorCode::Corruption);
    assert_eq!(v.read_extent(ExtentId(1)).unwrap(), data(2, 2048));
    let io = BlockIo::new(Arc::new(v));
    assert_eq!(io.read(0, 512).unwrap_err().code, ErrorCode::Corruption, "the block layer reports it too");
    assert_eq!(io.read(2048, 512).unwrap(), data(2, 2048)[..512]);
}

// -------------------------------------------------------------------------------------- C: fault injection

/// A fixed operation sequence against a seeded faulty medium: (outcome codes, fault trace).
fn scripted(seed: u64, plan: fn(u64, u32) -> FaultPlan) -> (Vec<std::result::Result<Vec<u8>, ErrorCode>>, Vec<Injection>) {
    let fm = FaultMedia::seeded(Arc::new(MemoryMedia::new()), plan(seed, 150_000));
    let mut out = Vec::new();
    for i in 0..60u64 {
        let r = at(i % 7, i / 7 + 1);
        out.push(fm.put(r, &data(i, 64)).map(|rc| rc.sha256.to_vec()).map_err(|e| e.code));
        out.push(fm.get(r).map_err(|e| e.code));
        if i % 5 == 0 {
            out.push(fm.remove(r).map(|_| vec![]).map_err(|e| e.code));
        }
        if i % 9 == 0 {
            out.push(fm.sync().map(|_| vec![]).map_err(|e| e.code));
            out.push(fm.list().map(|l| vec![l.len() as u8]).map_err(|e| e.code));
        }
    }
    (out, fm.trace())
}

#[test]
fn seeded_faults_are_deterministic() {
    for plan in [FaultPlan::honest as fn(u64, u32) -> FaultPlan, FaultPlan::lying] {
        let a = scripted(42, plan);
        assert_eq!(a, scripted(42, plan), "same seed + same calls = same faults and outcomes");
        assert_ne!(a.1, scripted(43, plan).1, "another seed injects other faults");
        assert!(a.1.len() > 10, "the plan injects faults ({} injected)", a.1.len());
    }
}

#[test]
fn faulty_wrapper_keeps_conformance_when_no_fault_fires_and_takes_the_inner_durability() {
    let d = Dir::new("img");
    let img: Arc<dyn MediaAdapter> = Arc::new(ImageMedia::create(&d.0.join("i")).unwrap());
    let fm = FaultMedia::seeded(img, FaultPlan::honest(1, 0));
    conformance(&fm).unwrap();
    assert_eq!(fm.info().durability, vnxd::media::Durability::FileSync);
    conformance(&FaultMedia::new()).unwrap();
}

#[test]
fn the_faulty_wrapper_forwards_sealing() {
    use vnxd::seal::SealedMedia;
    struct Plain;
    impl vnxd::seal::Sealer for Plain {
        fn seal(&self, _: &[u8; 16], _: GenerationRef, p: &[u8]) -> vnxd::error::Result<Vec<u8>> {
            Ok(p.to_vec())
        }
        fn open(&self, _: &[u8; 16], _: GenerationRef, s: &[u8]) -> vnxd::error::Result<Vec<u8>> {
            Ok(s.to_vec())
        }
        fn key_check(&self, _: &[u8; 16]) -> vnxd::error::Result<[u8; 32]> {
            Ok([0; 32])
        }
    }
    let sealed: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(Arc::new(MemoryMedia::new()), Arc::new(Plain), [0; 16], 4096));
    assert!(FaultMedia::over(sealed).sealed());
    assert!(!FaultMedia::new().sealed());
}

/// Volume + block I/O + fallback over a seeded faulty medium: random writes and reads; every read is the last
/// completed write or an error.
fn chaos(seed: u64, plan: FaultPlan, inner: Arc<dyn MediaAdapter>) -> (u64, u64, Vec<Fault>) {
    let d = Dir::new("chaos");
    let fm = Arc::new(FaultMedia::over(inner));
    let health = HealthConfig { degrade_after: 2, unavailable_after: 1000, probe_interval: Duration::ZERO, score_alpha: 0.2 };
    let retry = RetryPolicy { max_attempts: 4, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    let rm: Arc<dyn MediaAdapter> = Arc::new(ResilientMedia::new(fm.clone(), retry, health).unwrap());
    // create the volume cleanly, then let faults loose
    let v = Arc::new(Volume::create(&d.0, geo(), [9; 16], 0, rm, VolumeConfig::default()).unwrap());
    let io = BlockIo::new(v.clone());
    fm.set_plan(Some(plan));
    let mut rng = SplitMix64::new(seed);
    let mut model: Vec<Vec<u8>> = vec![vec![0u8; 2048]; 8];
    let (mut ok, mut failed) = (0, 0);
    for step in 0..300u64 {
        let e = (rng.next_u64() % 8) as usize;
        if rng.next_u64() % 2 == 0 {
            let off = (rng.next_u64() % 2048) as usize;
            let len = 1 + (rng.next_u64() % (2048 - off) as u64) as usize;
            let buf = data(seed ^ step, len);
            match io.write((e * 2048 + off) as u64, &buf) {
                Ok(_) => {
                    model[e][off..off + len].copy_from_slice(&buf);
                    ok += 1;
                }
                Err(err) => {
                    failed += 1;
                    assert!(
                        matches!(err.code, ErrorCode::BackendUnavailable | ErrorCode::BackendBusy | ErrorCode::Timeout
                            | ErrorCode::Corruption | ErrorCode::NotFound | ErrorCode::ReadOnly),
                        "unexpected error class {err}"
                    );
                }
            }
        } else {
            match io.read((e * 2048) as u64, 2048) {
                Ok(d) => {
                    assert_eq!(d, model[e], "seed {seed} step {step}: a read returned other bytes than the last completed write");
                    ok += 1;
                }
                Err(_) => failed += 1,
            }
        }
    }
    // the faults stop: every extent reads back as the model says (no write that failed changed anything), unless
    // the damage was detected and reported
    fm.set_plan(None);
    for e in 0..8 {
        match io.read((e * 2048) as u64, 2048) {
            Ok(d) => assert_eq!(d, model[e], "seed {seed}: final read of extent {e}"),
            Err(err) => assert!(matches!(err.code, ErrorCode::Corruption | ErrorCode::NotFound), "{err}"),
        }
    }
    let mut kinds: Vec<Fault> = Vec::new();
    for i in fm.trace() {
        if !kinds.contains(&i.fault) {
            kinds.push(i.fault);
        }
    }
    (ok, failed, kinds)
}

fn plan(seed: u64, ppm: u32, lying: bool) -> FaultPlan {
    if lying { FaultPlan::lying(seed, ppm) } else { FaultPlan::honest(seed, ppm) }
}

#[test]
fn volume_and_block_io_survive_seeded_honest_faults() {
    for seed in 0..6u64 {
        let p = plan(seed, 40_000, false);
        let (ok, failed, _) = chaos(seed, p, Arc::new(MemoryMedia::new()));
        assert!(ok > 200, "retries absorb most honest faults (ok {ok}, failed {failed})");
    }
}

#[test]
fn volume_and_block_io_never_return_wrong_bytes_under_lying_faults() {
    let mut seen: Vec<Fault> = Vec::new();
    for seed in 0..6u64 {
        let d = Dir::new("lie");
        let inner: Arc<dyn MediaAdapter> = if seed % 2 == 0 { Arc::new(MemoryMedia::new()) } else { Arc::new(ImageMedia::create(&d.0.join("i")).unwrap()) };
        let (ok, failed, kinds) = chaos(seed, plan(seed, 30_000, true), inner);
        assert!(ok > 0 && failed > 0, "faults fired and work went on (ok {ok}, failed {failed})");
        seen.extend(kinds);
    }
    for f in [Fault::TornPut, Fault::WrongReceipt, Fault::SilentCorruptWrite, Fault::CorruptAckLost, Fault::BitRot, Fault::ShortRead, Fault::Lost] {
        assert!(seen.contains(&f), "{f:?} was injected by some seed");
    }
}

#[test]
fn an_offline_medium_turns_the_device_read_only_and_it_recovers() {
    let d = Dir::new("off");
    let fm = Arc::new(FaultMedia::over(Arc::new(ImageMedia::create(&d.0.join("i")).unwrap())));
    // the probe interval must outlast the four refused writes below: with 1 ms, a slow host (qemu-user emulation)
    // made every write a due probe, so none was refused READ_ONLY
    let probe = Duration::from_millis(500);
    let health = HealthConfig { degrade_after: 1, unavailable_after: 2, probe_interval: probe, score_alpha: 0.2 };
    let retry = RetryPolicy { max_attempts: 2, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    let rm = Arc::new(ResilientMedia::new(fm.clone(), retry, health).unwrap());
    let v = Volume::create(&d.0.join("v"), geo(), [3; 16], 0, rm.clone(), VolumeConfig::default()).unwrap();
    v.write_extent(ExtentId(0), &data(1, 2048)).unwrap();
    fm.set_offline(true);
    let mut codes = Vec::new();
    for _ in 0..4 {
        codes.push(v.write_extent(ExtentId(0), &data(2, 2048)).unwrap_err().code);
    }
    assert_eq!(rm.health().state, BackendState::Unavailable);
    assert!(codes.contains(&ErrorCode::ReadOnly), "{codes:?}");
    assert_eq!(v.read_extent(ExtentId(0)).unwrap_err().code, ErrorCode::BackendUnavailable, "offline: reads fail, typed");
    fm.set_offline(false);
    std::thread::sleep(probe + Duration::from_millis(5));
    let mut recovered = false;
    for _ in 0..10 {
        if v.write_extent(ExtentId(0), &data(3, 2048)).is_ok() {
            recovered = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    assert!(recovered, "a probe brings the backend back");
    assert_eq!(rm.health().state, BackendState::Healthy);
    assert_eq!(v.read_extent(ExtentId(0)).unwrap(), data(3, 2048));
}

// ------------------------------------------------------------------------------------ A/D: DNA adapters

fn sim_over(store: Arc<dyn MediaAdapter>, cfg: DnaSimConfig) -> Arc<dyn MediaAdapter> {
    Arc::new(DnaSimMedia::new(store, Arc::new(ToyCodec), cfg).unwrap())
}

#[test]
fn dna_sim_pools_kept_in_an_image_survive_restarts() {
    let d = Dir::new("dsim");
    let (img, vol) = (d.0.join("pools"), d.0.join("vol"));
    let cfg = DnaSimConfig { channel: Some("toy-sub-1000".into()), seed: 5, ..Default::default() };
    {
        let m = sim_over(Arc::new(ImageMedia::create(&img).unwrap()), cfg.clone());
        let v = Volume::create(&vol, geo(), [4; 16], 0, m, VolumeConfig::default()).unwrap();
        for e in 0..8u64 {
            v.write_extent(ExtentId(e), &data(50 + e, 2048)).unwrap();
        }
    }
    let m = sim_over(Arc::new(ImageMedia::open(&img).unwrap()), cfg);
    let (v, _) = Volume::open(&vol, m, VolumeConfig::default()).unwrap();
    for e in 0..8u64 {
        assert_eq!(v.read_extent(ExtentId(e)).unwrap(), data(50 + e, 2048));
    }
}

#[test]
fn dna_sim_failures_reach_the_volume_as_typed_errors() {
    let d = Dir::new("dsimf");
    let cfg = DnaSimConfig { seed: 8, failures: FailureModel { synthesis_failure_ppm: 250_000, sequencing_failure_ppm: 250_000 }, ..Default::default() };
    let sim = sim_over(Arc::new(MemoryMedia::new()), cfg);
    let retry = RetryPolicy { max_attempts: 6, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    let health = HealthConfig { degrade_after: 2, unavailable_after: 1000, probe_interval: Duration::ZERO, score_alpha: 0.2 };
    let rm: Arc<dyn MediaAdapter> = Arc::new(ResilientMedia::new(sim, retry, health).unwrap());
    let v = Volume::create(&d.0, geo(), [5; 16], 0, rm, VolumeConfig::default()).unwrap();
    let mut model: Vec<Option<Vec<u8>>> = vec![None; 8];
    for round in 0..4u64 {
        for e in 0..8u64 {
            let b = data(round * 100 + e, 2048);
            if v.write_extent(ExtentId(e), &b).is_ok() {
                model[e as usize] = Some(b);
            }
        }
        for e in 0..8u64 {
            match (v.read_extent(ExtentId(e)), &model[e as usize]) {
                (Ok(d), Some(m)) => assert_eq!(&d, m),
                (Ok(d), None) => assert_eq!(d, vec![0u8; 2048], "never written: zeros"),
                (Err(err), _) => assert!(err.is_retryable(), "{err}"),
            }
        }
    }
    assert!(model.iter().all(Option::is_some), "retries got every extent written at least once");
}

#[test]
fn a_volume_runs_over_the_simulated_lab() {
    let d = Dir::new("lab");
    let lab = Arc::new(SimulatedLab::new(SimLabConfig { seed: 2, substitution_ppm: 200, synthesis_failure_ppm: 200_000, sequencing_failure_ppm: 200_000, aliquots: 100, ..Default::default() }));
    let cfg = LabMediaConfig { job_retries: 5, ..Default::default() };
    let m: Arc<dyn MediaAdapter> = Arc::new(LabMedia::new(lab.clone(), Arc::new(ToyCodec), Arc::new(MemoryMedia::new()), cfg).unwrap());
    let v = Volume::create(&d.0, geo(), [6; 16], 0, m, VolumeConfig::default()).unwrap();
    for e in 0..4u64 {
        v.write_extent(ExtentId(e), &data(70 + e, 2048)).unwrap();
    }
    v.write_extent(ExtentId(1), &data(99, 2048)).unwrap();
    for e in 0..4u64 {
        assert_eq!(v.read_extent(ExtentId(e)).unwrap(), if e == 1 { data(99, 2048) } else { data(70 + e, 2048) });
    }
    use vnxd::lab::{DnaLab, PoolLifecycle};
    let destroyed = lab.pools().unwrap().into_iter().filter(|p| lab.pool(p).unwrap().lifecycle == PoolLifecycle::Destroyed).count();
    assert_eq!(destroyed, 1, "the overwritten generation's pool was destroyed when the volume released it");
}
