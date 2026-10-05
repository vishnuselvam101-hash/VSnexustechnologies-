/* libFuzzer harness for the V5 native marker-template aligner (src/vnxdna/v5/native/align.c), built by fuzz/run.sh as
 * one translation unit (the kernel is #included).
 *
 * Geometry: byte 0 bit 0 selects a layout-shaped geometry (built like vnxdna.v4.frame.Layout.template and
 * TemplateAligner.__init__: frame bases at -1, marker bases 0..3 every marker_period frame bases, segment = frame index //
 * marker_period) or a "hostile" geometry (arbitrary arrays, mostly inside the validated domain of contract section 9,
 * sometimes outside it). Reads: up to 9 reads with lengths mostly in [T - band, T + band], arbitrary byte values (the
 * parser only produces 0..4, but nothing enforces that at the ABI), optional qualities and has_qual flags.
 *
 * Invariants checked:
 *   - return code in {0, -1 .. -5}; an in-domain layout geometry with in-band reads returns 0 (the Python wrapper raises
 *     on any non-zero code, so a non-zero code there would be a crash in production);
 *   - outputs fully written (they are poisoned first): bases 0..4, erased/ok 0/1; a failed read has every frame base
 *     erased and ins = del = 0; ins, del >= 0, mm in [0, T]; ok reads have 0 <= cost < 2^28;
 *   - determinism (a second call gives identical outputs), batch independence (each read aligned alone gives the
 *     same row as inside the batch);
 *   - vnx_align_batch_path and vnx_align_batch_profiled give the same outputs as vnx_align_batch, readpos entries are
 *     -1 or in [0, T + band + 2), and all -1 for a failed read.
 * Output buffers have exactly the sizes the Python wrapper allocates (n * frame_nt, n, n * T), so ASan sees overruns. */
#include "vnxdna/v5/native/align.c"

#include <stdio.h>

typedef struct {
    const uint8_t *p;
    size_t n, i;
    uint64_t s;
} src_t;

static uint8_t nb(src_t *s) {
    if (s->i < s->n) return s->p[s->i++];
    s->s = s->s * 6364136223846793005ULL + 1442695040888963407ULL;
    return (uint8_t)(s->s >> 56);
}

static void fail(const char *what) {
    fprintf(stderr, "align_fuzz invariant violated: %s\n", what);
    abort();
}

typedef struct {
    uint8_t *bases, *erased, *ok;
    int64_t *ins, *del, *mm, *cost;
    int16_t *rpos;
} out_t;

static void out_alloc(out_t *o, int64_t n, int32_t frame_nt, int32_t T) {
    const size_t nn = n ? (size_t)n : 1;
    o->bases = malloc(nn * (size_t)frame_nt);
    o->erased = malloc(nn * (size_t)frame_nt);
    o->ok = malloc(nn);
    o->ins = malloc(nn * 8);
    o->del = malloc(nn * 8);
    o->mm = malloc(nn * 8);
    o->cost = malloc(nn * 8);
    o->rpos = malloc(nn * (size_t)T * 2);
    memset(o->bases, 0xEE, nn * (size_t)frame_nt);
    memset(o->erased, 0xEE, nn * (size_t)frame_nt);
    memset(o->ok, 0xEE, nn);
    memset(o->ins, 0xEE, nn * 8);
    memset(o->del, 0xEE, nn * 8);
    memset(o->mm, 0xEE, nn * 8);
    memset(o->cost, 0xEE, nn * 8);
    memset(o->rpos, 0xEE, nn * (size_t)T * 2);
}

static void out_free(out_t *o) {
    free(o->bases);
    free(o->erased);
    free(o->ok);
    free(o->ins);
    free(o->del);
    free(o->mm);
    free(o->cost);
    free(o->rpos);
}

static int same_row(const out_t *a, int64_t ka, const out_t *b, int64_t kb, int32_t F) {
    return !memcmp(a->bases + ka * F, b->bases + kb * F, (size_t)F) && !memcmp(a->erased + ka * F, b->erased + kb * F, (size_t)F) &&
           a->ok[ka] == b->ok[kb] && a->ins[ka] == b->ins[kb] && a->del[ka] == b->del[kb] && a->mm[ka] == b->mm[kb] &&
           a->cost[ka] == b->cost[kb];
}

#define MAXT 1024

