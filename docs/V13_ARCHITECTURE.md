# VNX-DNA V13 architecture notes

What V13 changes in the decode path, why, and what stays fixed. The measured effect of every change is in
`docs/V13_BENCHMARK_REPORT.md`; the plan and the pre-declared workloads are in `docs/V13_PLAN.md`. All channel numbers
are **SIMULATED** (channel simulator, one host).

## 1. Unchanged by design

- Archive, container, superblock and strand formats: no new format version; V4/V5/V6 golden archives decode as before
  (`tests/compat/`), outputs are byte-identical to V12 on every benchmark seed.
- Encoding mappings, inner Reed–Solomon + CRC semantics, sync-marker template and the banded DP of the aligner
  (`vnxdna.sync.template`, `v5/native/align_dp.inc`), consensus rules, outer decoding, the fail-closed publication
  rule (whole-container SHA-256 + structural validation), VNX-Secure, authentication and encryption.
- The V12 memory behaviour: chunked gathers (`_GATHER_ROWS`), one filtered copy per pass-2 bucket, `trim_heap()`
  between stages, the pass-1 result window of 2 × workers and the pass-2 bucket window of 2 × workers.
- The pass-1 decisions of the V12 decoder for every read the V12 decoder accepted and for every orientation it chose;
  see §3 for the one rule whose *work* changes while its decisions are preserved, and §4 for the one step that can
  accept reads V12 left pending (never the reverse).

## 2. Pass-1 data path (C1–C3, E1)

V12 pass 1 turned every batch of 8,192 reads into per-read NumPy views (two Python lists), handed lists to
`_try` → `TemplateAligner.project` → `native.align.align_usable`, which rebuilt a contiguous buffer with
`np.fromiter` + `np.concatenate` before calling the kernel, and reversed the reads of the reverse-complement retry one
by one. In the baseline profile (`docs/V13_PLAN.md` §3) this bookkeeping was about 15 % of the single-worker wall.

V13 keeps the flat batch (`codes`, `offsets`, `lengths`, optional `quals`) from the native FASTQ parser to the kernel:

| layer | V12 | V13 |
|---|---|---|
| `recovery.pass1._process` | builds `reads` / `ql` view lists | default configuration: `reads = ql = None`; the lists are built only for smart/soft recovery or quality-weighted consensus (`_FLAT_PATH` switches the default path back to the lists for tests) |
| `recovery.pass1._try` | list API | accepts `reads=None` with `flat=(codes, quals, offsets, lengths)`; refuses that form outside the default configuration |
| `sync.template.TemplateAligner.project_flat` | — | `project` for a selection of a flat batch; same `Projection`; the reference and retry-band paths view the reads exactly as `project` did |
| `native.align.align_usable_flat` | — | validates the ranges (`_flat_in_domain`) and calls the kernel on them in place |
| `v5/native/align.c` **ABI 4** | `vnx_align_batch_lv(n, codes, offsets[n+1], total, …)` | adds `vnx_align_batch_sel_lv(n, codes, starts[n], lens[n], total, …)`: any n reads of a larger buffer, in any order; the lane kernel takes an optional `lens` pointer (`L = lens ? lens[k] : offsets[k+1] − offsets[k]`); the contiguous entries are unchanged |
| reverse-complement retry | `[_RC[reads[i][::-1]] for i in bad]` | `_rc_flat`: one gather builds the reversed, complemented flat batch of the retried reads |
| `dnaenc.mapping.nt_to_bytes` | four strided shift/or passes over an (N, B, 4) view | one masked uint32 multiply: `((x & 0x03030303) * 0x40100401) >> 24` per output byte (the four partial products land in disjoint 2-bit slots, nothing carries); the shift/or form stays as `nt_to_bytes_reference` for any other input |
| `recovery.consensus.snap_addresses` | `np.unique(rows, axis=0, return_inverse=True)` (void-dtype sort) for the membership table | `_row_ids`: one `lexsort` of the four columns, same partition |

Equivalence is pinned, not assumed: `tests/v13/test_pass1_flat.py` (flat entry vs list entry in any selection order,
`project_flat` vs `project` on both backends and with the retry band, `_rc_flat` vs the loop, `_process` with the flag
on and off on simulated reads of a real archive under five channel/option combinations), `tests/v13/test_nt_to_bytes.py`,
`tests/v13/test_row_ids.py`; the aligner stress fuzz runs the flat form every round; `tests/arch/test_snap_addresses.py`
still compares against the reference.

