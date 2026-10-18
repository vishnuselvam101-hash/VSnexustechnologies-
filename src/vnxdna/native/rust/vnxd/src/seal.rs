//! Sealing at the extent boundary (docs/V14_SECURITY_MODEL.md §3).
//!
//! A volume created with `FLAG_SEALED` stores every extent generation sealed: [`SealedMedia`] wraps the backend
//! adapter and seals on `put`, opens on `get`. The cipher is the existing VNX-DNA security layer (AES-256-GCM with
//! HKDF-SHA256 key derivation, Python `cryptography`/OpenSSL): vnxd never implements a cipher and never holds the
//! key. It calls a [`Sealer`]; the production sealer is [`WorkerSealer`], a pool of Python worker processes
//! (`python -m vnxdna.device.worker`) speaking `vnx.codec-ipc/1` (`key_check`, `seal_extent`, `open_extent`), which
//! read the key file themselves.
//!
//! * the sealed object (format `vnx.sealed-extent/1`, specified with its Python implementation in
//!   `vnxdna.device.seal`) binds the device id, the extent and the generation as associated data, so an object moved
//!   to another extent, generation or device, or replayed from an older generation, fails authentication: CORRUPTION,
//!   never other bytes;
//! * the backend's binding marker is stored with the key's check value appended: opening a sealed volume with a
//!   different key is UNAUTHORIZED at `open`, before anything is read or garbage-collected;
//! * the volume's receipts and map digests stay those of the plaintext (the journal is host-local, inside the trust
//!   boundary); the backend only ever sees sealed bytes.

