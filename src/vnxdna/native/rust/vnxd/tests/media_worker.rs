//! The DNA adapters over the REAL VNX-DNA codec (`python -m vnxdna.device.dnasim`): conformance, a volume on top,
//! simulated channel runs and failure modes. Everything is SIMULATED. Ignored by plain `cargo test` because it needs
//! the Python package; `tests/v14/test_media_adapters.py` runs it with
//! `cargo test --test media_worker -- --ignored` and VNXD_TEST_PYTHON set.

use std::sync::Arc;
use std::time::Duration;
use vnxd::addr::{ExtentId, Generation, GenerationRef, Geometry};
use vnxd::codec::{frame, Codec, PoolFile, WorkerCodec};
use vnxd::digest::sha256;
use vnxd::dnasim::{DnaSimConfig, DnaSimMedia};
use vnxd::error::ErrorCode;
use vnxd::image::ImageMedia;
use vnxd::lab::{LabMedia, LabMediaConfig, SimLabConfig, SimulatedLab};
use vnxd::media::{conformance, MediaAdapter, MemoryMedia};
use vnxd::volume::{Volume, VolumeConfig};
use vnxd::worker::WorkerConfig;

fn root(tag: &str) -> std::path::PathBuf {
    let r = std::env::temp_dir().join(format!("vnxd-mw-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&r);
    let staging = r.join("staging");
    std::fs::create_dir_all(&staging).unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o700)).unwrap();
    r
}

fn codec(r: &std::path::Path) -> Arc<WorkerCodec> {
    let python = std::env::var("VNXD_TEST_PYTHON").expect("VNXD_TEST_PYTHON must be set (run through tests/v14/test_media_adapters.py)");
    let staging = r.join("staging");
    let cmd = vec![python, "-m".into(), "vnxdna.device.dnasim".into(), "--staging".into(), staging.display().to_string()];
    Arc::new(WorkerCodec::new(WorkerConfig { command: cmd, staging, workers: 2, timeout: Duration::from_secs(120) }).unwrap())
}

fn at(e: u64, g: u64) -> GenerationRef {
    GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
}

fn bytes(seed: u64, n: usize) -> Vec<u8> {
    let mut r = vnxd::fault::SplitMix64::new(seed);
    (0..n).map(|_| r.next_u64() as u8).collect()
}

#[test]
#[ignore]
fn dna_sim_over_the_vnxdna_codec() {
    let r = root("sim");
    let c = codec(&r);
    // the codec round trip, and the labelled pool format
    let a = at(4, 2);
    let framed = frame(a, &bytes(1, 3000));
    let pool = c.encode(a, &framed).unwrap();
    let p = PoolFile::parse(&pool).unwrap();
    assert_eq!(p.header["evidence"].as_str(), Some("SIMULATED"));
    assert!(!p.strands().unwrap().is_empty());
    assert_eq!(c.decode(a, &pool, &sha256(&framed), framed.len() as u64, None).unwrap(), framed);
    // failure modes: other generation, tampered strands, wrong expectation, unknown channel model
    assert_eq!(c.decode(at(4, 3), &pool, &sha256(&framed), framed.len() as u64, None).unwrap_err().code, ErrorCode::Corruption);
    let tampered = p.with_body("strands", p.body.iter().map(|b| if *b == b'A' { b'C' } else { *b }).collect()).to_bytes();
    assert_eq!(c.decode(a, &tampered, &sha256(&framed), framed.len() as u64, None).unwrap_err().code, ErrorCode::Corruption);
    assert_eq!(c.decode(a, &pool, &[0; 32], framed.len() as u64, None).unwrap_err().code, ErrorCode::Corruption);
    let unknown = vnxd::codec::ChannelRun { model: "no-such-model".into(), seed: 1 };
    assert_eq!(c.decode(a, &pool, &sha256(&framed), framed.len() as u64, Some(&unknown)).unwrap_err().code, ErrorCode::InvalidArgument);

    // conformance: a perfect channel, and a simulated sequencing run with the shipped illumina-like model
    for channel in [None, Some("illumina-like".to_string())] {
        let m = DnaSimMedia::new(Arc::new(MemoryMedia::new()), c.clone(), DnaSimConfig { channel, seed: 17, ..Default::default() }).unwrap();
        conformance(&m).unwrap();
        assert!(m.report().as_obj().unwrap()["sequencing_runs"].as_int().unwrap() > 0);
    }

    // a volume over the simulator, pools persisted in an image, across a restart
    let cfg = DnaSimConfig { channel: Some("illumina-like".into()), seed: 3, ..Default::default() };
    let geo = Geometry::new(512, 8, 32).unwrap();
    {
        let m: Arc<dyn MediaAdapter> = Arc::new(DnaSimMedia::new(Arc::new(ImageMedia::create(&r.join("pools")).unwrap()), c.clone(), cfg.clone()).unwrap());
        let v = Volume::create(&r.join("vol"), geo, [8; 16], 0, m, VolumeConfig::default()).unwrap();
        for e in 0..4u64 {
            v.write_extent(ExtentId(e), &bytes(e, 4096)).unwrap();
        }
    }
    let m: Arc<dyn MediaAdapter> = Arc::new(DnaSimMedia::new(Arc::new(ImageMedia::open(&r.join("pools")).unwrap()), c.clone(), cfg).unwrap());
    let (v, _) = Volume::open(&r.join("vol"), m, VolumeConfig::default()).unwrap();
    for e in 0..4u64 {
        assert_eq!(v.read_extent(ExtentId(e)).unwrap(), bytes(e, 4096));
    }

    // a channel that destroys most strands: CORRUPTION after every attempt, never other bytes
    let lossy = DnaSimMedia::new(Arc::new(MemoryMedia::new()), c.clone(), DnaSimConfig { channel: Some("dropout-20".into()), seed: 5, read_attempts: 2, ..Default::default() }).unwrap();
    lossy.put(at(1, 1), &bytes(9, 2048)).unwrap();
    match lossy.get(at(1, 1)) {
        Ok(d) => assert_eq!(d, bytes(9, 2048), "a decode that succeeds returns exactly the stored bytes"),
        Err(e) => assert_eq!(e.code, ErrorCode::Corruption),
    }
    drop(v);
    assert_eq!(std::fs::read_dir(r.join("staging")).unwrap().count(), 0, "no staging file is left behind");
    let _ = std::fs::remove_dir_all(&r);
}

#[test]
#[ignore]
fn lab_interface_over_the_vnxdna_codec() {
    let r = root("lab");
    let c = codec(&r);
    let lab = Arc::new(SimulatedLab::new(SimLabConfig { seed: 4, substitution_ppm: 2_000, dropout_ppm: 10_000, ..Default::default() }));
    let m = LabMedia::new(lab, c, Arc::new(MemoryMedia::new()), LabMediaConfig::default()).unwrap();
    conformance(&m).unwrap();
    let _ = std::fs::remove_dir_all(&r);
}
