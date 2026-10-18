//! Cross-language golden tests: the Rust implementation must reproduce every entry of the golden files that the
//! Python implementation generated (tests/v14/golden/contracts, docs/V14_CONTRACTS.md §8).

use std::path::PathBuf;
use vnxd::digest::hex;
use vnxd::error::ErrorCode;
use vnxd::json::Value;
use vnxd::proto::{self, Proto};
use vnxd::record::{self, Record};

fn golden(name: &str) -> Vec<Value> {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../../../tests/v14/golden/contracts");
    let text = std::fs::read_to_string(dir.join(name)).unwrap_or_else(|e| panic!("{name}: {e}"));
    match Value::parse(&text).unwrap() {
        Value::Arr(a) => a,
        _ => panic!("{name}: not an array"),
    }
}

fn field<'a>(v: &'a Value, k: &str) -> &'a Value {
    &v.as_obj().expect("object")[k]
}

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

#[test]
fn records_encode_and_decode_byte_for_byte() {
    let entries = golden("records.json");
    assert_eq!(entries.len(), 6);
    for e in entries {
        let name = field(&e, "name").as_str().unwrap();
        let desc = field(&e, "record");
        let rec = record::from_json(desc).unwrap();
        let bytes = rec.encode().unwrap();
        assert_eq!(hex(&bytes), field(&e, "hex").as_str().unwrap(), "{name}: encoding differs from Python");
        let (back, used): (Record, usize) = Record::decode(&bytes).unwrap();
        assert_eq!(used, bytes.len());
        assert_eq!(&record::to_json(&back).unwrap(), desc, "{name}: description differs");
    }
}

#[test]
fn bad_records_fail_with_the_same_code() {
    let entries = golden("bad_records.json");
    assert!(entries.len() >= 15);
    for e in entries {
        let name = field(&e, "name").as_str().unwrap();
        let want = ErrorCode::parse(field(&e, "error").as_str().unwrap()).unwrap();
        let got = Record::decode(&unhex(field(&e, "hex").as_str().unwrap())).unwrap_err();
        assert_eq!(got.code, want, "{name}: {got}");
    }
}

#[test]
fn messages_validate_and_canonicalise_identically() {
    let entries = golden("messages.json");
    assert!(entries.len() >= 20);
    for e in entries {
        let name = field(&e, "name").as_str().unwrap();
        let p = Proto::parse(field(&e, "proto").as_str().unwrap()).unwrap();
        let op = field(&e, "op").as_str().unwrap();
        let request = field(&e, "direction").as_str() == Some("request");
        let result = Value::parse(field(&e, "text").as_str().unwrap()).and_then(|v| {
            if request {
                proto::validate_request(p, &v).map(|_| v)
            } else {
                // a well-formed error response is a valid message
                proto::validate_response(p, op, &v).map(|_| v)
            }
        });
        match e.as_obj().unwrap().get("canonical") {
            Some(Value::Str(want)) => assert_eq!(&result.unwrap().canonical(), want, "{name}"),
            _ => {
                let want = field(&e, "error").as_str().unwrap();
                assert_eq!(result.unwrap_err().code.as_str(), want, "{name}");
            }
        }
    }
}

#[test]
fn every_error_code_and_op_is_known_to_both_languages() {
    // the Python mirror lists the same codes and ops; the golden messages cover hello on both protocols
    let codes: Vec<&str> = ErrorCode::ALL.iter().map(|c| c.as_str()).collect();
    assert_eq!(codes.len(), 17);
    assert_eq!(proto::op_names(Proto::Mgmt), ["hello", "capacity", "health", "stats", "shutdown"]);
    assert_eq!(proto::op_names(Proto::Codec), ["hello", "encode_extent", "decode_extent"]);
}
