# V15 CC-3 — independent review of the nanopore branch `work/v15-cc3`

Reviewer scope: the CC-3 commit range `cfd6ee0..work/v15-cc3` (22 commits). The branch is already merged into
`build/v15`, so the range was located with `git log --oneline cfd6ee0..work/v15-cc3` and read with `git show` /
`git diff cfd6ee0..work/v15-cc3 -- <path>`. `cfd6ee0` is the pre-CC-3 base (the "base" tree of the experiments);
`c416349`, `074f6dd` and `4e32e18` are the "new" trees the runs were made on.

All channel results below are SIMULATED (reads from a software channel model). No DNA was synthesised, stored or
sequenced. Every number is taken from a committed file, which is named at each use; where a number in the branch's
own documents cannot be pointed at a committed file, that is stated as unverified rather than repeated as fact.

Summary of the outcome:

| Item | Verdict |
|---|---|
| 1. Cluster budget fix | should-fix (one reachable silent-drop edge case; the committed E/F numbers are **not** invalidated) |
| 2. Preset-F outer-list decoder in C++ | pass — no must-fix, no wrong-data path found |
| 3. Determinism | pass, with one should-fix note on how the native binary is chosen |
| 4. Traceability of the claims | should-fix — four figures in the results README are not traceable to any committed file, one sentence mislabels an ABI |
| 5. Test quality | pass — a real end-to-end revert detector exists; one untested edge case |
| 6. Evidence labels | pass — every row, summary and document carries the SIMULATED label; no superlatives, no DNA claims |

---

## 1. The cluster budget fix — VERDICT: should-fix

What the fix does: over the candidate-pair budget, the stage lowers the bucket cap to the largest cap whose slot
pairs fit instead of stopping (`src/vnxdna/recovery/cluster/graph.py:113-162`, native equivalent
`src/vnxdna/native/c/cluster.c:153-250`, ABI 6, `src/vnxdna/native/cluster.py:49`). The floor is
`ClusterConfig.bucket_cap_floor`, default 2 (`src/vnxdna/recovery/cluster/__init__.py:55`); the lowered run is
reported as a budget record with `"stopped": false` and a new report key `budget_limited`
(`src/vnxdna/recovery/cluster/graph.py:60-82`, `src/vnxdna/recovery/cluster/stage.py:371`).

### 1a. Can a stage still be silently dropped? — **should-fix** (`graph.py:150-162`, `cluster.c:236-248`)

Yes, in one reachable case, and it is shared by the reference and the C kernel (so it is not a native/reference
divergence — it is one defect in two implementations).

The cap is chosen from the bucket-size histogram:

```python
hist = Counter(m.size for _, m in buckets)          # graph.py:150
acc, cap = 0, 1
for sz in range(2, min(bucket_cap, n) + 1):          # graph.py:152
    add = hist.get(sz, 0) * (sz * (sz - 1) // 2)
    if acc + add > max_pairs:
        break
    acc, cap = acc + add, sz                        # graph.py:156  <-- cap is set for EVERY sz
if cap < lo_cap:                                    # graph.py:157
    ... stop ...
kept = [(i, m) for i, m in buckets if m.size <= cap] # graph.py:161
```

`cap` is advanced on every iteration that does not overflow, including sizes for which there is no bucket at all
(`hist.get(sz, 0) == 0`, a gap in the histogram). The C kernel has the same shape
(`cluster.c:238-242`: `c = sz` is assigned inside the loop, the `hist[sz] &&` guard only prevents the `break`).

Consequence: when the smallest bucket sizes present are 3 or more (no size-2 bucket anywhere) and the size-3 group
alone exceeds the budget, the loop breaks at `sz = 3` with `cap = 2`. `cap >= lo_cap` (2 >= 2), so **no stop is
raised**; `kept` is empty because no bucket has size <= 2; the stage continues with **zero candidate pairs**, drops
every bucket, and the report says `stopped: false` / "clustering continued".

Reproduced locally on the branch (reference and native, identical output):

