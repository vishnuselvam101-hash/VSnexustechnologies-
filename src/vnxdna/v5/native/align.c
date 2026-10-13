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
 * SIMD levels (ABI 3): the per-level code lives in align_dp.inc and is compiled three times: LANES 4 (portable,
 * 128-bit vectors, no target attribute), LANES 8 with target("avx2") and LANES 16 with target("avx512f,avx512bw")
 * (x86 only). The recurrence of a lane is the same at every width, so every level returns byte-identical results.
 * The level is an explicit argument of the *_lv entry points (no global state). A level this CPU/OS cannot run is
 * refused with VNX_E_UNSUPPORTED before any of its code executes (cpuid + xgetbv, as in v6/native/rs.c). The ABI-2
 * entry points remain and run LANES 4.
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

#if defined(__x86_64__) || defined(__i386__)
#define VNX_X86 1
#include <cpuid.h>
#else
#define VNX_X86 0
#endif

/* 2: adds vnx_align_batch_path (read position of every template base); 3: SIMD levels (*_lv, vnx_align_levels) */
#define VNX_ALIGN_ABI 4

#define INF_COST ((int32_t)(1 << 28))
#define OP_DIAG 0
#define OP_DEL 1
#define OP_INS 2

#define MAX_BAND 64
#define MAX_TEMPLATE 8192
#define MAX_COST 65536
#define MAX_GUARD 1024

/* lane-wise select; m is a comparison result (all ones or all zeros per lane) */
#define vsel(m, a, b) ((((a)) & (m)) | (((b)) & ~(m)))

enum {
    VNX_OK = 0,
    VNX_E_ARG = -1,       /* null pointer or inconsistent argument */
    VNX_E_DOMAIN = -2,    /* outside the native domain (contract section 9) */
    VNX_E_READ = -3,      /* offsets not monotone, or a read outside the band */
    VNX_E_NOMEM = -4,
    VNX_E_TRACE = -5,     /* traceback left the band or exceeded its step bound */
    VNX_E_UNSUPPORTED = -6,   /* SIMD level not compiled in or not supported by this CPU/OS */
};

int vnx_align_abi_version(void) { return VNX_ALIGN_ABI; }
int vnx_align_lanes(void) { return 4; }   /* lanes of the ABI-2 entry points */

typedef struct {
    int32_t T, B, W, width, n_segments, frame_nt;
    const int16_t *tpl;
    const int32_t *seg_of, *prev_seg, *next_seg, *frame_pos, *seg_frame;
    int32_t c_mm, c_ins, c_del, c_x, guard, min_q;
} geom_t;

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

/* ------------------------------------------------------------------------------------------- SIMD levels */
#define VNX_TARGET
#define LANES 4
#define SFX l4
#include "align_dp.inc"
#undef SFX
#undef LANES
#undef VNX_TARGET

#if VNX_X86
#define VNX_TARGET __attribute__((target("avx2")))
#define LANES 8
#define SFX l8
#include "align_dp.inc"
#undef SFX
#undef LANES
#undef VNX_TARGET

#define VNX_TARGET __attribute__((target("avx512f,avx512bw")))
#define LANES 16
#define SFX l16
#include "align_dp.inc"
#undef SFX
#undef LANES
#undef VNX_TARGET

static uint64_t read_xcr0(void) {
    uint32_t eax, edx;
    __asm__ volatile(".byte 0x0f, 0x01, 0xd0" /* xgetbv */ : "=a"(eax), "=d"(edx) : "c"(0));
    return ((uint64_t)edx << 32) | eax;
}
#endif

static int detect_levels(void) {
    int levels = 1 << 4;
#if VNX_X86
    unsigned a, b, c, d;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return levels;
    const int osxsave = (c >> 27) & 1, avx = (c >> 28) & 1;
    if (!osxsave || !avx) return levels;
    const uint64_t xcr0 = read_xcr0();
    if ((xcr0 & 0x6u) != 0x6u) return levels;   /* XMM and YMM state enabled by the OS */
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return levels;
    const int avx2 = (b >> 5) & 1, avx512f = (b >> 16) & 1, avx512bw = (b >> 30) & 1;
    if (avx2) levels |= 1 << 8;
    /* XCR0 bits 5-7: opmask, upper ZMM0-15, ZMM16-31 */
    if (avx2 && avx512f && avx512bw && (xcr0 & 0xE6u) == 0xE6u) levels |= 1 << 16;
#endif
    return levels;
}

