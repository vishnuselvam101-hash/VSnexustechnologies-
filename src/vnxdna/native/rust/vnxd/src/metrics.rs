//! Prometheus text exposition for the `vnxd` service (format version 0.0.4).
//!
//! The service keeps no metric state of its own: every sample is derived from the structures the service already
//! holds — the counters of the stats view ([`crate::nbdserve::NbdDevice::stats_json`]), the health view
//! ([`crate::nbdserve::NbdDevice::health_json`]), the backend health ([`crate::fallback::Health`]), the cache counters
//! ([`crate::cache::CacheStats`]), the scrub report ([`crate::volume::ScrubReport`]), the capacity view and the DNA
//! simulator counters ([`crate::dnasim::DnaSimStats`]). Nothing is counted twice and nothing is lost between two
//! scrapes.
//!
//! * the output is the Prometheus text exposition format 0.0.4: one `# HELP` line and one `# TYPE` line per metric
//!   name, then the series, sorted by metric name and by labels, so the same samples in any order render to the same
//!   bytes and two scrapes can be diffed;
//! * every value is an integer: a ratio is a count of parts per million, a time is a count of milliseconds;
//! * label values are escaped (`\\`, `\"`, `\n`), help texts are escaped (`\\`, `\n`);
//! * the DNA simulator counters are SIMULATED measurements of the software model, not of synthesised DNA.
//!
//! A metric whose name ends in `_total` is a counter; every other metric is a gauge. The two rules the renderers
//! follow are the same: [`from_stats`] maps every counter of the stats view to `vnxd_<key>_total` (or to
//! `vnxd_<key>` for the few counters that are levels, not counts), and the other converters use the names of
//! [`DEFS`].

use crate::cache::CacheStats;
use crate::dnasim::DnaSimStats;
use crate::fallback::Health;
use crate::json::Value;
use crate::volume::ScrubReport;
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};

/// The type of a metric.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// a counter that only grows.
    Counter,
    /// a value that goes up and down.
    Gauge,
}

impl Kind {
    /// The name of the type in the exposition format (`counter`, `gauge`).
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Counter => "counter",
            Kind::Gauge => "gauge",
        }
    }
}

/// One metric series: a name, its help text, its type, its labels and its integer value.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Sample {
    /// the metric name (`[a-zA-Z_:][a-zA-Z0-9_:]*`).
    pub name: Cow<'static, str>,
    /// the help text of the metric (one per name).
    pub help: Cow<'static, str>,
    /// the type of the metric.
    pub kind: Kind,
    /// the labels of the series; the label names are fixed, the values are not.
    pub labels: Vec<(&'static str, String)>,
    /// the value of the series.
    pub value: i64,
}

impl Sample {
    /// A sample without labels.
    pub fn new(name: impl Into<Cow<'static, str>>, help: impl Into<Cow<'static, str>>, kind: Kind, value: i64) -> Sample {
        Sample { name: name.into(), help: help.into(), kind, labels: Vec::new(), value }
    }

    /// The sample with one more label.
    pub fn with_label(mut self, key: &'static str, value: impl Into<String>) -> Sample {
        self.labels.push((key, value.into()));
        self
    }
}

/// The metrics whose help text and type are fixed.
pub struct Def {
    /// the metric name.
    pub name: &'static str,
    /// the help text of the metric.
    pub help: &'static str,
    /// the type of the metric.
    pub kind: Kind,
}

