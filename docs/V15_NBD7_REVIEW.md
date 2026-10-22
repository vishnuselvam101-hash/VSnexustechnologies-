# V15 NBD kernel-7 attach fix — independent review

Branch `work/v15-nbd7`: three commits on top of `bd311c9` (`f5a29df` the fix, `c3bda1e` the docs, `ffd490e` the extra
tests and the stop re-check). Files touched: `src/vnxdna/native/rust/vnxd/src/nbdctl.rs`,
`src/vnxdna/native/rust/vnxd/src/nbdsys.rs`, `tests/v14/nbd_support.py`, `tests/v14/test_device_nbd.py`,
`docs/V14_DEVICE_INTERFACE.md`, `experiments/v15/nbd7/REVIEW.md`.

This is a second, independent review: it does not rely on `experiments/v15/nbd7/REVIEW.md` and reaches its own
conclusions from the diff, the touched sources and the running kernel.

## How the findings below were checked

The review ran on the V15 host named in the kernel matrix of `docs/V14_DEVICE_INTERFACE.md:111`
(kernel 7.0.0-38-generic), as root, with the `nbd` module loaded — the exact kernel the fix targets. On that host:

* the pre-fix `vnxd` was rebuilt from the branch base `bd311c9` and the branch's own harness check was run against
  it: in every attach cycle of that run the pre-fix binary announced `attached` while `/sys/block/nbdN/size` still
  reported a capacity of zero, and the branch's new test failed. The same test passes with the branch build.
* the sysfs `size` attribute was read next to the pid attribute while the device came up, and the pid attribute is
  published before the capacity on this kernel — the ordering the fix relies on is observed, not assumed.
* the readiness timeout was forced by building a scratch copy of the crate in which the readiness size is
  deliberately unsatisfiable; the resulting run exercised the whole failure path (the `attach_not_ready` event, the
  detach, the exit status and the state of the device afterwards).
* the whole of `tests/v14` and the crate's own `cargo test` suite are green on this host with the branch build.

Where a finding states a number, the number comes from a committed file in the tree and the file is named; the
observations above are described in words because they belong to this review's own runs and are not recorded in the
tree.

---

## 1. Is the readiness test correct?

`src/vnxdna/native/rust/vnxd/src/nbdsys.rs:128-143`

**VERDICT: correct — no must-fix, no should-fix.**

* The unit is right. `/sys/block/nbdN/size` is the block layer's generic `size` attribute, counted in 512-byte
  sectors whatever the device's logical block size is (a volume's default block size is 4096,
  `nbdctl.rs:149`). The rule divides the byte capacity by 512 (`nbdsys.rs:142`), and on the V15 host the attribute
  of a 64 MiB volume (`tests/v14/test_device_nbd.py:536`) read exactly the sector count the `attached` event
  announced as bytes, so the conversion is unit-correct. This attribute is part of the block layer's
  stable sysfs ABI for every block device, not an nbd-specific one, so the conversion does not depend on the kernel
  version.
* The condition cannot be satisfied too early. The kernel publishes the serving pid before it applies the capacity,
  so the capacity is the last thing to appear: a device that reports the final capacity is up. A *stale* capacity
  left by another process cannot satisfy it either, because the claim refuses a device that already has one
  (`nbdsys.rs:194-197`) and the claim itself (`NBD_SET_SOCK`) is refused with `EBUSY` when another task owns the
  device (`nbdsys.rs:224-230`).
* The condition can, in principle, never be satisfied — if the kernel applied a capacity other than the negotiated
  one. That is the correct direction to fail: a wrongly sized device is refused instead of handed out. The values in
  play are consistent today: the server advertises the same block size it was given
  (`src/vnxdna/native/rust/vnxd/src/nbd.rs:156-160`), and the configure step requires the capacity to be a whole
  multiple of the block size (`nbdsys.rs:237-239`), so the kernel's rounding of the capacity to the block size cannot
  change it.
* The exactness of the comparison is safe for the same reason, and the extra `size_bytes % 512 == 0` guard
  (`nbdsys.rs:142`) only removes sizes the driver would have refused anyway.
* The rule is extracted as a pure function (`ready_state`, `nbdsys.rs:141`) so it can be tested without a kernel,
  and the two new unit tests (`nbdsys.rs:295-325`) cover the kernel-7 window (pid published, capacity still zero),
  a missing or foreign pid, one sector short or long, and a size that is not a whole number of sectors.

