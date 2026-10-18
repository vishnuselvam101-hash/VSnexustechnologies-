//! Pool of `vnx.codec-ipc/1` worker processes (docs/V14_CONTRACTS.md §6), shared by the sealing adapter
//! ([`crate::seal::WorkerSealer`], P4) and the DNA codec adapters ([`crate::codec::WorkerCodec`], P7).
//!
//! A worker is a child process that reads one request per line on stdin and writes one response per line on stdout.
//! Bulk data never crosses the pipe: it is exchanged as files in a private staging directory (mode 0700, checked),
//! named `<tag>-<pid hex>-<start ns hex>-<job>.<suffix>` so that two processes sharing the directory never collide
//! and the files of a dead process can be swept. A worker that dies, hangs or breaks the protocol is killed and
//! replaced at the next request; its request fails with BACKEND_UNAVAILABLE or TIMEOUT (retryable).

use crate::digest::{hex, parse_hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::{obj, Value};
use crate::proto::{self, Proto};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError};
use std::sync::Mutex;
use std::time::Duration;

/// How to start the workers.
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

/// Longest response line read from a worker (the protocol's document limit plus the newline).
const MAX_LINE: usize = crate::json::MAX_DOC + 1;

/// The staging directory must be a real directory (not a symlink) with no group or other access: only this user
/// (or root) can create, swap or read files in it.
pub fn check_staging(dir: &Path) -> Result<()> {
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

pub(crate) fn unavailable(msg: impl Into<String>) -> VnxError {
    VnxError::new(ErrorCode::BackendUnavailable, msg)
}

/// Create `path` exclusively with mode 0600 and write `data`.
pub(crate) fn write_private(path: &Path, data: &[u8]) -> Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .map_err(|e| unavailable(format!("staging {}: {e}", path.display())))?;
    f.write_all(data).map_err(|e| unavailable(format!("staging {}: {e}", path.display())))
}

/// A pool of worker processes speaking `vnx.codec-ipc/1`.
pub struct WorkerPool {
    cfg: WorkerConfig,
    /// what the workers are for, in error messages ("sealing", "codec")
    role: &'static str,
    slots: Vec<Mutex<Option<Worker>>>,
    next: AtomicU64,
    job: AtomicU64,
    /// staging file names of this process: "<tag>-<pid>-<start ns>-<job>.<suffix>" (never reused across restarts)
    prefix: String,
}

impl WorkerPool {
    /// `tag`: the staging-file prefix of this kind of worker ("seal", "dsim", ...). Files with this tag left by a
    /// process that no longer runs (it died mid-call) are removed before anything else happens; files of live
    /// processes are kept (another pool may share the directory).
    pub fn new(cfg: WorkerConfig, tag: &str, role: &'static str) -> Result<WorkerPool> {
        if cfg.command.is_empty() || cfg.workers == 0 || cfg.workers > 64 {
            return err(ErrorCode::InvalidArgument, format!("{role} workers: a command and 1..=64 workers"));
        }
        if tag.is_empty() || !tag.bytes().all(|b| b.is_ascii_lowercase()) {
            return err(ErrorCode::InvalidArgument, "worker staging tag must be lowercase letters");
        }
        check_staging(&cfg.staging)?;
        let lead = format!("{tag}-");
        if let Ok(entries) = std::fs::read_dir(&cfg.staging) {
            for e in entries.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                let Some(rest) = name.strip_prefix(&lead) else { continue };
                let pid = rest.split('-').next().and_then(|p| u32::from_str_radix(p, 16).ok());
                let alive = pid.map_or(false, |p| Path::new(&format!("/proc/{p}")).exists());
                if !alive {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos());
        let prefix = format!("{tag}-{:x}-{:x}", std::process::id(), nanos);
        let slots = (0..cfg.workers).map(|_| Mutex::new(None)).collect();
        Ok(WorkerPool { cfg, role, slots, next: AtomicU64::new(0), job: AtomicU64::new(1), prefix })
    }

    pub fn staging(&self) -> &Path {
        &self.cfg.staging
    }

    /// A fresh job number (also the request id space).
    pub fn next_job(&self) -> u64 {
        self.job.fetch_add(1, Ordering::Relaxed)
    }

    /// Staging file name for `job` with `suffix` (e.g. "in", "out").
    pub fn file_name(&self, job: u64, suffix: &str) -> String {
        format!("{}-{job}.{suffix}", self.prefix)
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
            .map_err(|e| unavailable(format!("cannot start the {} worker: {e}", self.role)))?;
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
            return Err(unavailable(format!("the {} worker did not negotiate vnx.codec-ipc/1", self.role)));
        }
        Ok(w)
    }

