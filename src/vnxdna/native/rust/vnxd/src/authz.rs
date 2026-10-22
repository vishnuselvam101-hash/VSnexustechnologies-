//! Authorisation of management operations (docs/V14_SECURITY_MODEL.md §4).
//!
//! The management socket (P8) is a Unix socket created with mode 0660; who may connect is decided by its owner and
//! group. On top of that every session must authenticate before anything but `hello`:
//!
//! * a **policy** file lists `<role> <sha256-hex of a token>` lines (`#` comments). Roles: `reader` (`capacity`,
//!   `health`, `stats`) and `admin` (those and `shutdown`). Tokens are random secrets of at least 32 bytes handed to
//!   the operator; the policy stores only their SHA-256, so reading the policy file does not reveal a token. The
//!   policy file must not be writable by group or others nor readable by others (refused otherwise);
//! * `authenticate {token}` compares the token's SHA-256 with every entry in constant time (no early exit);
//! * an operation the session's role does not allow is UNAUTHORIZED and audited (`mgmt_denied`); a failed
//!   `authenticate` is audited (`mgmt_auth_failed`), and after [`MAX_AUTH_FAILURES`] failures the session refuses
//!   everything until it is closed; after [`MAX_DENIALS`] refused operations likewise (so one session cannot flood
//!   the audit log). Granted authentications and every `shutdown` are audited too. Limiting guesses across
//!   sessions (per peer) is the socket server's job (P8); tokens are 256-bit random values, so guessing is hopeless
//!   anyway.
//!
//! [`MgmtSession`] is transport-free: it turns one request line into one response line, so it is tested here and
//! wired to the socket by the service.

use crate::audit::{record_or_report, AuditSink};
use crate::digest::{parse_hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::{obj, Value};
use crate::proto::{self, Proto};
use std::path::Path;

pub const MAX_AUTH_FAILURES: u32 = 3;
/// Refused operations after which a session is cut off (and its denials are no longer audited one by one).
pub const MAX_DENIALS: u32 = 16;
/// Shortest token accepted at authentication (bytes): tokens are random secrets, not passwords.
pub const MIN_TOKEN_LEN: usize = 32;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Role {
    Reader,
    Admin,
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Role::Reader => "reader",
            Role::Admin => "admin",
        }
    }

    pub fn parse(s: &str) -> Option<Role> {
        [Role::Reader, Role::Admin].into_iter().find(|r| r.as_str() == s)
    }
}

/// May a session with `role` run `op`?
pub fn allowed(role: Option<Role>, op: &str) -> bool {
    match op {
        "hello" | "authenticate" => true,
        "capacity" | "health" | "stats" => role.is_some(),
        "shutdown" => role == Some(Role::Admin),
        _ => false,
    }
}

#[derive(Clone, Debug, Default)]
pub struct Policy {
    entries: Vec<(Role, [u8; 32])>,
}

pub(crate) fn ct_eq(a: &[u8; 32], b: &[u8; 32]) -> bool {
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

impl Policy {
    pub fn parse(text: &str) -> Result<Policy> {
        let mut entries = Vec::new();
        for (i, line) in text.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let mut parts = line.split_whitespace();
            let (role, digest, rest) = (parts.next(), parts.next(), parts.next());
            let entry = match (role.and_then(Role::parse), digest.and_then(parse_hex::<32>), rest) {
                (Some(r), Some(d), None) => (r, d),
                _ => return err(ErrorCode::InvalidArgument, format!("policy line {}: expected `<reader|admin> <sha256 hex>`", i + 1)),
            };
            entries.push(entry);
        }
        if entries.is_empty() {
            return err(ErrorCode::InvalidArgument, "the policy grants nothing (no entries)");
        }
        Ok(Policy { entries })
    }

    /// Load a policy file, refusing a symlink and a file that others could read or group/others could write. The
    /// mode is checked on the opened file itself (no window between the check and the read).
    pub fn load(path: &Path) -> Result<Policy> {
        use std::io::Read;
        use std::os::unix::fs::PermissionsExt;
        let unreadable = |e: std::io::Error| VnxError::new(ErrorCode::NotFound, format!("policy {}: {e}", path.display()));
        if std::fs::symlink_metadata(path).map_err(unreadable)?.file_type().is_symlink() {
            return err(ErrorCode::Unauthorized, format!("policy {} is a symlink", path.display()));
        }
        let f = std::fs::File::open(path).map_err(unreadable)?;
        let meta = f.metadata().map_err(unreadable)?;
        let mode = meta.permissions().mode() & 0o777;
        if !meta.is_file() || mode & 0o026 != 0 {
            return err(ErrorCode::Unauthorized, format!("policy {} has mode {mode:o}: group/other write or other read is not allowed", path.display()));
        }
        let mut text = String::new();
        f.take(1 << 20).read_to_string(&mut text).map_err(|e| VnxError::new(ErrorCode::InvalidArgument, format!("policy {}: {e}", path.display())))?;
        Policy::parse(&text)
    }

    /// The role a token grants (the highest, if it is listed twice). Every entry is compared, in constant time.
    pub fn authenticate(&self, token: &str) -> Option<Role> {
        let d = sha256(token.as_bytes());
        let mut best = None;
        for (role, h) in &self.entries {
            if ct_eq(&d, h) {
                best = best.max(Some(*role));
            }
        }
        if token.len() < MIN_TOKEN_LEN {
            return None;
        }
        best
    }
}