**VERDICT (nice-to-have): the readiness could also compare the kernel's block size.** `queue/logical_block_size`
would catch a device that came up with the right capacity but the wrong block size, which the current rule would
accept. It is out of scope for this bug and is not a merge blocker.

## 2. The 30 s timeout

`src/vnxdna/native/rust/vnxd/src/nbdctl.rs:560` (the literal), `:579-591` (the timeout branch)

**VERDICT: should-fix — the timeout is hard-coded where the same command already has a timeout option, and the
surfaced error hides the reason.**

* It is hard-coded: `Duration::from_secs(30)` in the readiness loop (`nbdctl.rs:560`). The same command already
  exposes `--kernel-timeout-s` (default 120, `nbdctl.rs:147` and `:195`) and `--request-timeout-s`
  (`nbdctl.rs:194`), so a hard-coded wait is inconsistent with the rest of the CLI, and the number is not
  configurable for an operator whose storage is slow to open.
* The value is well chosen against its neighbours: it is shorter than the kernel timeout (120 s, `nbdctl.rs:147`)
  and shorter than the harness's own readiness timeout of 60 s (`tests/v14/nbd_support.py:232`), so the failure is
  detected before the caller gives up. It is also longer than the observed kernel window by a wide margin.
* The exit code is consistent with the rest of the code: `main` returns 1 on any error (`nbdctl.rs:772-775`), and the
  documentation states that the exit status is 1 on any error (`docs/V14_DEVICE_INTERFACE.md:50-52`). The forced
  failure run confirmed the exit status and the `attach_not_ready` event described in the documentation
  (`docs/V14_DEVICE_INTERFACE.md:49`).
* The *message* is the weak part. The failure is surfaced as `the device stopped without a requested detach (the
  device did not come up with the configured size)` (`nbdctl.rs:612-613`), because the readiness stop reason is not a
  signal or a management request (`nbdctl.rs:604`) and therefore falls into the generic "nobody asked for this"
  branch. The reason is present, but only in parentheses, and the leading phrase is misleading — a stop *was*
  requested, just not by a signal.

**VERDICT (should-fix): make the readiness timeout an option** (for example `--attach-timeout-s`, validated the
way `--kernel-timeout-s` is), so that an operator can tune it and a test can force the failure path — see item 6.

**VERDICT (should-fix): give the readiness failure its own error text** instead of the generic "stopped without a
requested detach" phrase, so that the exit-1 message names the actual cause.

**VERDICT (should-fix): the `pid` field of the `attach_not_ready` event is silently discarded.** The event is
built with a `pid` field holding the pid the kernel reports for the device (`nbdctl.rs:585`), but the `log` helper
inserts its own `pid` field — the process id — after collecting the caller's fields (`nbdctl.rs:41`), so the
caller's value is overwritten. The forced failure run showed the event carrying the process id, not the device pid.
The field is exactly the one an operator needs when the device never came up (did the kernel publish a pid at all,
and which one), and the `sectors` field alone does not answer it. Rename it (for example `device_pid`) or make the
`log` helper not overwrite a field the caller supplied.

## 3. Race conditions between the pid, the size and the handshake

`src/vnxdna/native/rust/vnxd/src/nbdctl.rs:556-578`

**VERDICT: no must-fix, no should-fix — the races that matter are handled; the remaining ones are benign.**

* The window the fix exists for is real and is now closed: the readiness check is a conjunction, and the capacity is
  the later of the two publications.
* The stop re-check before announcing (`nbdctl.rs:562-565`, added in `ffd490e`) closes the important race: a stop
  requested by the server thread, by a signal or by the management socket between the readiness read and the
  announcement no longer produces an `attached` event for a device that is being torn down. This is the right place
  for the check, and the branch author's own note reaches the same conclusion
  (`experiments/v15/nbd7/REVIEW.md`, section 4).
* The timeout branch is guarded by the `running` flag (`nbdctl.rs:579`), which is cleared only after `NBD_DO_IT`
  returns (`nbdctl.rs:602`); a device that stopped for another reason therefore never logs a spurious
  `attach_not_ready`. Correct.
* The two sysfs reads are not atomic, but the kernel only ever moves the capacity from zero to its final value, so
  the worst outcome of reading the pid just before the capacity appeared is one more poll tick of waiting, never a
  false "ready". The same holds for a size that is momentarily stale in the other direction: a false negative simply
  costs another tick.
* The re-check and the announcement are not atomic with the device staying up, so a teardown that starts between them
  can still produce an `attached` event for a device that is going away. The consequence is a failed `mkfs` in the
  caller, not a corrupt device, and the window is the length of one `announce` call.