    /// One request/response on a worker. Transport failures are BACKEND_UNAVAILABLE / TIMEOUT; a well-formed error
    /// response is returned with its own code.
    fn exchange(&self, w: &mut Worker, op: &str, args: Value) -> Result<Value> {
        let id = self.next_job() as i64;
        let req = proto::request(Proto::Codec, id, op, args)?;
        let mut line = req.canonical();
        line.push('\n');
        w.stdin.write_all(line.as_bytes()).and_then(|_| w.stdin.flush()).map_err(|e| unavailable(format!("worker write: {e}")))?;
        let text = match w.lines.recv_timeout(self.cfg.timeout) {
            Ok(t) => t,
            Err(RecvTimeoutError::Timeout) => {
                return err(ErrorCode::Timeout, format!("{} worker gave no answer to {op} within {:?}", self.role, self.cfg.timeout))
            }
            Err(RecvTimeoutError::Disconnected) => return Err(unavailable(format!("the {} worker exited", self.role))),
        };
        let v = Value::parse(&text).map_err(|e| unavailable(format!("worker sent invalid JSON: {}", e.message)))?;
        let resp = proto::validate_response(Proto::Codec, op, &v).map_err(|e| unavailable(format!("worker broke the protocol: {}", e.message)))?;
        if v.as_obj().and_then(|o| o.get("id")) != Some(&Value::Int(id)) {
            return Err(unavailable("worker answered another request"));
        }
        resp
    }

    /// Run `op` on some worker, (re)starting it as needed; a transport failure discards the worker. Error responses
    /// come back with the worker's own code: the caller decides which verdicts it passes on.
    pub fn call(&self, op: &str, args: Value) -> Result<Value> {
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
        r
    }

    /// File-to-file job: stage `input` as "<job>.in", call `op` with `args(job, input name, input sha256 hex, output
    /// name)`, and return the output file after checking it against the worker's report (`output_sha256`,
    /// `output_bytes`): a mismatch is CORRUPTION. Both staging files are removed whatever happens. `map` is applied
    /// to every error of the call (transport failures and the worker's error responses) and decides the verdict.
    pub fn file_job(
        &self,
        op: &str,
        input: &[u8],
        args: impl FnOnce(u64, &str, &str, &str) -> Value,
        map: impl FnOnce(VnxError) -> VnxError,
    ) -> Result<Vec<u8>> {
        let job = self.next_job();
        let (iname, oname) = (self.file_name(job, "in"), self.file_name(job, "out"));
        let (ipath, opath) = (self.cfg.staging.join(&iname), self.cfg.staging.join(&oname));
        let r = (|| {
            write_private(&ipath, input)?;
            let res = self.call(op, args(job, &iname, &hex(&sha256(input)), &oname)).map_err(map)?;
            let o = res.as_obj().expect("validated");
            let meta = std::fs::symlink_metadata(&opath)
                .map_err(|e| unavailable(format!("{} output {}: {e}", self.role, opath.display())))?;
            if !meta.file_type().is_file() {
                return err(ErrorCode::Corruption, format!("the {} output is not a regular file", self.role));
            }
            let out = std::fs::read(&opath).map_err(|e| unavailable(format!("{} output {}: {e}", self.role, opath.display())))?;
            let want = o["output_sha256"].as_str().and_then(parse_hex::<32>);
            if want != Some(sha256(&out)) || o["output_bytes"] != Value::Int(out.len() as i64) {
                return err(ErrorCode::Corruption, format!("the {} worker's output does not match its report", self.role));
            }
            Ok(out)
        })();
        let _ = std::fs::remove_file(&ipath);
        let _ = std::fs::remove_file(&opath);
        r
    }
}