/// What the management operations act on (the device, in the service).
pub trait MgmtBackend {
    fn capacity(&self) -> Result<Value>;
    fn health(&self) -> Result<Value>;
    fn stats(&self) -> Result<Value>;
    fn shutdown(&self) -> Result<Value>;
}

pub struct MgmtSession<'a> {
    policy: &'a Policy,
    backend: &'a dyn MgmtBackend,
    audit: Option<&'a dyn AuditSink>,
    /// how the transport describes the peer (for audit events), e.g. "uid 1000"
    peer: String,
    server: String,
    role: Option<Role>,
    hello: bool,
    failures: u32,
    denials: u32,
}

impl<'a> MgmtSession<'a> {
    pub fn new(policy: &'a Policy, backend: &'a dyn MgmtBackend, audit: Option<&'a dyn AuditSink>, peer: &str) -> MgmtSession<'a> {
        MgmtSession {
            policy,
            backend,
            audit,
            peer: peer.to_string(),
            server: format!("{} {}", crate::NAME, crate::VERSION),
            role: None,
            hello: false,
            failures: 0,
            denials: 0,
        }
    }

    pub fn role(&self) -> Option<Role> {
        self.role
    }

    fn audit(&self, event: &str, op: &str, extra: Option<(&str, Value)>) {
        let mut d = vec![
            ("peer", Value::Str(self.peer.clone())),
            ("op", Value::Str(op.into())),
            ("role", self.role.map_or(Value::Null, |r| Value::Str(r.as_str().into()))),
        ];
        if let Some(x) = extra {
            d.push(x);
        }
        let detail = Value::Obj(d.into_iter().map(|(k, v)| (k.to_string(), v)).collect());
        record_or_report(self.audit, event, detail);
    }

    /// Handle one request line; returns the response line (canonical JSON, no newline).
    pub fn handle(&mut self, line: &str) -> String {
        let parsed = Value::parse(line).and_then(|v| proto::validate_request(Proto::Mgmt, &v));
        let req = match parsed {
            Ok(r) => r,
            Err(e) => {
                // the id of an unparseable request is unknown: 0
                return proto::error_response(Proto::Mgmt, 0, &e).canonical();
            }
        };
        let out = self.dispatch(&req.op, &req.args);
        match out.and_then(|res| proto::ok_response(Proto::Mgmt, req.id, &req.op, res)) {
            Ok(v) => v.canonical(),
            Err(e) => proto::error_response(Proto::Mgmt, req.id, &e).canonical(),
        }
    }

    fn dispatch(&mut self, op: &str, args: &Value) -> Result<Value> {
        if self.failures >= MAX_AUTH_FAILURES || self.denials >= MAX_DENIALS {
            return err(ErrorCode::Unauthorized, "too many refused requests: close the session");
        }
        if op != "hello" && !self.hello {
            return err(ErrorCode::Protocol, "the first request of a session must be hello");
        }
        let a = args.as_obj().expect("validated");
        match op {
            "hello" => {
                let versions = match &a["versions"] {
                    Value::Arr(v) => v.iter().filter_map(|x| if let Value::Int(i) = x { Some(*i) } else { None }).collect::<Vec<_>>(),
                    _ => Vec::new(),
                };
                if !versions.contains(&proto::VERSION) {
                    self.hello = false;
                    return err(ErrorCode::VersionMismatch, format!("vnx.mgmt versions {versions:?}; supported [{}]", proto::VERSION));
                }
                self.hello = true;
                Ok(obj([("server", Value::Str(self.server.clone())), ("version", Value::Int(proto::VERSION))]))
            }
            "authenticate" => {
                let token = a["token"].as_str().expect("validated");
                match self.policy.authenticate(token) {
                    Some(role) => {
                        self.role = Some(role);
                        self.audit("mgmt_auth_granted", op, None);
                        Ok(obj([("role", Value::Str(role.as_str().into()))]))
                    }
                    None => {
                        self.role = None; // a failed authentication never keeps an earlier grant
                        self.failures += 1;
                        self.audit("mgmt_auth_failed", op, Some(("failures", Value::Int(i64::from(self.failures)))));
                        err(ErrorCode::Unauthorized, "authentication failed")
                    }
                }
            }
            _ if !allowed(self.role, op) => {
                self.denials += 1;
                let last = (self.denials == MAX_DENIALS).then(|| ("session_cut_off", Value::Bool(true)));
                self.audit("mgmt_denied", op, last);
                err(ErrorCode::Unauthorized, format!("operation {op} is not allowed for this session"))
            }
            "capacity" => self.backend.capacity(),
            "health" => self.backend.health(),
            "stats" => self.backend.stats(),
            "shutdown" => {
                self.audit("mgmt_shutdown", op, None);
                self.backend.shutdown()
            }
            _ => err(ErrorCode::Protocol, format!("unknown operation {op}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audit::MemorySink;
    use crate::digest::hex;
    use crate::journal::tests::TempDir;
    use std::cell::Cell;

    const READER: &str = "reader-token-0123456789abcdef0123456789";
    const ADMIN: &str = "admin-token-0123456789abcdef0123456789ab";

    fn policy() -> Policy {
        Policy::parse(&format!(
            "# roles\nreader {}\n\nadmin {}\n",
            hex(&sha256(READER.as_bytes())),
            hex(&sha256(ADMIN.as_bytes()))
        ))
        .unwrap()
    }

    struct Dev {
        shut: Cell<bool>,
    }
    impl MgmtBackend for Dev {
        fn capacity(&self) -> Result<Value> {
            Ok(obj([
                ("block_size", Value::Int(4096)),
                ("blocks_per_extent", Value::Int(256)),
                ("capacity_blocks", Value::Int(1024)),
                ("capacity_bytes", Value::Int(4 << 20)),
                ("extent_count", Value::Int(4)),
            ]))
        }
        fn health(&self) -> Result<Value> {
            Ok(obj([("state", Value::Str("ok".into())), ("detail", obj([]))]))
        }
        fn stats(&self) -> Result<Value> {
            Ok(obj([("counters", obj([]))]))
        }
        fn shutdown(&self) -> Result<Value> {
            self.shut.set(true);
            Ok(obj([]))
        }
    }

    fn req(id: i64, op: &str, args: Value) -> String {
        proto::request(Proto::Mgmt, id, op, args).unwrap().canonical()
    }

    fn code(resp: &str) -> Option<String> {
        let v = Value::parse(resp).unwrap();
        let o = v.as_obj().unwrap();
        if o["ok"] == Value::Bool(true) {
            None
        } else {
            Some(o["error"].as_obj().unwrap()["code"].as_str().unwrap().to_string())
        }
    }

    fn hello() -> String {
        req(1, "hello", obj([("client", Value::Str("t".into())), ("versions", Value::Arr(vec![Value::Int(1)]))]))
    }

    fn auth(token: &str) -> String {
        req(2, "authenticate", obj([("token", Value::Str(token.into()))]))
    }

    #[test]
    fn roles_gate_every_operation_and_denials_are_audited() {
        let (p, dev, audit) = (policy(), Dev { shut: Cell::new(false) }, MemorySink::new());
        let mut s = MgmtSession::new(&p, &dev, Some(&audit), "uid 1000");
        assert_eq!(code(&s.handle(&req(1, "stats", obj([])))).as_deref(), Some("PROTOCOL"), "hello first");
        assert_eq!(code(&s.handle(&hello())), None);
        for op in ["capacity", "health", "stats", "shutdown"] {
            assert_eq!(code(&s.handle(&req(3, op, obj([])))).as_deref(), Some("UNAUTHORIZED"), "{op} before authenticate");
        }
        assert_eq!(code(&s.handle(&auth(READER))), None);
        assert_eq!(s.role(), Some(Role::Reader));
        for op in ["capacity", "health", "stats"] {
            assert_eq!(code(&s.handle(&req(4, op, obj([])))), None, "{op} as reader");
        }
        assert_eq!(code(&s.handle(&req(5, "shutdown", obj([])))).as_deref(), Some("UNAUTHORIZED"));
        assert!(!dev.shut.get());
        assert_eq!(code(&s.handle(&auth(ADMIN))), None);
        assert_eq!(code(&s.handle(&req(6, "shutdown", obj([])))), None);
        assert!(dev.shut.get());
        // a failed authentication drops the earlier grant
        assert_eq!(code(&s.handle(&auth("not-the-token-0123456789abcdef0123456789"))).as_deref(), Some("UNAUTHORIZED"));
        assert_eq!((s.role(), code(&s.handle(&req(7, "stats", obj([])))).as_deref()), (None, Some("UNAUTHORIZED")));
        let names = audit.names();
        assert_eq!(names.iter().filter(|n| *n == "mgmt_denied").count(), 6);
        assert_eq!(names.iter().filter(|n| *n == "mgmt_auth_granted").count(), 2);
        assert!(names.contains(&"mgmt_shutdown".to_string()));
        // malformed requests: a typed error, never a crash
        assert_eq!(code(&s.handle("{not json")).as_deref(), Some("PROTOCOL"));
    }

    #[test]
    fn failed_authentication_is_audited_and_locks_the_session() {
        let (p, dev, audit) = (policy(), Dev { shut: Cell::new(false) }, MemorySink::new());
        let mut s = MgmtSession::new(&p, &dev, Some(&audit), "uid 1001");
        s.handle(&hello());
        assert_eq!(code(&s.handle(&auth("wrong-token-0123456789abcdef0123456789"))).as_deref(), Some("UNAUTHORIZED"));
        assert_eq!(code(&s.handle(&auth("short"))).as_deref(), Some("UNAUTHORIZED"));
        assert_eq!(code(&s.handle(&auth(&ADMIN[..20]))).as_deref(), Some("UNAUTHORIZED"));
        assert_eq!(s.role(), None);
        // locked: even the right token is refused now
        assert_eq!(code(&s.handle(&auth(ADMIN))).as_deref(), Some("UNAUTHORIZED"));
        assert_eq!(s.role(), None);
        assert_eq!(audit.names().iter().filter(|n| *n == "mgmt_auth_failed").count(), 3);
        // a short token is refused even when the policy lists its hash
        let weak = Policy::parse(&format!("admin {}", hex(&sha256(b"short")))).unwrap();
        assert_eq!(weak.authenticate("short"), None);
    }

    #[test]
    fn policies_are_strict_and_must_be_private() {
        use std::os::unix::fs::PermissionsExt;
        for bad in ["", "# only a comment", "root 00", "admin xyz", &format!("admin {} extra", "0".repeat(64)), &format!("owner {}", "0".repeat(64))] {
            assert_eq!(Policy::parse(bad).unwrap_err().code, ErrorCode::InvalidArgument, "{bad:?}");
        }
        let d = TempDir::new("policy");
        let f = d.0.join("policy");
        std::fs::write(&f, format!("admin {}\n", hex(&sha256(ADMIN.as_bytes())))).unwrap();
        for (mode, ok) in [(0o600, true), (0o640, true), (0o644, false), (0o660, false), (0o602, false)] {
            std::fs::set_permissions(&f, std::fs::Permissions::from_mode(mode)).unwrap();
            assert_eq!(Policy::load(&f).is_ok(), ok, "mode {mode:o}");
        }
        assert!(allowed(None, "hello") && !allowed(None, "stats") && !allowed(Some(Role::Reader), "format"));
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o600)).unwrap();
        let link = d.0.join("link");
        std::os::unix::fs::symlink(&f, &link).unwrap();
        assert_eq!(Policy::load(&link).unwrap_err().code, ErrorCode::Unauthorized, "symlinks refused");
    }

    #[test]
    fn a_session_flooding_denied_requests_is_cut_off() {
        let (p, dev, audit) = (policy(), Dev { shut: Cell::new(false) }, MemorySink::new());
        let mut s = MgmtSession::new(&p, &dev, Some(&audit), "uid 1002");
        s.handle(&hello());
        for i in 0..100 {
            assert_eq!(code(&s.handle(&req(i, "stats", obj([])))).as_deref(), Some("UNAUTHORIZED"));
        }
        assert_eq!(audit.names().len(), MAX_DENIALS as usize, "the audit log is not flooded");
        assert_eq!(code(&s.handle(&auth(ADMIN))).as_deref(), Some("UNAUTHORIZED"), "the session is cut off");
    }
}