use crate::addr::GenerationRef;
use crate::digest::{hex, parse_hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo};
use crate::proto::{self, Proto};
use crate::volume::MARKER_EXTENT;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Seals and opens extent generations of one device. Implementations must never return bytes that failed
/// authentication.
pub trait Sealer: Send + Sync {
    fn seal(&self, device_id: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>>;
    fn open(&self, device_id: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>>;
    /// The key's check value for this device (identifies the key without revealing it).
    fn key_check(&self, device_id: &[u8; 16]) -> Result<[u8; 32]>;
}

/// Bytes a sealed object adds to its extent (header + tag of `vnx.sealed-extent/1`).
pub const SEAL_OVERHEAD: u64 = 40 + 16;

pub struct SealedMedia {
    inner: Arc<dyn MediaAdapter>,
    sealer: Arc<dyn Sealer>,
    device_id: [u8; 16],
    /// largest object the backend may hand back (the largest extent + overhead): anything bigger is refused before
    /// it is staged
    max_object: u64,
}

impl SealedMedia {
    /// `max_extent_bytes`: the device's extent size (`Geometry::extent_bytes`).
    pub fn new(inner: Arc<dyn MediaAdapter>, sealer: Arc<dyn Sealer>, device_id: [u8; 16], max_extent_bytes: u64) -> SealedMedia {
        SealedMedia { inner, sealer, device_id, max_object: max_extent_bytes.saturating_add(SEAL_OVERHEAD) }
    }
}

impl MediaAdapter for SealedMedia {
    fn info(&self) -> MediaInfo {
        self.inner.info()
    }

    fn sealed(&self) -> bool {
        true
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let stored = if at.extent == MARKER_EXTENT {
            [data, &self.sealer.key_check(&self.device_id)?[..]].concat()
        } else {
            self.sealer.seal(&self.device_id, at, data)?
        };
        let r = self.inner.put(at, &stored)?;
        if r.length != stored.len() as u64 || r.sha256 != sha256(&stored) {
            return err(ErrorCode::Corruption, format!("backend receipt for {at:?} does not describe the sealed bytes"));
        }
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let stored = self.inner.get(at)?;
        if stored.len() as u64 > self.max_object {
            return err(ErrorCode::Corruption, format!("{at:?}: the backend returned {} bytes, more than any sealed extent", stored.len()));
        }
        if at.extent == MARKER_EXTENT {
            if stored.len() < 32 {
                return err(ErrorCode::Unauthorized, "the backend was not created sealed (its marker carries no key check)");
            }
            let (marker, kcv) = stored.split_at(stored.len() - 32);
            if kcv != self.sealer.key_check(&self.device_id)? {
                return err(ErrorCode::Unauthorized, "the key does not match the key this device was sealed with");
            }
            return Ok(marker.to_vec());
        }
        // the device's key was verified at open (the marker): a per-object key-check or version mismatch is damage
        // to this object, reported like any other, so a scrub lists the extent instead of stopping
        self.sealer.open(&self.device_id, at, &stored).map_err(|e| match e.code {
            ErrorCode::Unauthorized | ErrorCode::VersionMismatch => {
                VnxError::new(ErrorCode::Corruption, format!("{at:?}: {}", e.message))
            }
            _ => e,
        })
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.inner.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.inner.list()
    }

    fn sync(&self) -> Result<()> {
        self.inner.sync()
    }
}

/// How to start the sealing workers.
#[derive(Clone, Debug)]
pub struct WorkerConfig {
    /// program and arguments, e.g. ["python3", "-m", "vnxdna.device.worker", "--key-file", K, "--staging", S]
    pub command: Vec<String>,
    /// the staging directory the workers were given (private to the service, mode 0700; tmpfs recommended)
    pub staging: PathBuf,
    pub workers: usize,
    /// longest wait for one response before the worker is killed and the request fails with TIMEOUT
    pub timeout: Duration,
}

struct Worker {
    child: Child,
    stdin: ChildStdin,
    lines: Receiver<String>,
}

impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// A pool of Python sealing workers (`vnx.codec-ipc/1`). A worker that dies, hangs or breaks the protocol is killed
/// and replaced at the next request; its request fails with BACKEND_UNAVAILABLE or TIMEOUT (retryable).
pub struct WorkerSealer {
    cfg: WorkerConfig,
    slots: Vec<Mutex<Option<Worker>>>,
    next: AtomicU64,
    job: AtomicU64,
    /// staging file names of this process: "seal-<pid>-<start ns>-<job>.in|out" (never reused across restarts)
    prefix: String,
}

/// Longest response line read from a worker (the protocol's document limit plus the newline).
const MAX_LINE: usize = crate::json::MAX_DOC + 1;

/// Staging files are named "seal-<pid hex>-<start ns hex>-<job>.in|out".
/// The staging directory must be a real directory (not a symlink) with no group or other access: only this user
/// (or root) can create, swap or read files in it.
fn check_staging(dir: &std::path::Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let m = std::fs::symlink_metadata(dir).map_err(|e| VnxError::new(ErrorCode::NotFound, format!("staging {}: {e}", dir.display())))?;
    if !m.file_type().is_dir() {
        return err(ErrorCode::InvalidArgument, format!("staging {} is not a directory (symlinks are refused)", dir.display()));
    }
    let mode = m.permissions().mode() & 0o777;
    if mode & 0o077 != 0 {
        return err(ErrorCode::Unauthorized, format!("staging {} has mode {mode:o}: group/other access is not allowed", dir.display()));
    }
    Ok(())
}

fn unavailable(msg: impl Into<String>) -> VnxError {
    VnxError::new(ErrorCode::BackendUnavailable, msg)
}

impl WorkerSealer {
    pub fn new(cfg: WorkerConfig) -> Result<WorkerSealer> {
        if cfg.command.is_empty() || cfg.workers == 0 || cfg.workers > 64 {
            return err(ErrorCode::InvalidArgument, "sealing workers: a command and 1..=64 workers");
        }
        check_staging(&cfg.staging)?;
        // plaintext left by a process that died mid-call: removed before anything else runs. Only files of dead
        // processes: another sealer (this or another live process) may share the directory
        if let Ok(entries) = std::fs::read_dir(&cfg.staging) {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                let Some(rest) = name.strip_prefix("seal-") else { continue };
                let pid = rest.split('-').next().and_then(|p| u32::from_str_radix(p, 16).ok());
                let alive = pid.map_or(false, |p| std::path::Path::new(&format!("/proc/{p}")).exists());
                if !alive {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos());
        let prefix = format!("seal-{:x}-{:x}", std::process::id(), nanos);
        let slots = (0..cfg.workers).map(|_| Mutex::new(None)).collect();
        Ok(WorkerSealer { cfg, slots, next: AtomicU64::new(0), job: AtomicU64::new(1), prefix })
    }

    fn spawn(&self) -> Result<Worker> {
        let mut child = Command::new(&self.cfg.command[0])
            .args(&self.cfg.command[1..])
            // never the service's working directory: `python -m` imports from the current directory first
            .current_dir(&self.cfg.staging)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| unavailable(format!("cannot start the sealing worker: {e}")))?;
        let stdin = child.stdin.take().ok_or_else(|| unavailable("worker stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| unavailable("worker stdout"))?;
        let (tx, lines) = channel();
        std::thread::spawn(move || {
            // bounded lines: a worker that writes more than one protocol document without a newline is cut off
            // (the channel closes and the request fails BACKEND_UNAVAILABLE)
            let mut r = BufReader::new(stdout).take(0);
            loop {
                let mut buf = Vec::new();
                r.set_limit(MAX_LINE as u64 + 1);
                match r.read_until(b'\n', &mut buf) {
                    Ok(0) | Err(_) => return,
                    Ok(_) if buf.last() != Some(&b'\n') => return,
                    Ok(_) => {}
                }
                buf.pop();
                let Ok(l) = String::from_utf8(buf) else { return };
                if tx.send(l).is_err() {
                    return;
                }
            }
        });
        let mut w = Worker { child, stdin, lines };
        let hello = obj([("client", Value::Str(format!("{} {}", crate::NAME, crate::VERSION))), ("versions", Value::Arr(vec![Value::Int(1)]))]);
        let r = self.exchange(&mut w, "hello", hello)?;
        if r.as_obj().and_then(|o| o.get("version")) != Some(&Value::Int(proto::VERSION)) {
            return Err(unavailable("the sealing worker did not negotiate vnx.codec-ipc/1"));
        }
        Ok(w)
    }

    /// One request/response on a worker. Transport failures are BACKEND_UNAVAILABLE / TIMEOUT; a well-formed error
    /// response is returned with its own code.
    fn exchange(&self, w: &mut Worker, op: &str, args: Value) -> Result<Value> {
        let id = self.job.fetch_add(1, Ordering::Relaxed) as i64;
        let req = proto::request(Proto::Codec, id, op, args)?;
        let mut line = req.canonical();
        line.push('\n');
        w.stdin.write_all(line.as_bytes()).and_then(|_| w.stdin.flush()).map_err(|e| unavailable(format!("worker write: {e}")))?;
        let text = match w.lines.recv_timeout(self.cfg.timeout) {
            Ok(t) => t,
            Err(RecvTimeoutError::Timeout) => return err(ErrorCode::Timeout, format!("sealing worker gave no answer to {op} within {:?}", self.cfg.timeout)),
            Err(RecvTimeoutError::Disconnected) => return Err(unavailable("the sealing worker exited")),
        };
        let v = Value::parse(&text).map_err(|e| unavailable(format!("worker sent invalid JSON: {}", e.message)))?;
        let resp = proto::validate_response(Proto::Codec, op, &v).map_err(|e| unavailable(format!("worker broke the protocol: {}", e.message)))?;
        if v.as_obj().and_then(|o| o.get("id")) != Some(&Value::Int(id)) {
            return Err(unavailable("worker answered another request"));
        }
        resp
    }

    /// Run `op` on some worker, (re)starting it as needed; a transport failure discards the worker.
    fn call(&self, op: &str, args: Value) -> Result<Value> {
        let i = (self.next.fetch_add(1, Ordering::Relaxed) % self.slots.len() as u64) as usize;
        let mut slot = self.slots[i].lock().unwrap_or_else(|p| p.into_inner());
        if slot.is_none() {
            *slot = Some(self.spawn()?);
        }
        let w = slot.as_mut().expect("just set");
        let r = self.exchange(w, op, args);
        if let Err(e) = &r {
            if matches!(e.code, ErrorCode::BackendUnavailable | ErrorCode::Timeout | ErrorCode::Protocol) {
                *slot = None; // killed on drop; the next request starts a fresh worker
            }
        }
        // only verdicts about the data or the key pass through; anything else the worker reports (a missing staging
        // file, an existing output, INTERNAL, ...) is a failure of the sealing service, never "not stored" or damage
        r.map_err(|e| match e.code {
            ErrorCode::Corruption | ErrorCode::Unauthorized | ErrorCode::VersionMismatch | ErrorCode::Timeout
            | ErrorCode::BackendUnavailable => e,
            _ => unavailable(format!("sealing worker: {e}")),
        })
    }

    fn transform(&self, op: &str, device_id: &[u8; 16], at: GenerationRef, input: &[u8]) -> Result<Vec<u8>> {
        let extent = i64::try_from(at.extent.0).or_else(|_| err(ErrorCode::Overflow, "extent beyond the protocol range"))?;
        let generation = i64::try_from(at.generation.get()).or_else(|_| err(ErrorCode::Overflow, "generation beyond the protocol range"))?;
        let job = self.job.fetch_add(1, Ordering::Relaxed);
        let (iname, oname) = (format!("{}-{job}.in", self.prefix), format!("{}-{job}.out", self.prefix));
        let (ipath, opath) = (self.cfg.staging.join(&iname), self.cfg.staging.join(&oname));
        let cleanup = || {
            let _ = std::fs::remove_file(&ipath);
            let _ = std::fs::remove_file(&opath);
        };
        let r = (|| {
            write_private(&ipath, input)?;
            let args = obj([
                ("job", Value::Int(job as i64)),
                ("device_id", Value::Str(hex(device_id))),
                ("extent", Value::Int(extent)),
                ("generation", Value::Int(generation)),
                ("input", Value::Str(iname.clone())),
                ("input_sha256", Value::Str(hex(&sha256(input)))),
                ("output", Value::Str(oname.clone())),
            ]);
            let res = self.call(op, args)?;
            let o = res.as_obj().expect("validated");
            let meta = std::fs::symlink_metadata(&opath).map_err(|e| unavailable(format!("sealing output {}: {e}", opath.display())))?;
            if !meta.file_type().is_file() {
                return err(ErrorCode::Corruption, "the sealing output is not a regular file");
            }
            let out = std::fs::read(&opath).map_err(|e| unavailable(format!("sealing output {}: {e}", opath.display())))?;
            let want = o["output_sha256"].as_str().and_then(parse_hex::<32>);
            if want != Some(sha256(&out)) || o["output_bytes"] != Value::Int(out.len() as i64) {
                return err(ErrorCode::Corruption, "the sealing worker's output does not match its report");
            }
            Ok(out)
        })();
        cleanup();
        r
    }
}

fn write_private(path: &std::path::Path, data: &[u8]) -> Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| unavailable(format!("staging {}: {e}", path.display())))?;
    f.write_all(data).map_err(|e| unavailable(format!("staging {}: {e}", path.display())))
}

impl Sealer for WorkerSealer {
    fn seal(&self, device_id: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
        self.transform("seal_extent", device_id, at, plain)
    }

    fn open(&self, device_id: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
        self.transform("open_extent", device_id, at, sealed)
    }

    fn key_check(&self, device_id: &[u8; 16]) -> Result<[u8; 32]> {
        let r = self.call("key_check", obj([("device_id", Value::Str(hex(device_id)))]))?;
        r.as_obj()
            .and_then(|o| o.get("key_check"))
            .and_then(Value::as_str)
            .and_then(parse_hex::<32>)
            .ok_or_else(|| unavailable("key_check result"))
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation, Geometry};
    use crate::journal::tests::TempDir;
    use crate::media::MemoryMedia;
    use crate::record::FLAG_SEALED;
    use crate::volume::{Volume, VolumeConfig};

    /// TEST-ONLY stand-in for the Python sealer: a SHA-256 keystream and a keyed SHA-256 tag. Never shipped, never a
    /// cipher; it only exercises SealedMedia's contract (binding, tamper refusal, key check).
    pub(crate) struct ToySealer(pub [u8; 32]);

    impl ToySealer {
        fn bind(&self, d: &[u8; 16], at: GenerationRef) -> Vec<u8> {
            [&self.0[..], d, &at.extent.0.to_be_bytes(), &at.generation.get().to_be_bytes()].concat()
        }
        fn stream(&self, b: &[u8], n: usize) -> Vec<u8> {
            (0..n.div_ceil(32) as u64).flat_map(|i| sha256(&[b, &i.to_be_bytes()[..]].concat())).take(n).collect()
        }
    }

    impl Sealer for ToySealer {
        fn seal(&self, d: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
            let b = self.bind(d, at);
            let ct: Vec<u8> = plain.iter().zip(self.stream(&b, plain.len())).map(|(p, k)| p ^ k).collect();
            Ok([&ct[..], &sha256(&[&b[..], &ct[..]].concat())[..]].concat())
        }
        fn open(&self, d: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
            if sealed.len() < 32 {
                return err(ErrorCode::Corruption, "short");
            }
            let (ct, tag) = sealed.split_at(sealed.len() - 32);
            let b = self.bind(d, at);
            if sha256(&[&b[..], ct].concat())[..] != *tag {
                return err(ErrorCode::Corruption, "sealed extent failed authentication");
            }
            Ok(ct.iter().zip(self.stream(&b, ct.len())).map(|(c, k)| c ^ k).collect())
        }
        fn key_check(&self, d: &[u8; 16]) -> Result<[u8; 32]> {
            Ok(sha256(&[&self.0[..], d, b"kcv"].concat()))
        }
    }

    const DEV: [u8; 16] = [5; 16];

    fn sealed_vol(d: &TempDir, raw: Arc<MemoryMedia>, key: u8) -> Result<Volume> {
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(raw, Arc::new(ToySealer([key; 32])), DEV, 2048));
        Volume::create(&d.0, Geometry::new(512, 4, 16).unwrap(), DEV, FLAG_SEALED, m, VolumeConfig::default())
    }

    fn reopen(d: &TempDir, raw: Arc<MemoryMedia>, key: u8) -> Result<Volume> {
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(raw, Arc::new(ToySealer([key; 32])), DEV, 2048));
        Volume::open(&d.0, m, VolumeConfig::default()).map(|(v, _)| v)
    }