/* Bitmask of usable levels, bit (1 << LANES): 4 always; 8 with AVX2 (+ OS YMM state); 16 with AVX-512F/BW (+ OS
 * opmask/ZMM state). Computed on every call (cpuid is cheap next to an alignment batch); no global state.
 * Test builds only: -DVNX_ALIGN_LEVEL_MASK=<mask> removes levels from the detected set, so the refusal of a level the
 * CPU lacks is testable on any host. Release builds never define it. */
int vnx_align_levels(void) {
#ifdef VNX_ALIGN_LEVEL_MASK
    return detect_levels() & ((VNX_ALIGN_LEVEL_MASK) | (1 << 4));
#else
    return detect_levels();
#endif
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

/*
 * Align n usable reads. codes/quals are flat buffers indexed by offsets[0..n] (offsets[0] == 0, non-decreasing,
 * offsets[n] == total_len). quals may be NULL; has_qual[k] (NULL = every read) says whether read k has qualities.
 * ABI 4: with lens != NULL, read k is codes[offsets[k] .. offsets[k] + lens[k]) instead (any n reads of a larger
 * buffer of total_len entries, in any order; offsets then has n entries and need not be monotone or start at 0).
 * Outputs: bases/erased (n * frame_nt uint8), ok (n uint8), ins/del/mm/cost (n int64).
 */
static int align_batch(int64_t n, const uint8_t *codes, const int64_t *offsets, const int64_t *lens, int64_t total_len,
                       const uint8_t *quals, const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of, const int32_t *prev_seg,
                       const int32_t *next_seg, int32_t n_segments, int32_t frame_nt, const int32_t *frame_pos,
                       const int32_t *seg_frame, int32_t band, int32_t c_mm, int32_t c_ins, int32_t c_del, int32_t c_x, int32_t guard,
                       int32_t min_q, uint8_t *out_bases, uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins, int64_t *out_del,
                       int64_t *out_mm, int64_t *out_cost, int16_t *out_rpos, timers_t *tm, int32_t lanes) {
    if (lanes != 4 && lanes != 8 && lanes != 16) return VNX_E_UNSUPPORTED;
    if (lanes != 4 && !(vnx_align_levels() & (1 << lanes))) return VNX_E_UNSUPPORTED;
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
    if (!lens && (offsets[0] != 0 || offsets[n] != total_len)) return VNX_E_READ;
    for (int64_t k = 0; k < n; k++) {
        const int64_t a = offsets[k];
        const int64_t L = lens ? lens[k] : offsets[k + 1] - a;
        if (L < 0 || a < 0 || a > total_len - L || L < (int64_t)T - band || L > (int64_t)T + band) return VNX_E_READ;
        if (has_qual && has_qual[k] && L > 0 && !quals) return VNX_E_ARG;   /* an empty read reads no quality */
    }
    if ((uint64_t)n > SIZE_MAX / (size_t)frame_nt) return VNX_E_ARG;   /* output row indexing cannot overflow */
    if (out_rpos && (uint64_t)n > SIZE_MAX / (size_t)T) return VNX_E_ARG;

    for (;;) {
        switch (lanes) {
        case 4: rc = align_groups_l4(&g, n, codes, offsets, lens, quals, has_qual, out_bases, out_erased, out_ok, out_ins,
                                     out_del, out_mm, out_cost, out_rpos, tm); break;
#if VNX_X86
        case 8: rc = align_groups_l8(&g, n, codes, offsets, lens, quals, has_qual, out_bases, out_erased, out_ok, out_ins,
                                     out_del, out_mm, out_cost, out_rpos, tm); break;
        case 16: rc = align_groups_l16(&g, n, codes, offsets, lens, quals, has_qual, out_bases, out_erased, out_ok, out_ins,
                                       out_del, out_mm, out_cost, out_rpos, tm); break;
#endif
        default: return VNX_E_UNSUPPORTED;
        }
        /* scratch grows with LANES (16.9 MB at LANES 16, T 8192): on allocation failure retry one level narrower; the
         * result is identical at every level. The narrower level is checked like any request (never run a level this
         * CPU/OS lacks). Callers report the level they requested; only this out-of-memory retry runs a narrower one. */
        if (rc != VNX_E_NOMEM || lanes == 4) return rc;
        lanes /= 2;
        if (!(vnx_align_levels() & (1 << lanes))) return rc;
    }
}

/* Public entry points. The parameter list is shared; the *_lv variants add the SIMD level (lanes: 4, 8 or 16) as the
 * last argument and return VNX_E_UNSUPPORTED for a level this build/CPU/OS cannot run. Without _lv: LANES 4 (ABI 2). */
#define VNX_PARAMS                                                                                                          \
    int64_t n, const uint8_t *codes, const int64_t *offsets, int64_t total_len, const uint8_t *quals,                      \
        const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of, const int32_t *prev_seg,            \
        const int32_t *next_seg, int32_t n_segments, int32_t frame_nt, const int32_t *frame_pos, const int32_t *seg_frame, \
        int32_t band, int32_t c_mm, int32_t c_ins, int32_t c_del, int32_t c_x, int32_t guard, int32_t min_q,              \
        uint8_t *out_bases, uint8_t *out_erased, uint8_t *out_ok, int64_t *out_ins, int64_t *out_del, int64_t *out_mm,     \
        int64_t *out_cost
#define VNX_ARGS                                                                                                            \
    n, codes, offsets, total_len, quals, has_qual, T, tpl, seg_of, prev_seg, next_seg, n_segments, frame_nt, frame_pos,    \
        seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, out_bases, out_erased, out_ok, out_ins, out_del, out_mm,   \
        out_cost
/* the same arguments for align_batch, which takes the ABI-4 lens pointer (NULL: contiguous offsets[0..n]) */
#define VNX_ARGS_L                                                                                                          \
    n, codes, offsets, NULL, total_len, quals, has_qual, T, tpl, seg_of, prev_seg, next_seg, n_segments, frame_nt,         \
        frame_pos, seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, out_bases, out_erased, out_ok, out_ins, out_del, \
        out_mm, out_cost

int vnx_align_batch_lv(VNX_PARAMS, int32_t lanes) { return align_batch(VNX_ARGS_L, NULL, NULL, lanes); }

int vnx_align_batch(VNX_PARAMS) { return align_batch(VNX_ARGS_L, NULL, NULL, 4); }

/* ABI 4 (V13): align n selected reads of a flat buffer in place. Read k is codes[starts[k] .. starts[k] + lens[k])
 * (quals likewise); total_len is the size of the buffers, every read must lie inside them. Same outputs as
 * vnx_align_batch_lv on the same reads copied into a contiguous buffer. */
int vnx_align_batch_sel_lv(int64_t n, const uint8_t *codes, const int64_t *starts, const int64_t *lens, int64_t total_len,
                           const uint8_t *quals, const uint8_t *has_qual, int32_t T, const int16_t *tpl, const int32_t *seg_of,
                           const int32_t *prev_seg, const int32_t *next_seg, int32_t n_segments, int32_t frame_nt,
                           const int32_t *frame_pos, const int32_t *seg_frame, int32_t band, int32_t c_mm, int32_t c_ins,
                           int32_t c_del, int32_t c_x, int32_t guard, int32_t min_q, uint8_t *out_bases, uint8_t *out_erased,
                           uint8_t *out_ok, int64_t *out_ins, int64_t *out_del, int64_t *out_mm, int64_t *out_cost,
                           int32_t lanes) {
    if (!lens && n > 0) return VNX_E_ARG;
    return align_batch(n, codes, starts, lens, total_len, quals, has_qual, T, tpl, seg_of, prev_seg, next_seg, n_segments,
                       frame_nt, frame_pos, seg_frame, band, c_mm, c_ins, c_del, c_x, guard, min_q, out_bases, out_erased,
                       out_ok, out_ins, out_del, out_mm, out_cost, NULL, NULL, lanes);
}

/* Same as vnx_align_batch, and also writes out_readpos (n * T int16): for every template position the read index it
 * was aligned to on the traceback path (DIAG steps), or -1 if it was deleted or the read is not ok. */
int vnx_align_batch_path_lv(VNX_PARAMS, int16_t *out_readpos, int32_t lanes) {
    if (!out_readpos && n > 0) return VNX_E_ARG;
    return align_batch(VNX_ARGS_L, out_readpos, NULL, lanes);
}

int vnx_align_batch_path(VNX_PARAMS, int16_t *out_readpos) { return vnx_align_batch_path_lv(VNX_ARGS, out_readpos, 4); }

/* Same as vnx_align_batch, and adds stage times in seconds: timings[0..3] += pack, DP, traceback, projection.
 * For benchmarks only; the outputs are identical. */
int vnx_align_batch_profiled_lv(VNX_PARAMS, double *timings, int32_t lanes) {
    if (!timings) return VNX_E_ARG;
    timers_t tm = {0.0, 0.0, 0.0, 0.0};
    const int rc = align_batch(VNX_ARGS_L, NULL, &tm, lanes);
    timings[0] += tm.pack;
    timings[1] += tm.dp;
    timings[2] += tm.traceback;
    timings[3] += tm.projection;
    return rc;
}

int vnx_align_batch_profiled(VNX_PARAMS, double *timings) { return vnx_align_batch_profiled_lv(VNX_ARGS, timings, 4); }
