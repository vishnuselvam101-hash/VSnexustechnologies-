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
- The pass-1 decisions of the V12 decoder (which reads are accepted, which are pending, which orientation is used) for
  every configuration; see §3 for the one rule whose *work* changes while its decisions are preserved.

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

## 4. What was not changed, and why

- The DP alignment itself (≈ 30 % of the single-worker wall after V13; the floor of the current algorithm) and the
  inner RS kernel (≈ 10 %): both already native and SIMD; a marker-anchored speculative projection accepted only on
  inner-code verification (plan §4 item 6) would remove most DP calls but needs a new native kernel with its own
  sanitizer and fuzz evidence; not attempted in this session.
- Batch transport between the main process and the workers (pickled NumPy buffers; ≈ 0.3 s of main-process time at
  4 workers, overlapped with worker time) and the serial outer-row decode of pass 2 (≈ 0.2 s): measured, left as is.
- Worker count heuristics: none added; `workers` stays an explicit option.

## 5. Diagnostics

`report["reads"]["rc_retry_skipped"]` (every mode; 0 with `"always"`), `report["native_backends"]` (orient ABI 2,
align ABI 4), stage counters unchanged in meaning (`orientation.rc_retry_attempted` now counts retries actually run).
