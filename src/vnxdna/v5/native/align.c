/*
 * VNX-DNA V5 native marker-template aligner.
 *
 * Bit-exact implementation of the V4 NumPy aligner (vnxdna.v4.sync.TemplateAligner._align + _traceback).
 * The normative specification is docs/V5_NATIVE_ALIGNMENT_CONTRACT.md; section numbers below refer to it.
 *
 * Interface: plain C ABI, loaded with ctypes (no Python headers). One call aligns a batch of reads that were already
 * selected as usable (|L - T| <= B). Reads are passed as one flat uint8 buffer plus n + 1 offsets.
 *
 * Structure:
 *   - DP (section 4): LANES reads at a time in SIMD lanes (GCC/Clang vector extensions, no intrinsics). Every control
 *     quantity of the recurrence (row i, band slot w, template base, col, j, "w < W - 1", "col < 0") is the same for
 *     all reads. Only the read bases and the length L differ per lane. Each lane therefore evaluates exactly the
 *     scalar recurrence of its own read, and lanes never interact. The last group is padded with copies of a real
 *     read, whose results are discarded.
 *   - Traceback and projection (sections 5-6): scalar, one lane at a time.
 * The result of a read therefore does not depend on the other reads in the batch or on its position.
 *
 * Safety: every size is validated (domain of contract section 9) before any allocation. Allocation sizes are bounded
 * (T <= 8192, B <= 64), and every index is checked against validated sizes. Errors are returned, never asserted.
 * There is no global state; the library is reentrant. It holds no Python objects; ctypes releases the GIL during the call.
 */
#define _POSIX_C_SOURCE 199309L   /* clock_gettime, used only by the profiled entry point */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define VNX_ALIGN_ABI 2   /* 2: adds vnx_align_batch_path (read position of every template base) */

#define INF_COST ((int32_t)(1 << 28))
#define OP_DIAG 0
#define OP_DEL 1
#define OP_INS 2

#define MAX_BAND 64
#define MAX_TEMPLATE 8192
#define MAX_COST 65536
#define MAX_GUARD 1024

#define LANES 4
typedef int32_t vi32 __attribute__((vector_size(4 * LANES)));
typedef int8_t vi8 __attribute__((vector_size(LANES)));

/* lane-wise select; m is a comparison result (all ones or all zeros per lane) */
#define vsel(m, a, b) ((((a)) & (m)) | (((b)) & ~(m)))

enum {
    VNX_OK = 0,
    VNX_E_ARG = -1,       /* null pointer or inconsistent argument */
    VNX_E_DOMAIN = -2,    /* outside the native domain (contract section 9) */
    VNX_E_READ = -3,      /* offsets not monotone, or a read outside the band */
    VNX_E_NOMEM = -4,
    VNX_E_TRACE = -5,     /* traceback left the band or exceeded its step bound */
};

int vnx_align_abi_version(void) { return VNX_ALIGN_ABI; }
int vnx_align_lanes(void) { return LANES; }

typedef struct {
    int32_t T, B, W, width, n_segments, frame_nt;
    const int16_t *tpl;
    const int32_t *seg_of, *prev_seg, *next_seg, *frame_pos, *seg_frame;
    int32_t c_mm, c_ins, c_del, c_x, guard, min_q;
} geom_t;

typedef struct {          /* per-call scratch, allocated once */
    vi32 *D, *N;          /* W + 1 vectors each */
    vi32 *R;              /* (B + width) vectors: R[B + col] = lane's read base at col, 5 beyond its end */
    vi8 *ptr;             /* (T + 1) * W vectors of pointers */
    uint8_t *tb, *bad, *hit;
} scratch_t;

/* Optional stage timers (profiled entry point only): pack reads, DP, traceback, projection; seconds. */
typedef struct {
    double pack, dp, traceback, projection;
} timers_t;

static inline double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

static inline int32_t clip32(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : (v > hi ? hi : v); }