**VERDICT (nice-to-have):** none of these need a change before the merge; they are noted so that the reasoning is
not lost.

## 4. The error path and stale devices

`src/vnxdna/native/rust/vnxd/src/nbdctl.rs:579-591`, `:601-615`, `:410-445`, `:545-552`;
`src/vnxdna/native/rust/vnxd/src/nbdsys.rs:152-162`, `:259-264`

**VERDICT: should-fix — the recovery is correct, but the path is not covered by any test.**

* The failed attach does not leave a device that a later attach cannot recover from. The forced failure run (the
  readiness condition made unsatisfiable in a scratch build) ended with the `attach_not_ready` event, the normal
  detach sequence (`detaching`, `device_stopped`, `detached`), and the device free afterwards: no serving pid in
  sysfs and a capacity of zero, which is exactly what `free_devices` requires to hand the device out again
  (`nbdsys.rs:152-162`). The mechanism is the same one used by every other exit path: the timeout requests a stop
  (`nbdctl.rs:590`), the controller detaches (`nbdctl.rs:594-600`), `NBD_DO_IT` returns, the claim is released
  (`nbdctl.rs:606`), and `NBD_CLEAR_SOCK` resets the socket and the capacity (`nbdsys.rs:259-264`). The release is
  idempotent (`nbdctl.rs:418-426`) and the drop guard runs on every exit path, including a panic
  (`nbdctl.rs:545-552`).
* The failure is reported as an error and the exit status is 1 (`nbdctl.rs:772-775`), consistent with the
  documentation (`docs/V14_DEVICE_INTERFACE.md:50-52`) and with the rest of the error handling in `attach`.
* The path is untested. No test in the branch drives a readiness failure, so the event, the exit status and the
  state of the device afterwards are not protected against regressions. Making the timeout configurable (item 2)
  is what makes such a test possible.

**VERDICT (should-fix): add a test for the readiness failure** that forces the timeout, checks the
`attach_not_ready` event, the exit status and that the device is free again afterwards.

**VERDICT (nice-to-have):** the detach flushes the block device even when the device never reached the announced
state (`nbdctl.rs:437`); on a device that never came up with a capacity that flush has nothing to do and can only
add a second, misleading failure string. Skipping it when the device was never announced would keep the failure
report to the real cause.

## 5. Older kernels

`src/vnxdna/native/rust/vnxd/src/nbdsys.rs:136-143`; `docs/V14_DEVICE_INTERFACE.md:99-111`

**VERDICT: correct — the fix cannot break a kernel on which the size was set before the pid.**

* The rule is a conjunction of two conditions. On a kernel that publishes the capacity first, the capacity condition
  is already satisfied when the pid appears, so the readiness check succeeds at the first poll after the pid appears;
  the only cost is that one poll tick, not a behavioural change.
* The one behaviour that does change on such a kernel is the failure direction: a device that never receives its
  capacity is now refused by the attach instead of being announced and failing later in the caller. That is
  stricter, not breaking, and it is the same behaviour on every kernel.
* The kernel matrix is honest about the coverage: the row for 6.8.0-146-generic says the re-test with the fix is
  **pending** because the host is retired (`docs/V14_DEVICE_INTERFACE.md:110`). No 6.8 host is available in this lab,
  so this review could not confirm that row either; the code path itself is kernel-independent.

**VERDICT (nice-to-have):** the matrix should say that the 6.8 row is unverified with the fix, not only that the
re-test is pending, so a reader cannot mistake it for a confirmed result.

## 6. Test quality

`tests/v14/test_device_nbd.py:529-548`; `tests/v14/nbd_support.py:102-113`, `:116-126`, `:256-264`

**VERDICT: should-fix — the kernel-7 condition is genuinely exercised and the test does fail without the fix, but
the failure path is untested and the new test's own assertions never run.**

* There is a test that fails without the fix, and the kernel-7 condition is exercised rather than assumed. The
  pre-fix build of the branch base announced `attached` with a capacity of zero in every attach cycle of this
  review's run, the branch's new test failed against it, and the same test passes with the branch build. The
  condition is exercised on the real kernel: the test reads `/sys/block/nbdN/size` right after the event and
  compares it with the announced capacity (`tests/v14/test_device_nbd.py:542-543`), and the harness itself refuses
  a size-0 device for every destructive tool call (`tests/v14/nbd_support.py:121-122`).
