//! Strict JSON for the service protocols (docs/V14_CONTRACTS.md §6), standard library only.
//!
//! [`Value::canonical`] produces exactly the bytes of Python's `vnxdna.core.util.canonical_json`
//! (`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`): sorted keys, no whitespace, the short
//! escapes `\" \\ \n \r \t \b \f`, and every other character outside printable ASCII 0x20..=0x7e (DEL included)
//! as `\uXXXX` (lowercase hex, surrogate pairs above U+FFFF). Floats are not part of the contract: the parser rejects
//! fractions and exponents. Integers are limited to the signed 64-bit range. Duplicate keys, trailing data, documents
//! larger than [`MAX_DOC`] and values nested deeper than [`MAX_DEPTH`] (the top-level value is depth 1 and a scalar
//! counts as a level: 32 nested empty arrays are accepted, a scalar inside 32 arrays is not) are errors. Callers hand
//! the parser decoded text: invalid UTF-8 on the wire is rejected by the transport as PROTOCOL before parsing.

use crate::error::{err, ErrorCode, Result};
use std::collections::BTreeMap;

pub const MAX_DEPTH: usize = 32;
pub const MAX_DOC: usize = 1 << 20;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Arr(Vec<Value>),
    Obj(BTreeMap<String, Value>),
}

fn bad<T>(msg: impl Into<String>) -> Result<T> {
    err(ErrorCode::Protocol, msg)
}

impl Value {
    pub fn canonical(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }

    fn write(&self, out: &mut String) {
        match self {
            Value::Null => out.push_str("null"),
            Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Value::Int(i) => out.push_str(&i.to_string()),
            Value::Str(s) => write_str(s, out),
            Value::Arr(a) => {
                out.push('[');
                for (i, v) in a.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    v.write(out);
                }
                out.push(']');
            }
            Value::Obj(o) => {
                out.push('{');
                // Python sorts keys by code point; BTreeMap<String> sorts by UTF-8 bytes, which is the same order
                for (i, (k, v)) in o.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    write_str(k, out);
                    out.push(':');
                    v.write(out);
                }
                out.push('}');
            }
        }
    }

    pub fn parse(text: &str) -> Result<Value> {
        if text.len() > MAX_DOC {
            return bad(format!("document of {} bytes exceeds {MAX_DOC}", text.len()));
        }
        let mut p = Parser { b: text.as_bytes(), pos: 0 };
        p.ws();
        let v = p.value(0)?;
        p.ws();
        if p.pos != p.b.len() {
            return bad(format!("trailing data at byte {}", p.pos));
        }
        Ok(v)
    }

    pub fn as_obj(&self) -> Option<&BTreeMap<String, Value>> {
        match self {
            Value::Obj(o) => Some(o),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_int(&self) -> Option<i64> {
        match self {
            Value::Int(i) => Some(*i),
            _ => None,
        }
    }
}

fn write_str(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut units = [0u16; 2];
                for u in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{:04x}", u));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

struct Parser<'a> {
    b: &'a [u8],
    pos: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.pos < self.b.len() && matches!(self.b[self.pos], b' ' | b'\t' | b'\n' | b'\r') {
            self.pos += 1;
        }
    }

    fn peek(&self) -> Option<u8> {
        self.b.get(self.pos).copied()
    }

    fn expect(&mut self, lit: &[u8]) -> Result<()> {
        if self.b[self.pos..].starts_with(lit) {
            self.pos += lit.len();
            Ok(())
        } else {
            bad(format!("invalid literal at byte {}", self.pos))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Value> {
        if depth >= MAX_DEPTH {
            return bad(format!("nesting deeper than {MAX_DEPTH}"));
        }
        match self.peek() {
            None => bad("unexpected end of document"),
            Some(b'n') => self.expect(b"null").map(|_| Value::Null),
            Some(b't') => self.expect(b"true").map(|_| Value::Bool(true)),
            Some(b'f') => self.expect(b"false").map(|_| Value::Bool(false)),
            Some(b'"') => self.string().map(Value::Str),
            Some(b'[') => {
                self.pos += 1;
                let mut a = Vec::new();
                self.ws();
                if self.peek() == Some(b']') {
                    self.pos += 1;
                    return Ok(Value::Arr(a));
                }
                loop {
                    self.ws();
                    a.push(self.value(depth + 1)?);
                    self.ws();
                    match self.peek() {
                        Some(b',') => self.pos += 1,
                        Some(b']') => {
                            self.pos += 1;
                            return Ok(Value::Arr(a));
                        }
                        _ => return bad(format!("expected ',' or ']' at byte {}", self.pos)),
                    }
                }
            }
            Some(b'{') => {
                self.pos += 1;
                let mut o = BTreeMap::new();
                self.ws();
                if self.peek() == Some(b'}') {
                    self.pos += 1;
                    return Ok(Value::Obj(o));
                }
                loop {
                    self.ws();
                    if self.peek() != Some(b'"') {
                        return bad(format!("expected a string key at byte {}", self.pos));
                    }
                    let k = self.string()?;
                    self.ws();
                    if self.peek() != Some(b':') {
                        return bad(format!("expected ':' at byte {}", self.pos));
                    }
                    self.pos += 1;
                    self.ws();
                    let v = self.value(depth + 1)?;
                    if o.insert(k.clone(), v).is_some() {
                        return bad(format!("duplicate key {k:?}"));
                    }
                    self.ws();
                    match self.peek() {
                        Some(b',') => self.pos += 1,
                        Some(b'}') => {
                            self.pos += 1;
                            return Ok(Value::Obj(o));
                        }
                        _ => return bad(format!("expected ',' or '}}' at byte {}", self.pos)),
                    }
                }
            }
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(c) => bad(format!("unexpected byte {c:#04x} at {}", self.pos)),
        }
    }

    fn number(&mut self) -> Result<Value> {
        let start = self.pos;
        if self.peek() == Some(b'-') {
            self.pos += 1;
        }
        let digits = self.pos;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.pos += 1;
        }
        let n = self.pos - digits;
        if n == 0 || (n > 1 && self.b[digits] == b'0') {
            return bad(format!("invalid number at byte {start}"));
        }
        if matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            return bad(format!("non-integer number at byte {start} (floats are not allowed)"));
        }
        let text = std::str::from_utf8(&self.b[start..self.pos]).expect("ASCII digits");
        if text == "-0" {
            return bad("negative zero is not canonical");
        }
        text.parse::<i64>().map(Value::Int).or_else(|_| bad(format!("integer {text} outside the 64-bit range")))
    }

    fn hex4(&mut self) -> Result<u32> {
        let h = self.b.get(self.pos..self.pos + 4).ok_or(()).or_else(|_| bad("truncated \\u escape"))?;
        let s = std::str::from_utf8(h).map_err(|_| ()).or_else(|_| bad("invalid \\u escape"))?;
        if !s.bytes().all(|c| c.is_ascii_hexdigit()) {
            return bad("invalid \\u escape");
        }
        self.pos += 4;
        Ok(u32::from_str_radix(s, 16).expect("hex digits"))
    }

    fn string(&mut self) -> Result<String> {
        self.pos += 1; // opening quote
        let mut out = String::new();
        loop {
            let start = self.pos;
            while let Some(c) = self.peek() {
                if c == b'"' || c == b'\\' || c < 0x20 {
                    break;
                }
                self.pos += 1;
            }
            // the input is a &str, so any run of bytes between ASCII delimiters is valid UTF-8
            out.push_str(std::str::from_utf8(&self.b[start..self.pos]).expect("utf-8 slice"));
            match self.peek() {
                None => return bad("unterminated string"),
                Some(b'"') => {
                    self.pos += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.pos += 1;
                    let e = self.peek().ok_or(()).or_else(|_| bad("unterminated escape"))?;
                    self.pos += 1;
                    match e {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{08}'),
                        b'f' => out.push('\u{0c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let hi = self.hex4()?;
                            let cp = if (0xD800..0xDC00).contains(&hi) {
                                if !self.b[self.pos..].starts_with(b"\\u") {
                                    return bad("unpaired surrogate");
                                }
                                self.pos += 2;
                                let lo = self.hex4()?;
                                if !(0xDC00..0xE000).contains(&lo) {
                                    return bad("unpaired surrogate");
                                }
                                0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00)
                            } else if (0xDC00..0xE000).contains(&hi) {
                                return bad("unpaired surrogate");
                            } else {
                                hi
                            };
                            out.push(char::from_u32(cp).expect("valid scalar value"));
                        }
                        _ => return bad(format!("invalid escape at byte {}", self.pos - 1)),
                    }
                }
                Some(_) => return bad(format!("unescaped control character at byte {}", self.pos)),
            }
        }
    }
}