static void mark(uint8_t *hit, const geom_t *g, int32_t lo, int32_t hi) {
    lo = clip32(lo - g->guard, 0, g->n_segments - 1);
    hi = clip32(hi + g->guard, 0, g->n_segments - 1);
    for (int32_t s = lo; s <= hi; s++) hit[s] = 1;
}

/* Section 4 for one group of LANES reads (each L in [T - B, T + B]). Leaves the final D row in s->D. */
static void dp_group(const geom_t *g, scratch_t *s, const vi32 *Lp) {
    const vi32 Lv = *Lp;
    const int32_t T = g->T, B = g->B, W = g->W;
    const int32_t c_mm = g->c_mm, c_ins = g->c_ins, c_del = g->c_del, c_x = g->c_x;
    const vi32 zero = {0};
    const vi32 INFv = zero + INF_COST;
    vi32 *D = s->D, *N = s->N;
    /* row 0: leading insertions, no masking by L */
    for (int32_t w = 0; w < W; w++) {
        const int32_t d = w - B;
        D[w] = d >= 0 ? zero + d * c_ins : INFv;
        s->ptr[w] = (vi8){0} + (int8_t)(d >= 0 ? OP_INS : OP_DIAG);
    }
    /* Steps 1-5 of a cell are fused into one pass over w. That is equivalent: step 4's running minimum at w needs only
     * the step-3 values of k <= w, all computed by then, and step 5 masks after step 4 has read the unmasked value.
     * The running minimum starts at INT32_MAX, so min(INT32_MAX, v) == v at w = 0, as in the reference.
     * CELL(col_valid, has_del, j_nonneg) is one cell. Rows i > B + 1 have col >= 0 and j >= 0 for every w, so they use
     * a branch-free steady-state loop with w = W - 1 (no deletion source) peeled. The first B + 1 rows use the
     * general form. */
#define CELL(COL_OK, HAS_DEL, J_OK)                                                         \
    do {                                                                                    \
        const vi32 diag = (COL_OK) ? D[w] + ((Rrow[w] != t) & mm_cost) : INFv;              \
        const vi32 dele = (HAS_DEL) ? D[w + 1] + del_cost : INFv;                           \
        const vi32 is_del = dele < diag;                    /* ties to DIAG */              \
        vi32 v3 = vsel(is_del, dele, diag);                                                 \
        vi32 op = is_del & OP_DEL;                                                          \
        const int32_t ramp = w * c_ins;                                                     \
        const vi32 v = v3 - ramp;                                                           \
        run = vsel(v < run, v, run);                                                        \
        const vi32 best = run + ramp;                                                       \
        const vi32 is_ins = best < v3;                      /* ties keep DIAG/DEL */        \
        v3 = vsel(is_ins, best, v3);                                                        \
        op = vsel(is_ins, zero + OP_INS, op);                                               \
        const vi32 masked = (J_OK) ? ((zero + (i + w - B)) > Lv) : (zero - 1);              \
        N[w] = vsel(masked, INFv, v3);                                                      \
        P[w] = __builtin_convertvector(op, vi8);                                            \
    } while (0)
    const vi32 RUN0 = zero + INT32_MAX;
    for (int32_t i = 1; i <= T; i++) {
        const int32_t t = g->tpl[i - 1];
        const int32_t del_cost = c_del + (t >= 0 ? c_x : 0);
        const int32_t mm_cost = t >= 0 ? c_mm : 0;     /* frame position: substitution cost 0 */
        vi8 *P = s->ptr + (size_t)i * (size_t)W;
        const vi32 *Rrow = s->R + (i - 1);             /* Rrow[w] == R[col], col = i - 1 + (w - B); col < width always */
        vi32 run = RUN0;
        int32_t w = 0;
        if (i - 1 - B >= 0) {
            for (; w < W - 1; w++) CELL(1, 1, 1);
            CELL(1, 0, 1);
        } else {
            for (; w < W; w++) {
                const int col_ok = i - 1 + w - B >= 0;
                const int has_del = w < W - 1;
                const int j_ok = i + w - B >= 0;
                CELL(col_ok, has_del, j_ok);
            }
        }
        vi32 *tmp = D;
        D = N;
        N = tmp;
    }
#undef CELL
    if (D != s->D) memcpy(s->D, D, sizeof(vi32) * (size_t)W);
}

