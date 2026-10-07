# VNX-Secure — Benchmarks (SIMULATED)

All numbers come from `experiments/vnx-secure/results/simulation.json` and `fuzz-campaign.json`, at commit 8c3bd94.
Reproduce them with `vnx security simulate -o simulation.json`.

**These are synthetic attacks against a local, in-memory control plane on one host. They are not real-world security
guarantees.** The host is 4 cores / 8 threads, Python 3.12, load1 3.28. The V9 decoder evaluation was running on 3
workers at the same time, so wall-clock times include that contention.

## Method

Each scenario runs as follows:

- It builds a fresh control plane with a deterministic clock.
- It uses a real AES-256-GCM VNX4 archive (16,621 B, 4 KiB chunks) and a 4-file component directory as the integrity
  baseline.
- The attack is made through the public API (`login`, `authorize`, `SecureArchive`).
- Detection, scoring and containment run synchronously inside the triggering request.

Latencies are therefore reported in three ways:

- **events to detect:** attack events up to and including the one that produced the first finding;
- **simulated seconds:** clock time from the first attack event to that event;
- **ms:** wall time of the triggering call, which includes detection, containment and audit writes.

Each scenario checks six things:

- detection;
- containment;
- revocation (where the playbook revokes);
- the evidence bundle (its hash re-verified);
- `recover()` → VERIFIED;
- final integrity VERIFIED and audit chain VERIFIED.

## Scenarios

| # | Scenario | First detector | Events to detect | Sim. s | Detect + contain (ms) | Recovery (ms) | Pass |
|---|---|---|---|---|---|---|---|
| 1 | Credential brute force | auth_failures | 5 | 4 | 6.7 | 10.9 | ✓ |
| 2 | Unauthorized archive access | authz_failures | 5 | 4 | 4.5 | 15.3 | ✓ |
| 3 | Privilege escalation | privilege_escalation | 1 | 0 | 4.0 | 10.8 | ✓ |
| 4 | Malformed input | malformed_input | 1 | 0 | 4.3 | 25.7 | ✓ |
| 5 | Replay | replay | 1 | 0 | 4.3 | 11.6 | ✓ |
| 6 | Token misuse (other session) | token_misuse | 1 | 0 | 4.1 | 9.8 | ✓ |
| 7 | Integrity tampering (component) | integrity_monitor | 1 | 0 | 6.1 | 11.4 | ✓ |
| 8 | Abnormal request rate | request_rate | 121 | 24 | 8.4 | 52.0 | ✓ |
| 9 | Malicious archive metadata | malformed_input | 1 | 0 | 4.0 | 18.9 | ✓ |
| 10 | Compromised session (deception hit) | deception | 1 | 0 | 6.0 | 12.9 | ✓ |

Summary:

| Measure | Result |
|---|---|
| Scenarios passing | 10 / 10 |
| Missed-attack rate on these scenarios | 0 / 10 |
| Median detect = contain latency | 4.4 ms (synchronous) |
| Median recovery time | 12.2 ms |

Credential revocation is part of the same call: a revoked token is refused on its next use. Scenarios 7 and 8 do not
revoke credentials, by design: an integrity failure isolates the system, and a rate anomaly rate-limits the session.

Scenario-specific checks:

- **Scenario 7:** a recovery attempted while the component is still modified returns FAILED and leaves the system
  ISOLATED. Recovery succeeds only after the file is restored.
- **Scenario 9:** the tampered archive stays locked after recovery, because it does not verify.

## False positives

The benign workload (`simulation.json` → `benign`) runs 30 simulated minutes of normal use:

- 3 users;
- 270 requests: list, verify, read and chunk reads, plus admin calls;
- one mistyped password;
- token renewal every 14 minutes.

The result was 0 findings and 0 denials, a false-positive rate of 0 / 270. This is one synthetic workload, not a
measured rate on real traffic.

## Overhead (random access, median of 30)

| Operation | Direct (ms) | Through the gate (ms) | Overhead (ms) |
|---|---|---|---|
| `read_file` (table.csv) | 3.70 | 4.68 | 0.98 |
| `read_chunk` (with Merkle proof) | 2.10 | 3.13 | 1.03 |

Other costs:

- **Audit storage:** about 419 bytes per request, in an in-memory chain. The disk cost is the same per line.
- **Memory:** peak Python heap over the whole simulation was 2.7 MB (tracemalloc).
- **Throughput:** the gate serialises decisions (one lock), so its decision rate is bounded by about 1 / 1.0 ms per
  process on this host for small requests. This is not measured under contention.

## Fuzzing

`fuzz-campaign.json` (hypothesis, `VNX_SECURE_FUZZ_EXAMPLES=5000`, 90 s, statistics enabled):

| Target | Passing examples |
|---|---|
| Policy parser | 5,000 |
| Token parser | 5,000 |
| Token bit-flips | 5,000 |
| Audit-line parser | 5,000 |
| Event-stream processing | 5,000 |
| Archive mutations through the gate | 100 |

That is 25,100 passing examples and 0 failing. During development the fuzzer found one bug: the token parser let
`binascii.Error` escape on impossible base64 lengths. It was fixed in 3549f76.

## Sanitizers

VNX-Secure adds no native code, so ASan/UBSan do not apply to it. The existing VNX-DNA native kernels are unchanged.
