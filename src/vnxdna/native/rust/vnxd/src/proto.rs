//! Service protocols `vnx.mgmt/1` and `vnx.codec-ipc/1` (docs/V14_CONTRACTS.md §6).
//!
//! One canonical-JSON message per line over a Unix-domain socket. A request is
//! `{"args":{…},"id":N,"kind":"request","op":"…","proto":"vnx.mgmt","v":1}`; a response is
//! `{"id":N,"kind":"response","ok":true,"proto":…,"result":{…},"v":1}` or
//! `{"error":{"class":…,"code":…,"message":…},"id":N,"kind":"response","ok":false,"proto":…,"v":1}`.
//! The first exchange on a connection is `hello`. Validation is exact (no missing and no unknown keys) and runs in a
//! fixed order so that both implementations report the same error code for the same message:
//! JSON syntax -> object -> `proto` -> `v` (VERSION_MISMATCH) -> envelope keys -> op -> fields (all PROTOCOL).

use crate::digest::parse_hex;
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::{obj, Value};
use std::collections::BTreeMap;

pub const VERSION: i64 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Proto {
    Mgmt,
    Codec,
}

impl Proto {
    pub fn name(self) -> &'static str {
        match self {
            Proto::Mgmt => "vnx.mgmt",
            Proto::Codec => "vnx.codec-ipc",
        }
    }

    pub fn parse(s: &str) -> Option<Proto> {
        [Proto::Mgmt, Proto::Codec].into_iter().find(|p| p.name() == s)
    }
}

/// Field types of the schemas.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ty {
    /// any string of 1..=1024 bytes
    Str,
    /// integer >= 0
    Count,
    /// integer >= 1
    Positive,
    /// 64 lowercase hex digits
    Sha256,
    /// a file name inside the staging directory: 1..=255 bytes of [A-Za-z0-9._-], not starting with '.'
    Name,
    /// an object whose content the receiver validates itself (codec options, counters)
    Object,
    /// a non-empty array of integers >= 1
    Versions,
    /// one of the health states
    Health,
    /// 32 lowercase hex digits (a 16-byte device id)
    Hex128,
}

type Schema = &'static [(&'static str, Ty)];

/// (op, request args, ok-response result) for every operation of a protocol.
fn ops(proto: Proto) -> &'static [(&'static str, Schema, Schema)] {
    const HELLO_ARGS: Schema = &[("client", Ty::Str), ("versions", Ty::Versions)];
    const HELLO_RESULT: Schema = &[("server", Ty::Str), ("version", Ty::Positive)];
    const SEAL_ARGS: Schema = &[
        ("job", Ty::Count),
        ("device_id", Ty::Hex128),
        ("extent", Ty::Count),
        ("generation", Ty::Positive),
        ("input", Ty::Name),
        ("input_sha256", Ty::Sha256),
        ("output", Ty::Name),
    ];
    const SEAL_RESULT: Schema = &[("job", Ty::Count), ("output_sha256", Ty::Sha256), ("output_bytes", Ty::Positive)];
    match proto {
        Proto::Mgmt => &[
            ("hello", HELLO_ARGS, HELLO_RESULT),
            (
                "capacity",
                &[],
                &[
                    ("block_size", Ty::Positive),
                    ("blocks_per_extent", Ty::Positive),
                    ("capacity_blocks", Ty::Positive),
                    ("capacity_bytes", Ty::Positive),
                    ("extent_count", Ty::Positive),
                ],
            ),
            ("health", &[], &[("state", Ty::Health), ("detail", Ty::Object)]),
            ("stats", &[], &[("counters", Ty::Object)]),
            ("shutdown", &[], &[]),
            ("authenticate", &[("token", Ty::Str)], &[("role", Ty::Str)]),
        ],
        Proto::Codec => &[
            ("hello", HELLO_ARGS, HELLO_RESULT),
            (
                "encode_extent",
                &[
                    ("job", Ty::Count),
                    ("extent", Ty::Count),
                    ("generation", Ty::Positive),
                    ("input", Ty::Name),
                    ("input_sha256", Ty::Sha256),
                    ("output", Ty::Name),
                    ("options", Ty::Object),
                ],
                &[("job", Ty::Count), ("output_sha256", Ty::Sha256), ("output_bytes", Ty::Positive)],
            ),
            (
                "decode_extent",
                &[
                    ("job", Ty::Count),
                    ("extent", Ty::Count),
                    ("generation", Ty::Positive),
                    ("input", Ty::Name),
                    ("output", Ty::Name),
                    ("expect_sha256", Ty::Sha256),
                    ("expect_bytes", Ty::Positive),
                    ("options", Ty::Object),
                ],
                &[("job", Ty::Count), ("output_sha256", Ty::Sha256), ("output_bytes", Ty::Positive)],
            ),
            ("key_check", &[("device_id", Ty::Hex128)], &[("key_check", Ty::Sha256)]),
            ("seal_extent", SEAL_ARGS, SEAL_RESULT),
            ("open_extent", SEAL_ARGS, SEAL_RESULT),
        ],
    }
}