/* Section 4 (final cost), 5 and 6 for one lane of the current group. */
static int finish_lane(const geom_t *g, scratch_t *s, int lane, const uint8_t *r, const uint8_t *q, int32_t Li, uint8_t *out_bases,
                       uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins, int64_t *out_del, int64_t *out_mm, int64_t *out_cost,
                       int16_t *out_rpos, timers_t *tm) {
    double t0 = tm ? now_s() : 0.0;
    const int32_t T = g->T, B = g->B, W = g->W;
    const int32_t w_end = (Li - T) + B;
    int32_t final = INF_COST;
    const int inside = w_end >= 0 && w_end < W;
    if (inside) final = s->D[w_end][lane];
    const int ok = inside && final < INF_COST;
    uint8_t *tb = s->tb, *bad = s->bad, *hit = s->hit;
    memset(tb, 4, (size_t)T);
    memset(bad, 0, (size_t)T);
    memset(hit, 0, (size_t)g->n_segments);
    if (out_rpos) for (int32_t p = 0; p < T; p++) out_rpos[p] = -1;   /* -1: deleted, or the read is not ok */
    int64_t n_ins = 0, n_del = 0;
    if (ok) {
        int32_t i = T, d = Li - T;
        const int64_t max_steps = 4 * ((int64_t)T + B) + 8;
        int64_t steps = 0;
        while (i > 0 || d > 0) {
            if (++steps > max_steps) return VNX_E_TRACE;
            const int32_t w = d + B;
            if (w < 0 || w >= W || i < 0 || i > T) return VNX_E_TRACE;
            const int8_t op = s->ptr[(size_t)i * (size_t)W + (size_t)w][lane];
            if (op == OP_DIAG) {
                if (i < 1) return VNX_E_TRACE;
                const int32_t j = i - 1 + d;
                if (j < 0 || j >= g->width) return VNX_E_TRACE;
                const int32_t rv = j < Li ? (int32_t)r[j] : 5;
                const int32_t qv = (q != NULL && j < Li) ? (int32_t)q[j] : 99;
                tb[i - 1] = (uint8_t)(rv < 4 ? rv : 4);
                if (out_rpos) out_rpos[i - 1] = (int16_t)j;               /* j < width <= 8192 + 64 + 2 */
                bad[i - 1] = (uint8_t)(rv > 3 || qv < g->min_q);
                i -= 1;
            } else if (op == OP_DEL) {
                if (i < 1) return VNX_E_TRACE;
                bad[i - 1] = 1;
                n_del += 1;
                const int32_t sg = g->seg_of[i - 1];
                mark(hit, g, sg >= 0 ? sg : g->prev_seg[i - 1], sg >= 0 ? sg : g->next_seg[i - 1]);
                i -= 1;
                d += 1;
            } else if (op == OP_INS) {
                n_ins += 1;
                const int32_t left = clip32(i - 1, 0, T - 1);
                const int32_t right = clip32(i, 0, T - 1);
                const int32_t sl = i >= 1 ? g->seg_of[left] : -1;
                const int32_t sr = i < T ? g->seg_of[right] : -1;
                const int32_t lo = sl >= 0 ? sl : (sr >= 0 ? sr : g->prev_seg[left]);
                const int32_t hi = sr >= 0 ? sr : (sl >= 0 ? sl : g->next_seg[right]);
                mark(hit, g, lo < hi ? lo : hi, lo < hi ? hi : lo);
                d -= 1;
            } else {
                return VNX_E_TRACE;
            }
        }
    }
    double t1 = 0.0;
    if (tm) {
        t1 = now_s();
        tm->traceback += t1 - t0;
    }
    int64_t mm = 0;
    for (int32_t f = 0; f < g->frame_nt; f++) {
        const int32_t p = g->frame_pos[f];
        out_bases[f] = tb[p];
        out_erased[f] = ok ? (uint8_t)(bad[p] || hit[g->seg_frame[f]]) : 1;
    }
    if (ok) {
        for (int32_t p = 0; p < T; p++)
            if (g->tpl[p] >= 0 && tb[p] != (uint8_t)g->tpl[p]) mm += 1;
    }
    *out_ok = (uint8_t)ok;
    *out_ins = ok ? n_ins : 0;
    *out_del = ok ? n_del : 0;
    *out_mm = mm;
    *out_cost = (int64_t)final;
    if (tm) tm->projection += now_s() - t1;
    return VNX_OK;
}