* The harness change is the stronger of the two: `Service` now asserts, on the `attached` event, that the kernel's
  size equals the announced capacity (`tests/v14/nbd_support.py:256-264`), so every kernel test in the repository
  fails if the readiness rule is broken — not only the new one. That is a strengthening, not a weakening: the
  branch's diff adds lines only, no test was removed or skipped, and the whole of `tests/v14` is green on the V15
  host with the branch build.
* The new test's own assertions are unreachable on the failing path. The harness assertion fires first, so the
  failure is reported from `tests/v14/nbd_support.py:263` and the test body's own size and pid assertions
  (`tests/v14/test_device_nbd.py:542-544`) never execute. The test still fails, which is what a regression test has
  to do, but the unique value of the test body (the pid assertion and the repetition over attach cycles) is not what
  guards the fix.
* The kernel tests are host-level tests: they need root and the `nbd` module, and skip otherwise
  (`tests/v14/nbd_support.py:67-76`), so they do not run in CI. The pure rule is covered by the Rust unit tests
  (`nbdsys.rs:295-325`), which do run in CI, but they cannot exercise the kernel's ordering — only the host can.
  The documentation should say this, because the kernel matrix reads as if the kernel behaviour is covered
  automatically.
* The failure path (the timeout) has no test at all (item 4).

**VERDICT (should-fix): cover the readiness failure with a test** (needs a configurable timeout).

**VERDICT (nice-to-have): state in the documentation that the kernel-7 regression test is a host-level test that
skips without root and the `nbd` module**, so that a reader knows which part of the matrix is protected in CI and
which part is protected by the lab host.

## 7. Project rules

`docs/V14_DEVICE_INTERFACE.md:45-113`; the branch's three commits

**VERDICT: should-fix — the code respects the rules; two statements in the documentation do not meet the project's
traceability rule.**

* No format change: the diff is confined to the attach path, the documentation and the tests; the volume journal and
  the backend format code are untouched.
* No new dependency: `Cargo.toml` and `Cargo.lock` are untouched, and the new code uses only the standard library.
* No other operation changed: `create` and `serve` are untouched, and the shared test harness only gains refusals.
* No CI configuration, version file or out-of-scope file is touched.
* The commits are authored by the founder's identity, carry no trailer and name no tool; a search of the whole diff
  for scratch paths and tool names finds none.
* **Traceability.** The lane rule is that every number in a document comes from a committed file in the tree, and
  that the file is named. The kernel matrix row for 7.0.0-38-generic claims that the kernel tests of
  `test_device_nbd.py` and B01-B09 pass, with "5 consecutive runs" (`docs/V14_DEVICE_INTERFACE.md:111`). No
  committed file in the tree records those runs, and the matrix row for the older kernel already uses the word
  "pending" where the evidence is missing (`docs/V14_DEVICE_INTERFACE.md:110`); the same wording should be used
  here.
* **Traceability.** The same row and the code comment (`nbdsys.rs:130-135`) attribute the widened window to a
  specific kernel change (`queue_limits_commit_update_frozen`, since 6.11). No kernel source is vendored in the tree
  and the host has no kernel headers installed, so that attribution cannot be checked from the tree; the observable
  part of the claim (the pid is published before the capacity, and the window is wide enough to matter) was
  confirmed on the V15 host by this review, but the causal part was not.

**VERDICT (should-fix): remove the "5 consecutive runs" claim, or point it at a committed log**, in the same way the
6.8 row points at what is missing.

**VERDICT (nice-to-have):** keep the kernel-mechanism claim to what is verifiable, or cite where the kernel source
that supports it can be found.

---

## MERGE VERDICT

**This branch is safe to merge into `build/v15` as it stands.** The fix is correct, minimal and in the right place:
the readiness rule is unit-correct, cannot be satisfied too early, and turns the kernel-7 race into a loud failure
instead of a size-0 device handed to `mkfs`; the ioctl sequence, the on-disk format, the dependency set and the
other two operations are untouched; the new test genuinely fails without the fix and passes with it on the target
kernel, and the shared harness change makes every other kernel test protect the same rule. Three things should be
done, and none of them blocks the merge of the fix itself: make the hard-coded readiness timeout an option and add a
test for the failure path, because that path is currently untested and untestable; stop the `log` helper from
overwriting the `pid` field of the `attach_not_ready` event, so the diagnostic the branch meant to publish is
actually published, and surface the readiness reason in the exit-1 error instead of the generic "stopped without a
requested detach" phrase; and bring the kernel matrix row and the kernel-mechanism comment back to what the tree
can support, in the same way the older-kernel row already does.
