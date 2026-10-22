//! End-to-end sealing with the real Python worker (AES-256-GCM via `cryptography`). Ignored by plain `cargo test`
//! because it needs the Python package; `tests/v14/test_device_seal.py` runs it with
//! `cargo test --test seal_worker -- --ignored` and VNXD_TEST_PYTHON / VNXD_TEST_KEY / VNXD_TEST_KEY2 set.

use std::sync::Arc;
use std::time::Duration;
use vnxd::addr::{ExtentId, Geometry};
use vnxd::error::ErrorCode;
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::record::FLAG_SEALED;
use vnxd::seal::{SealedMedia, WorkerConfig, WorkerSealer};
use vnxd::volume::{Volume, VolumeConfig};

fn env(k: &str) -> String {
    std::env::var(k).unwrap_or_else(|_| panic!("{k} must be set (run through tests/v14/test_device_seal.py)"))
}

fn sealer(key: &str, staging: &std::path::Path, workers: usize) -> Arc<WorkerSealer> {
    let cmd = vec![
        env("VNXD_TEST_PYTHON"),
        "-m".into(),
        "vnxdna.device.worker".into(),
        "--key-file".into(),
        key.into(),
        "--staging".into(),
        staging.display().to_string(),
    ];
    Arc::new(WorkerSealer::new(WorkerConfig { command: cmd, staging: staging.into(), workers, timeout: Duration::from_secs(30) }).unwrap())
}

#[test]
#[ignore]
fn sealed_volume_over_the_python_worker() {
    let root = std::env::temp_dir().join(format!("vnxd-sealw-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let staging = root.join("staging");
    std::fs::create_dir_all(&staging).unwrap();
    use std::os::unix::fs::PermissionsExt;
    // a shared staging directory is refused; a private one is required
    std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o755)).unwrap();
    let shared = WorkerConfig { command: vec!["python3".into()], staging: staging.clone(), workers: 1, timeout: Duration::from_secs(5) };
    assert_eq!(WorkerSealer::new(shared).err().unwrap().code, ErrorCode::Unauthorized);
    std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o700)).unwrap();
    // plaintext left behind by a crashed process is swept when the sealer starts
    std::fs::write(staging.join("seal-dead-1-7.in"), b"leftover plaintext").unwrap();
    let dev = [0x42u8; 16];
    let raw = Arc::new(MemoryMedia::new());
    let geo = Geometry::new(512, 8, 64).unwrap();
    let mk = |key: &str| -> Arc<dyn MediaAdapter> { Arc::new(SealedMedia::new(raw.clone(), sealer(key, &staging, 2), dev, geo.extent_bytes())) };
    let first = mk(&env("VNXD_TEST_KEY"));
    assert!(!staging.join("seal-dead-1-7.in").exists(), "leftovers swept");
    drop(first);
    let v = Volume::create(&root.join("vol"), geo, dev, FLAG_SEALED, mk(&env("VNXD_TEST_KEY")), VolumeConfig::default()).unwrap();
    let secret = b"VNX sealed extent secret payload. ".repeat(200);
    for e in 0..8u64 {
        v.write_extent(ExtentId(e), &secret[e as usize..e as usize + 4096], 1000).unwrap();
    }
    for e in 0..8u64 {
        assert_eq!(v.read_extent(ExtentId(e)).unwrap(), &secret[e as usize..e as usize + 4096]);
    }
    for at in raw.list().unwrap() {
        assert!(!raw.get(at).unwrap().windows(16).any(|w| w == b"sealed extent se"), "plaintext on the backend at {at:?}");
    }
    // a moved object is refused by AES-GCM authentication
    let g = |e: u64| vnxd::addr::GenerationRef { extent: ExtentId(e), generation: v.mapping(ExtentId(e)).unwrap().unwrap() };
    let other = raw.get(g(1)).unwrap();
    raw.remove(g(0)).unwrap();
    raw.put(g(0), &other).unwrap();
    assert_eq!(v.read_extent(ExtentId(0)).unwrap_err().code, ErrorCode::Corruption);
    drop(v);
    // the wrong key: refused at open, nothing collected
    let before = raw.list().unwrap();
    let e = Volume::open(&root.join("vol"), mk(&env("VNXD_TEST_KEY2")), VolumeConfig::default()).err().unwrap();
    assert_eq!(e.code, ErrorCode::Unauthorized, "{e}");
    assert_eq!(raw.list().unwrap(), before);
    // the right key: open, read
    let (v, _) = Volume::open(&root.join("vol"), mk(&env("VNXD_TEST_KEY")), VolumeConfig::default()).unwrap();
    assert_eq!(v.read_extent(ExtentId(5)).unwrap(), &secret[5..5 + 4096]);
    assert_eq!(std::fs::read_dir(&staging).unwrap().count(), 0, "no staging file is left behind");
    // a worker that cannot start is BACKEND_UNAVAILABLE (retryable), not a crash
    let bad = WorkerSealer::new(WorkerConfig {
        command: vec!["/nonexistent/python".into()],
        staging: staging.clone(),
        workers: 1,
        timeout: Duration::from_secs(5),
    })
    .unwrap();
    use vnxd::seal::Sealer;
    assert_eq!(bad.key_check(&dev).unwrap_err().code, ErrorCode::BackendUnavailable);
    std::fs::remove_dir_all(&root).unwrap();
}