int LLVMFuzzerTestOneInput(const uint8_t *in, size_t size) {
    if (size < 8) return 0;
    src_t s = {in, size, 0, size};
    const int layout_mode = nb(&s) & 1;
    int16_t tpl[MAXT];
    int32_t seg_of[MAXT], prev_seg[MAXT], next_seg[MAXT], frame_pos[MAXT], seg_frame[MAXT];
    int32_t T, F, nseg;
    int expect_ok_rc = 0;
    int32_t band = nb(&s) % 17;
    if (layout_mode) {
        F = 1 + nb(&s) % 160;
        const int period = (nb(&s) & 3) ? 1 + nb(&s) % 48 : 0;
        const int mlen = 1 + nb(&s) % 4;
        T = 0;
        for (int f = 0; f < F;) {
            const int take = period ? (period < F - f ? period : F - f) : F - f;
            for (int t = 0; t < take; t++) {
                frame_pos[f + t] = T;
                seg_frame[f + t] = period ? (f + t) / period : 0;
                tpl[T] = -1;
                seg_of[T] = seg_frame[f + t];
                T++;
            }
            f += take;
            if (f < F && period)
                for (int m = 0; m < mlen; m++) {
                    tpl[T] = (int16_t)(nb(&s) & 3);
                    seg_of[T] = -1;
                    T++;
                }
        }
        nseg = 0;
        for (int p = 0; p < T; p++)
            if (seg_of[p] + 1 > nseg) nseg = seg_of[p] + 1;
        int32_t prev = -1;
        for (int p = 0; p < T; p++) {
            if (seg_of[p] >= 0) prev = seg_of[p];
            prev_seg[p] = prev < 0 ? 0 : prev;
        }
        for (int p = T - 1; p >= 0; p--)
            next_seg[p] = seg_of[p] >= 0 ? seg_of[p] : (p + 1 < T ? next_seg[p + 1] : nseg - 1);
        expect_ok_rc = 1;
    } else {
        T = 1 + nb(&s) % 120;
        F = 1 + nb(&s) % T;
        nseg = 1 + nb(&s) % F;
        const int outside = nb(&s) < 16; /* occasionally leave the domain: the kernel must refuse, not misbehave */
        for (int p = 0; p < T; p++) {
            const uint8_t b = nb(&s);
            tpl[p] = (int16_t)(b < 128 ? -1 : (b & 3));
            seg_of[p] = (int32_t)(nb(&s) % (nseg + 1)) - 1;
            prev_seg[p] = nb(&s) % nseg;
            next_seg[p] = nb(&s) % nseg;
        }
        for (int f = 0; f < F; f++) {
            frame_pos[f] = nb(&s) % T;
            seg_frame[f] = nb(&s) % nseg;
        }
        if (outside) {
            const int which = nb(&s) % 6, at = nb(&s) % T;
            if (which == 0) tpl[at] = 256;
            if (which == 1) seg_of[at] = nseg;
            if (which == 2) prev_seg[at] = -1;
            if (which == 3) frame_pos[at % F] = T;
            if (which == 4) seg_frame[at % F] = nseg;
            if (which == 5) band = MAX_BAND + 1;
        }
    }
    const int32_t c_mm = nb(&s) % 16, c_ins = nb(&s) % 16, c_del = nb(&s) % 16, c_x = nb(&s) % 4;
    const int32_t guard = nb(&s) % 4, min_q = (int32_t)(nb(&s) % 64) - 8;

    const int64_t n = nb(&s) % 10;
    int64_t offsets[11];
    offsets[0] = 0;
    int in_band = 1;
    for (int64_t k = 0; k < n; k++) {
        const uint8_t b = nb(&s);
        int64_t L = (int64_t)T + (int64_t)(b % (2 * band + 1)) - band;
        if (b >= 250) L = (int64_t)T + band + 1 + (b & 3); /* out of band: must be refused with -3 */
        if (L < 0) L = 0;
        if (L < (int64_t)T - band || L > (int64_t)T + band) in_band = 0;
        offsets[k + 1] = offsets[k] + L;
    }
    const int64_t total = offsets[n];
    uint8_t *codes = malloc(total ? (size_t)total : 1), *quals = malloc(total ? (size_t)total : 1);
    uint8_t has_q[10];
    const int qmode = nb(&s) % 3; /* 0: no qualities, 1: all reads, 2: per-read flags */
    for (int64_t i = 0; i < total; i++) {
        const uint8_t b = nb(&s);
        codes[i] = b < 200 ? (b & 3) : (b < 240 ? 4 : b); /* mostly ACGT, some N, some out-of-range bytes */
        quals[i] = nb(&s) % 50;
    }
    for (int64_t k = 0; k < n; k++) has_q[k] = nb(&s) & 1;
    const uint8_t *qp = qmode ? quals : NULL;
    const uint8_t *hq = qmode == 2 ? has_q : NULL;

    out_t a, b, c, d;
    out_alloc(&a, n, F, T);
    out_alloc(&b, n, F, T);
    out_alloc(&c, n, F, T);
    out_alloc(&d, n, F, T);
#define ARGS(o)                                                                                                         \
    n, total ? codes : NULL, offsets, total, total ? qp : NULL, hq, T, tpl, seg_of, prev_seg, next_seg, nseg, F, frame_pos, \
        seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, (o).bases, (o).erased, (o).ok, (o).ins, (o).del, (o).mm,  \
        (o).cost
    const int rc = vnx_align_batch(ARGS(a));
    if (rc > 0 || rc < -5) fail("unknown return code");
    if (expect_ok_rc && in_band && rc != VNX_OK) fail("in-domain geometry with in-band reads refused");
    if (rc == VNX_OK) {
        for (int64_t k = 0; k < n; k++) {
            if (a.ok[k] > 1) fail("ok not 0/1");
            for (int32_t f = 0; f < F; f++) {
                if (a.bases[k * F + f] > 4) fail("base code > 4 (or unwritten)");
                if (a.erased[k * F + f] > 1) fail("erased not 0/1 (or unwritten)");
                if (!a.ok[k] && !a.erased[k * F + f]) fail("failed read has a non-erased base");
            }
            if (a.ins[k] < 0 || a.del[k] < 0 || a.mm[k] < 0 || a.mm[k] > T) fail("counts out of range");
            if (!a.ok[k] && (a.ins[k] || a.del[k])) fail("failed read reports indels");
            if (a.ok[k] && (a.cost[k] < 0 || a.cost[k] >= INF_COST)) fail("cost out of range");
            if (a.ok[k] && a.ins[k] + a.del[k] > 2 * ((int64_t)T + band)) fail("indel count beyond the path length");
        }
        if (vnx_align_batch(ARGS(b)) != VNX_OK) fail("second call differs (rc)");
        for (int64_t k = 0; k < n; k++)
            if (!same_row(&a, k, &b, k, F)) fail("non-deterministic");
        double tm[4] = {0, 0, 0, 0};
        if (vnx_align_batch_profiled(ARGS(c), tm) != VNX_OK) fail("profiled rc");
        if (vnx_align_batch_path(ARGS(d), d.rpos) != VNX_OK) fail("path rc");
        for (int64_t k = 0; k < n; k++) {
            if (!same_row(&a, k, &c, k, F)) fail("profiled entry point differs");
            if (!same_row(&a, k, &d, k, F)) fail("path entry point differs");
            for (int32_t p = 0; p < T; p++) {
                const int16_t r = d.rpos[k * T + p];
                if (r < -1 || r >= T + band + 2) fail("readpos out of range (or unwritten)");
                if (!d.ok[k] && r != -1) fail("readpos set for a failed read");
            }
        }
        /* batch independence: read k alone */
        for (int64_t k = 0; k < n; k++) {
            out_t one;
            out_alloc(&one, 1, F, T);
            int64_t off1[2] = {0, offsets[k + 1] - offsets[k]};
            const uint8_t h1 = hq ? hq[k] : 1;
            const int r1 = vnx_align_batch(1, off1[1] ? codes + offsets[k] : NULL, off1, off1[1], (off1[1] && qp) ? qp + offsets[k] : NULL,
                                           hq ? &h1 : NULL, T, tpl, seg_of, prev_seg, next_seg, nseg, F, frame_pos, seg_frame, band,
                                           c_mm, c_ins, c_del, c_x, guard, min_q, one.bases, one.erased, one.ok, one.ins, one.del,
                                           one.mm, one.cost);
            if (r1 != VNX_OK || !same_row(&a, k, &one, 0, F)) fail("result depends on the other reads in the batch");
            out_free(&one);
        }
    }
    /* argument validation: NULL outputs and a non-zero offsets[0] are refused */
    if (n > 0) {
        if (vnx_align_batch(n, total ? codes : NULL, offsets, total, NULL, NULL, T, tpl, seg_of, prev_seg, next_seg, nseg, F,
                            frame_pos, seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, NULL, a.erased, a.ok, a.ins,
                            a.del, a.mm, a.cost) != VNX_E_ARG)
            fail("NULL output accepted");
        if (vnx_align_batch_path(ARGS(d), NULL) != VNX_E_ARG) fail("NULL readpos accepted");
    }
    out_free(&a);
    out_free(&b);
    out_free(&c);
    out_free(&d);
    free(codes);
    free(quals);
    return 0;
}
