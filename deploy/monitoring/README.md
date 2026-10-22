# Monitoring the VNX storage service (`vnxd`)

AP-6: the service exposes its own metrics, and the alert rules in `vnxd-alerts.yml` turn them into alerts.

## What is exposed

The `vnxd` service exports the state of the device, of the backend, of the cache, of the last scrub and of the
volume capacity in the **Prometheus text exposition format, version 0.0.4** (one `# HELP` line and one `# TYPE`
line per metric name, then the series). The exposition is built by
`src/vnxdna/native/rust/vnxd/src/metrics.rs` from the structures the service already holds: the counters of the
stats view, the health view, the health of the backend, the counters of the cache, the report of the last scrub and
the capacity of the volume. The service keeps no metric state of its own, so nothing is counted twice and nothing
is lost between two scrapes.

Every value is an integer: a ratio is a count of parts per million, a time is a count of milliseconds.

## How it is scraped

**There is no HTTP server.** The service never opens a network port; the metrics travel over the management socket
it already has.

* the socket is a Unix socket, created by the service with mode `0660` (see
  `src/vnxdna/native/rust/vnxd/src/nbdctl.rs`); in the packaged deployment it is `/run/vnxd/mgmt.sock`
  (`RuntimeDirectory=vnxd` of `deploy/systemd/vnxd.service`, the option `--mgmt` of the service);
* the protocol is `vnx.mgmt/1`: one canonical-JSON message per line. The metrics are the `metrics` operation, which
  takes no argument and answers with `{"text": "<the whole exposition>"}`;
* the operation needs an authenticated session: any role of the policy file (`reader` or `admin`) may read the
  metrics, exactly like `health` and `stats` (see `src/vnxdna/native/rust/vnxd/src/authz.rs`);
* the exposition is a text of at most 1 MiB (`Ty::Text` of the protocol, `src/vnxdna/native/rust/vnxd/src/proto.rs`),
  because one line of the text format per series is longer than the 1024 bytes a `Ty::Str` field accepts;
* the device is an **offline** device: it is reached over the socket of the machine it runs on, so the scrape is
  local to that machine. The monitoring host reads the socket and exposes the text to Prometheus, either with a
  small textfile exporter or with a scrape configuration of its own.

The session is the ordinary one of the management socket:

```
hello  {"client":"prometheus","versions":[1]}
authenticate {"token":"<the token of the reader role>"}
metrics  {}
```

The answer of `metrics` holds the exposition in the `text` field. It is the exposition a Prometheus scrape of the
exporter would return, byte for byte.

## How the rules are loaded

`vnxd-alerts.yml` is a **Prometheus rule file**: a group of alerting rules, in the format
`promtool check rules` accepts.

* with Prometheus itself: copy the file into the configuration directory of Prometheus and list it in
  `rule_files` of `prometheus.yml`;
* with the tool of the monitoring host: `promtool check rules vnxd-alerts.yml` checks the file before it is
  loaded, and `promtool test rules` runs the rules against a series of test input.

The file holds one group (`vnxd`) with the rules below. Every rule carries a `severity` label (`critical`,
`warning` or `info`) and annotations for the operator.

| Alert | Fires when | Severity |
|---|---|---|
| `VnxdHealthDegraded` | the device is `degraded` for 5 minutes | warning |
| `VnxdHealthFailed` | the device is `failed` | critical |
| `VnxdReadOnly` | the device refuses every write | warning |
| `VnxdDamagedExtents` | extents of the map do not match their stored bytes | critical |
| `VnxdBackendUnavailable` | the backend of the volume does not answer | critical |
| `VnxdBackendDegraded` | the backend is `degraded` for 5 minutes | warning |
| `VnxdScrubFailures` | the last scrub found failed extents | critical |
| `VnxdScrubStale` | the last scrub is older than 7 days | warning |
| `VnxdCacheWritebackFailures` | a dirty extent could not be written back | warning |
| `VnxdCacheHitRateLow` | less than half of the reads hit the cache for 30 minutes | info |
| `VnxdIntegrityFailures` | the volume found a digest mismatch in the last 10 minutes | critical |
| `VnxdIoTimeouts` | a request did not complete before its deadline | warning |
| `VnxdDnaSimDecodeFailures` | a simulated decoding failed | warning |

The DNA simulator counters are **SIMULATED** measurements of the software model of the channel, never of
synthesised DNA. The service writes to a real backend, not to the simulator, so these series are absent until a
simulator is attached.

## The metric names

Every name starts with `vnxd_`. The names below are the ones the rules use; the full list is the `DEFS` table of
`src/vnxdna/native/rust/vnxd/src/metrics.rs`, and the test `tests/v15/test_ap6_alert_rules.py` checks that every
name in the rules is one the service can emit.

| Name | Kind | What it holds |
|---|---|---|
| `vnxd_health_state{state="ok"\|"degraded"\|"failed"}` | gauge | 1 for the state of the device, 0 for the others |
| `vnxd_backend_state{state="healthy"\|"degraded"\|"unavailable"}` | gauge | 1 for the state of the backend, 0 for the others |
| `vnxd_read_only` | gauge | 1 when the device refuses writes |
| `vnxd_damaged_extents` | gauge | extents of the map that do not match their stored bytes |
| `vnxd_scrub_failed_extents` | gauge | extents that failed the last scrub |
| `vnxd_scrub_last_run_unix_ms` | gauge | the time of the last scrub in milliseconds since the unix epoch (0 when no scrub ran) |
| `vnxd_cache_writeback_failures_total` | counter | dirty extents that could not be written back |
| `vnxd_cache_hits_total` | counter | extents served from the cache |
| `vnxd_cache_misses_total` | counter | extents read from the backend because the cache did not hold them |
| `vnxd_volume_integrity_failures_total` | counter | digest mismatches the volume found |
| `vnxd_io_timed_out_total` | counter | I/O operations that did not complete before their deadline |
| `vnxd_decode_failures_total` | counter | simulated decodings that failed (SIMULATED) |

## Notes for the operator

* The `for` of a rule is the time the condition must hold before the alert fires: a device that is degraded for a
  moment is not an incident, a device that is `failed` is.
* `vnxd_scrub_last_run_unix_ms` is `0` until a scrub runs. The staleness rule fires on a volume that was never
  scrubbed, which is the honest reading: the data of that volume was never checked.
* The counters only grow. A rule that counts an event uses `increase()` over a window, so a restart of the service
  (which resets nothing, the counters live in the process) does not make the rule fire again.
* The rules are the same for every device: the exporter adds the labels of the machine it runs on.
