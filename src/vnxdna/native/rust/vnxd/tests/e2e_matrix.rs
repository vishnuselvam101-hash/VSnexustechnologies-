//! V14 Phase 9: the integrated end-to-end matrix (docs/V14_VALIDATION_PLAN.md, master directive §19).
//!
//! Every test drives the WHOLE storage stack through its top-most public interface that exists before the block
//! device (P8): the `IoQueue` (VNX-IO requests with ordering, barriers, cancellation and deadlines) over `BlockIo`
//! over the `ExtentCache` (write-through or write-back) over the `Volume` (journal, generations, recovery) over the
//! fallback layer (`ResilientMedia`: retries, health) over a media adapter, with VNX-RAM leases on every request. Each
//! row checks the outcome recorded in the plan BEFORE it was run. The invariant behind every row: no request ever
//! returns bytes other than the last completed write of that range (an oracle of the device bytes checks it).
//!
//! Faults are deterministic: a seeded generator (`Rng`) chooses offsets, lengths and data; `FaultMedia` rules,
//! volume crash hooks, journal edits and a real SIGKILL of a child process inject the faults.
//!
//! Rows that need the P7 media adapters are `#[ignore = "needs P7/P8 integration: ..."]`: they run the same workload
//! over the adapter returned by [`p7_adapter`], which the integration lead fills in after merging P7. The
//! block-device rows (P8) are in tests/v14/integration/test_e2e_blockdev.py. An ignored row is never a pass.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use vnxd::addr::{ExtentId, Generation, GenerationRef, Geometry};
use vnxd::authz::{MgmtBackend, MgmtSession, Policy};
use vnxd::cache::{CacheMode, ExtentCache};
use vnxd::digest::{hex, sha256};
use vnxd::error::{err, ErrorCode, Result, VnxError};
use vnxd::fallback::{HealthConfig, ResilientMedia, RetryPolicy};
use vnxd::codec::ToyCodec;
use vnxd::dnasim::{DnaSimConfig, DnaSimMedia};
use vnxd::fault::{Fault, FaultMedia, FaultPlan, LatencyMedia, MediaOp, Rule};
use vnxd::image::ImageMedia;
use vnxd::lab::{LabMedia, LabMediaConfig, SimLabConfig, SimulatedLab};
use vnxd::io::{BlockIo, ExtentStore, IoQueue, Op, QueueConfig, Reply, MAX_REQUEST_BYTES};
use vnxd::json::{obj, Value};
use vnxd::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo, MemoryMedia};
use vnxd::ram::{MemoryManager, RamConfig, Subsystem};
use vnxd::seal::{SealedMedia, Sealer, WorkerConfig, WorkerSealer};
use vnxd::state::WriteState;
use vnxd::volume::{FaultHook, RecoveryReport, Stage, Volume, VolumeConfig, JOURNAL_FILE};

// ---------------------------------------------------------------------------------------------------------------
// fixtures

struct Dir(PathBuf);
impl Dir {
    fn new(tag: &str) -> Dir {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!("vnxd-e2e-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
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

/// SplitMix64: the only source of randomness (fixed seeds, identical on every host).
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n.max(1)
    }
    fn bytes(&mut self, n: usize) -> Vec<u8> {
        (0..n).map(|_| self.next() as u8).collect()
    }
}

/// The expected device contents: the last completed write of every byte (zeros where nothing was written).
struct Oracle(Vec<u8>);
impl Oracle {
    fn new(cap: u64) -> Oracle {
        Oracle(vec![0; cap as usize])
    }
    fn apply(&mut self, off: u64, data: &[u8]) {
        self.0[off as usize..off as usize + data.len()].copy_from_slice(data);
    }
    fn get(&self, off: u64, len: u64) -> &[u8] {
        &self.0[off as usize..(off + len) as usize]
    }
}

/// A durable file-per-generation adapter (test fixture, not a device backend: P7 delivers the real file-image
/// adapter). `put` writes a temporary file, fsyncs it, renames it into place and fsyncs the directory, so a SIGKILL
/// leaves either the whole object or none of it.
struct FileMedia {
    dir: PathBuf,
    lock: Mutex<()>,
}
impl FileMedia {
    fn new(dir: &Path) -> FileMedia {
        std::fs::create_dir_all(dir).unwrap();
        FileMedia { dir: dir.to_path_buf(), lock: Mutex::new(()) }
    }
    fn name(at: GenerationRef) -> String {
        format!("g-{:016x}-{:016x}", at.extent.0, at.generation.get())
    }
    fn io(e: std::io::Error) -> VnxError {
        VnxError::new(ErrorCode::BackendUnavailable, e.to_string())
    }
    fn sync_dir(&self) -> Result<()> {
        std::fs::File::open(&self.dir).and_then(|d| d.sync_all()).map_err(FileMedia::io)
    }
}
impl MediaAdapter for FileMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "test-file", durability: Durability::FileSync, max_in_flight: 16 }
    }
    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        use std::io::Write;
        let _g = self.lock.lock().unwrap();
        let fin = self.dir.join(FileMedia::name(at));
        if fin.exists() {
            return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored"));
        }
        let tmp = self.dir.join(format!("tmp-{}", FileMedia::name(at)));
        let mut f = std::fs::File::create(&tmp).map_err(FileMedia::io)?;
        f.write_all(data).and_then(|_| f.sync_all()).map_err(FileMedia::io)?;
        std::fs::rename(&tmp, &fin).map_err(FileMedia::io)?;
        self.sync_dir()?;
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }
    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        match std::fs::read(self.dir.join(FileMedia::name(at))) {
            Ok(b) => Ok(b),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => err(ErrorCode::NotFound, format!("{at:?} not stored")),
            Err(e) => Err(FileMedia::io(e)),
        }
    }
    fn remove(&self, at: GenerationRef) -> Result<()> {
        let _g = self.lock.lock().unwrap();
        match std::fs::remove_file(self.dir.join(FileMedia::name(at))) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => err(ErrorCode::NotFound, format!("{at:?} not stored")),
            Err(e) => Err(FileMedia::io(e)),
        }
    }
    fn list(&self) -> Result<Vec<GenerationRef>> {
        let mut out = Vec::new();
        for e in std::fs::read_dir(&self.dir).map_err(FileMedia::io)? {
            let n = e.map_err(FileMedia::io)?.file_name().to_string_lossy().into_owned();
            let Some(rest) = n.strip_prefix("g-") else { continue };
            let (a, b) = rest.split_once('-').unwrap();
            let gen = Generation::new(u64::from_str_radix(b, 16).unwrap())?;
            out.push(GenerationRef { extent: ExtentId(u64::from_str_radix(a, 16).unwrap()), generation: gen });
        }
        out.sort();
        Ok(out)
    }
    fn sync(&self) -> Result<()> {
        self.sync_dir()
    }
}

/// A keyed test sealer (binding to device, extent and generation; SHA-256 keystream + tag). It exercises the
/// sealing contract end to end without the Python worker; the AES-GCM worker itself is covered by seal_worker.rs.
struct TestSealer([u8; 32]);
impl TestSealer {
    fn bind(&self, d: &[u8; 16], at: GenerationRef) -> Vec<u8> {
        [&self.0[..], d, &at.extent.0.to_be_bytes(), &at.generation.get().to_be_bytes()].concat()
    }
    fn stream(b: &[u8], n: usize) -> Vec<u8> {
        (0..(n as u64 + 31) / 32).flat_map(|i| sha256(&[b, &i.to_be_bytes()[..]].concat())).take(n).collect()
    }
}
impl Sealer for TestSealer {
    fn seal(&self, d: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
        let b = self.bind(d, at);
        let ct: Vec<u8> = plain.iter().zip(TestSealer::stream(&b, plain.len())).map(|(p, k)| p ^ k).collect();
        Ok([&ct[..], &sha256(&[&b[..], &ct[..]].concat())[..]].concat())
    }
    fn open(&self, d: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
        if sealed.len() < 32 {
            return err(ErrorCode::Corruption, "short sealed object");
        }
        let (ct, tag) = sealed.split_at(sealed.len() - 32);
        let b = self.bind(d, at);
        if sha256(&[&b[..], ct].concat())[..] != *tag {
            return err(ErrorCode::Corruption, "sealed extent failed authentication");
        }
        Ok(ct.iter().zip(TestSealer::stream(&b, ct.len())).map(|(c, k)| c ^ k).collect())
    }
    fn key_check(&self, d: &[u8; 16]) -> Result<[u8; 32]> {
        Ok(sha256(&[&self.0[..], d, b"kcv"].concat()))
    }
}

/// The P7 adapter under test, by kind, over a backend kept in `dir` (opened again when it exists, so a restart can
/// build a fresh adapter over the same stored bytes): "file-image" (ImageMedia), "dna-sim" (DnaSimMedia over an
/// image store, TEST-ONLY ToyCodec, SIMULATED channel), "fault-injection" (seeded FaultMedia over an image, faults off
/// until a row sets a plan), "lab" (LabMedia over the SimulatedLab of the vnx.dna-lab/1 interface; in-memory, so it is
/// not reopened). The real VNX-DNA codec behind these adapters is exercised by tests/media_worker.rs.
fn p7_adapter(kind: &str, dir: &Path) -> Option<Arc<dyn MediaAdapter>> {
    let image = |d: &Path| Arc::new(ImageMedia::open_or_create(d).unwrap());
    Some(match kind {
        "file-image" => image(dir),
        "dna-sim" => {
            let cfg = DnaSimConfig { channel: Some("toy-sub-1000".into()), seed: 72, ..Default::default() };
            Arc::new(DnaSimMedia::new(image(dir), Arc::new(ToyCodec), cfg).unwrap())
        }
        "fault-injection" => Arc::new(FaultMedia::seeded(image(dir), FaultPlan { seed: 73, entries: Vec::new() })),
        "lab" => {
            let lab = Arc::new(SimulatedLab::new(SimLabConfig {
                seed: 74, substitution_ppm: 200, synthesis_failure_ppm: 50_000, sequencing_failure_ppm: 50_000, aliquots: 100_000,
                ..Default::default()
            }));
            let cfg = LabMediaConfig { job_retries: 8, ..Default::default() };
            Arc::new(LabMedia::new(lab, Arc::new(ToyCodec), Arc::new(MemoryMedia::new()), cfg).unwrap())
        }
        _ => return None,
    })
}