```
min_shared=1  max_pairs=1  pairs 0
  rec {'limit': 'max_candidate_pairs', 'allowed': 1, 'used': 0, 'requested': 3, 'bucket_cap': 2,
       'buckets_dropped': 1, 'stopped': False,
       'effect': 'bucket cap lowered from 256 to 2: 1 more buckets ignored; clustering continued'}
min_shared=1  max_pairs=2  pairs 0   (same shape)
floor-less path, same input, max_pairs=1:
  rec {'limit': 'max_candidate_pairs', 'allowed': 1, 'used': 3,
       'effect': 'clustering stage stopped; no cluster frame produced'}   is_stop -> True
```

So the fix does remove the explicit "clustering stage stopped; no cluster frame produced" refusal in this case and
replaces it with a silent continuation that clusters nothing. Nothing wrong can be published from it (every frame is
still verified by inner RS + CRC-32 and the container SHA-256 decides publication, `experiments/v15/cc3/README.md:38-39`),
but the diagnosis is lost: the decode fails later with a generic error instead of the explicit budget stop, and
`budget_limited` is true although nothing was clustered.

Not triggered by the committed runs: the three 10 MB runs lowered the cap to 52 with 574,950,149 / 575,159,370 /
575,247,934 slot pairs used (`experiments/v15/cc3/results/kaggle-cc3-m{0,1,2}/rows.jsonl`), i.e. hundreds of
thousands of buckets were kept, so the histogram had no gap at the floor. There is also no test for this case
(`tests/v11/test_candidate_cap_lowering.py:39-129` uses pools that always contain size-2 buckets).

Recommended fix (one line in each implementation): advance `cap`/`c` only to sizes that actually have buckets
(skip empty histogram sizes), and if the resulting kept set is empty, raise the floor-less stop instead of continuing.
Add a regression test with a sketch that has no size-2 bucket.

### 1b. Does `bucket_cap_floor` change the results of the other presets (E, F)? — **pass** (the committed numbers stand)

The floor is a `ClusterConfig` default, so it applies to E and F as well as to the plain `--read-clustering
fallback` path — the harness builds `ClusterConfig(**cluster_config(config))` for E and F
(`experiments/v15/cc3/run.py:101`), and the CHANGELOG says so explicitly (CHANGELOG.md, "Changed: read clustering").

That the floor is **inert below the budget** in every committed 1 MiB row is verifiable from the committed files: of
the 271 rows in `experiments/v15/cc3/results/kaggle-cc3-*/rows.jsonl`, only the nine 10 MB rows carry a
`cluster_budget` record at all; all 262 other rows have `cluster_budget: []` and `candidate_bucket_cap: null`, i.e.
the budget was never reached. Consequently:

- base E and new E are **identical on all 20 held-out seeds** (same exact/failure outcome and the same
  `failed_groups`), which is what `experiments/v15/cc3/README.md:28` claims;
- the 1 MiB table (base E 1/20, new E 1/20, base F 13/20, new F 15/20) is therefore a comparison of the outer-list
  work only, and the floor does not invalidate it.

At 10 MB the floor is decisive — that is the point of the fix — and the base/new difference there is the floor plus
the `members()` one-sort refactor (`f17ea8a`, behaviour-identical) for E, and the outer-list work for F.

**nice-to-have / should-fix (release note):** the floor changes the behaviour of an existing path. A user of plain
`--read-clustering fallback` who was previously getting an explicit `budget_exceeded` refusal will now get a
continued stage with a lowered cap. The CHANGELOG and `docs/CLI.md` state this, but it deserves a line in the V15
release notes and in the finalisation report, because "the preset" is not the only way to get the new behaviour.

**nice-to-have:** the code comment at `src/vnxdna/recovery/cluster/__init__.py:51-54` states that "at 10 MB D13-F1
… a floor of 64 still stopped". No committed file records a run with a floor of 64, so that sentence is a code
comment, not a result; it should either be dropped or backed by a committed run.

---

## 2. The preset-F outer-list decoder in C++ — VERDICT: pass