Native interface and ownership (ABI 4): the caller owns every buffer; the kernel reads `codes`/`quals` and writes the
seven output arrays only; `starts[k] + lens[k] <= total_len`, `|lens[k] − T| <= band` and `lens[k] >= 0` are checked
before any lane runs (`VNX_E_READ` otherwise, nothing written); `n == 0` is a no-op; `n > 0` without `lens` is
`VNX_E_ARG`. Fallback: `align_usable_flat` returns `None` outside the native domain and `project_flat` views the reads
for the NumPy reference, exactly as the list path does. A library with another ABI number is rejected on load.

## 3. Bounded reverse-complement retry (D1)

V12 rule (`pass1._process`): every read that pass 1 did not accept and whose forward alignment cost exceeds
2 × insertion is aligned again as a reverse complement; the retry is adopted when its frame verifies (inner RS + CRC)
**or** its alignment cost is below the forward cost. On the 4 MiB indel workload this re-aligned 82,837 reads per decode
and adopted none (the V12 orientation pre-pass already settles the orientation of almost every read).

V13 computes, before the retry, a **lower bound on the cost of any band-limited marker-template alignment of the
reverse complement** (`vnx_or_rc_bound`, orient kernel ABI 2; reference `pass1._rc_bound_reference`):

    bound(read) = Σ over marker blocks b of min( c_mm × (|b| − max over |s| ≤ band of hits_b(s)), c_ins, c_del )

An alignment of band `band` either keeps one shift `s` for all the markers of block `b` — then it pays `c_mm` per
mismatching marker at that shift, at least `c_mm × (|b| − hits_b(s))` — or it places an insertion or deletion
inside the block, paying at least `min(c_ins, c_del)` (a deleted marker pays `c_del + marker_deletion_extra`). The
blocks are disjoint and so are the operations inside them, hence the sum never exceeds the DP's cost (every other
DP term is non-negative). `tests/v13/test_rc_retry_bound.py` checks `bound <= cost` against the DP itself on random
reads (five layouts, four cost tables, native and reference aligner) and the native bound against the reference.

Rule: with `DecodeOptions.orientation_retry = "bounded"` (default) a read is retried only when `bound < forward cost`.
Where `bound >= forward cost`, the cost criterion of the V12 rule cannot adopt the retry; only a *verified*
reverse-complement frame of a read whose reverse-complement alignment is at least as costly as its forward one could.
Such an adoption is what the inner code's false-accept bound protects against, and on the validation corpus it never
occurs: `_process` with `"bounded"` and `"always"` returns identical accepted frames, pending records, adopted
orientations and counters (except the number of retries attempted) on indel, 50 % reverse-complement and heavy
channels; the two harsh stage-counter pools re-pinned in `tests/v7/stage_counters_golden.json` changed only
`orientation.rc_retry_attempted` (1,422 → 364 and 3,177 → 2,994) while `rc_retry_adopted` stayed 109 and 137; every
benchmark seed decodes to the byte-identical container. `"always"` restores the V12 behaviour (`--orientation-retry
always`). The bound is applied on the flat path only; the smart/soft/quality-weighted list path keeps the V12 rule.
The report carries `reads.rc_retry_skipped`. Unaligned reads (forward cost = ∞) are always retried.

This is the one V13 change that touches a decision rule. It is deterministic, recorded in the report, bounded in
work (one kernel pass over the candidate reads), and falls back to the full retry whenever the bound does not settle
the case.

## 4. Marker-anchored speculative projection (B1)

After C1–E1 and D1 the banded DP (`align_groups_l16`) was still 24 % of the single-worker samples
(`results/v13/pyspy-w1-indel-7b433d6.txt` in the lab) although most of the reads it aligned were then verified by the
inner code: on the 4 MiB indel workload 303k of the 443k reads of the sync path verify. V13 projects such reads
without the DP, behind the same verification that guards the exact-length fast path.

Kernel `vnx_or_spec_project` (orient ABI 3; reference `pass1._spec_project_reference`, the specification):