/// Every metric this service exposes, with its help text and its type.
pub const DEFS: &[Def] = &[
    // the health of the device
    Def { name: "vnxd_health_state", help: "health state of the device (1 = the state is current, 0 = it is not)", kind: Kind::Gauge },
    Def { name: "vnxd_read_only", help: "the device is read-only (1) or writable (0)", kind: Kind::Gauge },
    Def { name: "vnxd_damaged_extents", help: "extents whose stored bytes do not match the map", kind: Kind::Gauge },
    Def { name: "vnxd_inflight", help: "requests in flight", kind: Kind::Gauge },
    // the health of the backend
    Def { name: "vnxd_backend_state", help: "health state of the backend (1 = the state is current, 0 = it is not)", kind: Kind::Gauge },
    Def { name: "vnxd_backend_failures_total", help: "backend operations that failed", kind: Kind::Counter },
    Def { name: "vnxd_backend_retries_total", help: "retries of failed backend operations", kind: Kind::Counter },
    Def { name: "vnxd_backend_refused_read_only_total", help: "writes refused because the backend is unavailable", kind: Kind::Counter },
    Def { name: "vnxd_backend_transitions_total", help: "transitions between the backend health states", kind: Kind::Counter },
    Def { name: "vnxd_backend_consecutive_failures", help: "consecutive failed backend operations", kind: Kind::Gauge },
    Def { name: "vnxd_backend_score_ppm", help: "success score of the backend in parts per million", kind: Kind::Gauge },
    // the cache
    Def { name: "vnxd_cache_hits_total", help: "extents served from the cache", kind: Kind::Counter },
    Def { name: "vnxd_cache_misses_total", help: "extents read from the backend because the cache did not hold them", kind: Kind::Counter },
    Def { name: "vnxd_cache_evictions_total", help: "extents evicted from the cache", kind: Kind::Counter },
    Def { name: "vnxd_cache_writebacks_total", help: "extents written back from the cache to the backend", kind: Kind::Counter },
    Def { name: "vnxd_cache_writeback_failures_total", help: "writebacks that failed", kind: Kind::Counter },
    Def { name: "vnxd_cache_checksum_failures_total", help: "cached extents whose checksum did not match", kind: Kind::Counter },
    Def { name: "vnxd_cache_bypasses_total", help: "reads that bypassed the cache", kind: Kind::Counter },
    Def { name: "vnxd_cache_lost_total", help: "extents lost from the cache before they were written back", kind: Kind::Counter },
    Def { name: "vnxd_cache_prefetched_total", help: "extents read into the cache by read-ahead", kind: Kind::Counter },
    Def { name: "vnxd_cache_prefetch_hits_total", help: "hits on extents that read-ahead brought in", kind: Kind::Counter },
    Def { name: "vnxd_cache_extents", help: "extents held in the cache", kind: Kind::Gauge },
    Def { name: "vnxd_cache_dirty_extents", help: "extents held in the cache that are not written back yet", kind: Kind::Gauge },
    // the scrub
    Def { name: "vnxd_scrub_checked_extents", help: "extents checked by the last scrub", kind: Kind::Gauge },
    Def { name: "vnxd_scrub_failed_extents", help: "extents that failed the last scrub", kind: Kind::Gauge },
    Def { name: "vnxd_scrub_last_run_unix_ms", help: "unix time in milliseconds of the last scrub", kind: Kind::Gauge },
    Def { name: "vnxd_scrub_failure", help: "extents that failed the last scrub, by error code", kind: Kind::Counter },
    // the capacity
    Def { name: "vnxd_capacity_bytes", help: "capacity of the volume in bytes", kind: Kind::Gauge },
    Def { name: "vnxd_extent_count", help: "number of extents of the volume", kind: Kind::Gauge },
    Def { name: "vnxd_block_size_bytes", help: "block size of the volume in bytes", kind: Kind::Gauge },
    // the DNA simulator (SIMULATED)
    Def { name: "vnxd_synthesis_runs_total", help: "simulated synthesis runs", kind: Kind::Counter },
    Def { name: "vnxd_synthesis_failures_total", help: "simulated synthesis runs that failed", kind: Kind::Counter },
    Def { name: "vnxd_sequencing_runs_total", help: "simulated sequencing runs", kind: Kind::Counter },
    Def { name: "vnxd_sequencing_failures_total", help: "simulated sequencing runs that failed", kind: Kind::Counter },
    Def { name: "vnxd_decode_failures_total", help: "simulated decodings that failed", kind: Kind::Counter },
    Def { name: "vnxd_reads_unrecoverable_total", help: "simulated reads that no codeword could recover", kind: Kind::Counter },
    // the counters of the stats view that are levels, not counts
    Def { name: "vnxd_ram_in_use", help: "bytes of the memory manager in use", kind: Kind::Gauge },
    Def { name: "vnxd_ram_peak", help: "peak bytes of the memory manager in use", kind: Kind::Gauge },
    Def { name: "vnxd_nbd_max_inflight", help: "highest number of requests in flight on the NBD connections", kind: Kind::Gauge },
    Def { name: "vnxd_io_max_queue_depth", help: "highest depth of the IO queue", kind: Kind::Gauge },
    // the counters of the stats view
    Def { name: "vnxd_nbd_connections_total", help: "NBD connections accepted", kind: Kind::Counter },
    Def { name: "vnxd_nbd_requests_total", help: "NBD requests received", kind: Kind::Counter },
    Def { name: "vnxd_nbd_reads_total", help: "NBD read requests", kind: Kind::Counter },
    Def { name: "vnxd_nbd_writes_total", help: "NBD write requests", kind: Kind::Counter },
    Def { name: "vnxd_nbd_fua_writes_total", help: "NBD write requests that asked for the write to be flushed", kind: Kind::Counter },
    Def { name: "vnxd_nbd_flushes_total", help: "NBD flush requests", kind: Kind::Counter },
    Def { name: "vnxd_nbd_trims_total", help: "NBD trim requests", kind: Kind::Counter },
    Def { name: "vnxd_nbd_disconnects_total", help: "NBD connections closed", kind: Kind::Counter },
    Def { name: "vnxd_nbd_bytes_read_total", help: "bytes read over NBD", kind: Kind::Counter },
    Def { name: "vnxd_nbd_bytes_written_total", help: "bytes written over NBD", kind: Kind::Counter },
    Def { name: "vnxd_nbd_errors_total", help: "NBD requests that failed", kind: Kind::Counter },
    Def { name: "vnxd_nbd_eio_total", help: "NBD requests that failed with an I/O error", kind: Kind::Counter },
    Def { name: "vnxd_nbd_protocol_errors_total", help: "NBD requests that violated the protocol", kind: Kind::Counter },
    Def { name: "vnxd_nbd_cancelled_on_close_total", help: "NBD requests cancelled because the connection closed", kind: Kind::Counter },
    Def { name: "vnxd_io_submitted_total", help: "IO operations submitted to the backend", kind: Kind::Counter },
    Def { name: "vnxd_io_completed_total", help: "IO operations completed", kind: Kind::Counter },
    Def { name: "vnxd_io_failed_total", help: "IO operations that failed", kind: Kind::Counter },
    Def { name: "vnxd_io_cancelled_total", help: "IO operations cancelled", kind: Kind::Counter },
    Def { name: "vnxd_io_timed_out_total", help: "IO operations that timed out", kind: Kind::Counter },
    Def { name: "vnxd_volume_reads_total", help: "extent reads served by the volume", kind: Kind::Counter },
    Def { name: "vnxd_volume_commits_total", help: "extents committed by the volume", kind: Kind::Counter },
    Def { name: "vnxd_volume_integrity_failures_total", help: "integrity failures found by the volume", kind: Kind::Counter },
    Def { name: "vnxd_faults_injected_total", help: "faults injected by the fault gate", kind: Kind::Counter },
];