/// Build an object from (key, value) pairs.
pub fn obj<const N: usize>(pairs: [(&str, Value); N]) -> Value {
    Value::Obj(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_matches_python_rules() {
        let v = Value::parse(r#" { "b" : [1, -2, true, null], "a": "x\u00e9\ud83d\ude00\u0001\n/\"" } "#).unwrap();
        assert_eq!(v.canonical(), r#"{"a":"x\u00e9\ud83d\ude00\u0001\n/\"","b":[1,-2,true,null]}"#);
        assert_eq!(Value::parse(&v.canonical()).unwrap(), v);
        // Python escapes everything outside printable ASCII 0x20..=0x7e, DEL included
        assert_eq!(Value::Str("\u{7f}~".into()).canonical(), "\"\\u007f~\"");
    }

    #[test]
    fn rejects_non_contract_json() {
        for bad in [
            "", "1.5", "1e3", "01", "-0", "-", "[1,]", "{\"a\":1,}", "{\"a\":1,\"a\":2}", "{a:1}", "\"\\x\"",
            "\"\\ud800\"", "\"\\udc00\"", "\"a\u{01}\"", "[1] 2", "99999999999999999999", "tru", "nul",
        ] {
            assert!(Value::parse(bad).is_err(), "accepted {bad:?}");
        }
        let deep = "[".repeat(MAX_DEPTH + 1) + &"]".repeat(MAX_DEPTH + 1);
        assert!(Value::parse(&deep).is_err());
        let ok = "[".repeat(MAX_DEPTH) + &"]".repeat(MAX_DEPTH);
        assert!(Value::parse(&ok).is_ok());
    }

    #[test]
    fn integer_range() {
        assert_eq!(Value::parse("-9223372036854775808").unwrap(), Value::Int(i64::MIN));
        assert_eq!(Value::parse("9223372036854775807").unwrap(), Value::Int(i64::MAX));
        assert!(Value::parse("9223372036854775808").is_err());
    }
}