1. a read of length `L` (`delta = L − T`, `|delta| <= band`) visits its marker blocks left to right with a running
   shift `s_prev` (0 at the start). A block takes the first shift of `s_prev, s_prev−1, s_prev+1, s_prev−2, …`
   (`|s| <= band`, the whole block inside the read) at which every marker matches; if none does, it keeps `s_prev`
   when at most one marker mismatches there (counted), otherwise the read is not projected;
2. template position `p` lies in region `r` = the number of blocks whose first marker is `<= p`; its base is the
   read base at `p + (shift of block r−1, 0 for the first region)`, 4 outside the read; it is erased when it is not
   A/C/G/T, when its quality is below `min_quality`, or when the shifts of blocks `r−1` and `r` differ (`delta` after
   the last block): the region holds an indel and the inner code treats it as an erasure, as the DP would for the
   segment it blames;
3. the frame goes through `decode_frames` with the sync-path rule (`errors_only_retry=False`). A verified frame is
   accepted (`path` 5, counted under `sync` and `speculative` in the report, `inner_ecc.verified_speculative` in the
   stage counters); every other read of the sync path takes the DP exactly as before.

The projection minimises no cost; it is only ever used behind the inner RS + CRC verification, so a read it projects
wrongly fails verification and is aligned by the DP. The step can therefore only add acceptances: on simulated reads
of a real archive (indel, 50 % reverse-complement and heavy channels) every read accepted with
`sync_speculation="off"` is accepted with `"markers"` with the same fields and payload, and the container is
byte-identical on every benchmark seed. Acceptances it adds are reads whose DP alignment did not verify (the DP blames
one segment per indel; the region rule erases the whole region) — the pending set shrinks (4 MiB indel seed 95300:
pending 139,942 → 111,221, consensus attempts 107 → 31). The harsh indel stage-counter pool re-pinned in
`tests/v7/stage_counters_golden.json` gains 37 verified reads and decodes one more outer row (still a failing pool).

Cost: one pass over the marker blocks per read (3.8 % of the single-worker samples at
`results/v13/pyspy-w1-indel-59e6463.txt`) plus one inner decode of every projected frame; the DP's share fell from
24 % to 11 %, the inner RS kernel's rose from 7 % to 10 % (it now decodes the speculative frames too). Native ==
reference on 125,273 fuzz cases (release build) and under gcc/clang ASan+UBSan and UBSan-trap
(`results/v13/orient-sanitizers-b1-*.txt`); golden hashes and the rules on hand-built reads in
`tests/v13/test_spec_projection.py`. `DecodeOptions.sync_speculation = "markers"` (default) | `"off"`,
CLI `--sync-speculation`, SDK config key; the flat path only (the smart/soft/quality-weighted list path keeps the DP
for every read).

## 5. Pass-1 glue after B1 (S1, S3, S4)

With the DP mostly bypassed, the single-worker profile at 0f5b5bb (`results/v13/pyspy-w1-indel-0f5b5bb.txt` in the
lab) was dominated by NumPy passes around the native kernels: `decode_frames` 21 % of the samples with the RS kernel
itself at 11.5 %, the exact-length path's gather + marker strip + `np.minimum` + `nt_to_bytes` + erasure folding
about 12 %, the serial FASTQ parse 9 %. Three changes, each pinned to its V12 form:

- **S1 `decode_frames`** (`dnaenc.frame4`): the keystream XOR and the CRC of the first (CRC-first) pass run once, over
  the rows without flagged erasures only (a row with erasures is never clean); the rows the inner code touched are
  descrambled and CRC-checked again from the corrected frame. The V12 form stays as `decode_frames_reference`;
  `tests/v13/test_decode_frames.py` pins every field of `Parsed` on every profile with random errors, erasures and both
  retry modes.
- **S3 native frame packing** (`vnx_or_pack_exact`, `vnx_or_pack_projection`; orient kernel ABI 4; references
  `pass1._pack_exact_reference` / `_pack_projection_reference`): one pass per read gathers the exact-length read,
  counts its marker mismatches, applies the N and quality erasures and packs `hard = min(b, 3)`, the projection with
  erased bases = 4, the frame bytes (`nt_to_bytes` order) and the per-byte erasure flags; the projection form packs
  the speculative and DP projections. Used by the flat path only (the list path and quality-weighted consensus keep
  the NumPy form). `tests/v13/test_pack_frames.py` (every layout, with and without qualities, golden hashes,
  backend-independent pass-1 results, C argument checks); the orient stress fuzz compares both kernels every round;
  gcc/clang ASan+UBSan and UBSan-trap PASS (`results/v13/orient-sanitizers-s3-*.txt`), 1,000-round release fuzz
  127,206 cases 0 mismatches (`results/v13/fuzz-orient-s3-*.txt`).