static int validate_geometry(const geom_t *g) {
    if (g->B < 0 || g->B > MAX_BAND || g->T < 1 || g->T > MAX_TEMPLATE) return VNX_E_DOMAIN;
    if (g->frame_nt < 1 || g->frame_nt > g->T || g->n_segments < 1 || g->n_segments > g->frame_nt) return VNX_E_DOMAIN;
    if (g->c_mm < 0 || g->c_mm > MAX_COST || g->c_ins < 0 || g->c_ins > MAX_COST || g->c_del < 0 || g->c_del > MAX_COST ||
        g->c_x < 0 || g->c_x > MAX_COST || g->guard < 0 || g->guard > MAX_GUARD)
        return VNX_E_DOMAIN;
    for (int32_t p = 0; p < g->T; p++) {
        if (g->tpl[p] < -1 || g->tpl[p] > 255) return VNX_E_ARG;
        if (g->seg_of[p] < -1 || g->seg_of[p] >= g->n_segments) return VNX_E_ARG;
        if (g->prev_seg[p] < 0 || g->prev_seg[p] >= g->n_segments) return VNX_E_ARG;
        if (g->next_seg[p] < 0 || g->next_seg[p] >= g->n_segments) return VNX_E_ARG;
    }
    for (int32_t f = 0; f < g->frame_nt; f++) {
        if (g->frame_pos[f] < 0 || g->frame_pos[f] >= g->T) return VNX_E_ARG;
        if (g->seg_frame[f] < 0 || g->seg_frame[f] >= g->n_segments) return VNX_E_ARG;
    }
    return VNX_OK;
}

static void *alloc_vec(size_t count) {
    /* aligned_alloc needs a size that is a multiple of the alignment; vi32 is 4 * LANES bytes */
    if (count == 0 || count > SIZE_MAX / sizeof(vi32)) return NULL;
    return aligned_alloc(sizeof(vi32), count * sizeof(vi32));
}

/*
 * Align n usable reads. codes/quals are flat buffers indexed by offsets[0..n] (offsets[0] == 0, non-decreasing,
 * offsets[n] == total_len). quals may be NULL; has_qual[k] (NULL = every read) says whether read k has qualities.
 * Outputs: bases/erased (n * frame_nt uint8), ok (n uint8), ins/del/mm/cost (n int64).
 */
