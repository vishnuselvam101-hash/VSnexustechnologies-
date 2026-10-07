# VNX-Secure — Limitations

**VNX-Secure is not claimed to be mathematically or absolutely unhackable, "100% secure" or impossible to compromise.**
It is a first defensive layer, tested against synthetic attacks on one host. It has not been reviewed externally, has
not been tested against real attackers, and has not been measured on real traffic.

## What it does not protect

1. **Bypass of the gate.** The gate enforces policy only for access that goes through it. Anyone with file-system
   access to an archive and its key can read the archive directly. Archive confidentiality rests on AES-256-GCM and on
   key custody, not on VNX-Secure.
2. **Host compromise.** An attacker running as the same user or as root can do all of the following:
   - read `secure.key`;
   - forge tokens and MACs;
   - rewrite the audit log, the anchor and the state consistently;
   - modify the checking code.

   The master key lives on the same disk as the data it protects. An off-host anchor (a remote append-only log or a
   hardware key) is future work.
3. **Dependencies and supply chain.** The integrity baseline covers the `vnxdna` package files only. Dependencies are
   not hash-pinned, and releases are not signed (signing is PLANNED).
4. **Network.** VNX-DNA has no network service yet. Network isolation is an interface with a recording stub only.
5. **Sandbox.** `sandbox=True` bounds memory, CPU and wall time, and survives crashes. It is not a code-execution
   boundary: the child has the same user, file system and network as the parent. It also uses `fork` from a possibly
   multi-threaded process, which Python warns about.
6. **Process scope.** The replay cache, rate counters and detector windows are in-process.
   - After a restart, replay protection rests on the ±120 s timestamp check alone.
   - Several processes sharing one home are not coordinated: the last state write wins.
7. **Detection.** The rules are fixed thresholds, chosen for clarity, not tuned on real data.
   - Slow attacks below the thresholds are not detected: guessing under 5 attempts per 300 s, enumeration under 50
     resources per 300 s, or exfiltration within policy.
   - Abuse of a stolen token inside its own session looks like the user.
8. **Denial of service by design.** Failed logins lock the named principal's login for 900 s. An attacker who knows a
   user name can therefore lock that user out. CRITICAL findings move the whole system to DEGRADED, where writes are
   refused until an operator recovers.
9. **Tail truncation of the audit log** is detected only against the anchor. The anchor is stored on the same host.
10. **Physical security.** There is no protection against physical access to disks or machines, and the VNX-Secure home
    is not encrypted at rest. For DNA media, custody of the molecules is outside the software.
11. **VNX-RAM and VNX-Q** are ARCHITECTURAL only. No hardware exists, and no quantum mechanism is used or claimed.
12. **Post-quantum cryptography** is PLANNED, not implemented.

## Evidence classes

| Claim | Class |
|---|---|
| Mechanisms in `src/vnxdna/secure` | IMPLEMENTED |
| Behaviour covered by `tests/secure` (unit, integration, fuzz, concurrency, deterministic replay) | TESTED |
| Detection, containment and recovery latencies; false-positive and missed-attack rates | SIMULATED |
| Network isolation, VNX-RAM, VNX-Q, PQC migration | ARCHITECTURAL / PLANNED |
| Effectiveness against real attackers or real traffic | NOT VALIDATED |