/// The definition of a metric, if the service knows it.
pub fn def(name: &str) -> Option<&'static Def> {
    DEFS.iter().find(|d| d.name == name)
}

/// The counters of the stats view that are levels, not growing counts.
const STAT_GAUGE_KEYS: &[&str] = &["ram_in_use", "ram_peak", "nbd_max_inflight", "io_max_queue_depth"];

/// The metric name of a counter of the stats view.
pub fn stats_metric_name(key: &str) -> String {
    if STAT_GAUGE_KEYS.contains(&key) {
        format!("vnxd_{key}")
    } else {
        format!("vnxd_{key}_total")
    }
}

/// The counters of the stats view: the inner `counters` object of the stats view, or the object itself when the
/// caller passes the counters directly.
fn counter_entries(counters: &Value) -> Vec<(&String, &Value)> {
    let obj = match counters {
        Value::Obj(o) => o,
        _ => return Vec::new(),
    };
    if let Some(Value::Obj(inner)) = obj.get("counters") {
        return inner.iter().collect();
    }
    obj.iter().collect()
}

/// The samples of the counters of the stats view.
///
/// Every key of the view becomes a metric: a counter `vnxd_<key>_total`, or a gauge `vnxd_<key>` for the few keys
/// that are levels ([`STAT_GAUGE_KEYS`]). A key that the service does not know yet still becomes a metric, with a
/// help text derived from the key, so a new counter is never invisible.
pub fn from_stats(counters: &Value) -> Vec<Sample> {
    let mut out = Vec::new();
    for (key, value) in counter_entries(counters) {
        let v = match value {
            Value::Int(i) => *i,
            _ => continue,
        };
        let name = stats_metric_name(key);
        let kind = if STAT_GAUGE_KEYS.contains(&key.as_str()) { Kind::Gauge } else { Kind::Counter };
        let help = match def(&name) {
            Some(d) => Cow::Borrowed(d.help),
            None => Cow::Owned(format!("vnxd statistic {key}")),
        };
        out.push(Sample { name: Cow::Owned(name), help, kind, labels: Vec::new(), value: v });
    }
    out
}