// ---------------------------------------------------------------------------------------------------------------
// the integrated stack

#[derive(Clone)]
struct Cfg {
    block: u32,
    bpe: u32,
    blocks: u64,
    cache: Option<CacheMode>,
    ram_total: u64,
    workers: usize,
    depth: usize,
    vcfg: VolumeConfig,
    /// modelled backend latency (put, get) between the fault layer and the fallback layer
    latency: Option<(Duration, Duration)>,
    readahead: u32,
    /// overrides the cache quota of the RamConfig::split budget
    cache_quota: Option<u64>,
    /// the fallback layer's health probe interval (how soon an unavailable backend is tried again)
    probe: Duration,
}
impl Default for Cfg {
    fn default() -> Cfg {
        // 4 KiB extents, 1 MiB device
        Cfg { block: 512, bpe: 8, blocks: 2048, cache: None, ram_total: 64 << 20, workers: 4, depth: 64, vcfg: VolumeConfig::default(),
              latency: None, readahead: 0, cache_quota: None,
              probe: Duration::from_secs(3600) }
    }
}
impl Cfg {
    fn cached(mode: CacheMode) -> Cfg {
        Cfg { cache: Some(mode), ..Cfg::default() }
    }
    fn geometry(&self) -> Geometry {
        Geometry::new(self.block, self.bpe, self.blocks).unwrap()
    }
}

fn resilient(base: Arc<dyn MediaAdapter>) -> Arc<ResilientMedia> {
    resilient_probe(base, Duration::from_secs(3600))
}