pub const HEALTH_STATES: [&str; 3] = ["ok", "degraded", "failed"];

fn bad<T>(msg: impl Into<String>) -> Result<T> {
    err(ErrorCode::Protocol, msg)
}

fn check_ty(key: &str, ty: Ty, v: &Value) -> Result<()> {
    let ok = match (ty, v) {
        (Ty::Str, Value::Str(s)) => !s.is_empty() && s.len() <= 1024,
        (Ty::Count, Value::Int(i)) => *i >= 0,
        (Ty::Positive, Value::Int(i)) => *i >= 1,
        (Ty::Sha256, Value::Str(s)) => parse_hex::<32>(s).is_some(),
        (Ty::Name, Value::Str(s)) => is_staging_name(s),
        (Ty::Object, Value::Obj(_)) => true,
        (Ty::Versions, Value::Arr(a)) => !a.is_empty() && a.iter().all(|x| matches!(x, Value::Int(i) if *i >= 1)),
        (Ty::Health, Value::Str(s)) => HEALTH_STATES.contains(&s.as_str()),
        (Ty::Hex128, Value::Str(s)) => parse_hex::<16>(s).is_some(),
        _ => false,
    };
    if ok {
        Ok(())
    } else {
        bad(format!("field {key:?}: expected {ty:?}"))
    }
}

/// A file name the codec worker may open inside the staging directory (never a path).
pub fn is_staging_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 255
        && !s.starts_with('.')
        && s.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'_' | b'-'))
}

fn check_fields(what: &str, schema: Schema, v: &Value) -> Result<()> {
    let o = match v {
        Value::Obj(o) => o,
        _ => return bad(format!("{what} must be an object")),
    };
    for (k, ty) in schema {
        match o.get(*k) {
            Some(x) => check_ty(k, *ty, x)?,
            None => return bad(format!("{what}: missing field {k:?}")),
        }
    }
    if let Some(k) = o.keys().find(|k| !schema.iter().any(|(s, _)| s == k)) {
        return bad(format!("{what}: unknown field {k:?}"));
    }
    Ok(())
}

fn check_keys(o: &BTreeMap<String, Value>, want: &[&str]) -> Result<()> {
    let mut have: Vec<&str> = o.keys().map(|k| k.as_str()).collect();
    have.sort_unstable();
    let mut w = want.to_vec();
    w.sort_unstable();
    if have == w {
        Ok(())
    } else {
        bad(format!("envelope keys {have:?}, expected {w:?}"))
    }
}

/// Steps shared by requests and responses: object, protocol name, version.
fn envelope(proto: Proto, v: &Value) -> Result<&BTreeMap<String, Value>> {
    let o = match v {
        Value::Obj(o) => o,
        _ => return bad("a message must be a JSON object"),
    };
    if o.get("proto").and_then(Value::as_str) != Some(proto.name()) {
        return bad(format!("expected proto {:?}", proto.name()));
    }
    match o.get("v") {
        Some(Value::Int(VERSION)) => {}
        Some(Value::Int(n)) => return err(ErrorCode::VersionMismatch, format!("{} version {n} (supported: 1)", proto.name())),
        _ => return bad("missing or invalid version"),
    }
    Ok(o)
}