/// The samples of the health view: the state of the device, whether it is read-only, the damaged extents and the
/// requests in flight.
///
/// The state is a label, not a value: `vnxd_health_state` carries one series per state (`ok`, `degraded`, `failed`),
/// and the series of the current state is 1 while the others are 0.
pub fn from_health(health: &Value) -> Vec<Sample> {
    let obj = match health.as_obj() {
        Some(o) => o,
        None => return Vec::new(),
    };
    let state = obj.get("state").and_then(|v| v.as_str()).unwrap_or("");
    let detail = obj.get("detail").and_then(|v| v.as_obj());
    let read_only = detail.and_then(|d| d.get("read_only")).map_or(false, |v| matches!(v, Value::Bool(true)));
    let damaged = detail
        .and_then(|d| d.get("damaged_extents"))
        .map_or(0i64, |v| match v {
            Value::Arr(a) => a.len() as i64,
            _ => 0,
        });
    let inflight = detail.and_then(|d| d.get("inflight")).and_then(|v| v.as_int()).unwrap_or(0);
    let mut out = Vec::new();
    for s in ["ok", "degraded", "failed"] {
        out.push(
            Sample::new("vnxd_health_state", def("vnxd_health_state").map_or("", |d| d.help), Kind::Gauge, i64::from(state == s))
                .with_label("state", s),
        );
    }
    out.push(Sample::new("vnxd_read_only", def("vnxd_read_only").map_or("", |d| d.help), Kind::Gauge, i64::from(read_only)));
    out.push(Sample::new("vnxd_damaged_extents", def("vnxd_damaged_extents").map_or("", |d| d.help), Kind::Gauge, damaged));
    out.push(Sample::new("vnxd_inflight", def("vnxd_inflight").map_or("", |d| d.help), Kind::Gauge, inflight));
    out
}

/// The samples of the backend health: the state of the backend, its counters and its success score.
///
/// The score is a ratio: it is exported in parts per million, never as a float.
pub fn from_backend(h: &Health) -> Vec<Sample> {
    let help = |name: &str| def(name).map_or("", |d| d.help);
    let mut out = Vec::new();
    for s in ["healthy", "degraded", "unavailable"] {
        out.push(
            Sample::new("vnxd_backend_state", help("vnxd_backend_state"), Kind::Gauge, i64::from(h.state.as_str() == s))
                .with_label("state", s),
        );
    }
    out.push(Sample::new("vnxd_backend_failures_total", help("vnxd_backend_failures_total"), Kind::Counter, h.failures as i64));
    out.push(Sample::new("vnxd_backend_retries_total", help("vnxd_backend_retries_total"), Kind::Counter, h.retries as i64));
    out.push(Sample::new(
        "vnxd_backend_refused_read_only_total",
        help("vnxd_backend_refused_read_only_total"),
        Kind::Counter,
        h.refused_read_only as i64,
    ));
    out.push(Sample::new("vnxd_backend_transitions_total", help("vnxd_backend_transitions_total"), Kind::Counter, h.transitions as i64));
    out.push(Sample::new(
        "vnxd_backend_consecutive_failures",
        help("vnxd_backend_consecutive_failures"),
        Kind::Gauge,
        i64::from(h.consecutive_failures),
    ));
    out.push(Sample::new(
        "vnxd_backend_score_ppm",
        help("vnxd_backend_score_ppm"),
        Kind::Gauge,
        (h.score * 1_000_000.0).round() as i64,
    ));
    out
}

