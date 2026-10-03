# Native alignment contract (V5 Phase 2)

This is the normative specification of the V4 marker-template aligner, `TemplateAligner.project` →
`_align` → `_traceback` in `src/vnxdna/v4/sync.py` (V4.0.0, `a358ae8`). The NumPy implementation is the **oracle**.
A native kernel conforms only if it reproduces every output field exactly, bit for bit, for every input in its
**domain** (§9). Inputs outside the domain must be routed to the oracle unchanged.

Notation: `T` = template length (= `layout.strand_nt`), `B` = band, `W = 2B + 1`, `INF = 2^28` (int32).
`c_mm, c_ins, c_del, c_x, guard` = `SyncCosts.marker_mismatch, insertion, deletion, marker_deletion_extra,
guard_segments`. `minq` = `min_quality`. Band offset `d = w − B` for band slot `w ∈ [0, W)`.

## 1. Template geometry (computed once per layout, shared by both implementations)

* `tpl[0..T)`: int16; marker base code 0..3 at marker positions, −1 at frame positions (`Layout.template()`).
* `frame_pos[0..frame_nt)`: strand index of every frame base, increasing.
* `seg_of[p]`: for a frame position, `frame_index // (marker_period or frame_nt)`; for a marker position, −1.
  `n_segments = max(seg_of) + 1`.
* `prev_seg[p]`: largest `seg_of[q] ≥ 0` with `q ≤ p`; 0 if there is none.
* `next_seg[p]`: `seg_of[p]` if ≥ 0, otherwise `next_seg[p+1]`; `n_segments − 1` past the end.
* `seg_frame[f] = f // (marker_period or frame_nt)` for frame index `f`.

The native wrapper receives these arrays from `TemplateAligner`, so the geometry is never re-derived in C.

## 2. Inputs

* `reads`: a list of `n` one-dimensional arrays of base codes. Codes from the V4 parsers are 0..3 (ACGT) and 4 (N),
  but the oracle accepts any `uint8` value. A value is compared with marker bases as an integer.
* `quals`: `None`, or a list of `n` entries, each `None` or an array of the **same length** as its read (Phred).
* `min_quality`: integer.

`project` selects the reads with `|len − T| ≤ B` ("usable"). All other reads get the **unaligned record** (§6) without
running the DP. Usable reads are processed in chunks of 2,048. Results are independent of the chunking and of the
other reads in a chunk: every quantity below is defined per read.

## 3. Read view

For a read `r` of length `L`, the oracle builds `R[col]` for `col ∈ [0, T + B + 2)`: `r[col]` if `col < L`, else 5.
It builds `Q[col]`: `q[col]` if a quality array exists and `col < L`, else 99. Columns outside `[0, T + B + 2)` are
*invalid*. The value 5 never equals a marker base.

## 4. DP (int32 arithmetic throughout)

`D[i][w]` = minimal cost of aligning `template[:i]` with `read[:i + d]`. `P[i][w]` ∈ {DIAG = 0, DEL = 1, INS = 2}.

**Row 0** (no masking by `L`): `D[0][w] = d · c_ins` and `P[0][w] = INS` if `d ≥ 0`; otherwise `D[0][w] = INF` and
`P[0][w] = DIAG`.

**Row i = 1..T**, with `t = tpl[i − 1]`, computed in this order:

1. `col = i − 1 + d`. If `col` is invalid, `diag = INF`. Otherwise `diag = D[i−1][w] + sub`, where `sub = c_mm` if
   `t ≥ 0` and `R[col] ≠ t`, else 0.
2. `dele = D[i−1][w+1] + c_del + (c_x if t ≥ 0 else 0)` for `w < W − 1`; `dele = INF` for `w = W − 1`.
3. `new[w] = dele`, `P = DEL` if `dele < diag`; otherwise `new[w] = diag`, `P = DIAG`. **Ties go to DIAG.**
4. Insertions, over the values from step 3 (before any update): `best[w] = min_{k ≤ w}(new[k] − k · c_ins) + w · c_ins`.
   Where `best[w] < new[w]` (strictly), set `new[w] = best[w]` and `P = INS`. **Ties keep DIAG/DEL.**
5. Mask: where `j = i + d` satisfies `j < 0` or `j > L`, set `new[w] = INF`. `P` keeps its step-4 value.
6. `D[i] = new`.

Values derived only from INF cells can exceed INF (for example INF + c_mm). They are kept exactly. They never wrap
inside the domain of §9.

**Final cost.** `w_end = (L − T) + B`. If `0 ≤ w_end < W`, `final = D[T][w_end]`; otherwise `final = INF`. The read is
`ok` iff `w_end` is inside and `final < INF`.

## 5. Traceback (only for `ok` reads)