Verdict: **pass** — no must-fix, no should-fix in the kernel itself. One small robustness note in the Python side
(item 2c).

The CC-3 change to the C++ file is the RANSAC tuple order (`src/vnxdna/native/cpp/outerlist.cpp:461-509`): tuples are
now tried best-first (by their largest hypothesis index, then lexicographically) so that the tuple budget is spent on
the best-ranked hypotheses. The Python reference was changed to match
(`src/vnxdna/recovery/outer_list.py:254-259`, `_tuples`, used at `outer_list.py:291`).

### 2a. Index bounds and buffer sizes — **pass**

- Entry-point validation: `k >= 1`, `m >= 1`, `k + m <= MAX_N (255)`, `1 <= P <= MAX_P (4096)`, `nh <= 4096`,
  `1 <= tau <= P`, `1 <= max_tuples <= 2^32` (`outerlist.cpp:438-440`); null checks at 441; every hypothesis position
  is range-checked against `sp.n` and against the known set (`:446-449`).
- `build_space` returns `OL_EARG` when `f = k - h` is outside `[1, MAX_F (8)]` (`outerlist.cpp:113-122`), so `f >= 1`
  is guaranteed before the tuple loop; `head` is therefore sized `f - 1 >= 0` (`:461`).
- `S` is sized `f`, `sub` `f*f` (<= 64), `y`/`z` `f`, `cnt`/`bestcnt` `H` (<= 4096) (`:461-463`); `acol` is
  `u * P` (<= 255 * 4096 = 1,044,480 bytes) (`:453-457`).
- The tuple enumeration is complete and injective: for each `top` in `[f-1, H-1]`, `next_comb(head, top)`
  (`:74-82`) enumerates the `(f-1)`-subsets of `{0..top-1}`; every f-subset of `{0..H-1}` appears exactly once,
  with its largest element as `top`. The tuple is strictly increasing, so the "distinct positions" test on
  `hu[]` (`:473-476`) is equivalent to distinct indices. `f == 1` is handled: `head` is empty, `next_comb` returns
  false immediately, and one tuple per `top` is tried.
- The cap is spent only on tuples with distinct positions, and the distinctness test runs **before** the cap check
  (`:477-478`), exactly as in the reference (`outer_list.py:293-296`). The two backends therefore agree on *which*
  tuples are tried, which is what `tests/arch/test_outer_list.py:359`
  (`test_ransac_cap_is_spent_best_first_in_both_backends`) checks.

### 2b. Integer overflow — **pass**

Every product that could exceed 2^31 is computed in 64-bit: `hval[h * P + col]` uses an `int64_t` cast
(`outerlist.cpp:487-488`), `acol[hu[h] * P + col]` uses `size_t` casts (`:457`, `:497-498`), the vector sizes are
`size_t` products of bounded factors, and the counters (`support`, `best`, `tried`) are `int`/`int64_t` over
`H <= 4096` and `2^32`. The cluster kernel's counters are `int64_t` and its `total` is saturated rather than
wrapped (`cluster.c:279`, `graph.py:161` clamps the Python side at `2^63 - 1`).

### 2c. Can it produce wrong bytes instead of an error? — **pass (no)**

The kernel's outputs are *proposals*, not decoded data. A symbol proposed by the outer list is only accepted when the
frame verifies at its address (inner RS + CRC-32), and the container SHA-256 decides publication
(`experiments/v15/cc3/README.md:38-39`; the verification contract is stated in
`src/vnxdna/recovery/cluster/__init__.py:97-107`). The kernel itself never raises across the C ABI: every entry
point catches `std::bad_alloc` (`OL_ENOMEM`) and everything else (`OL_EINTERNAL`) (`outerlist.cpp:512-516`).
The new Python-side guard refuses `1 <= f = k - known <= MAX_F` before the call
(`src/vnxdna/native/outerlist.py:59-63`), which is what the kernel would answer with `OL_EARG`; the reference raises
the same way, and both are covered by
`tests/v11/test_redteam_outerlist_buffers.py::test_no_missing_data_symbol_is_refused_like_the_reference`.