/// The samples of the cache: its counters, the extents it holds and the extents it holds dirty.
///
/// `cached` and `dirty` are the numbers of extents the caller holds in the cache and the number of them that are not
/// written back yet.
pub fn from_cache(s: &CacheStats, cached: usize, dirty: usize) -> Vec<Sample> {
    let help = |name: &str| def(name).map_or("", |d| d.help);
    let l = |a: &AtomicU64| a.load(Ordering::Relaxed) as i64;
    vec![
        Sample::new("vnxd_cache_hits_total", help("vnxd_cache_hits_total"), Kind::Counter, l(&s.hits)),
        Sample::new("vnxd_cache_misses_total", help("vnxd_cache_misses_total"), Kind::Counter, l(&s.misses)),
        Sample::new("vnxd_cache_evictions_total", help("vnxd_cache_evictions_total"), Kind::Counter, l(&s.evictions)),
        Sample::new("vnxd_cache_writebacks_total", help("vnxd_cache_writebacks_total"), Kind::Counter, l(&s.writebacks)),
        Sample::new("vnxd_cache_writeback_failures_total", help("vnxd_cache_writeback_failures_total"), Kind::Counter, l(&s.writeback_failures)),
        Sample::new("vnxd_cache_checksum_failures_total", help("vnxd_cache_checksum_failures_total"), Kind::Counter, l(&s.checksum_failures)),
        Sample::new("vnxd_cache_bypasses_total", help("vnxd_cache_bypasses_total"), Kind::Counter, l(&s.bypasses)),
        Sample::new("vnxd_cache_lost_total", help("vnxd_cache_lost_total"), Kind::Counter, l(&s.lost)),
        Sample::new("vnxd_cache_prefetched_total", help("vnxd_cache_prefetched_total"), Kind::Counter, l(&s.prefetched)),
        Sample::new("vnxd_cache_prefetch_hits_total", help("vnxd_cache_prefetch_hits_total"), Kind::Counter, l(&s.prefetch_hits)),
        Sample::new("vnxd_cache_extents", help("vnxd_cache_extents"), Kind::Gauge, cached as i64),
        Sample::new("vnxd_cache_dirty_extents", help("vnxd_cache_dirty_extents"), Kind::Gauge, dirty as i64),
    ]
}

/// The samples of a scrub: the extents it checked, the extents that failed, the time it ran and the failures by
/// error code.
///
/// `unix_ms` is the time of the run, in milliseconds since the unix epoch, given by the caller: the service never
/// reads the wall clock itself.
pub fn from_scrub(r: &ScrubReport, unix_ms: i64) -> Vec<Sample> {
    let help = |name: &str| def(name).map_or("", |d| d.help);
    let mut out = vec![
        Sample::new("vnxd_scrub_checked_extents", help("vnxd_scrub_checked_extents"), Kind::Gauge, r.checked as i64),
        Sample::new("vnxd_scrub_failed_extents", help("vnxd_scrub_failed_extents"), Kind::Gauge, r.failed.len() as i64),
        Sample::new("vnxd_scrub_last_run_unix_ms", help("vnxd_scrub_last_run_unix_ms"), Kind::Gauge, unix_ms),
    ];
    // one counter per error code: how many extents failed with it
    let mut codes: BTreeMap<&'static str, i64> = BTreeMap::new();
    for (_, e) in &r.failed {
        *codes.entry(e.code.as_str()).or_insert(0) += 1;
    }
    for (code, n) in codes {
        out.push(Sample::new("vnxd_scrub_failure", help("vnxd_scrub_failure"), Kind::Counter, n).with_label("code", code));
    }
    out
}

/// The samples of the capacity view.
pub fn from_capacity(cap: &Value) -> Vec<Sample> {
    let help = |name: &str| def(name).map_or("", |d| d.help);
    let get = |key: &str| cap.as_obj().and_then(|o| o.get(key)).and_then(|v| v.as_int()).unwrap_or(0);
    vec![
        Sample::new("vnxd_capacity_bytes", help("vnxd_capacity_bytes"), Kind::Gauge, get("capacity_bytes")),
        Sample::new("vnxd_extent_count", help("vnxd_extent_count"), Kind::Gauge, get("extent_count")),
        Sample::new("vnxd_block_size_bytes", help("vnxd_block_size_bytes"), Kind::Gauge, get("block_size")),
    ]
}

/// The samples of the DNA simulator (SIMULATED: the counters describe the software model, not synthesised DNA).
pub fn from_dnasim(s: &DnaSimStats) -> Vec<Sample> {
    let help = |name: &str| def(name).map_or("", |d| d.help);
    let l = |a: &AtomicU64| a.load(Ordering::Relaxed) as i64;
    vec![
        Sample::new("vnxd_synthesis_runs_total", help("vnxd_synthesis_runs_total"), Kind::Counter, l(&s.synthesis_runs)),
        Sample::new("vnxd_synthesis_failures_total", help("vnxd_synthesis_failures_total"), Kind::Counter, l(&s.synthesis_failures)),
        Sample::new("vnxd_sequencing_runs_total", help("vnxd_sequencing_runs_total"), Kind::Counter, l(&s.sequencing_runs)),
        Sample::new("vnxd_sequencing_failures_total", help("vnxd_sequencing_failures_total"), Kind::Counter, l(&s.sequencing_failures)),
        Sample::new("vnxd_decode_failures_total", help("vnxd_decode_failures_total"), Kind::Counter, l(&s.decode_failures)),
        Sample::new("vnxd_reads_unrecoverable_total", help("vnxd_reads_unrecoverable_total"), Kind::Counter, l(&s.reads_unrecoverable)),
    ]
}