static int align_batch(int64_t n, const uint8_t *codes, const int64_t *offsets, int64_t total_len, const uint8_t *quals,
                       const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of, const int32_t *prev_seg,
                       const int32_t *next_seg, int32_t n_segments, int32_t frame_nt, const int32_t *frame_pos,
                       const int32_t *seg_frame, int32_t band, int32_t c_mm, int32_t c_ins, int32_t c_del, int32_t c_x, int32_t guard,
                       int32_t min_q, uint8_t *out_bases, uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins, int64_t *out_del,
                       int64_t *out_mm, int64_t *out_cost, int16_t *out_rpos, timers_t *tm) {
    if (n < 0 || total_len < 0) return VNX_E_ARG;
    if (n == 0) return VNX_OK;
    if (!offsets || !tpl || !seg_of || !prev_seg || !next_seg || !frame_pos || !seg_frame || !out_bases || !out_erased ||
        !out_ok || !out_ins || !out_del || !out_mm || !out_cost)
        return VNX_E_ARG;
    if (total_len > 0 && !codes) return VNX_E_ARG;
    geom_t g = {.T = T, .B = band, .W = 2 * band + 1, .width = T + band + 2, .n_segments = n_segments, .frame_nt = frame_nt,
                .tpl = tpl, .seg_of = seg_of, .prev_seg = prev_seg, .next_seg = next_seg, .frame_pos = frame_pos,
                .seg_frame = seg_frame, .c_mm = c_mm, .c_ins = c_ins, .c_del = c_del, .c_x = c_x, .guard = guard,
                .min_q = min_q};
    int rc = validate_geometry(&g);
    if (rc != VNX_OK) return rc;
    if (offsets[0] != 0 || offsets[n] != total_len) return VNX_E_READ;
    for (int64_t k = 0; k < n; k++) {
        const int64_t L = offsets[k + 1] - offsets[k];
        if (L < 0 || offsets[k + 1] > total_len || L < (int64_t)T - band || L > (int64_t)T + band) return VNX_E_READ;
        if (has_qual && has_qual[k] && L > 0 && !quals) return VNX_E_ARG;   /* an empty read reads no quality */
    }
    if ((uint64_t)n > SIZE_MAX / (size_t)frame_nt) return VNX_E_ARG;   /* output row indexing cannot overflow */
    if (out_rpos && (uint64_t)n > SIZE_MAX / (size_t)T) return VNX_E_ARG;

    /* scratch: ptr (T + 1) * W * LANES <= 8193 * 129 * 4 bytes (4.2 MB); R (B + width) * 16 bytes; the rest O(T) */
    scratch_t s = {0};
    const size_t rlen = (size_t)band + (size_t)g.width;
    s.D = (vi32 *)alloc_vec((size_t)g.W + 1);
    s.N = (vi32 *)alloc_vec((size_t)g.W + 1);
    s.R = (vi32 *)alloc_vec(rlen);
    s.ptr = (vi8 *)malloc(sizeof(vi8) * (size_t)(T + 1) * (size_t)g.W);
    s.tb = (uint8_t *)malloc((size_t)T);
    s.bad = (uint8_t *)malloc((size_t)T);
    s.hit = (uint8_t *)malloc((size_t)n_segments);
    if (!s.D || !s.N || !s.R || !s.ptr || !s.tb || !s.bad || !s.hit) {
        rc = VNX_E_NOMEM;
        goto done;
    }
    for (int64_t k0 = 0; k0 < n; k0 += LANES) {
        const int64_t take = (n - k0) < LANES ? (n - k0) : LANES;
        double tp = tm ? now_s() : 0.0;
        vi32 Lv;
        /* lane l holds read k0 + l; padding lanes repeat read k0 (results discarded) */
        for (int l = 0; l < LANES; l++) {
            const int64_t k = k0 + (l < take ? l : 0);
            const int64_t a = offsets[k];
            const int32_t Li = (int32_t)(offsets[k + 1] - a);
            Lv[l] = Li;
            for (size_t c = 0; c < rlen; c++) {
                const int64_t col = (int64_t)c - band;
                s.R[c][l] = (col >= 0 && col < Li) ? (int32_t)codes[a + col] : 5;
            }
        }
        if (tm) {
            const double t = now_s();
            tm->pack += t - tp;
            tp = t;
        }
        dp_group(&g, &s, &Lv);
        if (tm) tm->dp += now_s() - tp;
        for (int l = 0; l < take; l++) {
            const int64_t k = k0 + l;
            const int64_t a = offsets[k];
            const uint8_t *q = (quals && (!has_qual || has_qual[k])) ? quals + a : NULL;
            const size_t row = (size_t)k * (size_t)frame_nt;
            rc = finish_lane(&g, &s, l, codes ? codes + a : NULL, q, Lv[l], out_bases + row, out_erased + row, out_ok + k,
                             out_ins + k, out_del + k, out_mm + k, out_cost + k,
                             out_rpos ? out_rpos + (size_t)k * (size_t)T : NULL, tm);
            if (rc != VNX_OK) goto done;
        }
    }
done:
    free(s.D);
    free(s.N);
    free(s.R);
    free(s.ptr);
    free(s.tb);
    free(s.bad);
    free(s.hit);
    return rc;
}