- **S4 parser prefetch** (`pipeline.decode._prefetch`): a thread parses up to two batches ahead of pass 1; the native
  parser and the file reads release the GIL, so parsing overlaps the pass-1 work at one worker and the result handling
  of the pool path. Same batches, same order, the parser's exception raised at the consumer's position in the stream
  (`tests/v13/test_prefetch.py`); memory bounded by two extra batches.

Single-run effect at one worker on the 4 MiB indel workload (quiet host, SIMULATED, lab single runs; the A/B numbers
are in `docs/V13_BENCHMARK_REPORT.md`): 6.76 s at 0f5b5bb → 6.2 (S1) → 5.7 (S3) → 5.4 (S1 erasure-aware pass) →
5.0–5.1 s (S4). Rejected for want of a measurable gain: sorting the spill records once per batch instead of one mask
per bucket (within run-to-run noise at three buckets).

## 6. The 10× budget, and why the remaining floor is where it is

Founder target: 10× end-to-end against 6bf7749 (w1 9.36 s → 0.94 s, w4 2.99 s → 0.30 s on the 4 MiB indel single
runs). Measured at 60e1e54 (same runs): w1 ≈ 5.0–5.1 s, w4 ≈ 1.9–2.0 s. The single-worker budget after S4, from
the py-spy shares at b1fa07d (`results/v13/pyspy-s1b` in the lab session notes) scaled to the 5.4 s run:

| stage | seconds (w1) | nature |
|---|---|---|
| inner RS kernel (`decode_rows_avx2`), ~1.0 M frames incl. the speculative ones | 0.68 | native, SIMD |
| banded DP of the reads the projection did not verify + RC retries | 0.63 | native, SIMD |
| speculative projection kernel | 0.39 | native |
| `decode_frames` glue (XOR, CRC, gathers), after S1 | 0.40 | NumPy |
| FASTQ parse (native, serial; overlapped by S4 at w1) | 0.33 | native |
| pass 2 (duplicates, consensus, outer rows, container) | 0.46 | NumPy + Python |
| orientation scan + RC bound kernels | 0.37 | native |
| layout probe | 0.25 | NumPy + Python |
| pass-1 bookkeeping (pending headers, retry selection, stats, spill records) | 0.5 | NumPy |
| imports, output write, verify | 0.3 | — |

Removing every NumPy pass (a fused native pass 1: parse → orient → projection → RS/CRC → DP fallback over whole
batches) would leave the native kernels and pass 2: about 2.5 s at one worker, ≈ 3.7×. 10× at one worker (0.94 s)
is below the sum of the RS kernel, the DP and the parse alone (1.6 s) and therefore not reachable without a faster
DP, a faster RS decode (fewer frames or a cheaper erasure-only path) and a parallel parse. At four workers the
serial main-process work (parse, probe, spill, pass 2) is about 1.3 s of the 1.9 s; 0.30 s is below the serial
parse alone, so 10× at w4 needs byte-range parallel parsing in the workers and a native pass 2 as well. These are
the structural items left open (`docs/V13_COMPLETION_REPORT.md` "Next"); none was started in V13.

## 7. What was not changed, and why

- The DP alignment (`align_groups_l16`) and the inner RS kernel: both native and SIMD already; no cheaper exact form
  was found in V13.
- Batch transport between the main process and the workers (pickled NumPy buffers; ≈ 0.3 s of main-process time at
  4 workers, overlapped with worker time) and the serial outer-row decode of pass 2 (≈ 0.2 s): measured, left as is.
- Worker count heuristics: none added; `workers` stays an explicit option.

## 8. Diagnostics

`report["reads"]["rc_retry_skipped"]` (every mode; 0 with `"always"`), `report["reads"]["speculative"]` (0 with
`sync_speculation="off"`), `report["native_backends"]` (orient ABI 4, align ABI 4), stage counters unchanged in meaning
(`orientation.rc_retry_attempted` now counts retries actually run; `inner_ecc.verified_speculative` added).
