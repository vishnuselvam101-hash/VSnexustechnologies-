//! The `vnxd` command line: version and journal inspection.

use std::process::Command;
use vnxd::json::Value;
use vnxd::addr::{ExtentId, Generation, GenerationRef};
use vnxd::record::{self, GenerationReleased, Record};

fn vnxd(args: &[&str]) -> (i32, Value) {
    let out = Command::new(env!("CARGO_BIN_EXE_vnxd")).args(args).output().expect("run vnxd");
    let text = String::from_utf8(out.stdout).expect("utf-8");
    (out.status.code().unwrap_or(-1), Value::parse(text.trim_end()).unwrap_or(Value::Null))
}

fn obj(v: &Value) -> &std::collections::BTreeMap<String, Value> {
    v.as_obj().expect("object")
}

#[test]
fn version_names_the_contracts() {
    let (code, v) = vnxd(&["version"]);
    assert_eq!(code, 0);
    assert_eq!(obj(&v)["name"], Value::Str("vnxd".into()));
    assert_eq!(obj(&v)["record_format"], Value::Int(1));
    assert_eq!(
        obj(&v)["protocols"],
        Value::Arr(vec![Value::Str("vnx.mgmt/1".into()), Value::Str("vnx.codec-ipc/1".into())])
    );
}

#[test]
fn inspect_journal_reports_records_and_the_torn_tail() {
    let dir = std::env::temp_dir().join(format!("vnxd-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("journal");
    let mut journal = Vec::new();
    for g in 1..=3u64 {
        let at = GenerationRef { extent: ExtentId(g), generation: Generation::new(g).unwrap() };
        journal.extend(Record::GenerationReleased(GenerationReleased { txid: g, at }).encode().unwrap());
    }
    std::fs::write(&path, &journal).unwrap();
    let (code, v) = vnxd(&["inspect-journal", path.to_str().unwrap()]);
    assert_eq!(code, 0);
    assert_eq!(obj(&v)["valid_len"], Value::Int(journal.len() as i64));
    assert_eq!(obj(&v)["tail_error"], Value::Null);
    match &obj(&v)["records"] {
        Value::Arr(a) => assert_eq!(a[2], record::to_json(&record::scan(&journal).records[2]).unwrap()),
        _ => panic!("records"),
    }

    std::fs::write(&path, &journal[..journal.len() - 5]).unwrap();
    let (code, v) = vnxd(&["inspect-journal", path.to_str().unwrap()]);
    assert_eq!(code, 3);
    assert_eq!(obj(&v)["valid_len"], Value::Int(2 * 40));
    assert_eq!(obj(&obj(&v)["tail_error"])["code"], Value::Str("CORRUPTION".into()));
    std::fs::remove_dir_all(&dir).unwrap();

    let (code, _) = vnxd(&["inspect-journal", "/nonexistent/vnxd-journal"]);
    assert_eq!(code, 1);
    let (code, _) = vnxd(&["bogus"]);
    assert_eq!(code, 2);
}