    #[test]
    fn a_sealed_volume_never_hands_plaintext_to_the_backend() {
        let d = TempDir::new("seal");
        let raw = Arc::new(MemoryMedia::new());
        let v = sealed_vol(&d, raw.clone(), 1).unwrap();
        let data = b"top secret extent payload ".repeat(79);
        let data = &data[..2048];
        v.write_extent(ExtentId(2), data).unwrap();
        assert_eq!(v.read_extent(ExtentId(2)).unwrap(), data);
        for at in raw.list().unwrap() {
            let stored = raw.get(at).unwrap();
            assert!(!stored.windows(10).any(|w| w == b"top secret"), "plaintext reached the backend at {at:?}");
        }
        drop(v);
        assert_eq!(reopen(&d, raw.clone(), 1).unwrap().read_extent(ExtentId(2)).unwrap(), data);
    }

    #[test]
    fn moved_replayed_or_tampered_objects_are_corruption_never_data() {
        let d = TempDir::new("seal");
        let raw = Arc::new(MemoryMedia::new());
        let v = sealed_vol(&d, raw.clone(), 1).unwrap();
        v.write_extent(ExtentId(0), &[1u8; 2048]).unwrap();
        v.write_extent(ExtentId(1), &[2u8; 2048]).unwrap();
        let g = |e: u64| GenerationRef { extent: ExtentId(e), generation: v.mapping(ExtentId(e)).unwrap().unwrap() };
        // extent 1's object copied over extent 0's: the sealer refuses it (bound to its extent)
        let one = raw.get(g(1)).unwrap();
        raw.remove(g(0)).unwrap();
        raw.put(g(0), &one).unwrap();
        assert_eq!(v.read_extent(ExtentId(0)).unwrap_err().code, ErrorCode::Corruption);
        // a flipped byte
        let mut t = raw.get(g(1)).unwrap();
        t[5] ^= 1;
        raw.remove(g(1)).unwrap();
        raw.put(g(1), &t).unwrap();
        assert_eq!(v.read_extent(ExtentId(1)).unwrap_err().code, ErrorCode::Corruption);
        // an older generation's object replayed under the current generation
        v.write_extent(ExtentId(3), &[3u8; 2048]).unwrap();
        let old = raw.get(g(3)).unwrap();
        v.write_extent(ExtentId(3), &[4u8; 2048]).unwrap();
        raw.remove(g(3)).unwrap();
        raw.put(g(3), &old).unwrap();
        assert_eq!(v.read_extent(ExtentId(3)).unwrap_err().code, ErrorCode::Corruption);
    }