int vnx_align_batch(int64_t n, const uint8_t *codes, const int64_t *offsets, int64_t total_len, const uint8_t *quals,
                    const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of, const int32_t *prev_seg,
                    const int32_t *next_seg, int32_t n_segments, int32_t frame_nt, const int32_t *frame_pos,
                    const int32_t *seg_frame, int32_t band, int32_t c_mm, int32_t c_ins, int32_t c_del, int32_t c_x, int32_t guard,
                    int32_t min_q, uint8_t *out_bases, uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins, int64_t *out_del,
                    int64_t *out_mm, int64_t *out_cost) {
    return align_batch(n, codes, offsets, total_len, quals, has_qual, T, tpl, seg_of, prev_seg, next_seg, n_segments, frame_nt,
                       frame_pos, seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, out_bases, out_erased, out_ok, out_ins,
                       out_del, out_mm, out_cost, NULL, NULL);
}

/* Same as vnx_align_batch, and also writes out_readpos (n * T int16): for every template position the read index it
 * was aligned to on the traceback path (DIAG steps), or -1 if it was deleted or the read is not ok. */
int vnx_align_batch_path(int64_t n, const uint8_t *codes, const int64_t *offsets, int64_t total_len, const uint8_t *quals,
                         const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of, const int32_t *prev_seg,
                         const int32_t *next_seg, int32_t n_segments, int32_t frame_nt, const int32_t *frame_pos,
                         const int32_t *seg_frame, int32_t band, int32_t c_mm, int32_t c_ins, int32_t c_del, int32_t c_x, int32_t guard,
                         int32_t min_q, uint8_t *out_bases, uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins, int64_t *out_del,
                         int64_t *out_mm, int64_t *out_cost, int16_t *out_readpos) {
    if (!out_readpos && n > 0) return VNX_E_ARG;
    return align_batch(n, codes, offsets, total_len, quals, has_qual, T, tpl, seg_of, prev_seg, next_seg, n_segments, frame_nt,
                       frame_pos, seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, out_bases, out_erased, out_ok, out_ins,
                       out_del, out_mm, out_cost, out_readpos, NULL);
}

/* Same as vnx_align_batch, and adds stage times in seconds: timings[0..3] += pack, DP, traceback, projection.
 * For benchmarks only; the outputs are identical. */
int vnx_align_batch_profiled(int64_t n, const uint8_t *codes, const int64_t *offsets, int64_t total_len, const uint8_t *quals,
                             const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of, const int32_t *prev_seg,
                             const int32_t *next_seg, int32_t n_segments, int32_t frame_nt, const int32_t *frame_pos,
                             const int32_t *seg_frame, int32_t band, int32_t c_mm, int32_t c_ins, int32_t c_del, int32_t c_x,
                             int32_t guard, int32_t min_q, uint8_t *out_bases, uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins,
                             int64_t *out_del, int64_t *out_mm, int64_t *out_cost, double *timings) {
    if (!timings) return VNX_E_ARG;
    timers_t tm = {0.0, 0.0, 0.0, 0.0};
    const int rc = align_batch(n, codes, offsets, total_len, quals, has_qual, T, tpl, seg_of, prev_seg, next_seg, n_segments,
                               frame_nt, frame_pos, seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, out_bases, out_erased,
                               out_ok, out_ins, out_del, out_mm, out_cost, NULL, &tm);
    timings[0] += tm.pack;
    timings[1] += tm.dp;
    timings[2] += tm.traceback;
    timings[3] += tm.projection;
    return rc;
}