State `(i, d) = (T, L − T)`. The per-read arrays start as: `tb[0..T) = 4` (template base), `bad[0..T) = false`,
`hit[0..n_segments) = false`, `ins = del = 0`. While `i > 0` or `d > 0`, read `op = P[i][d + B]`:

* **DIAG:** `j = i − 1 + d`; `tb[i−1] = min(R[j], 4)`; `bad[i−1] = (R[j] > 3) or (Q[j] < minq)`; `i −= 1`.
* **DEL:** `bad[i−1] = true`; `del += 1`; `s = seg_of[i−1]`; `lo = s if s ≥ 0 else prev_seg[i−1]`;
  `hi = s if s ≥ 0 else next_seg[i−1]`; `mark(lo, hi)`; `i −= 1`; `d += 1`.
* **INS:** `ins += 1`; `left = clip(i−1, 0, T−1)`, `right = clip(i, 0, T−1)`;
  `sl = seg_of[left]` if `i ≥ 1` else −1; `sr = seg_of[right]` if `i < T` else −1;
  `lo = sl if sl ≥ 0 else (sr if sr ≥ 0 else prev_seg[left])`;
  `hi = sr if sr ≥ 0 else (sl if sl ≥ 0 else next_seg[right])`; `mark(min(lo, hi), max(lo, hi))`; `d −= 1`.

`mark(lo, hi)`: `lo' = clip(lo − guard, 0, n_segments − 1)`, `hi' = clip(hi + guard, 0, n_segments − 1)`; set
`hit[s] = true` for `lo' ≤ s ≤ hi'` (nothing if `lo' > hi'`).

Each DIAG or DEL step visits a template position exactly once. The oracle raises `RuntimeError` after more than
`4(T + B) + 8` steps; this cannot happen on a finite path. A native kernel must report the same condition as an
error, never loop or index out of bounds.

## 6. Outputs (`Projection`)

| field | dtype / shape | aligned (`ok`) read | not `ok` (aligned but `final ≥ INF`) | unaligned (`|L − T| > B`) |
|---|---|---|---|---|
| `bases` | uint8 (n, frame_nt) | `tb[frame_pos[f]]` | all 4 | all 4 |
| `erased` | bool (n, frame_nt) | `bad[frame_pos[f]] or hit[seg_frame[f]]` | all true | all true |
| `ok` | bool (n,) | true | false | false |
| `insertions`, `deletions` | int64 (n,) | traceback counts | 0 | 0 |
| `marker_mismatches` | int64 (n,) | `#{p : tpl[p] ≥ 0 and tb[p] ≠ tpl[p]}` (a deleted marker base has `tb = 4`, so it counts) | 0 | 0 |
| `cost` | int64 (n,) | `final` | `final` (may exceed INF) | exactly INF |

## 7. Orientation

The aligner is orientation-agnostic: it aligns the read exactly as given. The decoder (`decoder._process`) decides
orientation before and after calling `project`: a marker-agreement pre-pass flips reads, and a reverse-complement retry
follows for reads whose cost exceeds `2 · c_ins`. Both use the `cost` and `ok` fields defined here. The native kernel
changes neither step, so orientation behaviour is identical whenever §6 is identical.

## 8. Failure cases

* Read shorter or longer than `T ± B`: the unaligned record (§6), no error.
* Read inside the band but with no finite alignment: `ok = false`, the record of §6, and `cost = final`.
* Empty read list: all output arrays have length 0.
* Quality array of a different length than its read: the oracle raises `ValueError` (NumPy broadcast). The native
  path forwards this case to the oracle, so the same exception is raised.

## 9. Native domain (otherwise the oracle runs)

The native kernel is used only when all of these hold. Each is checked in Python before the call and again in C:

* every read and quality array is a one-dimensional `uint8` array, and each quality array has its read's length;
* `0 ≤ B ≤ 64`, `T ≤ 8192`, `min_quality` fits in int32;
* `0 ≤ c_mm, c_ins, c_del, c_x ≤ 65536` and `0 ≤ guard ≤ 1024`.

With non-negative costs, every cell on a finite path is finite. Every DP value is bounded by `INF + T · 2 · 65536`, and
`|best − new|` by `W · 65536`, both < 2³¹, so int32 never overflows. The traceback therefore only visits valid cells.
Any input outside the domain, or any error the kernel reports, uses the oracle.

## 10. Backend selection

`VNXDNA_ALIGN_BACKEND` = `auto` (default: native if the library loads, otherwise the oracle), `native` (raise if the
library is unavailable) or `reference` (always the oracle). `TemplateAligner(..., backend=...)` overrides the
environment. `vnx native` (or `vnxdna.v5.native_alignment.status()`, or `python -m vnxdna.v5.native_alignment`) reports
which backend is active, which library is loaded and, if native is unavailable, why. `vnx version` includes the active
backend.