The 271 committed rows contain 0 false successes (`experiments/v15/cc3/results/summary.json`,
`false_success_total: 0`, and every row's `false_success` is false), which is the end-to-end check that no wrong
bytes were published.

**nice-to-have:** the new nested loop body is indented with two spaces while the rest of the file uses four
(`outerlist.cpp:468-509`).

**should-fix (small, Python side):** `_ransac_scan` re-encodes a codeword inside a bare
`except Exception` (`src/vnxdna/recovery/outer_list.py:646-680`, the `except Exception` around
`self.codec.encode(self.codec.decode(...))`). The codeword is afterwards checked against the agreement counts the
RANSAC reported (`ransac_scan_mismatch`), which limits the damage, but a blanket `except` that silently disables a
stage is the kind of thing a future edit could misread. Narrow it to the expected exception types and count the
suppression.

---

## 3. Determinism (same seed, same output) — VERDICT: pass, with one note

- The lowered cap is a pure function of the bucket-size histogram, which depends only on the input
  (`experiments/v15/cc3/README.md:24`); it does not depend on the chunk size, the worker count or the slot order.
  `tests/v11/test_candidate_cap_lowering.py:71` checks native == reference over 12 seeds x 5 budgets x 5 floors x
  3 chunk sizes.
- The RANSAC order is fixed and RNG-free; ties are broken by the tuple order, identically in both backends
  (`outerlist.cpp:504`, `outer_list.py:291-296`).
- The new helpers are deterministic: the group table is built once from `n_groups`
  (`outer_list.py:520-526`), the chunking step is a fixed function of the table size (`outer_list.py:534-543`), and
  the variant ordering uses `np.bincount(...).argsort(kind="stable")` (`outer_list.py:680-681`) and
  `np.unique` + `np.lexsort` (`outer_list.py:730-733`) — all stable, order-defined operations.
- The `members()` one-sort refactor (`src/vnxdna/recovery/cluster/graph.py:196-210`) uses a stable sort and returns
  the same members as the old per-ID scan.

**should-fix (note, not a blocker):** which native binary runs is now decided by file modification times
(`src/vnxdna/native/_loader.py:41-79` `freshest_first`, used by `src/vnxdna/native/cluster.py:95-108`). The two
builds are asserted byte-equal, so the *result* does not change, but the *selection* is environment-dependent, and
the rule compares two build artefacts rather than the C source: a developer who edits
`src/vnxdna/native/c/cluster.c` and does not rebuild still runs a stale kernel — the same class of shadowing the
loader fix was written to close. The loader already knows the source path (`_loader.py:100` `self.source`,
`cluster.py:62` `_SOURCE`), so the freshness test could include the source's mtime, or the CHANGELOG claim
("the newer of the two is the one that matches the tree") could be qualified accordingly.

---

## 4. Traceability of the branch's claims — VERDICT: should-fix

Traced to committed files (all of these hold):

| Claim (README line) | Committed file that supports it |
|---|---|
| 10 MB table, base E explicit failure with the budget stop, new E and new F EXACT with cap 52, and the seconds (47-52) | `experiments/v15/cc3/results/kaggle-cc3-m{0,1,2}/rows.jsonl` — base E: `allowed/used` 577,669,600/577,675,344, 577,027,000/577,027,365, 575,547,800/575,550,887 with effect "clustering stage stopped"; new E/F: `bucket_cap` 52, `used` 574,950,149/575,159,370/575,247,934, `requested` 2,478,182,873/2,472,468,939/2,458,101,064, `buckets_dropped` 509,863/508,839/506,319; decode seconds 193.18/177.58/182.33 (base E), 2022.24/1723.66/1739.26 (new E), 7881.05/6982.45/6779.94 (new F) — all rows `exact: true` on the new tree |
| "0 FALSE_SUCCESS over all 271 rows" (45) | the 271 rows of `experiments/v15/cc3/results/kaggle-cc3-*/rows.jsonl`; every row has `false_success: false`; `experiments/v15/cc3/results/summary.json` has `false_success_total: 0` |
| 1 MiB table and the F failure list (59-67) | `kaggle-cc3-h{1,2,3,4}/rows.jsonl` — base E 1/20, new E 1/20, base F 13/20, new F 15/20; the five F failures with their rows (93124 [399], 93126 [136,331], 93130 [327], 93131 [313], 93135 [127]); 93123 and 93136 are the two extra seeds; no seed is exact on base F or new E and not on new F |
| McNemar, 14 discordant seeds all favouring F, p = 1.2e-4 (64) | recomputed from `kaggle-cc3-h{1,2,3,4}/rows.jsonl`: 14 discordant, 0 the other way, exact two-sided p = 1.221e-4 |
| Column-parity table, strands and the 18-of-20 seeds (69-79) | `kaggle-cc3-c{1,2}/rows.jsonl` — default 0/20, E 20/20, F 20/20; `strands` 32,837 (run H) -> 34,117 (run C) = +3.90 %; `rows_recovered_by_columns > 0` on 18 of 20 E rows |
| R and P (81-84) | `kaggle-cc3-r/rows.jsonl` (base default 0/10, E 1/10 with 93090 failing groups 195 and 402, F 9/10) and `kaggle-cc3-p/rows.jsonl` (new F 19/20, 93097 row 92); the same evaluation-5 numbers are in `experiments/arch/outer_list_eval/results/eval5-summary.json` (E 2, F 19, `mcnemar_exact_p` 1.53e-05, `false_success` 0) |
| Regression run G (86-91) | `kaggle-cc3-g/rows.jsonl` — clean, illumina-like and indel 3/3 EXACT on every tree and configuration; nanopore-like default 0/3, E 0/3, F 3/3 on both trees; the default rows carry `status: "ERROR:VNXDecodeError"` and `output_sha256: null` (so "no output written" is supported) |

Not traceable — these are numbers in a document that no committed file supports, or that the committed file
contradicts. They are the reason for the should-fix verdict on this item:

1. **should-fix — `experiments/v15/cc3/README.md:13`, "about 584.6 M requested against 584.57 M allowed".** The
   committed record for seed 93100 says `allowed: 577669600` and `used: 577675344` (an overshoot of 5,744 slot
   pairs) and, on the new tree, `requested: 2478182873`
   (`kaggle-cc3-m0/rows.jsonl`). No committed file contains 584.6 M or 584.57 M. The *point* of the sentence (the
   budget was exceeded by a hair) is supported; the figures are not.
2. **should-fix — `README.md:14`, "about 2.9 M unplaced reads".** No committed file records a read count. The rows
   record `strands` (322,515) and `cluster_frames_verified` (233,757) only.
3. **should-fix — `README.md:55`, "about 912-953 s per seed" (archive build plus read simulation).** The committed
   `prep_seconds` are 1136.1 (93100, new), 1160.0 (93100, base), 916.1 (93110, new), 947.6 (93110, base), 911.9
   (93111, new), 953.4 (93111, base). The stated range covers 93110 and 93111 but **not** 93100, which is 1136.1 s
   — i.e. the claim is wrong for one of the three seeds of the table it sits under.
4. **should-fix — `README.md:54`, "about 5.6 GiB on every run".** The committed `peak_rss_kib` is 5,838,464-5,859,984
   KiB (about 5.6 GiB) on the six *new* 10 MB rows, but 5,192,048-5,197,328 KiB (about 5.0 GiB) on the three *base*
   rows of the same table. The harness also measures
   `max(RUSAGE_SELF.ru_maxrss, RUSAGE_CHILDREN.ru_maxrss)` (`experiments/v15/cc3/run.py:115`), i.e. the peak of the
   largest single process, not the sum over the process tree, so "of the decode process tree" overstates it.
5. **should-fix — `README.md:94`, "the ABI 6 native change (outer-list kernel) was run clean by the reviewer on
   2026-10-19 (CC-3 review)".** Two problems: the outer-list kernel is C ABI 1
   (`src/vnxdna/native/cpp/outerlist.cpp:1,27,177`; `vnx_ol_abi_version()` returns 1) while **ABI 6 is the cluster
   kernel** (`src/vnxdna/native/cluster.py:49`); and no sanitiser log is committed, so the claim is a verbal report
   with the wrong kernel named. Fix the ABI number and either commit the log or attribute the sentence explicitly as
   an uncommitted report.
6. **nice-to-have — `experiments/v15/cc3/STATUS.md`**, "4869 passed, 8 skipped, 2 failed". No committed test log
   supports the counts. The two failures are named and stated to pass in isolation, which is honest, but under the
   evidence rule the counts should be committed with the log or dropped.
7. **nice-to-have — `README.md:42`**, "The `src/` difference between those three is a typing-only edit in
   `sdk/config.py`". The difference (`c416349..4e32e18`) is 13 lines in `sdk/config.py` and it restructures the
   preset plumbing rather than only typing it — but the substance is right: the harness builds `DecodeOptions`
   itself (`experiments/v15/cc3/run.py:101`) and never calls `sdk.config.decode_options`, so the runs are comparable.
   Wording only.
8. **nice-to-have — `README.md:105-107`**, "On base cfd6ee0 it fails (the stage stops with `budget_exceeded`)". The
   test does not exist on base; run there, `ClusterConfig(bucket_cap_floor=2)` raises `TypeError` (the field does not
   exist), so the test fails *earlier* than the stated reason. As a revert detector (i.e. remove the floor from the
   new tree) the test does catch the behaviour change, and it is a real end-to-end check — only the wording is off.

Everything else in the two READMEs, the two PREREGs, the harness and the CHANGELOG entry is either a file name, a
seed list, a run identifier or a number I could point at a committed file.

---

## 5. Test quality — VERDICT: pass

**A revert detector exists and uses the real codec.** `tests/v7/test_read_clustering.py:234`
(`test_candidate_pair_budget_lowers_the_bucket_cap_and_keeps_clustering`) is the scaled proxy of the 10 MB budget
path. It uses the `pools` fixture (a real archive and real reads), decodes end to end, and asserts both the report
(`status == "ran"`, a record with `stopped is False`, `budget_limited is True`, `used <= allowed < requested`,
`2 <= bucket_cap < 256`, `frames_verified > 0`) and the outcome (`EXACT`). Removing the fix makes it fail: the stage
stops and the record has no `"stopped": false`. The tight-budget half of the test also asserts that the decode is
either `EXACT` or an `EXPLICIT_FAILURE` — never a false success.

Supporting coverage:

- `tests/v11/test_candidate_cap_lowering.py:39` — the reference lowers to the largest cap that fits and equals a run
  at that cap; `:57` — no fitting cap above the floor gives exactly the floor-less stop; `:71` — native == reference
  over 12 seeds x 5 budgets x 5 floors x 3 chunk sizes.
- `tests/v7/test_cluster_unit.py:224` — the `budget_limited` report key and the unchanged `budget_exceeded` key.
- `tests/arch/test_outer_list.py` (294 lines added) — native == reference for `decode_row` and `ransac`, the
  best-first tuple order (`:348`), the cap spent best-first in both backends (`:359`), the group-ambiguity counting
  (`:379`), the RANSAC scan (`:440`), plus the variant handling.
- `tests/v11/test_redteam_outerlist_buffers.py` — the new `f < 1` refusal on both paths.
- `tests/v15/test_native_stale_extension.py` (220 lines) — the loader ordering.

Gaps (both nice-to-have relative to the findings above):

- the empty-kept / histogram-gap case of item 1a has **no test** — it should come with the fix.
- the cap-lowering unit tests use synthetic sketches (`tests/v10/test_candidates_chunked.py::_clustered`), not the
  real codec; the real-codec coverage is the single end-to-end test. The 10 MB experiment (`kaggle-cc3-m*`) does
  exercise the real codec, so the evidence compensates for the unit level, but a second real-codec case (for
  instance a 2 MiB D13-F1 archive with a tight budget) would be cheap insurance.
- `tests/v7/test_read_clustering.py:236` carries "120,223 slot pairs at cap 256" in its docstring — a figure that
  is not in a committed results file. It is a test-local number, but under the evidence rule it should either name a
  file or be dropped.

---

## 6. Evidence labels — VERDICT: pass

- Every one of the 271 committed rows carries
  `"label": "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"`, and every
  `summary.json` under `experiments/v15/cc3/results/` and `experiments/arch/outer_list_eval/results/` carries the
  same label (the harness writes it from `experiments/v15/cc3/run.py`, `LABEL`).
- `experiments/v15/cc3/README.md:1-5`, `PREREG.md`, `STATUS.md`, `experiments/arch/outer_list_eval/README.md` (the
  label is repeated for each evaluation) and the CHANGELOG entry all state that the channels are simulated and that
  no DNA was synthesised, stored or sequenced.
- No sentence in the branch claims DNA was synthesised, stored or sequenced, and none of the five "0 false success"
  style claims is presented as a hardware result. The recommendation section (README:96-102) is written in terms of
  simulated channels and states the limits (no 32 MB step, F's 10 MB decode time).
- No superlatives: the only "best" in the whole diff is "best-first tuple order" (a technical term for the
  enumeration order). No guaranteed lifetimes, no marketing language, no AI-tool names, no attribution trailers, no
  scratch paths in the committed files.
- The one blemish is item 4.5 above: the sanitiser sentence names the wrong ABI and has no committed log, and a
  verification claim is exactly the kind of sentence that must point at a file.

---

## MERGE VERDICT

**The branch is safe to merge into `build/v15` as it stands, provided the four untraceable figures and the mislabelled
sanitiser sentence are corrected in the same merge.** Nothing I found can produce wrong data: the outer-list kernel
returns an error rather than bad bytes, every frame the new code proposes is verified by inner RS + CRC-32, the
container SHA-256 still decides publication, no test was weakened or skipped, and the on-disk formats are unchanged.
The committed results are sound — 271 rows, 0 false successes, and the E/F numbers I could trace (10 MB base failure
and new EXACT, 1 MiB 1/20, 13/20, 15/20 with the McNemar result recomputed at p = 1.221e-4, column parity 0/20, 20/20,
20/20) all match the committed rows exactly, and the `bucket_cap_floor` default of 2 is provably inert on every 1 MiB
row, so it does not invalidate the other presets' numbers.

What must be fixed first (documentation only, in the same merge): `experiments/v15/cc3/README.md:13` (the 584.6 M /
584.57 M figures — the committed record says 577,669,600 allowed and 577,675,344 used), `:14` (the 2.9 M unplaced
reads), `:54` (the "5.6 GiB on every run" — the base rows are about 5.0 GiB, and the measurement is a per-process
peak), `:55` (the 912-953 s per seed — seed 93100 is 1136.1 s in the committed rows) and `:94` (the outer-list
kernel is ABI 1, not ABI 6, and no sanitiser log is committed). Each of these is a sentence that a reader would
otherwise take for a measured result.

What should be fixed but need not block the merge: the one reachable silent-drop case in the cap selection
(`src/vnxdna/recovery/cluster/graph.py:150-162` and `src/vnxdna/native/c/cluster.c:236-248`), where a gap in the
bucket-size histogram can leave the stage with zero candidate pairs while the report says "clustering continued" —
it is not triggered by any committed run and it cannot publish wrong data, but it removes an explicit refusal and it
is untested, so it should be fixed with a regression test before the next release. Also worth doing in the same
release: state the `bucket_cap_floor` behaviour change of the plain `--read-clustering fallback` path in the V15
release notes, narrow the blanket `except Exception` in `_ransac_scan`
(`src/vnxdna/recovery/outer_list.py:646-680`), and either compare the native loader's freshness against the C
source's mtime (`src/vnxdna/native/_loader.py:41-79`) or qualify the CHANGELOG claim.
