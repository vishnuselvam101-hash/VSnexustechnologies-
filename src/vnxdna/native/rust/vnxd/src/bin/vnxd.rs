//! `vnxd` command line: contract inspection tools (Phase 1) and the block-device service (`vnxd nbd ...`, Phase 8;
//! docs/V14_DEVICE_INTERFACE.md).
//!
//! * `vnxd version`: name, version, record format and protocol versions as canonical JSON;
//! * `vnxd inspect-journal <file>`: decode a volume journal (mirrored record pairs) or a plain record file and print
//!   its layout, every record, the valid length, the reason reading stopped and the offsets of repaired pairs, as
//!   canonical JSON, without changing the file. Exit 0 when the whole file decodes (a mirrored journal with a
//!   repairable pair included), 3 otherwise.

use std::process::ExitCode;
use vnxd::json::{obj, Value};
use vnxd::error::{ErrorCode, VnxError};
use vnxd::{journal, proto, record};

fn usage() -> ExitCode {
    eprintln!("usage: vnxd version | vnxd inspect-journal <file> | vnxd nbd <create|attach|serve> ...");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("nbd") {
        return ExitCode::from(vnxd::nbdctl::main(&args[1..]) as u8);
    }
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["version"] => {
            let protocols = [proto::Proto::Mgmt, proto::Proto::Codec]
                .iter()
                .map(|p| Value::Str(format!("{}/{}", p.name(), proto::VERSION)))
                .collect();
            let v = obj([
                ("name", Value::Str(vnxd::NAME.into())),
                ("version", Value::Str(vnxd::VERSION.into())),
                ("record_format", Value::Int(record::FORMAT_VERSION as i64)),
                ("protocols", Value::Arr(protocols)),
            ]);
            println!("{}", v.canonical());
            ExitCode::SUCCESS
        }
        ["inspect-journal", path] => {
            let buf = match std::fs::read(path) {
                Ok(b) => b,
                Err(e) => {
                    eprintln!("vnxd: cannot read {path}: {e}");
                    return ExitCode::from(1);
                }
            };
            // a mirrored journal first; a file that is not one is read as plain concatenated records
            let (layout, records, valid_len, stop, repaired): (&str, Vec<record::Record>, usize, Option<VnxError>, Vec<u64>) =
                match journal::parse(&buf) {
                    Ok(p) => ("mirrored", p.records, p.valid_len, p.torn_tail, p.repaired),
                    Err(e) if e.code == ErrorCode::Corruption && !buf.is_empty() => {
                        // damage inside a mirrored journal: report where the plain reading stops too
                        let s = record::scan(&buf);
                        ("mirrored", s.records, s.valid_len, Some(e), Vec::new())
                    }
                    Err(_) => {
                        let s = record::scan(&buf);
                        ("plain", s.records, s.valid_len, s.tail_error, Vec::new())
                    }
                };
            let mut recs = Vec::new();
            for r in &records {
                match record::to_json(r) {
                    Ok(v) => recs.push(v),
                    Err(e) => recs.push(obj([("unprintable", Value::Str(e.to_string()))])),
                }
            }
            let tail = match &stop {
                Some(e) => obj([("code", Value::Str(e.code.as_str().into())), ("message", Value::Str(e.message.clone()))]),
                None => Value::Null,
            };
            let v = obj([
                ("bytes", Value::Int(buf.len() as i64)),
                ("layout", Value::Str(layout.into())),
                ("valid_len", Value::Int(valid_len as i64)),
                ("records", Value::Arr(recs)),
                ("tail_error", tail),
                ("repaired", Value::Arr(repaired.iter().map(|o| Value::Int(*o as i64)).collect())),
            ]);
            println!("{}", v.canonical());
            if stop.is_none() {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(3)
            }
        }
        _ => usage(),
    }
}