fn find_op(proto: Proto, op: &str) -> Result<(Schema, Schema)> {
    match ops(proto).iter().find(|(name, _, _)| *name == op) {
        Some((_, a, r)) => Ok((a, r)),
        None => bad(format!("unknown {} op {op:?}", proto.name())),
    }
}

/// A validated request.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Request {
    pub id: i64,
    pub op: String,
    pub args: Value,
}

pub fn validate_request(proto: Proto, v: &Value) -> Result<Request> {
    let o = envelope(proto, v)?;
    check_keys(o, &["proto", "v", "kind", "id", "op", "args"])?;
    if o["kind"].as_str() != Some("request") {
        return bad("kind must be \"request\"");
    }
    let id = match o["id"] {
        Value::Int(i) if i >= 0 => i,
        _ => return bad("id must be an integer >= 0"),
    };
    let op = o["op"].as_str().ok_or(()).or_else(|_| bad("op must be a string"))?;
    let (args, _) = find_op(proto, op)?;
    check_fields("args", args, &o["args"])?;
    Ok(Request { id, op: op.to_string(), args: o["args"].clone() })
}

/// Validate a response to a request of operation `op`; returns `Ok(Err(error))` for a well-formed error response.
pub fn validate_response(proto: Proto, op: &str, v: &Value) -> Result<std::result::Result<Value, VnxError>> {
    let o = envelope(proto, v)?;
    let ok = matches!(o.get("ok"), Some(Value::Bool(true)));
    let body = if ok { "result" } else { "error" };
    check_keys(o, &["proto", "v", "kind", "id", "ok", body])?;
    if o["kind"].as_str() != Some("response") {
        return bad("kind must be \"response\"");
    }
    if !matches!(o["id"], Value::Int(i) if i >= 0) {
        return bad("id must be an integer >= 0");
    }
    if !matches!(o["ok"], Value::Bool(_)) {
        return bad("ok must be a boolean");
    }
    let (_, result) = find_op(proto, op)?;
    if ok {
        check_fields("result", result, &o["result"])?;
        return Ok(Ok(o["result"].clone()));
    }
    check_fields("error", &[("code", Ty::Str), ("class", Ty::Str), ("message", Ty::Str)], &o["error"])?;
    let e = &o["error"];
    let code = ErrorCode::parse(e.as_obj().expect("checked")["code"].as_str().expect("checked"))
        .ok_or(())
        .or_else(|_| bad("unknown error code"))?;
    if e.as_obj().expect("checked")["class"].as_str() != Some(code.class().as_str()) {
        return bad("error class does not match its code");
    }
    Ok(Err(VnxError::new(code, e.as_obj().expect("checked")["message"].as_str().expect("checked"))))
}

pub fn request(proto: Proto, id: i64, op: &str, args: Value) -> Result<Value> {
    let v = obj([
        ("proto", Value::Str(proto.name().into())),
        ("v", Value::Int(VERSION)),
        ("kind", Value::Str("request".into())),
        ("id", Value::Int(id)),
        ("op", Value::Str(op.into())),
        ("args", args),
    ]);
    validate_request(proto, &v)?;
    Ok(v)
}

pub fn ok_response(proto: Proto, id: i64, op: &str, result: Value) -> Result<Value> {
    let v = obj([
        ("proto", Value::Str(proto.name().into())),
        ("v", Value::Int(VERSION)),
        ("kind", Value::Str("response".into())),
        ("id", Value::Int(id)),
        ("ok", Value::Bool(true)),
        ("result", result),
    ]);
    // ok = true, so a valid message is never an error response
    validate_response(proto, op, &v)?.map(|_| v)
}