    /// A sealer whose every open fails with `code` (a worker reporting it).
    struct Failing(ErrorCode);
    impl Sealer for Failing {
        fn seal(&self, _: &[u8; 16], _: GenerationRef, p: &[u8]) -> Result<Vec<u8>> {
            Ok(p.to_vec())
        }
        fn open(&self, _: &[u8; 16], _: GenerationRef, _: &[u8]) -> Result<Vec<u8>> {
            err(self.0, "injected")
        }
        fn key_check(&self, _: &[u8; 16]) -> Result<[u8; 32]> {
            Ok([0; 32])
        }
    }

    #[test]
    fn per_object_key_or_version_mismatches_are_damage_and_scrub_lists_them() {
        for code in [ErrorCode::Unauthorized, ErrorCode::VersionMismatch] {
            let raw = Arc::new(MemoryMedia::new());
            let m = SealedMedia::new(raw.clone(), Arc::new(Failing(code)), DEV, 2048);
            let at = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
            m.put(at, &[1u8; 2048]).unwrap();
            assert_eq!(m.get(at).unwrap_err().code, ErrorCode::Corruption, "{code:?}");
        }
        let d = TempDir::new("seal-scrub");
        let raw = Arc::new(MemoryMedia::new());
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(raw, Arc::new(Failing(ErrorCode::Unauthorized)), DEV, 2048));
        let v = Volume::create(&d.0, Geometry::new(512, 4, 16).unwrap(), DEV, FLAG_SEALED, m, VolumeConfig { verify_readback: false, ..Default::default() }).unwrap();
        v.write_extent(ExtentId(0), &[1u8; 2048]).unwrap();
        v.write_extent(ExtentId(1), &[2u8; 2048]).unwrap();
        let s = v.scrub().unwrap();
        assert_eq!(s.failed.len(), 2, "both extents listed, the scrub did not stop");
        // an oversized object from the backend is refused before staging
        let raw = Arc::new(MemoryMedia::new());
        let m = SealedMedia::new(raw.clone(), Arc::new(ToySealer([1; 32])), DEV, 2048);
        let at = GenerationRef { extent: ExtentId(3), generation: Generation::FIRST };
        raw.put(at, &vec![0u8; 2048 + SEAL_OVERHEAD as usize + 1]).unwrap();
        assert_eq!(m.get(at).unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn the_wrong_key_or_a_mismatched_adapter_is_refused_at_open() {
        let d = TempDir::new("seal");
        let raw = Arc::new(MemoryMedia::new());
        let v = sealed_vol(&d, raw.clone(), 1).unwrap();
        v.write_extent(ExtentId(0), &[9u8; 2048]).unwrap();
        drop(v);
        let before = raw.list().unwrap();
        assert_eq!(reopen(&d, raw.clone(), 2).err().unwrap().code, ErrorCode::Unauthorized);
        assert_eq!(raw.list().unwrap(), before, "nothing collected under the wrong key");
        // a sealed volume over a plain adapter, a plain volume over the sealing adapter
        assert_eq!(Volume::open(&d.0, raw.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::InvalidArgument);
        let d2 = TempDir::new("seal");
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(Arc::new(MemoryMedia::new()), Arc::new(ToySealer([1; 32])), DEV, 2048));
        assert_eq!(
            Volume::create(&d2.0, Geometry::new(512, 4, 16).unwrap(), DEV, 0, m, VolumeConfig::default()).err().unwrap().code,
            ErrorCode::InvalidArgument
        );
        // an unsealed backend opened through the sealing adapter: its marker carries no key check
        let d3 = TempDir::new("seal");
        let plain = Arc::new(MemoryMedia::new());
        drop(Volume::create(&d3.0, Geometry::new(512, 4, 16).unwrap(), DEV, 0, plain.clone(), VolumeConfig::default()).unwrap());
        let s = SealedMedia::new(plain, Arc::new(ToySealer([1; 32])), DEV, 2048);
        let marker = GenerationRef { extent: MARKER_EXTENT, generation: Generation::FIRST };
        assert_eq!(s.get(marker).unwrap_err().code, ErrorCode::Unauthorized);
    }
}
