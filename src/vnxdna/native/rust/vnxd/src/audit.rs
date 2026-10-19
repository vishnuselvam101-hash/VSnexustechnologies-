//! Audit log: an append-only, hash-chained file of JSON lines (docs/V14_SECURITY_MODEL.md §5).
//!
//! Every line is the canonical JSON of `{"detail": {...}, "event": "...", "prev": "<hex>", "seq": n, "ts_ms": t}`
//! followed by `\n`. `prev` is the SHA-256 of the previous line (without its newline); the first line's `prev` is 64
//! zeros and its `seq` is 1. So removing, reordering or editing any line breaks the chain at the next line, which
//! [`verify`] reports with its line number. Truncating the newest lines cannot be detected from the file alone: the
//! service reports the head hash (`head()`, in `stats`) so that an external copy can anchor it.
//!
//! * `record` appends one line and, with `sync`, calls `sync_data` before returning (security events are rare; the
//!   service opens its audit log with `sync` on);
//! * `open` verifies the whole chain and continues it. A last line without its newline is a torn append and is cut
//!   (reported); any other damage is CORRUPTION and the file is left untouched — the service then refuses to start
//!   rather than write past a broken chain. A failed append is cut back to the last complete line (if even that
//!   fails the log is poisoned until reopened), so a fragment is never followed by another line;
//! * the log is created with mode 0600 and a symlink is refused. The chain is not keyed: it detects partial edits
//!   and edits by anyone who cannot rewrite the whole file consistently; the service user could rewrite it whole,
//!   which only an external copy of `head()` detects;
//! * events that cannot be written are never dropped silently: `record` returns the error, and [`AuditSink`] users
//!   that cannot fail (health transitions inside a media adapter) report it on stderr.