/// A row of the exposition: a sample with its labels in a fixed order.
struct Row {
    name: String,
    help: String,
    kind: Kind,
    labels: Vec<(&'static str, String)>,
    value: i64,
}

impl From<&Sample> for Row {
    fn from(s: &Sample) -> Row {
        Row {
            name: s.name.to_string(),
            help: s.help.to_string(),
            kind: s.kind,
            labels: s.labels.clone(),
            value: s.value,
        }
    }
}

/// Two readings of the same series: the counter keeps the larger value (a counter only grows), a gauge keeps the
/// smaller one (a level that was read twice is not added up).
fn merge(kind: Kind, a: i64, b: i64) -> i64 {
    match kind {
        Kind::Counter => a.max(b),
        Kind::Gauge => a.min(b),
    }
}

/// The label value of a series, escaped (`\\`, `\"`, `\n`).
pub fn escape_label_value(v: &str) -> String {
    let mut out = String::with_capacity(v.len());
    for c in v.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            c => out.push(c),
        }
    }
    out
}

/// The help text of a metric, escaped (`\\`, `\n`).
pub fn escape_help(v: &str) -> String {
    let mut out = String::with_capacity(v.len());
    for c in v.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            c => out.push(c),
        }
    }
    out
}

/// Whether `name` is a valid metric name (`[a-zA-Z_:][a-zA-Z0-9_:]*`).
pub fn is_valid_name(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' || c == ':' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == ':')
}

/// The names of the metrics of the samples, sorted and without duplicates.
pub fn names(samples: &[Sample]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for s in samples {
        if !out.iter().any(|n| n == s.name.as_ref()) {
            out.push(s.name.to_string());
        }
    }
    out.sort();
    out
}

/// Render the samples in the Prometheus text exposition format (version 0.0.4).
///
/// The output holds one `# HELP` line and one `# TYPE` line per metric name, then the series of that metric, sorted
/// by the label values. The same samples in any order render to the same bytes.
pub fn render(samples: &[Sample]) -> String {
    let mut rows: Vec<Row> = samples.iter().map(Row::from).collect();
    for r in &mut rows {
        r.labels.sort_by(|a, b| a.0.cmp(b.0));
    }
    rows.sort_by(|a, b| {
        a.name.cmp(&b.name).then_with(|| a.labels.cmp(&b.labels)).then_with(|| a.value.cmp(&b.value))
    });
    let mut out = String::new();
    let mut i = 0;
    while i < rows.len() {
        let start = i;
        while i < rows.len() && rows[i].name == rows[start].name {
            i += 1;
        }
        let group = &rows[start..i];
        out.push_str("# HELP ");
        out.push_str(&group[0].name);
        out.push(' ');
        out.push_str(&escape_help(&group[0].help));
        out.push('\n');
        out.push_str("# TYPE ");
        out.push_str(&group[0].name);
        out.push(' ');
        out.push_str(group[0].kind.as_str());
        out.push('\n');
        let mut j = 0;
        while j < group.len() {
            let key = &group[j].labels;
            let mut value = group[j].value;
            let mut k = j + 1;
            while k < group.len() && group[k].labels == *key {
                value = merge(group[j].kind, value, group[k].value);
                k += 1;
            }
            out.push_str(&group[j].name);
            if !key.is_empty() {
                out.push('{');
                for (n, (lk, lv)) in key.iter().enumerate() {
                    if n > 0 {
                        out.push(',');
                    }
                    out.push_str(lk);
                    out.push_str("=\"");
                    out.push_str(&escape_label_value(lv));
                    out.push('"');
                }
                out.push('}');
            }
            out.push(' ');
            out.push_str(&value.to_string());
            out.push('\n');
            j = k;
        }
    }
    out
}
