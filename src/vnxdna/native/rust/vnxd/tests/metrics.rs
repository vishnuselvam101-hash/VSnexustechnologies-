//! AP-6: the Prometheus text exposition of `vnxd` (text format version 0.0.4).
//!
//! The tests check that the exposition is well formed (one HELP line and one TYPE line per metric, every sample
//! line `name{labels} integer`), that it is deterministic (the same samples in any order render to the same bytes),
//! that a silent corruption of the backend is reported by the scrub, and that the backend state walks from healthy
//! to degraded to unavailable.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use vnxd::addr::{ExtentId, Generation, GenerationRef, Geometry};
use vnxd::cache::CacheStats;
use vnxd::dnasim::DnaSimStats;
use vnxd::error::ErrorCode;
use vnxd::fallback::{BackendState, HealthConfig, ResilientMedia, RetryPolicy};
use vnxd::fault::{Fault, FaultMedia, MediaOp, Rule};
use vnxd::json::{obj, Value};
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::metrics::{self, Kind, Sample};
use vnxd::nbdserve::{Device, DeviceConfig};
use vnxd::volume::{Volume, VolumeConfig};

/// A fresh directory under the system temp dir, removed on drop.
struct Dir(PathBuf);

impl Dir {
    fn new(tag: &str) -> Dir {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!("vnxd-metrics-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The samples of the exposition under test: two metric names with labels, one with a label value that has to be
/// escaped, and a few plain ones.
fn samples() -> Vec<Sample> {
    vec![
        Sample::new("vnxd_health_state", "health state of the device", Kind::Gauge, 1).with_label("state", "ok"),
        Sample::new("vnxd_health_state", "health state of the device", Kind::Gauge, 0).with_label("state", "degraded"),
        Sample::new("vnxd_health_state", "health state of the device", Kind::Gauge, 0).with_label("state", "failed"),
        Sample::new("vnxd_backend_state", "health state of the backend", Kind::Gauge, 1).with_label("state", "healthy"),
        Sample::new("vnxd_backend_state", "health state of the backend", Kind::Gauge, 0).with_label("state", "degraded"),
        Sample::new("vnxd_backend_state", "health state of the backend", Kind::Gauge, 0).with_label("state", "unavailable"),
        Sample::new("vnxd_read_only", "the device is read-only", Kind::Gauge, 0),
        Sample::new("vnxd_backend_failures_total", "backend operations that failed", Kind::Counter, 4),
        Sample::new("vnxd_backend_score_ppm", "success score of the backend in parts per million", Kind::Gauge, 512000),
        Sample::new("vnxd_scrub_failure", "extents that failed the last scrub, by error code", Kind::Counter, 1)
            .with_label("code", "CORRUPTION"),
        // a label value with a quote, a backslash and a newline: all three are escaped
        Sample::new("vnxd_scrub_failure", "extents that failed the last scrub, by error code", Kind::Counter, 2)
            .with_label("code", "a\"b\\c\nd"),
    ]
}

/// Check the exposition line by line: every sample line is `name{labels} integer`, every metric name has exactly one
/// HELP line and one TYPE line.
fn check_grammar(text: &str) {
    let mut helped: Vec<String> = Vec::new();
    let mut typed: Vec<String> = Vec::new();
    let mut count = 0;
    for line in text.lines() {
        assert!(!line.is_empty(), "the exposition holds no empty line");
        if let Some(rest) = line.strip_prefix("# HELP ") {
            let (name, help) = rest.split_once(' ').expect("a HELP line holds a name and a help text");
            assert!(metrics::is_valid_name(name), "{name} is not a metric name");
            assert!(!help.is_empty(), "{name} has no help text");
            assert!(!helped.contains(&name.to_string()), "two HELP lines for {name}");
            helped.push(name.to_string());
            continue;
        }
        if let Some(rest) = line.strip_prefix("# TYPE ") {
            let (name, kind) = rest.split_once(' ').expect("a TYPE line holds a name and a type");
            assert!(metrics::is_valid_name(name), "{name} is not a metric name");
            assert!(kind == "counter" || kind == "gauge", "{name} has the type {kind}");
            assert!(!typed.contains(&name.to_string()), "two TYPE lines for {name}");
            typed.push(name.to_string());
            continue;
        }
        let (series, value) = line.rsplit_once(' ').expect("a sample line holds a series and a value");
        assert!(value.parse::<i64>().is_ok(), "the value of {line} is not an integer");
        match series.split_once('{') {
            None => assert!(metrics::is_valid_name(series), "{series} is not a metric name"),
            Some((name, labels)) => {
                assert!(metrics::is_valid_name(name), "{name} is not a metric name");
                let labels = labels.strip_suffix('}').expect("the labels of a series end with }");
                check_labels(name, labels);
            }
        }
        count += 1;
    }
    assert_eq!(helped, typed, "every metric with a HELP line has a TYPE line");
    assert!(count > 0, "the exposition holds no sample");
}

/// The labels of one series: `name="value"`, separated by commas, the value quoted and escaped.
fn check_labels(name: &str, labels: &str) {
    let mut rest = labels.trim();
    while !rest.is_empty() {
        let (key, tail) = rest.split_once('=').expect("a label is a name and a value");
        let key = key.trim();
        assert!(is_label_name(key), "{key} is not a label name of {name}");
        let tail = tail.trim_start();
        assert!(tail.starts_with('"'), "the value of the label {key} is not quoted");
        let bytes = tail.as_bytes();
        let mut i = 1;
        while i < bytes.len() {
            match bytes[i] {
                b'\\' => i += 2,
                b'"' => break,
                _ => i += 1,
            }
        }
        assert!(i < bytes.len(), "the value of the label {key} is not closed");
        let tail = &tail[i + 1..];
        if tail.is_empty() {
            return;
        }
        assert!(tail.starts_with(','), "the labels of {name} are not separated by commas");
        rest = tail[1..].trim();
    }
}

fn is_label_name(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

#[test]
fn the_exposition_is_the_same_whatever_the_order_of_the_samples() {
    let s = samples();
    let text = metrics::render(&s);
    let mut reversed = s.clone();
    reversed.reverse();
    assert_eq!(text, metrics::render(&reversed), "the order of the samples changes the output");
    let mut rotated = s.clone();
    rotated.rotate_left(3);
    assert_eq!(text, metrics::render(&rotated), "the order of the samples changes the output");
    // the series are sorted: first by the name of the metric, then by the labels
    let names: Vec<&str> = text.lines().filter(|l| !l.starts_with('#')).map(|l| l.split(['{', ' ']).next().unwrap()).collect();
    let mut sorted = names.clone();
    sorted.sort_unstable();
    assert_eq!(names, sorted, "the series are not sorted by metric name");
}

#[test]
fn the_exposition_follows_the_text_format_grammar() {
    check_grammar(&metrics::render(&samples()));
    // the escaping of the label values
    let text = metrics::render(&samples());
    assert!(text.contains("vnxd_scrub_failure{code=\"CORRUPTION\"} 1\n"), "{text}");
    assert!(text.contains("vnxd_scrub_failure{code=\"a\\\"b\\\\c\\nd\"} 2\n"), "{text}");
}

#[test]
fn every_counter_of_the_stats_view_becomes_a_metric() {
    let stats = obj([(
        "counters",
        obj([
            ("nbd_requests", Value::Int(3)),
            ("volume_reads", Value::Int(11)),
            ("ram_in_use", Value::Int(4096)),
            ("nbd_max_inflight", Value::Int(2)),
            ("cache_hits", Value::Int(7)),
        ]),
    )]);
    let samples = metrics::from_stats(&stats);
    let names = metrics::names(&samples);
    assert!(names.contains(&"vnxd_nbd_requests_total".to_string()), "{names:?}");
    assert!(names.contains(&"vnxd_volume_reads_total".to_string()), "{names:?}");
    assert!(names.contains(&"vnxd_ram_in_use".to_string()), "{names:?}");
    assert!(names.contains(&"vnxd_nbd_max_inflight".to_string()), "{names:?}");
    assert!(names.contains(&"vnxd_cache_hits_total".to_string()), "{names:?}");
    let text = metrics::render(&samples);
    assert!(text.contains("vnxd_nbd_requests_total 3\n"), "{text}");
    assert!(text.contains("vnxd_volume_reads_total 11\n"), "{text}");
    assert!(text.contains("vnxd_ram_in_use 4096\n"), "{text}");
    assert!(text.contains("vnxd_nbd_max_inflight 2\n"), "{text}");
    assert!(text.contains("vnxd_cache_hits_total 7\n"), "{text}");
    check_grammar(&text);
}

#[test]
fn every_counter_of_a_real_stats_view_becomes_a_metric() {
    let dir = Dir::new("stats");
    let media = Arc::new(MemoryMedia::new());
    let geo = Geometry::new(512, 4, 16).unwrap();
    let v = Volume::create(&dir.0, geo, [9; 16], 0, media.clone(), VolumeConfig::default()).unwrap();
    drop(v);
    let dev = Device::open(&dir.0, media, DeviceConfig::default()).unwrap();
    let stats = dev.stats_json();
    let counters = stats
        .as_obj()
        .unwrap()
        .get("counters")
        .unwrap()
        .as_obj()
        .unwrap()
        .clone();
    let samples = metrics::from_stats(&stats);
    let names = metrics::names(&samples);
    assert_eq!(names.len(), counters.len(), "every counter of the stats view is a metric");
    let text = metrics::render(&samples);
    for (key, value) in &counters {
        let name = metrics::stats_metric_name(key);
        assert!(names.contains(&name), "{key} has no metric ({name})");
        let v = value.as_int().unwrap();
        assert!(text.contains(&format!("{name} {v}\n")), "{key} is missing from the exposition");
    }
    check_grammar(&text);
}

#[test]
fn the_health_view_becomes_metrics() {
    let health = obj([
        ("state", Value::Str("degraded".into())),
        (
            "detail",
            obj([
                ("damaged_extents", Value::Arr(vec![Value::Int(3)])),
                ("read_only", Value::Bool(true)),
                ("inflight", Value::Int(2)),
            ]),
        ),
    ]);
    let text = metrics::render(&metrics::from_health(&health));
    assert!(text.contains("vnxd_health_state{state=\"degraded\"} 1\n"), "{text}");
    assert!(text.contains("vnxd_health_state{state=\"ok\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_health_state{state=\"failed\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_read_only 1\n"), "{text}");
    assert!(text.contains("vnxd_damaged_extents 1\n"), "{text}");
    assert!(text.contains("vnxd_inflight 2\n"), "{text}");
    check_grammar(&text);
}

#[test]
fn the_cache_the_capacity_and_the_simulator_become_metrics() {
    let cache = CacheStats::default();
    cache.hits.store(5, Ordering::Relaxed);
    cache.misses.store(2, Ordering::Relaxed);
    cache.writeback_failures.store(1, Ordering::Relaxed);
    let text = metrics::render(&metrics::from_cache(&cache, 3, 1));
    assert!(text.contains("vnxd_cache_hits_total 5\n"), "{text}");
    assert!(text.contains("vnxd_cache_misses_total 2\n"), "{text}");
    assert!(text.contains("vnxd_cache_writeback_failures_total 1\n"), "{text}");
    assert!(text.contains("vnxd_cache_extents 3\n"), "{text}");
    assert!(text.contains("vnxd_cache_dirty_extents 1\n"), "{text}");

    let cap = obj([
        ("capacity_bytes", Value::Int(8192)),
        ("extent_count", Value::Int(4)),
        ("block_size", Value::Int(512)),
    ]);
    let text = metrics::render(&metrics::from_capacity(&cap));
    assert!(text.contains("vnxd_capacity_bytes 8192\n"), "{text}");
    assert!(text.contains("vnxd_extent_count 4\n"), "{text}");
    assert!(text.contains("vnxd_block_size_bytes 512\n"), "{text}");

    let sim = DnaSimStats::default();
    sim.synthesis_runs.store(2, Ordering::Relaxed);
    sim.decode_failures.store(1, Ordering::Relaxed);
    let text = metrics::render(&metrics::from_dnasim(&sim));
    assert!(text.contains("vnxd_synthesis_runs_total 2\n"), "{text}");
    assert!(text.contains("vnxd_decode_failures_total 1\n"), "{text}");
    check_grammar(&text);
}

#[test]
fn a_scrub_after_a_silent_corrupt_write_reports_one_failed_extent() {
    let dir = Dir::new("scrub");
    let fm = Arc::new(FaultMedia::new());
    let geo = Geometry::new(512, 4, 16).unwrap();
    // the read-back is off: the corruption is not seen at the write, only when the extent is read again
    let v = Volume::create(
        &dir.0,
        geo,
        [3; 16],
        0,
        fm.clone(),
        VolumeConfig { verify_readback: false, ..VolumeConfig::default() },
    )
    .unwrap();
    let data = vec![7u8; 2048];
    v.write_extent(ExtentId(0), &data, 1000).unwrap();
    // the backend stores other bytes than it was given and returns an honest receipt
    fm.add(Rule::once(MediaOp::Put, Fault::SilentCorruptWrite));
    v.write_extent(ExtentId(0), &data, 1001).unwrap();
    let report = v.scrub().unwrap();
    assert_eq!(report.checked, 1, "the scrub checks every extent of the map");
    assert_eq!(report.failed.len(), 1, "the corrupted extent is the only one that fails");
    assert_eq!(report.failed[0].0, ExtentId(0));
    assert_eq!(report.failed[0].1.code, ErrorCode::Corruption);
    let text = metrics::render(&metrics::from_scrub(&report, 1_700_000_000_000));
    assert!(text.contains("vnxd_scrub_checked_extents 1\n"), "{text}");
    assert!(text.contains("vnxd_scrub_failed_extents 1\n"), "{text}");
    assert!(text.contains("vnxd_scrub_failure{code=\"CORRUPTION\"} 1\n"), "{text}");
    assert!(text.contains("vnxd_scrub_last_run_unix_ms 1700000000000\n"), "{text}");
    check_grammar(&text);
}

#[test]
fn the_backend_state_walks_from_healthy_to_degraded_to_unavailable() {
    let fm = Arc::new(FaultMedia::new());
    let health = HealthConfig { degrade_after: 1, unavailable_after: 3, probe_interval: Duration::from_secs(3600), score_alpha: 0.2 };
    let retry = RetryPolicy { max_attempts: 1, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    let rm = ResilientMedia::new(fm.clone(), retry, health).unwrap();
    let at = GenerationRef { extent: ExtentId(0), generation: Generation::new(1).unwrap() };

    assert_eq!(rm.health().state, BackendState::Healthy);
    let text = metrics::render(&metrics::from_backend(&rm.health()));
    assert!(text.contains("vnxd_backend_state{state=\"healthy\"} 1\n"), "{text}");
    assert!(text.contains("vnxd_backend_state{state=\"degraded\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_backend_state{state=\"unavailable\"} 0\n"), "{text}");

    // the backend goes away: the first failure degrades it, the third makes it unavailable
    fm.set_offline(true);
    for i in 1..=2 {
        assert_eq!(rm.put(at, b"abc").unwrap_err().code, ErrorCode::BackendUnavailable);
        assert_eq!(rm.health().state, BackendState::Degraded, "after {i} failures");
    }
    let text = metrics::render(&metrics::from_backend(&rm.health()));
    assert!(text.contains("vnxd_backend_state{state=\"degraded\"} 1\n"), "{text}");
    assert!(text.contains("vnxd_backend_state{state=\"healthy\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_backend_state{state=\"unavailable\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_backend_failures_total 2\n"), "{text}");
    assert!(text.contains("vnxd_backend_consecutive_failures 2\n"), "{text}");

    assert_eq!(rm.put(at, b"abc").unwrap_err().code, ErrorCode::BackendUnavailable);
    assert_eq!(rm.health().state, BackendState::Unavailable);
    let text = metrics::render(&metrics::from_backend(&rm.health()));
    assert!(text.contains("vnxd_backend_state{state=\"unavailable\"} 1\n"), "{text}");
    assert!(text.contains("vnxd_backend_state{state=\"degraded\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_backend_state{state=\"healthy\"} 0\n"), "{text}");
    assert!(text.contains("vnxd_backend_failures_total 3\n"), "{text}");
    assert!(text.contains("vnxd_backend_consecutive_failures 3\n"), "{text}");
    assert!(text.contains("vnxd_backend_transitions_total 2\n"), "{text}");

    // the device is read-only while the backend is unavailable: the write is refused, not retried
    assert_eq!(rm.put(at, b"abc").unwrap_err().code, ErrorCode::ReadOnly);
    let text = metrics::render(&metrics::from_backend(&rm.health()));
    assert!(text.contains("vnxd_backend_refused_read_only_total 1\n"), "{text}");
    check_grammar(&text);
}

#[test]
fn every_metric_the_service_knows_has_a_valid_and_unique_name() {
    let mut names: Vec<&str> = Vec::new();
    for d in metrics::DEFS {
        assert!(metrics::is_valid_name(d.name), "{} is not a metric name", d.name);
        assert!(!d.help.is_empty(), "{} has no help text", d.name);
        assert!(!names.contains(&d.name), "{} is defined twice", d.name);
        names.push(d.name);
    }
    // a counter is named `..._total`, a gauge is not
    for d in metrics::DEFS {
        match d.kind {
            Kind::Counter => assert!(d.name.ends_with("_total") || d.name == "vnxd_scrub_failure", "{} is a counter", d.name),
            Kind::Gauge => assert!(!d.name.ends_with("_total"), "{} is a gauge", d.name),
        }
    }
}