use crate::digest::{hex, parse_hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::{obj, Value};
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// Where audit events go.
pub trait AuditSink: Send + Sync {
    fn record(&self, event: &str, detail: Value) -> Result<()>;
}

/// Record `event`, reporting a failure to write it on stderr (for callers that cannot fail).
pub fn record_or_report(sink: Option<&dyn AuditSink>, event: &str, detail: Value) {
    if let Some(s) = sink {
        if let Err(e) = s.record(event, detail) {
            eprintln!("vnxd audit: could not record {event}: {e}");
        }
    }
}

const ZERO_HASH: [u8; 32] = [0; 32];

struct Tail {
    file: File,
    seq: u64,
    prev: [u8; 32],
    /// file length after the last complete line (a failed append is cut back to it)
    len: u64,
    /// a failed append could not be cut back: no further line may be appended after a fragment
    poisoned: bool,
}

pub struct AuditLog {
    path: PathBuf,
    sync: bool,
    tail: Mutex<Tail>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verified {
    pub events: u64,
    /// SHA-256 of the last line (64 zeros for an empty log)
    pub head: [u8; 32],
    /// bytes of a torn last line (not part of the chain)
    pub torn_bytes: usize,
}

fn io(what: &str, path: &Path, e: std::io::Error) -> VnxError {
    VnxError::new(ErrorCode::BackendUnavailable, format!("audit {what} {}: {e}", path.display()))
}

fn now_ms() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

/// Check every line of an audit log; the error names the first broken line.
pub fn verify_bytes(buf: &[u8]) -> Result<Verified> {
    let mut prev = ZERO_HASH;
    let mut events = 0u64;
    let mut pos = 0usize;
    while pos < buf.len() {
        let Some(nl) = buf[pos..].iter().position(|&b| b == b'\n') else {
            // no newline: a torn append, only acceptable as the very last bytes
            return Ok(Verified { events, head: prev, torn_bytes: buf.len() - pos });
        };
        let line = &buf[pos..pos + nl];
        let n = events + 1;
        let bad = |why: String| VnxError::new(ErrorCode::Corruption, format!("audit line {n}: {why}"));
        let text = match std::str::from_utf8(line) {
            Ok(t) => t,
            Err(_) => return Err(bad("not UTF-8".into())),
        };
        let v = match Value::parse(text) {
            Ok(v) => v,
            Err(e) => return Err(bad(e.message)),
        };
        if v.canonical() != text {
            return Err(bad("not in canonical form".into()));
        }
        let o = v.as_obj().ok_or_else(|| bad("not an object".into()))?;
        let keys = ["detail", "event", "prev", "seq", "ts_ms"];
        if o.len() != keys.len() || keys.iter().any(|k| !o.contains_key(*k)) {
            return Err(bad(format!("fields must be exactly {keys:?}")));
        }
        match (&o["seq"], &o["prev"], &o["event"], &o["detail"], &o["ts_ms"]) {
            (Value::Int(s), Value::Str(p), Value::Str(ev), Value::Obj(_), Value::Int(_)) => {
                if *s as u64 != n || *s < 1 {
                    return Err(bad(format!("sequence {s}, expected {n}")));
                }
                if parse_hex::<32>(p) != Some(prev) {
                    return Err(bad("hash chain broken (an earlier line was changed, removed or reordered)".into()));
                }
                if ev.is_empty() {
                    return Err(bad("empty event name".into()));
                }
            }
            _ => return Err(bad("field types".into())),
        }
        prev = sha256(line);
        events = n;
        pos += nl + 1;
    }
    Ok(Verified { events, head: prev, torn_bytes: 0 })
}

pub fn verify(path: &Path) -> Result<Verified> {
    let buf = std::fs::read(path).map_err(|e| io("read", path, e))?;
    verify_bytes(&buf)
}

impl AuditLog {
    /// Open (or create) the log at `path`, verifying the existing chain. Returns the log and the verification
    /// result (a torn last line was cut from the file).
    pub fn open(path: &Path, sync: bool) -> Result<(AuditLog, Verified)> {
        use std::os::unix::fs::OpenOptionsExt;
        if std::fs::symlink_metadata(path).map_or(false, |m| m.file_type().is_symlink()) {
            return err(ErrorCode::InvalidArgument, format!("audit log {} is a symlink", path.display()));
        }
        let mut file = OpenOptions::new()
            .read(true)
            .append(true)
            .create(true)
            .mode(0o600)
            .open(path)
            .map_err(|e| io("open", path, e))?;
        let mut buf = Vec::new();
        file.read_to_end(&mut buf).map_err(|e| io("read", path, e))?;
        let v = verify_bytes(&buf)?;
        if v.torn_bytes > 0 {
            file.set_len((buf.len() - v.torn_bytes) as u64).and_then(|_| file.sync_all()).map_err(|e| io("truncate", path, e))?;
        }
        let tail = Tail { file, seq: v.events, prev: v.head, len: (buf.len() - v.torn_bytes) as u64, poisoned: false };
        Ok((AuditLog { path: path.to_path_buf(), sync, tail: Mutex::new(tail) }, v))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Number of events and the head hash.
    pub fn head(&self) -> (u64, [u8; 32]) {
        let t = self.tail.lock().unwrap_or_else(|p| p.into_inner());
        (t.seq, t.prev)
    }
}

impl AuditSink for AuditLog {
    fn record(&self, event: &str, detail: Value) -> Result<()> {
        if event.is_empty() {
            return err(ErrorCode::InvalidArgument, "audit event name is empty");
        }
        if !matches!(detail, Value::Obj(_)) {
            return err(ErrorCode::InvalidArgument, "audit detail must be an object");
        }
        let mut t = self.tail.lock().unwrap_or_else(|p| p.into_inner());
        if t.poisoned {
            return err(ErrorCode::Internal, format!("audit log {} is poisoned by a failed append; reopen it", self.path.display()));
        }
        let seq = t.seq + 1;
        let line = obj([
            ("detail", detail),
            ("event", Value::Str(event.into())),
            ("prev", Value::Str(hex(&t.prev))),
            ("seq", Value::Int(i64::try_from(seq).or_else(|_| err(ErrorCode::Overflow, "audit sequence"))?)),
            ("ts_ms", Value::Int(now_ms())),
        ])
        .canonical();
        let mut bytes = line.clone().into_bytes();
        bytes.push(b'\n');
        let sync = self.sync;
        let r = t.file.write_all(&bytes).and_then(|_| if sync { t.file.sync_data() } else { Ok(()) });
        if let Err(e) = r {
            // never leave a fragment that a later line would follow (the chain would break mid-file)
            let len = t.len;
            if t.file.set_len(len).and_then(|_| t.file.sync_data()).is_err() {
                t.poisoned = true;
            }
            return Err(io("append", &self.path, e));
        }
        t.len += bytes.len() as u64;
        t.seq = seq;
        t.prev = sha256(line.as_bytes());
        Ok(())
    }
}

/// An in-memory sink (tests, and components that collect events before the log is open).
#[derive(Default)]
pub struct MemorySink {
    events: Mutex<Vec<(String, Value)>>,
}

impl MemorySink {
    pub fn new() -> MemorySink {
        MemorySink::default()
    }

    pub fn events(&self) -> Vec<(String, Value)> {
        self.events.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    pub fn names(&self) -> Vec<String> {
        self.events().into_iter().map(|(n, _)| n).collect()
    }
}

impl AuditSink for MemorySink {
    fn record(&self, event: &str, detail: Value) -> Result<()> {
        self.events.lock().unwrap_or_else(|p| p.into_inner()).push((event.to_string(), detail));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::tests::TempDir;

    fn d(k: &str, v: i64) -> Value {
        obj([(k, Value::Int(v))])
    }

    #[test]
    fn records_chain_and_survive_reopen() {
        let dir = TempDir::new("audit");
        let p = dir.0.join("audit.log");
        let (log, v) = AuditLog::open(&p, true).unwrap();
        assert_eq!(v.events, 0);
        for i in 0..5 {
            log.record("test_event", d("i", i)).unwrap();
        }
        let head = log.head();
        drop(log);
        let (log, v) = AuditLog::open(&p, true).unwrap();
        assert_eq!((v.events, v.head), head);
        log.record("after_reopen", d("i", 5)).unwrap();
        assert_eq!(verify(&p).unwrap().events, 6);
        assert_eq!(log.record("", d("i", 0)).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(log.record("x", Value::Int(1)).unwrap_err().code, ErrorCode::InvalidArgument);
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600, "created private");
        let link = dir.0.join("link.log");
        std::os::unix::fs::symlink(&p, &link).unwrap();
        assert_eq!(AuditLog::open(&link, true).err().unwrap().code, ErrorCode::InvalidArgument, "symlinks refused");
    }

    #[test]
    fn any_edit_removal_or_reordering_is_detected() {
        let dir = TempDir::new("audit");
        let p = dir.0.join("audit.log");
        let (log, _) = AuditLog::open(&p, false).unwrap();
        for i in 0..4 {
            log.record("e", d("i", i)).unwrap();
        }
        drop(log);
        let clean = std::fs::read_to_string(&p).unwrap();
        let lines: Vec<&str> = clean.lines().collect();
        let join = |ls: &[&str]| ls.iter().map(|l| format!("{l}\n")).collect::<String>();
        let edited = clean.replacen("\"i\":1", "\"i\":7", 1);
        let cases = [
            edited,                                              // a changed detail
            join(&[lines[0], lines[2], lines[3]]),               // a removed line
            join(&[lines[0], lines[2], lines[1], lines[3]]),     // reordered lines
            join(&[lines[1], lines[2], lines[3]]),               // the first line removed
            clean.replacen("{\"detail\"", "{ \"detail\"", 1),     // not canonical
        ];
        for (i, bad) in cases.iter().enumerate() {
            std::fs::write(&p, bad).unwrap();
            assert_eq!(verify(&p).unwrap_err().code, ErrorCode::Corruption, "case {i}");
            assert_eq!(AuditLog::open(&p, false).err().unwrap().code, ErrorCode::Corruption, "case {i}");
            assert_eq!(std::fs::read_to_string(&p).unwrap(), *bad, "case {i}: left untouched");
        }
    }

    #[test]
    fn a_torn_last_line_is_cut_and_the_chain_continues() {
        let dir = TempDir::new("audit");
        let p = dir.0.join("audit.log");
        let (log, _) = AuditLog::open(&p, false).unwrap();
        log.record("a", d("i", 1)).unwrap();
        drop(log);
        let mut f = OpenOptions::new().append(true).open(&p).unwrap();
        f.write_all(b"{\"detail\":{},\"ev").unwrap();
        drop(f);
        let (log, v) = AuditLog::open(&p, false).unwrap();
        assert_eq!((v.events, v.torn_bytes), (1, 16));
        log.record("b", d("i", 2)).unwrap();
        assert_eq!(verify(&p).unwrap(), Verified { events: 2, head: log.head().1, torn_bytes: 0 });
    }
}