fn resilient_probe(base: Arc<dyn MediaAdapter>, probe: Duration) -> Arc<ResilientMedia> {
    let health = HealthConfig { degrade_after: 1, unavailable_after: 3, probe_interval: probe, score_alpha: 0.2 };
    let retry = RetryPolicy { max_attempts: 3, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    Arc::new(ResilientMedia::new(base, retry, health).unwrap())
}

struct Stack {
    // field order = drop order: the queue stops before the cache and the volume go
    q: Option<IoQueue>,
    cache: Option<Arc<ExtentCache>>,
    vol: Arc<Volume>,
    ram: Arc<MemoryManager>,
    rm: Arc<ResilientMedia>,
    /// the media below the fallback layer (survives a restart, like a backend outlives the service)
    base: Arc<dyn MediaAdapter>,
    fm: Option<Arc<FaultMedia>>,
    cfg: Cfg,
    dir: PathBuf,
    _d: Option<Dir>,
}

fn wrap_latency(cfg: &Cfg, m: Arc<dyn MediaAdapter>) -> Arc<dyn MediaAdapter> {
    match cfg.latency {
        Some((p, g)) => Arc::new(LatencyMedia::new(m, p, g, 64)),
        None => m,
    }
}

impl Stack {
    /// A new device over a fresh fault-injection media (no rules = a perfect backend).
    fn new(cfg: Cfg, seed: u64) -> Stack {
        Stack::new_hooked(cfg, seed, None)
    }

    fn new_hooked(cfg: Cfg, seed: u64, hook: Option<FaultHook>) -> Stack {
        let d = Dir::new("stk");
        let fm = Arc::new(FaultMedia::new());
        let base = wrap_latency(&cfg, fm.clone());
        let mut s = Stack::create_over(cfg, seed, base, d.0.clone(), hook);
        s.fm = Some(fm);
        s._d = Some(d);
        s
    }

    /// A new device over any media adapter (the P7 rows, the file media).
    fn create_over(cfg: Cfg, seed: u64, base: Arc<dyn MediaAdapter>, dir: PathBuf, hook: Option<FaultHook>) -> Stack {
        let rm = resilient_probe(base.clone(), cfg.probe);
        let mut v = Volume::create(&dir.join("vol"), cfg.geometry(), [seed as u8; 16], 0, rm.clone(), cfg.vcfg.clone()).unwrap();
        if let Some(h) = hook {
            v = v.with_fault_hook(h);
        }
        Stack::assemble(cfg, Arc::new(v), rm, base, dir)
    }

    fn assemble(cfg: Cfg, vol: Arc<Volume>, rm: Arc<ResilientMedia>, base: Arc<dyn MediaAdapter>, dir: PathBuf) -> Stack {
        let mut rc = RamConfig::split(cfg.ram_total);
        if let Some(q) = cfg.cache_quota {
            rc.quotas.insert(Subsystem::Cache, q);
        }
        let ram = MemoryManager::new(rc).unwrap();
        let (cache, io) = match cfg.cache {
            Some(mode) => {
                let c = Arc::new(ExtentCache::new(vol.clone(), ram.clone(), mode).with_readahead(cfg.readahead));
                let io = BlockIo::over(c.clone() as Arc<dyn ExtentStore>, vol.clone()).unwrap().with_ram(ram.clone());
                (Some(c), io)
            }
            None => (None, BlockIo::new(vol.clone()).with_ram(ram.clone())),
        };
        let q = IoQueue::start(io, QueueConfig { workers: cfg.workers, depth: cfg.depth }).unwrap();
        Stack { q: Some(q), cache, vol, ram, rm, base, fm: None, cfg, dir, _d: None }
    }

    fn q(&self) -> &IoQueue {
        self.q.as_ref().unwrap()
    }
    fn fm(&self) -> &Arc<FaultMedia> {
        self.fm.as_ref().unwrap()
    }
    fn cap(&self) -> u64 {
        self.cfg.geometry().capacity_bytes()
    }
    fn eb(&self) -> u64 {
        self.cfg.geometry().extent_bytes()
    }

    fn read(&self, off: u64, len: u64) -> Result<Vec<u8>> {
        match self.q().submit(Op::Read { offset: off, len }, None)?.wait()? {
            Reply::Data(d) => Ok(d),
            r => panic!("read answered {r:?}"),
        }
    }
    fn write(&self, off: u64, data: &[u8]) -> Result<WriteState> {
        match self.q().submit(Op::Write { offset: off, data: data.to_vec() }, None)?.wait()? {
            Reply::Written(s) => Ok(s),
            r => panic!("write answered {r:?}"),
        }
    }
    fn flush(&self) -> Result<()> {
        match self.q().submit(Op::Flush, None)?.wait()? {
            Reply::Flushed => Ok(()),
            r => panic!("flush answered {r:?}"),
        }
    }

    /// Stop the service (drain the queue; nothing is flushed here: a test flushes explicitly when it wants the
    /// write-back data to survive) and start it again over the same backend: replay the journal, collect orphans.
    fn restart(mut self) -> (Stack, RecoveryReport) {
        self.q.take().unwrap().shutdown(true).unwrap();
        let Stack { cache, vol, base, cfg, dir, _d, fm, .. } = self;
        drop(cache);
        drop(vol);
        let rm = resilient(base.clone());
        let (v, rep) = Volume::open(&dir.join("vol"), rm.clone(), cfg.vcfg.clone()).unwrap();
        let mut s = Stack::assemble(cfg, Arc::new(v), rm, base, dir);
        s.fm = fm;
        s._d = _d;
        (s, rep)
    }

    /// Whole-device comparison with the oracle, in reads of `chunk` bytes.
    fn check_all(&self, o: &Oracle, chunk: u64) {
        let mut off = 0;
        while off < self.cap() {
            let len = chunk.min(self.cap() - off);
            assert!(self.read(off, len).unwrap() == o.get(off, len), "device differs from the oracle at {off}+{len}");
            off += len;
        }
    }

    /// No stored generation without a mapping (+ the marker), and no request memory left leased.
    fn check_clean(&self) {
        let mapped = (0..self.cfg.geometry().extent_count()).filter(|e| self.vol.mapping(ExtentId(*e)).unwrap().is_some()).count();
        assert_eq!(self.base.list().unwrap().len(), mapped + 1, "orphan or missing generations");
        assert_eq!(self.ram.usage(Subsystem::Io).in_use, 0, "I/O working memory leaked");
    }
}

/// A random request inside the device: offsets and lengths unaligned, up to `max_extents` extents long.
fn random_range(r: &mut Rng, cap: u64, eb: u64, max_extents: u64) -> (u64, u64) {
    let len = 1 + r.below(eb * max_extents);
    let off = r.below(cap - len + 1);
    (off, len)
}

fn random_workload(s: &Stack, o: &mut Oracle, r: &mut Rng, ops: usize, max_extents: u64) {
    for _ in 0..ops {
        let (off, len) = random_range(r, s.cap(), s.eb(), max_extents);
        if r.below(3) == 0 {
            assert!(s.read(off, len).unwrap() == o.get(off, len), "read {off}+{len}");
        } else {
            let data = r.bytes(len as usize);
            s.write(off, &data).unwrap();
            o.apply(off, &data);
        }
    }
}

/// Seeds of the randomized concurrency rows: `default`, or VNX_E2E_SEEDS for a longer soak run.
fn soak_seeds(default: u64) -> u64 {
    std::env::var("VNX_E2E_SEEDS").ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

const MODES: [Option<CacheMode>; 3] = [None, Some(CacheMode::WriteThrough), Some(CacheMode::WriteBack)];

// ---------------------------------------------------------------------------------------------------------------
// R01-R06: data path

/// R01 sequential read/write: the whole device written in 16 KiB requests, read back in 7 KiB requests, in every cache
/// mode, then again after a flush + restart. Expected: identical bytes; no orphans; no leaked I/O memory.
#[test]
fn r01_sequential_rw() {
    for (i, mode) in MODES.iter().enumerate() {
        let s = Stack::new(Cfg { cache: *mode, ..Cfg::default() }, 1 + i as u64);
        let mut o = Oracle::new(s.cap());
        let mut r = Rng(100 + i as u64);
        let mut off = 0;
        while off < s.cap() {
            let data = r.bytes(16384.min((s.cap() - off) as usize));
            s.write(off, &data).unwrap();
            o.apply(off, &data);
            off += data.len() as u64;
        }
        s.check_all(&o, 7 * 1024);
        s.flush().unwrap();
        let (s, rep) = s.restart();
        assert!(rep.torn_tail.is_none() && rep.orphans_removed.is_empty() && rep.damaged.is_empty(), "{rep:?}");
        s.check_all(&o, 7 * 1024);
        s.check_clean();
    }
}

/// R02 random read/write: 600 seeded requests (unaligned, up to 3 extents) per seed and cache mode, every read checked.
#[test]
fn r02_random_rw() {
    for seed in 0..3u64 {
        for mode in MODES {
            let s = Stack::new(Cfg { cache: mode, ..Cfg::default() }, seed);
            let mut o = Oracle::new(s.cap());
            random_workload(&s, &mut o, &mut Rng(seed), 600, 3);
            s.check_all(&o, 64 * 1024);
            s.flush().unwrap();
            s.check_clean();
        }
    }
}

/// R03 small files: 1-511-byte writes (sub-block, read-modify-write of one extent) at random offsets.
#[test]
fn r03_small_writes() {
    for mode in MODES {
        let s = Stack::new(Cfg { cache: mode, ..Cfg::default() }, 3);
        let mut o = Oracle::new(s.cap());
        let mut r = Rng(3);
        for _ in 0..800 {
            let len = 1 + r.below(511);
            let off = r.below(s.cap() - len);
            let data = r.bytes(len as usize);
            s.write(off, &data).unwrap();
            o.apply(off, &data);
            if r.below(4) == 0 {
                assert!(s.read(off, len).unwrap() == o.get(off, len));
            }
        }
        s.check_all(&o, 32 * 1024);
    }
}

/// R04 large files: 8 MiB requests spanning 2048 extents (more than one map_batch transaction), the 64 MiB request
/// limit, and a request past the end. Expected: exact readback; > MAX_REQUEST_BYTES is INVALID_ARGUMENT at submission;
/// past the end is OUT_OF_RANGE at submission; neither changes anything.
#[test]
fn r04_large_requests_and_limits() {
    for mode in [None, Some(CacheMode::WriteBack)] {
        let cfg = Cfg { blocks: 32768, cache: mode, ram_total: 256 << 20, ..Cfg::default() }; // 16 MiB, 4096 extents
        let s = Stack::new(cfg, 4);
        let mut o = Oracle::new(s.cap());
        let mut r = Rng(4);
        for off in [0u64, 8 << 20, 3 * 4096 + 77] {
            let len = (8u64 << 20).min(s.cap() - off);
            let data = r.bytes(len as usize);
            assert!(s.write(off, &data).unwrap().is_durable() || mode.is_some());
            o.apply(off, &data);
            assert!(s.read(off, len).unwrap() == o.get(off, len));
        }
        let e = s.q().submit(Op::Read { offset: 0, len: MAX_REQUEST_BYTES + 1 }, None).err().unwrap();
        assert_eq!(e.code, ErrorCode::InvalidArgument);
        let e = s.q().submit(Op::Write { offset: s.cap() - 10, data: vec![1; 11] }, None).err().unwrap();
        assert_eq!(e.code, ErrorCode::OutOfRange);
        s.check_all(&o, 1 << 20);
    }
}

/// R05 partial overwrites: overlapping unaligned writes crossing extent boundaries over existing data; every extent
/// keeps exactly one live generation.
#[test]
fn r05_partial_overwrites() {
    for mode in MODES {
        let s = Stack::new(Cfg { cache: mode, ..Cfg::default() }, 5);
        let mut o = Oracle::new(s.cap());
        let mut r = Rng(5);
        let base = r.bytes(s.cap() as usize);
        s.write(0, &base).unwrap();
        o.apply(0, &base);
        let eb = s.eb();
        for i in 0..300u64 {
            // around an extent boundary: the tail of one extent and the head of the next, overlapping the last write
            let b = (1 + r.below(s.cap() / eb - 2)) * eb;
            let off = b - 1 - r.below(eb / 2);
            let len = 2 + r.below(eb);
            let data = vec![(i % 251) as u8 + 1; len as usize];
            s.write(off, &data).unwrap();
            o.apply(off, &data);
        }
        s.check_all(&o, 4096 * 3 + 5);
        s.flush().unwrap();
        s.check_clean();
    }
}

/// R06 concurrency: 4 submitters x 250 requests on a 16-extent hot region (most requests conflict), 4 outstanding
/// requests per submitter (reads, writes and flush barriers), without cache, write-through and write-back, with and
/// without read-ahead. The queue orders conflicting requests by sequence number, so replaying the completed
/// requests in sequence order through the oracle must reproduce every read. Expected: all requests succeed, every
/// read equals the oracle at its sequence point, the final device equals the oracle.
#[test]
fn r06_concurrent_readers_writers() {
    let configs = [(None, 0), (Some(CacheMode::WriteThrough), 0), (Some(CacheMode::WriteBack), 0),
                   (Some(CacheMode::WriteThrough), 4), (Some(CacheMode::WriteBack), 4)];
    for (k, (mode, readahead)) in (0..soak_seeds(1)).flat_map(|_| configs.iter()).enumerate() {
        let s = Arc::new(Stack::new(Cfg { cache: *mode, readahead: *readahead, workers: 4, ..Cfg::default() }, 6));
        let hot = 16 * s.eb();
        let log: Arc<Mutex<Vec<(u64, Op, Reply)>>> = Arc::new(Mutex::new(Vec::new()));
        std::thread::scope(|sc| {
            for t in 0..4u64 {
                let (s, log) = (s.clone(), log.clone());
                sc.spawn(move || {
                    let mut r = Rng(600 + 10 * k as u64 + t);
                    for _ in 0..(250 / 4) {
                        let mut tickets = Vec::new();
                        for _ in 0..4 {
                            let (off, len) = random_range(&mut r, hot, s.eb(), 2);
                            let op = match r.below(10) {
                                0..=2 => Op::Read { offset: off, len },
                                3 => Op::Flush,
                                _ => Op::Write { offset: off, data: r.bytes(len as usize) },
                            };
                            let tk = s.q().submit(op.clone(), None).unwrap();
                            tickets.push((tk.seq, op, tk));
                        }
                        for (seq, op, tk) in tickets {
                            let rep = tk.wait().unwrap();
                            log.lock().unwrap().push((seq, op, rep));
                        }
                    }
                });
            }
        });
        let mut log = std::mem::take(&mut *log.lock().unwrap());
        log.sort_by_key(|x| x.0);
        let mut o = Oracle::new(s.cap());
        for (seq, op, rep) in &log {
            match (op, rep) {
                (Op::Write { offset, data }, Reply::Written(_)) => o.apply(*offset, data),
                (Op::Read { offset, len }, Reply::Data(d)) => assert!(d == o.get(*offset, *len), "read #{seq} saw bytes of another order"),
                (Op::Flush, Reply::Flushed) => {}
                x => panic!("unexpected {x:?}"),
            }
        }
        s.check_all(&o, 16 * 1024);
    }
}

/// R06b concurrency under transient and lying-backend faults: R06's workload with a seeded schedule of 40 fault
/// rules (busy, timeout, unavailable, lost acknowledgement, bit rot, short read, lost object, silent corruption, wrong
/// receipt, torn put) on the backend. Expected: every successful read equals the oracle at its sequence point; a
/// failed write has no effect (the previous mapping is unchanged); every error is typed; after the faults are cleared
/// the whole device equals the oracle and holds no orphan generations.
#[test]
fn r06b_concurrent_io_under_backend_faults() {
    const FAULTS: [Fault; 11] = [Fault::Busy, Fault::Timeout, Fault::Unavailable, Fault::AckLost, Fault::BitRot, Fault::ShortRead,
                                 Fault::Lost, Fault::SilentCorruptWrite, Fault::WrongReceipt, Fault::TornPut, Fault::CorruptAckLost];
    for seed in 0..soak_seeds(3) {
        for (k, mode) in MODES.iter().enumerate() {
            let cfg = Cfg { cache: *mode, workers: 4, probe: Duration::from_millis(5), ..Cfg::default() };
            let s = Arc::new(Stack::new(cfg, 60 + seed));
            let hot = 16 * s.eb();
            let mut o = Oracle::new(s.cap());
            let init = Rng(6000 + seed).bytes(hot as usize);
            s.write(0, &init).unwrap();
            s.flush().unwrap();
            o.apply(0, &init);
            let mut r = Rng(6100 + seed * 7 + k as u64);
            for _ in 0..40 {
                let f = FAULTS[r.below(FAULTS.len() as u64) as usize];
                let op = match f {
                    Fault::BitRot | Fault::ShortRead | Fault::Lost => MediaOp::Get,
                    _ => MediaOp::Put,
                };
                s.fm().add(Rule { op, fault: f, skip: r.below(300), times: 1 + r.below(2) });
            }
            let log: Arc<Mutex<Vec<(u64, Op, Result<Reply>)>>> = Arc::new(Mutex::new(Vec::new()));
            std::thread::scope(|sc| {
                for t in 0..4u64 {
                    let (s, log) = (s.clone(), log.clone());
                    sc.spawn(move || {
                        let mut r = Rng(6200 + 100 * seed + 10 * k as u64 + t);
                        for _ in 0..30 {
                            let mut tickets = Vec::new();
                            for _ in 0..4 {
                                let (off, len) = random_range(&mut r, hot, s.eb(), 2);
                                let op = match r.below(10) {
                                    0..=2 => Op::Read { offset: off, len },
                                    3 => Op::Flush,
                                    _ => Op::Write { offset: off, data: r.bytes(len as usize) },
                                };
                                let tk = s.q().submit(op.clone(), None).unwrap();
                                tickets.push((tk.seq, op, tk));
                            }
                            for (seq, op, tk) in tickets {
                                let rep = tk.wait();
                                log.lock().unwrap().push((seq, op, rep));
                            }
                        }
                    });
                }
            });
            let mut log = std::mem::take(&mut *log.lock().unwrap());
            log.sort_by_key(|x| x.0);
            let mut errors = 0;
            for (seq, op, rep) in &log {
                match (op, rep) {
                    (Op::Write { offset, data }, Ok(Reply::Written(_))) => o.apply(*offset, data),
                    (Op::Read { offset, len }, Ok(Reply::Data(d))) => {
                        assert!(d == o.get(*offset, *len), "seed {seed} mode {mode:?}: read #{seq} returned other bytes")
                    }
                    (Op::Flush, Ok(Reply::Flushed)) => {}
                    (_, Err(e)) => {
                        assert!(e.code != ErrorCode::Internal, "#{seq}: {e:?}");
                        errors += 1
                    }
                    x => panic!("unexpected {x:?}"),
                }
            }
            assert!(errors > 0, "the fault schedule fired");
            s.fm().clear();
            std::thread::sleep(Duration::from_millis(10));
            let mut flushed = s.flush();
            for _ in 0..3 {
                if flushed.is_ok() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
                flushed = s.flush();
            }
            flushed.unwrap();
            s.check_all(&o, 4096);
            let s = Arc::try_unwrap(s).ok().unwrap();
            let (s, _) = s.restart();
            s.check_all(&o, 4096);
            s.check_clean();
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------
// R07-R10: cache, flush, restart

/// R07 cache hit/miss: on a restarted device (empty cache) the first read of an extent is a miss and the second a hit;
/// a write after a cached read is never followed by a stale read.
#[test]
fn r07_cache_hit_miss() {
    let s = Stack::new(Cfg::cached(CacheMode::WriteThrough), 7);
    let mut r = Rng(7);
    let data = r.bytes(8 * 4096);
    s.write(0, &data).unwrap();
    let (s, _) = s.restart();
    let c = s.cache.as_ref().unwrap();
    for e in 0..8u64 {
        let (h0, m0) = (c.stats.hits.load(Ordering::Relaxed), c.stats.misses.load(Ordering::Relaxed));
        assert!(s.read(e * 4096, 4096).unwrap() == data[(e * 4096) as usize..((e + 1) * 4096) as usize]);
        assert_eq!((c.stats.hits.load(Ordering::Relaxed) - h0, c.stats.misses.load(Ordering::Relaxed) - m0), (0, 1), "first read: miss");
        s.read(e * 4096 + 100, 50).unwrap();
        assert_eq!(c.stats.hits.load(Ordering::Relaxed) - h0, 1, "second read: hit");
        let new = r.bytes(300);
        s.write(e * 4096 + 1000, &new).unwrap();
        assert!(s.read(e * 4096 + 1000, 300).unwrap() == new, "stale read after a write");
    }
}

/// R08 dirty eviction: write-back with a cache quota of 4 extents, 64 single-extent writes (STAGED), reads of all of
/// them, then a writeback failure (backend unavailable) and its retry. Expected: reads always return the last write;
/// evictions write dirty extents back; a failed flush is reported and keeps the data dirty (readable); the retried
/// flush (after the fallback layer's health probe interval) makes everything durable (visible after a restart).
#[test]
fn r08_dirty_eviction_and_failed_writeback() {
    let cfg = Cfg { cache: Some(CacheMode::WriteBack), cache_quota: Some(4 * 4096), probe: Duration::from_millis(20), ..Cfg::default() };
    let s = Stack::new(cfg, 8);
    let mut o = Oracle::new(s.cap());
    let mut r = Rng(8);
    for e in 0..64u64 {
        let d = r.bytes(4096);
        assert_eq!(s.write(e * 4096, &d).unwrap(), WriteState::Staged);
        o.apply(e * 4096, &d);
    }
    let c = s.cache.as_ref().unwrap();
    assert!(c.stats.evictions.load(Ordering::Relaxed) > 0 && c.stats.writebacks.load(Ordering::Relaxed) > 0);
    s.check_all(&o, 4096);
    for e in 0..3u64 {
        let d = r.bytes(4096);
        s.write(e * 4096, &d).unwrap();
        o.apply(e * 4096, &d);
    }
    s.fm().add(Rule::always(MediaOp::Put, Fault::Unavailable));
    assert!(s.flush().is_err(), "a failed writeback is reported");
    assert!(!c.dirty_extents().is_empty(), "and its data stays dirty");
    s.check_all(&o, 4096);
    s.fm().clear();
    // the backend is back: once the health probe interval has passed, the next flush writes everything back
    std::thread::sleep(Duration::from_millis(30));
    s.flush().unwrap();
    assert!(c.dirty_extents().is_empty());
    let (s, _) = s.restart();
    s.check_all(&o, 4096);
    s.check_clean();
}

/// R09 flush/sync: write-through acknowledges VERIFIED (durable without a flush); write-back acknowledges STAGED
/// (volatile) and only a flush makes it durable; a backend sync failure fails the flush (retryable, reported).
#[test]
fn r09_flush_and_durability() {
    let mut r = Rng(9);
    // write-through: durable at once
    let s = Stack::new(Cfg::cached(CacheMode::WriteThrough), 9);
    let d = r.bytes(5000);
    assert_eq!(s.write(100, &d).unwrap(), WriteState::Verified);
    let (s, _) = s.restart();
    assert!(s.read(100, 5000).unwrap() == d);
    drop(s);
    // write-back: STAGED is lost without a flush, durable after one
    let s = Stack::new(Cfg::cached(CacheMode::WriteBack), 9);
    let (a, b) = (r.bytes(4096), r.bytes(4096));
    assert_eq!(s.write(0, &a).unwrap(), WriteState::Staged);
    s.flush().unwrap();
    assert_eq!(s.write(0, &b).unwrap(), WriteState::Staged);
    let (s, _) = s.restart(); // the process stops before a flush: the volatile acknowledgement is lost
    assert!(s.read(0, 4096).unwrap() == a, "unflushed write-back data must not survive (nor corrupt the old)");
    // a barrier orders the requests around it, and a failing backend sync fails it
    s.fm().add(Rule::always(MediaOp::Sync, Fault::Busy));
    let e = s.flush().unwrap_err();
    assert!(e.code.class().as_str() == "retryable" || e.code == ErrorCode::ReadOnly, "{e:?}");
}

/// R10 device restart: a random workload, a clean stop (drain) and restart; then a stop WITHOUT drain while requests
/// are queued behind a slow backend. Expected: the clean restart replays without orphans or damage; without drain,
/// every queued request fails SHUTTING_DOWN and has no effect, every started one completes; the device after the
/// restart equals the oracle of the requests that succeeded.
#[test]
fn r10_device_restart() {
    let s = Stack::new(Cfg::cached(CacheMode::WriteThrough), 10);
    let mut o = Oracle::new(s.cap());
    random_workload(&s, &mut o, &mut Rng(10), 300, 2);
    let (s, rep) = s.restart();
    assert!(rep.torn_tail.is_none() && rep.orphans_removed.is_empty() && rep.damaged.is_empty());
    s.check_all(&o, 64 * 1024);
    drop(s);

    let cfg = Cfg { workers: 1, latency: Some((Duration::from_millis(15), Duration::ZERO)), ..Cfg::default() };
    let mut s = Stack::new(cfg, 11);
    let mut r = Rng(11);
    let mut tickets = Vec::new();
    for i in 0..12u64 {
        let d = r.bytes(1000);
        let off = i * 4096 + 7;
        tickets.push((off, d.clone(), s.q().submit(Op::Write { offset: off, data: d }, None).unwrap()));
    }
    std::thread::sleep(Duration::from_millis(20));
    s.q.take().unwrap().shutdown(false).unwrap();
    let mut o = Oracle::new(s.cap());
    let (mut done, mut refused) = (0, 0);
    for (off, d, t) in tickets {
        match t.wait() {
            Ok(Reply::Written(_)) => {
                o.apply(off, &d);
                done += 1
            }
            Err(e) => {
                assert_eq!(e.code, ErrorCode::ShuttingDown);
                refused += 1
            }
            x => panic!("{x:?}"),
        }
    }
    assert!(done >= 1 && refused >= 1, "done {done} refused {refused}");
    s.q = Some(IoQueue::start(BlockIo::new(s.vol.clone()), QueueConfig { workers: 1, depth: 8 }).unwrap());
    let (s, rep) = s.restart();
    assert!(rep.orphans_removed.is_empty(), "{rep:?}");
    s.check_all(&o, 64 * 1024);
}

// ---------------------------------------------------------------------------------------------------------------
// R11-R13: crashes, journal, damaged extents

/// R11a process crash at every commit stage of a 3-extent write through the queue (simulated by the volume's crash
/// hook). Expected: after restart the 3 extents are all old (crash before the map record is durable) or all new
/// (after it); the other extents are untouched; orphans are collected; a torn map record is reported.
#[test]
fn r11a_crash_at_every_commit_stage_is_atomic() {
    for (stage, new_survives) in [
        (Stage::AfterPut, false),
        (Stage::BeforeMapRecord, false),
        (Stage::TornMapRecord, false),
        (Stage::AfterMapRecord, true),
        (Stage::AfterRemove, true),
    ] {
        for run in 0..5u64 {
            let armed = Arc::new(std::sync::atomic::AtomicBool::new(false));
            let a2 = armed.clone();
            let hook: FaultHook = Arc::new(move |st, _| {
                if st == stage && a2.load(Ordering::SeqCst) {
                    err(ErrorCode::Internal, "simulated crash")
                } else {
                    Ok(())
                }
            });
            let s = Stack::new_hooked(Cfg::default(), run, Some(hook));
            let mut o = Oracle::new(s.cap());
            let mut r = Rng(1100 + run);
            let old = r.bytes(s.cap() as usize);
            s.write(0, &old).unwrap();
            o.apply(0, &old);
            armed.store(true, Ordering::SeqCst);
            let (off, len) = (4096 + 100 + run * 13, 2 * 4096 + 50);
            let new = r.bytes(len as usize);
            assert!(s.write(off, &new).is_err(), "{stage:?}");
            if new_survives {
                o.apply(off, &new);
            }
            let (s, rep) = s.restart();
            assert_eq!(rep.torn_tail.is_some(), stage == Stage::TornMapRecord, "{stage:?}");
            s.check_all(&o, 4096);
            s.check_clean();
        }
    }
}

/// R11b real process crash: a child process writes versioned extents (whole-extent and 2-extent requests) through the
/// queue over a durable file backend and reports every durable write (write-through rounds: each VERIFIED write;
/// write-back rounds: the writes before each completed flush); the parent SIGKILLs it at a seeded moment, restarts the
/// device and checks it, 4 rounds alternating write-through and write-back. Expected: every extent holds a complete version
/// written by the child (never a mix, never foreign bytes) that is not older than the last acknowledged one.
#[test]
fn r11b_sigkill_during_writes_keeps_acknowledged_data() {
    if std::env::var_os("VNX_E2E_CHILD").is_some() {
        return crash_child();
    }
    let d = Dir::new("kill");
    let exe = std::env::current_exe().unwrap();
    let mut acked: BTreeMap<u64, u64> = BTreeMap::new();
    for round in 0..4u64 {
        let mut child = std::process::Command::new(&exe)
            .args(["r11b_sigkill_during_writes_keeps_acknowledged_data", "--exact", "--nocapture", "--test-threads=1"])
            .env("VNX_E2E_CHILD", &d.0)
            .env("VNX_E2E_ROUND", round.to_string())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let out = BufReader::new(child.stdout.take().unwrap());
        let kill_after = 20 + Rng(round).below(40);
        let mut n = 0;
        for line in out.lines() {
            let line = line.unwrap();
            if let Some(rest) = line.strip_prefix("ack ") {
                let mut it = rest.split(' ').map(|x| x.parse::<u64>().unwrap());
                let (e, v) = (it.next().unwrap(), it.next().unwrap());
                let a = acked.entry(e).or_insert(0);
                *a = (*a).max(v);
                n += 1;
                if n == kill_after {
                    child.kill().unwrap(); // SIGKILL
                    break;
                }
            }
        }
        child.wait().unwrap();
        assert!(n >= kill_after, "round {round}: the child stopped after {n} writes");
        // restart in this process and check every extent
        let base: Arc<dyn MediaAdapter> = Arc::new(FileMedia::new(&d.0.join("media")));
        let (v, _rep) = Volume::open(&d.0.join("vol"), resilient(base.clone()), VolumeConfig::default()).unwrap();
        for e in 0..CRASH_EXTENTS {
            let got = v.read_extent(ExtentId(e)).unwrap();
            let ver = version_of(e, &got).unwrap_or_else(|| panic!("round {round}: extent {e} holds bytes no write produced"));
            assert!(ver >= *acked.get(&e).unwrap_or(&0), "round {round}: extent {e} lost acknowledged version");
        }
        assert_eq!(base.list().unwrap().len() as u64, (0..CRASH_EXTENTS).filter(|e| v.mapping(ExtentId(*e)).unwrap().is_some()).count() as u64 + 1);
    }
}

const CRASH_EXTENTS: u64 = 16;

fn versioned(e: u64, v: u64, len: usize) -> Vec<u8> {
    let mut r = Rng(e.wrapping_mul(0x1_0000_0001) ^ v);
    let mut b = r.bytes(len);
    b[..8].copy_from_slice(&e.to_le_bytes());
    b[8..16].copy_from_slice(&v.to_le_bytes());
    b
}

/// The version an extent holds (0 = never written), or None for bytes no write produced.
fn version_of(e: u64, b: &[u8]) -> Option<u64> {
    if b.iter().all(|x| *x == 0) {
        return Some(0);
    }
    let v = u64::from_le_bytes(b[8..16].try_into().unwrap());
    (versioned(e, v, b.len()) == b).then_some(v)
}

fn crash_child() {
    let dir = PathBuf::from(std::env::var_os("VNX_E2E_CHILD").unwrap());
    let round: u64 = std::env::var("VNX_E2E_ROUND").unwrap().parse().unwrap();
    let wb = round % 2 == 1;
    let cfg = Cfg { blocks: CRASH_EXTENTS * 8, cache: wb.then_some(CacheMode::WriteBack), ..Cfg::default() };
    let base: Arc<dyn MediaAdapter> = Arc::new(FileMedia::new(&dir.join("media")));
    let s = if dir.join("vol").join(JOURNAL_FILE).exists() {
        let rm = resilient(base.clone());
        let (v, _) = Volume::open(&dir.join("vol"), rm.clone(), cfg.vcfg.clone()).unwrap();
        Stack::assemble(cfg, Arc::new(v), rm, base, dir.clone())
    } else {
        Stack::create_over(cfg, 42, base, dir.clone(), None)
    };
    let mut r = Rng(9000 + round);
    let mut ver = 1_000_000 * (round + 1);
    let mut staged: Vec<(u64, u64)> = Vec::new();
    loop {
        ver += 1;
        let e = r.below(CRASH_EXTENTS - 1);
        let n = 1 + r.below(2); // one or two extents in one request (one transaction)
        let data: Vec<u8> = (e..e + n).flat_map(|x| versioned(x, ver, 4096)).collect();
        let st = s.write(e * 4096, &data).unwrap();
        staged.extend((e..e + n).map(|x| (x, ver)));
        // write-through: VERIFIED is durable at once; write-back: STAGED is volatile until a flush returns
        if (st == WriteState::Verified && !wb) || (wb && ver % 4 == 0) {
            if wb {
                s.flush().unwrap();
            }
            for (x, v) in staged.drain(..) {
                println!("ack {x} {v}");
            }
        }
    }
}

/// R12 journal recovery: a torn final record pair (crash inside an append), one damaged copy of a seeded record, and
/// both copies of a middle record damaged. Expected: torn tail -> open succeeds, tail reported and cut off, device
/// unchanged, nothing collected; one copy -> repaired, device unchanged; both copies of a middle record -> CORRUPTION refusal
/// with the backend untouched (nothing garbage-collected).
#[test]
fn r12_journal_recovery() {
    let s = Stack::new(Cfg::default(), 12);
    let mut o = Oracle::new(s.cap());
    random_workload(&s, &mut o, &mut Rng(12), 60, 1);
    let (s, _) = s.restart();
    let jp = s.dir.join("vol").join(JOURNAL_FILE);
    let pair_lens = |buf: &[u8]| -> Vec<usize> {
        let p = vnxd::journal::parse(buf).unwrap();
        let lens: Vec<usize> = p.records.iter().map(|r| vnxd::journal::encode_pair(r).unwrap().len()).collect();
        assert_eq!(lens.iter().sum::<usize>(), buf.len());
        lens
    };
    // (a) torn tail: a crash inside an append leaves a partial copy of a record pair at the end
    let mut s = s;
    s.q.take().unwrap().shutdown(true).unwrap();
    let clean = std::fs::read(&jp).unwrap();
    let lens = pair_lens(&clean);
    let last = *lens.last().unwrap();
    let mut torn = clean.clone();
    torn.extend_from_slice(&clean[clean.len() - last..clean.len() - last / 2 - 5]);
    std::fs::write(&jp, &torn).unwrap();
    s.q = Some(IoQueue::start(BlockIo::new(s.vol.clone()), QueueConfig { workers: 1, depth: 8 }).unwrap());
    let (s, rep) = s.restart();
    assert!(rep.torn_tail.is_some(), "the torn tail is reported");
    assert!(rep.orphans_removed.is_empty() && rep.damaged.is_empty(), "{rep:?}");
    assert_eq!(std::fs::read(&jp).unwrap(), clean, "the torn tail is cut off");
    s.check_all(&o, 64 * 1024);
    // (b) one copy of a seeded record damaged at a seeded byte: repaired, nothing changes
    for k in 0..8u64 {
        let jb = std::fs::read(&jp).unwrap();
        let lens = pair_lens(&jb);
        let mut r = Rng(1200 + k);
        let i = r.below(lens.len() as u64) as usize;
        let start: usize = lens[..i].iter().sum();
        let pos = start + (lens[i] / 2) * r.below(2) as usize + r.below(lens[i] as u64 / 2) as usize;
        let mut one = jb.clone();
        one[pos] ^= 0x10;
        std::fs::write(&jp, &one).unwrap();
        let (v, rep) = Volume::open(&s.dir.join("vol"), resilient(s.base.clone()), VolumeConfig::default()).unwrap();
        assert!(rep.journal_repaired >= 1 && rep.damaged.is_empty(), "record {i} byte {pos}: {rep:?}");
        for e in 0..s.cfg.geometry().extent_count() {
            assert!(v.read_extent(ExtentId(e)).unwrap() == o.get(e * 4096, 4096), "after repair at {pos}");
        }
    }
    // (c) both copies of a middle record: refused, nothing collected
    let jb = std::fs::read(&jp).unwrap();
    let lens = pair_lens(&jb);
    let mid = lens.len() / 2;
    let start: usize = lens[..mid].iter().sum();
    let half = lens[mid] / 2;
    let mut both = jb.clone();
    both[start + 20] ^= 1;
    both[start + half + 20] ^= 1;
    std::fs::write(&jp, &both).unwrap();
    let listed = s.base.list().unwrap();
    let e = Volume::open(&s.dir.join("vol"), resilient(s.base.clone()), VolumeConfig::default()).err().unwrap();
    assert_eq!(e.code, ErrorCode::Corruption);
    assert_eq!(s.base.list().unwrap(), listed, "a refused open collects nothing");
}

/// R13 missing and corrupt extents on the backend (object deleted; object bytes flipped), through a fresh cache.
/// Expected: reads of a damaged extent fail CORRUPTION every time (never other bytes, never zeros); the rest of the
/// device reads correctly; the restart lists the damaged extents (missing objects); scrub reports them; a
/// whole-extent write repairs one.
#[test]
fn r13_missing_and_corrupt_extents() {
    for mode in MODES {
        let s = Stack::new(Cfg { cache: mode, ..Cfg::default() }, 13);
        let mut o = Oracle::new(s.cap());
        let d = Rng(13).bytes(s.cap() as usize);
        s.write(0, &d).unwrap();
        o.apply(0, &d);
        s.flush().unwrap();
        let raw = s.fm().raw();
        let at = |e: u64| GenerationRef { extent: ExtentId(e), generation: s.vol.mapping(ExtentId(e)).unwrap().unwrap() };
        raw.remove(at(5)).unwrap(); // missing
        let mut bad = raw.get(at(9)).unwrap();
        bad[1234] ^= 0x40;
        raw.remove(at(9)).unwrap();
        raw.put(at(9), &bad).unwrap(); // corrupt
        let (s, rep) = s.restart();
        assert!(rep.damaged.contains(&ExtentId(5)), "a missing generation is found at restart: {rep:?}");
        for _ in 0..3 {
            for e in [5u64, 9] {
                assert_eq!(s.read(e * 4096 + 10, 100).unwrap_err().code, ErrorCode::Corruption, "extent {e}");
                // a request touching the damaged extent fails as a whole: no partial data
                assert_eq!(s.read(e * 4096 - 10, 30).unwrap_err().code, ErrorCode::Corruption);
            }
        }
        for e in (0..s.cfg.geometry().extent_count()).filter(|e| *e != 5 && *e != 9) {
            assert!(s.read(e * 4096, 4096).unwrap() == o.get(e * 4096, 4096));
        }
        let sc = s.vol.scrub().unwrap();
        let mut failed: Vec<u64> = sc.failed.iter().map(|(e, _)| e.0).collect();
        failed.sort();
        assert_eq!(failed, vec![5, 9]);
        let fix = Rng(131).bytes(4096);
        s.write(9 * 4096, &fix).unwrap();
        o.apply(9 * 4096, &fix);
        assert!(s.read(9 * 4096, 4096).unwrap() == fix, "a whole-extent write repairs the extent");
        s.flush().unwrap();
    }
}

/// R14 duplicate and reordered reads: the same range read 40 times concurrently, and every extent read in reverse
/// and shuffled order, with one injected bit-rot read among them. Expected: every successful read returns the
/// oracle's bytes; at most the injected read fails, and it fails CORRUPTION.
#[test]
fn r14_duplicate_and_reordered_reads() {
    let s = Stack::new(Cfg { workers: 4, ..Cfg::default() }, 14);
    let mut o = Oracle::new(s.cap());
    let d = Rng(14).bytes(s.cap() as usize);
    s.write(0, &d).unwrap();
    o.apply(0, &d);
    s.fm().add(Rule { op: MediaOp::Get, fault: Fault::BitRot, skip: 17, times: 1 });
    let mut ts = Vec::new();
    for _ in 0..40 {
        ts.push((3000, 9000, s.q().submit(Op::Read { offset: 3000, len: 9000 }, None).unwrap()));
    }
    let n = s.cfg.geometry().extent_count();
    let mut order: Vec<u64> = (0..n).rev().collect();
    let mut r = Rng(141);
    for i in (1..order.len()).rev() {
        order.swap(i, r.below(i as u64 + 1) as usize);
    }
    for e in order {
        ts.push((e * 4096, 4096, s.q().submit(Op::Read { offset: e * 4096, len: 4096 }, None).unwrap()));
    }
    let mut failed = 0;
    for (off, len, t) in ts {
        match t.wait() {
            Ok(Reply::Data(b)) => assert!(b == o.get(off, len)),
            Err(e) => {
                assert_eq!(e.code, ErrorCode::Corruption);
                failed += 1
            }
            x => panic!("{x:?}"),
        }
    }
    assert!(failed <= 1);
}

// ---------------------------------------------------------------------------------------------------------------
// R15-R17: backend faults

/// R15 out of space: the backend bound holds 8 extents. Expected: writes to new extents beyond it fail NO_SPACE
/// (permanent, ENOSPC) and change nothing; an overwrite at the bound either succeeds or fails NO_SPACE with the old
/// bytes kept; every extent read afterwards equals the oracle.
#[test]
fn r15_out_of_space() {
    let cfg = Cfg { vcfg: VolumeConfig { max_stored_bytes: 8 * 4096 + 64, ..Default::default() }, ..Cfg::default() };
    let s = Stack::new(cfg, 15);
    let mut o = Oracle::new(s.cap());
    let mut r = Rng(15);
    let mut full = 0;
    for e in 0..12u64 {
        let d = r.bytes(4096);
        match s.write(e * 4096, &d) {
            Ok(_) => o.apply(e * 4096, &d),
            Err(x) => {
                assert_eq!(x.code, ErrorCode::NoSpace, "extent {e}");
                assert_eq!(x.code.errno(), 28);
                full += 1
            }
        }
    }
    assert!(full >= 4, "{full}");
    let d = r.bytes(4096);
    match s.write(0, &d) {
        Ok(_) => o.apply(0, &d),
        Err(x) => assert_eq!(x.code, ErrorCode::NoSpace),
    }
    s.check_all(&o, 4096);
    s.check_clean();
}

/// R16 backend timeouts: one timeout is absorbed by the retries; persistent timeouts fail the write (TIMEOUT, then
/// READ_ONLY once the backend is declared unavailable) without changing data, and reads keep working; a request whose
/// deadline passes while it waits behind a slow request completes TIMEOUT with no effect.
#[test]
fn r16_backend_timeouts_and_deadlines() {
    let s = Stack::new(Cfg::default(), 16);
    let mut o = Oracle::new(s.cap());
    let d = Rng(16).bytes(4 * 4096);
    s.write(0, &d).unwrap();
    o.apply(0, &d);
    s.fm().add(Rule::once(MediaOp::Put, Fault::Timeout));
    let n = Rng(161).bytes(4096);
    assert_eq!(s.write(4096, &n).unwrap(), WriteState::Verified);
    o.apply(4096, &n);
    s.fm().add(Rule::always(MediaOp::Put, Fault::Timeout));
    let mut codes = Vec::new();
    for _ in 0..5 {
        codes.push(s.write(0, &[9; 100]).unwrap_err().code);
        assert!(s.read(0, 4 * 4096).unwrap() == o.get(0, 4 * 4096), "reads keep working");
    }
    assert!(codes.iter().all(|c| *c == ErrorCode::Timeout || *c == ErrorCode::ReadOnly), "{codes:?}");
    assert_eq!(*codes.last().unwrap(), ErrorCode::ReadOnly);
    drop(s);
    // deadlines in the queue
    let cfg = Cfg { workers: 1, latency: Some((Duration::from_millis(40), Duration::ZERO)), ..Cfg::default() };
    let s = Stack::new(cfg, 17);
    let slow = s.q().submit(Op::Write { offset: 0, data: vec![1; 4096] }, None).unwrap();
    let late = s.q().submit(Op::Write { offset: 0, data: vec![2; 4096] }, Some(Instant::now() + Duration::from_millis(5))).unwrap();
    assert!(matches!(slow.wait(), Ok(Reply::Written(_))));
    assert_eq!(late.wait().unwrap_err().code, ErrorCode::Timeout);
    assert!(s.read(0, 4096).unwrap() == vec![1; 4096], "the timed-out write had no effect");
    s.check_clean();
}

/// R17 failed verification: a backend that stores other bytes than given, lies in its receipt or tears the put,
/// under a 3-extent request. Expected: CORRUPTION; none of the 3 extents changes (one transaction); no orphans.
#[test]
fn r17_failed_verification_is_refused_atomically() {
    for fault in [Fault::SilentCorruptWrite, Fault::WrongReceipt, Fault::TornPut, Fault::CorruptAckLost] {
        for skip in 0..3u64 {
            let s = Stack::new(Cfg::default(), 17);
            let mut o = Oracle::new(s.cap());
            let d = Rng(17).bytes(s.cap() as usize);
            s.write(0, &d).unwrap();
            o.apply(0, &d);
            s.fm().add(Rule { op: MediaOp::Put, fault, skip, times: 1 });
            assert_eq!(s.write(4096 + 1, &vec![0xAB; 3 * 4096 - 2]).unwrap_err().code, ErrorCode::Corruption, "{fault:?}/{skip}");
            s.check_all(&o, 4096);
            s.check_clean();
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------
// R18-R21: security, memory, saturation, cancellation

struct DevMgmt<'a>(&'a Stack, std::cell::Cell<bool>);
impl MgmtBackend for DevMgmt<'_> {
    fn capacity(&self) -> Result<Value> {
        let g = self.0.cfg.geometry();
        Ok(obj([
            ("block_size", Value::Int(g.block_size() as i64)),
            ("blocks_per_extent", Value::Int(g.blocks_per_extent() as i64)),
            ("capacity_blocks", Value::Int(g.capacity_blocks() as i64)),
            ("capacity_bytes", Value::Int(g.capacity_bytes() as i64)),
            ("extent_count", Value::Int(g.extent_count() as i64)),
        ]))
    }
    fn health(&self) -> Result<Value> {
        Ok(obj([("detail", self.0.rm.health().to_json()), ("state", Value::Str(self.0.rm.health().state.device_state().into()))]))
    }
    fn stats(&self) -> Result<Value> {
        Ok(obj([("counters", obj([("submitted", Value::Int(self.0.q().metrics().submitted.load(Ordering::Relaxed) as i64))]))]))
    }
    fn shutdown(&self) -> Result<Value> {
        self.1.set(true);
        Ok(obj([]))
    }
}

fn req(id: i64, op: &str, args: Value) -> String {
    obj([
        ("args", args),
        ("id", Value::Int(id)),
        ("kind", Value::Str("request".into())),
        ("op", Value::Str(op.into())),
        ("proto", Value::Str("vnx.mgmt".into())),
        ("v", Value::Int(1)),
    ])
    .canonical()
}

fn code_of(resp: &str) -> Option<String> {
    let v = Value::parse(resp).unwrap();
    let o = v.as_obj().unwrap();
    o.get("error").map(|e| e.as_obj().unwrap()["code"].as_str().unwrap().to_string())
}

/// R18 unauthorized requests: vnx.mgmt sessions over a running device (no token, a reader token, wrong tokens, an
/// admin token), a sealed device opened with another key, and a tampered sealed object. Expected: ops before hello
/// are PROTOCOL; without authentication capacity/health/stats/shutdown are UNAUTHORIZED; a reader cannot shut down;
/// 3 failed authentications cut the session off (UNAUTHORIZED even for a valid token afterwards); only the admin's
/// shutdown reaches the device; the sealed device refuses the wrong key (UNAUTHORIZED, no data) and a tampered object
/// reads as CORRUPTION.
#[test]
fn r18_unauthorized_requests() {
    let s = Stack::new(Cfg::default(), 18);
    let (reader, admin) = ("reader-token-e2e-0123456789abcdef01234567", "admin-token-e2e-0123456789abcdef0123456789");
    let policy = Policy::parse(&format!("reader {}\nadmin {}\n", hex(&sha256(reader.as_bytes())), hex(&sha256(admin.as_bytes())))).unwrap();
    let dev = DevMgmt(&s, std::cell::Cell::new(false));
    let hello = |id| req(id, "hello", obj([("client", Value::Str("e2e".into())), ("versions", Value::Arr(vec![Value::Int(1)]))]));
    let auth = |id, t: &str| req(id, "authenticate", obj([("token", Value::Str(t.into()))]));
    let none = || obj([]);
    // anonymous
    let mut m = MgmtSession::new(&policy, &dev, None, "e2e-anon");
    assert_eq!(code_of(&m.handle(&req(1, "capacity", none()))).as_deref(), Some("PROTOCOL"));
    assert_eq!(code_of(&m.handle(&hello(2))), None);
    for op in ["capacity", "health", "stats", "shutdown"] {
        assert_eq!(code_of(&m.handle(&req(3, op, none()))).as_deref(), Some("UNAUTHORIZED"), "{op}");
    }
    // reader
    let mut m = MgmtSession::new(&policy, &dev, None, "e2e-reader");
    m.handle(&hello(1));
    assert_eq!(code_of(&m.handle(&auth(2, reader))), None);
    let cap = m.handle(&req(3, "capacity", none()));
    assert!(code_of(&cap).is_none() && cap.contains("\"capacity_bytes\":1048576"), "{cap}");
    assert_eq!(code_of(&m.handle(&req(4, "shutdown", none()))).as_deref(), Some("UNAUTHORIZED"));
    // brute force
    let mut m = MgmtSession::new(&policy, &dev, None, "e2e-brute");
    m.handle(&hello(1));
    for i in 0..3 {
        assert_eq!(code_of(&m.handle(&auth(2 + i, &format!("wrong-token-{i}-0123456789abcdef0123456789")))).as_deref(), Some("UNAUTHORIZED"));
    }
    assert_eq!(code_of(&m.handle(&auth(9, admin))).as_deref(), Some("UNAUTHORIZED"), "cut off after 3 failures");
    assert!(!dev.1.get());
    // admin
    let mut m = MgmtSession::new(&policy, &dev, None, "e2e-admin");
    m.handle(&hello(1));
    m.handle(&auth(2, admin));
    assert_eq!(code_of(&m.handle(&req(3, "shutdown", none()))), None);
    assert!(dev.1.get(), "only the admin's shutdown reaches the device");
    drop(m);
    drop(dev);
    drop(s);

    // sealed device: wrong key, tampering
    let d = Dir::new("seal");
    let fm = Arc::new(FaultMedia::new());
    let dev_id = [0x5e; 16];
    let geo = Cfg::default().geometry();
    let sealed = |k: u8| -> Arc<dyn MediaAdapter> { resilient(Arc::new(SealedMedia::new(fm.clone(), Arc::new(TestSealer([k; 32])), dev_id, geo.extent_bytes()))) };
    let v = Volume::create(&d.0.join("vol"), geo, dev_id, 1, sealed(1), VolumeConfig::default()).unwrap();
    let data = Rng(18).bytes(4 * 4096);
    let io = BlockIo::new(Arc::new(v));
    io.write(0, &data).unwrap();
    drop(io);
    let e = Volume::open(&d.0.join("vol"), sealed(2), VolumeConfig::default()).err().unwrap();
    assert_eq!(e.code, ErrorCode::Unauthorized, "{e:?}");
    let e = Volume::open(&d.0.join("vol"), resilient(fm.clone()), VolumeConfig::default()).err().unwrap();
    assert_eq!(e.code, ErrorCode::InvalidArgument, "a sealed volume needs the sealing adapter");
    let (v, _) = Volume::open(&d.0.join("vol"), sealed(1), VolumeConfig::default()).unwrap();
    let at = GenerationRef { extent: ExtentId(2), generation: v.mapping(ExtentId(2)).unwrap().unwrap() };
    let mut b = fm.raw().get(at).unwrap();
    b[3] ^= 1;
    fm.raw().remove(at).unwrap();
    fm.raw().put(at, &b).unwrap();
    let io = BlockIo::new(Arc::new(v));
    assert_eq!(io.read(2 * 4096, 10).unwrap_err().code, ErrorCode::Corruption);
    assert!(io.read(0, 2 * 4096).unwrap() == data[..2 * 4096]);
}

/// R19 memory exhaustion: a 512 KiB budget (io 128 KiB, cache 200 KiB). Expected: a request whose working memory
/// exceeds the io quota is refused INVALID_ARGUMENT (no allocation); 8 concurrent requests that each fit but not
/// together wait for each other and all succeed; peak usage never exceeds a quota or the total; nothing stays leased
/// for I/O afterwards.
#[test]
fn r19_memory_exhaustion() {
    let cfg = Cfg { cache: Some(CacheMode::WriteBack), ram_total: 512 * 1024, workers: 4, ..Cfg::default() };
    let s = Arc::new(Stack::new(cfg, 19));
    let e = s.q().submit(Op::Write { offset: 0, data: vec![1; 100 * 1024] }, None).unwrap().wait().unwrap_err();
    assert_eq!(e.code, ErrorCode::InvalidArgument, "{e:?}");
    let mut o = Oracle::new(s.cap());
    let mut r = Rng(19);
    let mut ts = Vec::new();
    for i in 0..8u64 {
        let d = r.bytes(30 * 1024);
        let off = i * 64 * 1024;
        o.apply(off, &d);
        ts.push(s.q().submit(Op::Write { offset: off, data: d }, None).unwrap());
    }
    for t in ts {
        t.wait().unwrap();
    }
    s.check_all(&o, 30 * 1024);
    for sub in Subsystem::ALL {
        let u = s.ram.usage(sub);
        assert!(u.peak <= s.ram.config().quotas[&sub], "{sub:?} peak {} over quota", u.peak);
    }
    assert!(s.ram.total_peak() <= 512 * 1024);
    s.flush().unwrap();
    s.check_clean();
}

/// R20 saturation: 1 worker, queue depth 4, a 10 ms backend, 40 try_submit writes. Expected: the excess is refused
/// RESOURCE_EXHAUSTED (retryable) at once and counted; the queue never holds more than 4; every accepted write
/// completes; the device equals the oracle of the accepted writes.
#[test]
fn r20_saturation_backpressure() {
    let cfg = Cfg { workers: 1, depth: 4, latency: Some((Duration::from_millis(10), Duration::ZERO)), ..Cfg::default() };
    let s = Stack::new(cfg, 20);
    let mut o = Oracle::new(s.cap());
    let mut r = Rng(20);
    let mut acc = Vec::new();
    let mut refused = 0u64;
    for i in 0..40u64 {
        let d = r.bytes(512);
        match s.q().try_submit(Op::Write { offset: i * 4096, data: d.clone() }, None) {
            Ok(t) => acc.push((i * 4096, d, t)),
            Err(e) => {
                assert_eq!(e.code, ErrorCode::ResourceExhausted);
                refused += 1
            }
        }
    }
    assert!(refused > 0);
    assert_eq!(s.q().metrics().rejected_full.load(Ordering::Relaxed), refused);
    assert!(s.q().metrics().max_queue_depth.load(Ordering::Relaxed) <= 4);
    for (off, d, t) in acc {
        t.wait().unwrap();
        o.apply(off, &d);
    }
    s.check_all(&o, 64 * 1024);
}

/// R21 cancellation: requests queued behind a slow write are cancelled. Expected: each cancelled request completes
/// CANCELLED at once and has no effect; the started request completes; no I/O memory stays leased; the queue keeps
/// serving afterwards.
#[test]
fn r21_cancellation() {
    let cfg = Cfg { workers: 1, latency: Some((Duration::from_millis(50), Duration::ZERO)), ..Cfg::default() };
    let s = Stack::new(cfg, 21);
    let first = s.q().submit(Op::Write { offset: 0, data: vec![7; 4096] }, None).unwrap();
    let queued: Vec<_> = (1..10u64).map(|i| s.q().submit(Op::Write { offset: i * 4096, data: vec![i as u8; 4096] }, None).unwrap()).collect();
    let t0 = Instant::now();
    for t in &queued {
        t.cancel();
    }
    for t in queued {
        assert_eq!(t.wait().unwrap_err().code, ErrorCode::Cancelled);
    }
    assert!(t0.elapsed() < Duration::from_millis(45), "cancellation did not wait for the slow request");
    first.wait().unwrap();
    let mut o = Oracle::new(s.cap());
    o.apply(0, &[7; 4096]);
    s.check_all(&o, 64 * 1024);
    s.check_clean();
}

// ---------------------------------------------------------------------------------------------------------------
// R22-R24: native boundary, configuration, constrained memory

fn private_dir(d: &Path) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let p = d.join("staging");
    std::fs::create_dir_all(&p).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o700)).unwrap();
    p
}

/// R22 native/worker failure at the sealing boundary: the sealing worker process dies at start, or answers garbage.
/// Expected: device creation fails with a typed error (BACKEND_UNAVAILABLE, TIMEOUT or PROTOCOL), never success and
/// never a half-made device (no journal is left, so the create can be retried); no staging file is left behind.
#[test]
fn r22_sealing_worker_failure() {
    for script in ["exit 3", "while read l; do echo not-json; done", "while read l; do :; done"] {
        let d = Dir::new("wk");
        let staging = private_dir(&d.0);
        let sealer = WorkerSealer::new(WorkerConfig {
            command: vec!["sh".into(), "-c".into(), script.into()],
            staging: staging.clone(),
            workers: 1,
            timeout: Duration::from_millis(500),
        })
        .unwrap();
        let geo = Cfg::default().geometry();
        let fm = Arc::new(FaultMedia::new());
        let media = resilient(Arc::new(SealedMedia::new(fm.clone(), Arc::new(sealer), [1; 16], geo.extent_bytes())));
        let e = Volume::create(&d.0.join("vol"), geo, [1; 16], 1, media.clone(), VolumeConfig::default()).err().unwrap();
        assert!(matches!(e.code, ErrorCode::BackendUnavailable | ErrorCode::Timeout | ErrorCode::Protocol), "{script}: {e:?}");
        assert!(fm.raw().list().unwrap().is_empty(), "{script}: nothing stored");
        assert!(!d.0.join("vol").join(JOURNAL_FILE).exists(), "{script}: a failed create leaves no journal (V14-P9-D2)");
        assert!(Volume::open(&d.0.join("vol"), media, VolumeConfig::default()).is_err(), "{script}: no usable half-made device");
        assert_eq!(std::fs::read_dir(&staging).unwrap().count(), 0, "{script}: staging left behind");
    }
}

/// R23 configuration corruption: management policy (garbage, empty, group-writable, symlink), queue and memory
/// configuration out of range, a volume directory opened over another device's backend, and a journal whose superblock
/// is damaged in both copies. Expected: every one is refused with the recorded typed error before any data moves.
#[test]
fn r23_config_corruption() {
    use std::os::unix::fs::PermissionsExt;
    let d = Dir::new("cfg");
    assert_eq!(Policy::parse("admin not-a-digest\n").err().unwrap().code, ErrorCode::InvalidArgument);
    assert_eq!(Policy::parse("# nothing\n").err().unwrap().code, ErrorCode::InvalidArgument);
    let p = d.0.join("policy");
    std::fs::write(&p, format!("admin {}\n", hex(&sha256(b"x")))).unwrap();
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o664)).unwrap();
    assert_eq!(Policy::load(&p).err().unwrap().code, ErrorCode::Unauthorized);
    std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert!(Policy::load(&p).is_ok());
    let l = d.0.join("policy-link");
    std::os::unix::fs::symlink(&p, &l).unwrap();
    assert_eq!(Policy::load(&l).err().unwrap().code, ErrorCode::Unauthorized);
    assert_eq!(Policy::load(&d.0.join("missing")).err().unwrap().code, ErrorCode::NotFound);
    assert_eq!(MemoryManager::new(RamConfig::split(0)).err().unwrap().code, ErrorCode::InvalidArgument);
    let s = Stack::new(Cfg::default(), 23);
    for qc in [QueueConfig { workers: 0, depth: 4 }, QueueConfig { workers: 1, depth: 0 }, QueueConfig { workers: 1, depth: 1 << 20 }] {
        assert_eq!(IoQueue::start(BlockIo::new(s.vol.clone()), qc).err().unwrap().code, ErrorCode::InvalidArgument);
    }
    s.write(0, &[1; 4096]).unwrap();
    // another device's backend under this volume directory
    let other = Stack::new(Cfg::default(), 99);
    let e = Volume::open(&s.dir.join("vol"), resilient(other.base.clone()), VolumeConfig::default()).err().unwrap();
    assert!(matches!(e.code, ErrorCode::InvalidArgument | ErrorCode::Corruption | ErrorCode::Unauthorized), "{e:?}");
    assert_eq!(other.base.list().unwrap().len(), 1, "the other backend is untouched");
    // both copies of the superblock
    let jp = s.dir.join("vol").join(JOURNAL_FILE);
    let mut j = std::fs::read(&jp).unwrap();
    j[20] ^= 1;
    j[52 + 20] ^= 1;
    std::fs::write(&jp, &j).unwrap();
    let listed = s.base.list().unwrap();
    assert_eq!(Volume::open(&s.dir.join("vol"), resilient(s.base.clone()), VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
    assert_eq!(s.base.list().unwrap(), listed);
}

/// R24 constrained memory (a Raspberry-Pi-class budget, SIMULATED on this host: real Pi acceptance is P10/P11 on the
/// hardware): 1 MiB extents, 32 MiB VNX-RAM budget, write-back cache, 2 workers, 200 random requests of up to 2
/// extents. Expected: correct data; no subsystem's peak above its quota; total peak within the budget.
#[test]
fn r24_pi_class_memory_budget() {
    let cfg = Cfg { block: 4096, bpe: 256, blocks: 16 * 256, cache: Some(CacheMode::WriteBack), ram_total: 32 << 20, workers: 2, ..Cfg::default() };
    let s = Stack::new(cfg, 24);
    let mut o = Oracle::new(s.cap());
    let mut r = Rng(24);
    for _ in 0..200 {
        let len = 1 + r.below(2 << 20);
        let off = r.below(s.cap() - len + 1);
        if r.below(3) == 0 {
            assert!(s.read(off, len).unwrap() == o.get(off, len));
        } else {
            let d = r.bytes(len as usize);
            s.write(off, &d).unwrap();
            o.apply(off, &d);
        }
    }
    s.flush().unwrap();
    s.check_all(&o, 1 << 20);
    for sub in Subsystem::ALL {
        assert!(s.ram.usage(sub).peak <= s.ram.config().quotas[&sub], "{sub:?}");
    }
    assert!(s.ram.total_peak() <= 32 << 20);
    s.check_clean();
}

// ---------------------------------------------------------------------------------------------------------------
// M-rows: the P7 media adapters under the integrated stack

fn adapter_workload(kind: &str, ops: usize) {
    let d = Dir::new(kind);
    vnxd::media::conformance(p7_adapter(kind, &d.0.join("conf")).unwrap().as_ref()).unwrap();
    let m = p7_adapter(kind, &d.0.join("media")).unwrap();
    let cfg = Cfg { blocks: 512, ..Cfg::cached(CacheMode::WriteThrough) }; // 64 extents
    let s = Stack::create_over(cfg.clone(), 70, m, d.0.clone(), None);
    let mut o = Oracle::new(s.cap());
    random_workload(&s, &mut o, &mut Rng(70), ops, 2);
    s.flush().unwrap();
    let (s, rep) = s.restart();
    assert!(rep.torn_tail.is_none() && rep.damaged.is_empty() && rep.orphans_removed.is_empty(), "{rep:?}");
    s.check_all(&o, 64 * 1024);
    s.check_clean();
    if kind != "lab" {
        // a new process: a fresh adapter over the same stored backend
        drop(s);
        let base = p7_adapter(kind, &d.0.join("media")).unwrap();
        let (v, rep) = Volume::open(&d.0.join("vol"), resilient(base.clone()), VolumeConfig::default()).unwrap();
        assert!(rep.damaged.is_empty() && rep.orphans_removed.is_empty(), "{rep:?}");
        let s = Stack::assemble(cfg, Arc::new(v), resilient(base.clone()), base, d.0.clone());
        s.check_all(&o, 64 * 1024);
        s.check_clean();
    }
}

/// M01 file-image adapter (P7): conformance, the random workload, restart, reopen by a fresh adapter (a new process).
/// Expected: as R02 + R10; nothing damaged or orphaned.
#[test]
fn m01_file_image_adapter() {
    adapter_workload("file-image", 300);
}

/// M02 DNA-simulator adapter (P7) over an image store, TEST-ONLY codec, SIMULATED substitution channel: conformance,
/// workload, restart, reopen. Expected: every read is the oracle's bytes (the simulated channel's errors are corrected
/// or retried), nothing damaged.
#[test]
fn m02_dna_simulator_adapter() {
    adapter_workload("dna-sim", 150);
}

/// M03 fault-injection adapter (P7): a seeded plan of lying and honest faults (2 % per operation kind) under 300
/// requests. Expected: every successful read equals the oracle; failed writes have no effect; no INTERNAL error;
/// after the plan is cleared the device equals the oracle, also after a restart and a reopen.
#[test]
fn m03_fault_injection_adapter() {
    let d = Dir::new("m03");
    let fm = Arc::new(FaultMedia::seeded(Arc::new(ImageMedia::create(&d.0.join("media")).unwrap()), FaultPlan { seed: 73, entries: Vec::new() }));
    let cfg = Cfg { blocks: 512, probe: Duration::from_millis(5), ..Cfg::cached(CacheMode::WriteThrough) };
    let s = Stack::create_over(cfg, 73, fm.clone(), d.0.clone(), None);
    let mut o = Oracle::new(s.cap());
    fm.set_plan(Some(FaultPlan::lying(73, 20_000)));
    let mut r = Rng(73);
    let mut errors = 0;
    for _ in 0..300 {
        let (off, len) = random_range(&mut r, s.cap(), s.eb(), 2);
        if r.below(3) == 0 {
            match s.read(off, len) {
                Ok(b) => assert!(b == o.get(off, len), "read {off}+{len} returned other bytes"),
                Err(e) => {
                    assert!(e.code != ErrorCode::Internal, "{e:?}");
                    errors += 1
                }
            }
        } else {
            let data = r.bytes(len as usize);
            match s.write(off, &data) {
                Ok(_) => o.apply(off, &data),
                Err(e) => {
                    assert!(e.code != ErrorCode::Internal, "{e:?}");
                    errors += 1
                }
            }
        }
    }
    assert!(errors > 0 && !fm.trace().is_empty(), "the plan fired");
    fm.set_plan(None);
    std::thread::sleep(Duration::from_millis(10));
    s.flush().unwrap();
    s.check_all(&o, 4096);
    let (s, _) = s.restart();
    s.check_all(&o, 4096);
    s.check_clean();
}

/// M04 physical-lab interface (P7 `vnx.dna-lab/1`, SIMULATED lab, TEST-ONLY codec; no equipment exists): conformance
/// and the workload through synthesis / retrieval / sequencing jobs with seeded equipment failures (retried).
/// Expected: every read is the oracle's bytes, after restart too; nothing damaged.
#[test]
fn m04_simulated_lab_interface_adapter() {
    adapter_workload("lab", 100);
}