pub fn error_response(proto: Proto, id: i64, e: &VnxError) -> Value {
    // a Str holds 1..=1024 bytes: cut on a character boundary; an empty message is replaced by the code
    let mut message = String::new();
    for c in e.message.chars() {
        if message.len() + c.len_utf8() > 1024 {
            break;
        }
        message.push(c);
    }
    if message.is_empty() {
        message = e.code.as_str().to_string();
    }
    obj([
        ("proto", Value::Str(proto.name().into())),
        ("v", Value::Int(VERSION)),
        ("kind", Value::Str("response".into())),
        ("id", Value::Int(id)),
        ("ok", Value::Bool(false)),
        (
            "error",
            obj([
                ("code", Value::Str(e.code.as_str().into())),
                ("class", Value::Str(e.code.class().as_str().into())),
                ("message", Value::Str(message)),
            ]),
        ),
    ])
}

/// Every op name of a protocol (for documentation and the cross-language tests).
pub fn op_names(proto: Proto) -> Vec<&'static str> {
    ops(proto).iter().map(|(n, _, _)| *n).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hello() -> Value {
        obj([("client", Value::Str("t".into())), ("versions", Value::Arr(vec![Value::Int(1)]))])
    }

    #[test]
    fn build_and_validate_round_trip() {
        let r = request(Proto::Mgmt, 3, "hello", hello()).unwrap();
        let parsed = Value::parse(&r.canonical()).unwrap();
        assert_eq!(validate_request(Proto::Mgmt, &parsed).unwrap().op, "hello");
        let ok = ok_response(Proto::Mgmt, 3, "hello", obj([("server", Value::Str("vnxd".into())), ("version", Value::Int(1))]))
            .unwrap();
        assert!(validate_response(Proto::Mgmt, "hello", &ok).unwrap().is_ok());
        let e = error_response(Proto::Codec, 9, &VnxError::new(ErrorCode::Corruption, "bad"));
        let back = validate_response(Proto::Codec, "decode_extent", &e).unwrap().unwrap_err();
        assert_eq!(back.code, ErrorCode::Corruption);
    }

    #[test]
    fn validation_order_and_codes() {
        let mut r = request(Proto::Mgmt, 1, "hello", hello()).unwrap();
        if let Value::Obj(o) = &mut r {
            o.insert("v".into(), Value::Int(2));
            o.remove("args");
        }
        // the version is checked before the envelope keys
        assert_eq!(validate_request(Proto::Mgmt, &r).unwrap_err().code, ErrorCode::VersionMismatch);
        let wrong_proto = request(Proto::Codec, 1, "hello", hello()).unwrap();
        assert_eq!(validate_request(Proto::Mgmt, &wrong_proto).unwrap_err().code, ErrorCode::Protocol);
        assert_eq!(request(Proto::Mgmt, 1, "format_disk", obj([])).unwrap_err().code, ErrorCode::Protocol);
        assert_eq!(request(Proto::Mgmt, -1, "stats", obj([])).unwrap_err().code, ErrorCode::Protocol);
        let extra = obj([("x", Value::Int(1))]);
        assert_eq!(request(Proto::Mgmt, 1, "stats", extra).unwrap_err().code, ErrorCode::Protocol);
    }

    #[test]
    fn staging_names_are_never_paths() {
        for ok in ["e7-g2.fasta", "a", "X_1-2.bin"] {
            assert!(is_staging_name(ok), "{ok}");
        }
        for bad in ["", ".", "..", ".hidden", "a/b", "../x", "/etc/passwd", "a b", "é", &"a".repeat(256)] {
            assert!(!is_staging_name(bad), "{bad}");
        }
    }

    #[test]
    fn error_class_must_match_code() {
        let mut e = error_response(Proto::Mgmt, 1, &VnxError::new(ErrorCode::Timeout, "slow"));
        if let Value::Obj(o) = &mut e {
            if let Some(Value::Obj(inner)) = o.get_mut("error") {
                inner.insert("class".into(), Value::Str("permanent".into()));
            }
        }
        assert_eq!(validate_response(Proto::Mgmt, "stats", &e).unwrap_err().code, ErrorCode::Protocol);
    }
}
