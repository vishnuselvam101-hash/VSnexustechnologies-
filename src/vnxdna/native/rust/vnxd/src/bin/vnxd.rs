//! `vnxd` command line (Phase 1): contract inspection tools. The block-device service is added in Phase 8.
//!
//! * `vnxd version`: name, version, record format and protocol versions as canonical JSON;
//! * `vnxd inspect-journal <file>`: decode a record file (superblock or journal) and print every record, the valid
//!   length and the reason reading stopped, as canonical JSON. Exit 0 when the whole file decodes, 3 otherwise.

use std::process::ExitCode;
use vnxd::json::{obj, Value};
use vnxd::{proto, record};

fn usage() -> ExitCode {
    eprintln!("usage: vnxd version | vnxd inspect-journal <file>");
    ExitCode::from(2)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
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
            let scan = record::scan(&buf);
            let mut recs = Vec::new();
            for r in &scan.records {
                match record::to_json(r) {
                    Ok(v) => recs.push(v),
                    Err(e) => recs.push(obj([("unprintable", Value::Str(e.to_string()))])),
                }
            }
            let tail = match &scan.tail_error {
                Some(e) => obj([("code", Value::Str(e.code.as_str().into())), ("message", Value::Str(e.message.clone()))]),
                None => Value::Null,
            };
            let v = obj([
                ("bytes", Value::Int(buf.len() as i64)),
                ("valid_len", Value::Int(scan.valid_len as i64)),
                ("records", Value::Arr(recs)),
                ("tail_error", tail),
            ]);
            println!("{}", v.canonical());
            if scan.tail_error.is_none() {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(3)
            }
        }
        _ => usage(),
    }
}
