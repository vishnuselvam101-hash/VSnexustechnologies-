/*
 * VNX-DNA V6 native inner Reed-Solomon decoder (GF(2^8)/0x11D, generator 2, first consecutive root 0).
 *
 * Specification: src/vnxdna/v4/rs_fast.py (== src/vnxdna/ecc/rs_batch.py, the V3 reference). For every codeword this
 * file computes the same (corrected codeword, ok flag, errata count) as rs_fast.decode_batch, including every
 * failure decision:
 *
 *   f = number of flagged erasures; S = syndromes (S_i = c(alpha^i), i < nsym)
 *   f > nsym                                   -> fail (row unchanged)
 *   S == 0                                     -> ok, errata 0 (row unchanged)
 *   Gamma(x) = prod (1 + X_j x) over flagged j (ascending j), coefficients truncated to width = nsym + 1
 *   Berlekamp-Massey with erasures (r = f+1 .. nsym), x*B truncated to width, exactly as rs_fast
 *   feasible iff deg(Lambda) == L, 2(L - f) + f <= nsym, L <= n,
 *                #roots of Lambda among X_j^-1 == L, every flagged erasure is a root,
 *                Lambda'(X_j^-1) != 0 at every root, syndromes of the corrected word are all zero
 *   Forney: e_j = X_j * Omega(X_j^-1) / Lambda'(X_j^-1), Omega = S * Lambda mod x^nsym
 *   ok -> row ^= e, errata = #roots;  infeasible -> fail (row unchanged), errata 0
 *
 * Exactness notes (field arithmetic is exact, so these are identities, not approximations):
 *   - polynomial values at the fixed points are computed by accumulation of coefficient * power vectors (rs_fast's
 *     table gathers) or by Horner's rule; both give the same field element;
 *   - Forney values are only needed at roots (elsewhere the magnitude is forced to 0 by rs_fast);
 *   - the re-check uses linearity of the syndrome map: S(c ^ e) = S(c) ^ S(e), e non-zero only at roots;
 *   - after the first failed condition the remaining conditions cannot change the outcome (all are AND-ed), so the
 *     row stops early.
 *
 * Dispatch: one code path, three "multiply-accumulate" kernels dst ^= c * src (GF) (plus a register-accumulating
 * syndrome kernel for nsym <= 16 on the SIMD levels):
 *   scalar  : 256-entry row of the multiplication table (baseline x86-64 / any C11 target)
 *   avx2    : split-nibble PSHUFB, 32 bytes per step       (__attribute__((target("avx2"))))
 *   avx512  : split-nibble VPSHUFB, 64 bytes per step, masked tail (target("avx512f,avx512bw"))
 * The library itself is built without -march; a SIMD level is only executed when cpuid reports the instructions and
 * xgetbv reports that the OS saves the register state. Requesting a level the CPU lacks returns VNX_RS_EUNSUPPORTED.
 * "auto" selects AVX2 (else scalar); AVX-512 runs only when requested explicitly (see vnx_rs_best_level).
 *
 * Memory: one bounded allocation per call (tables for (n, nsym), at most 255*254 + 255*256 bytes); per-row work
 * arrays are fixed-size on the stack. Single-threaded and deterministic.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(__x86_64__) || defined(__i386__)
#define VNX_X86 1
#include <cpuid.h>
#include <immintrin.h>
#else
#define VNX_X86 0
#endif

#define VNX_RS_ABI_VERSION 1

#define VNX_RS_OK 0
#define VNX_RS_EINVAL (-1)
#define VNX_RS_EUNSUPPORTED (-2)
#define VNX_RS_ENOMEM (-3)
#define VNX_RS_EOVERLAP (-4)

#define LEVEL_AUTO 0
#define LEVEL_SCALAR 1
#define LEVEL_AVX2 2
#define LEVEL_AVX512 3

#define BIT(level) (1 << (level))

/* ------------------------------------------------------------------------------------------------ field tables */
static uint8_t EXP[512];
static uint8_t LOG[256];
static uint8_t INV[256];
static uint8_t MUL[256][256];
static uint8_t NIB[256][32]; /* NIB[c][x] = c*x (x < 16), NIB[c][16 + x] = c*(x << 4) */
static int CPU_LEVELS;        /* bitmask of BIT(level) usable on this CPU + OS (set once at load) */
static int LEVEL_MASK = -1;   /* test hook: levels allowed by vnx_rs_restrict_levels */

static void init_tables(void) {
    unsigned x = 1;
    for (int i = 0; i < 255; i++) {
        EXP[i] = (uint8_t)x;
        LOG[x] = (uint8_t)i;
        x <<= 1;
        if (x & 0x100u) x ^= 0x11Du;
    }
    for (int i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
    LOG[0] = 0;
    INV[0] = 0;
    for (int a = 1; a < 256; a++) INV[a] = EXP[(255 - LOG[a]) % 255];
    for (int a = 0; a < 256; a++) {
        for (int b = 0; b < 256; b++) MUL[a][b] = (a && b) ? EXP[LOG[a] + LOG[b]] : 0;
    }
    for (int c = 0; c < 256; c++) {
        for (int v = 0; v < 16; v++) {
            NIB[c][v] = MUL[c][v];
            NIB[c][16 + v] = MUL[c][v << 4];
        }
    }
}

/* ------------------------------------------------------------------------------------------------ CPU detection */
#if VNX_X86
static uint64_t read_xcr0(void) {
    uint32_t eax, edx;
    __asm__ volatile(".byte 0x0f, 0x01, 0xd0" /* xgetbv */ : "=a"(eax), "=d"(edx) : "c"(0));
    return ((uint64_t)edx << 32) | eax;
}

static int detect_levels(void) {
    int levels = BIT(LEVEL_SCALAR);
    unsigned a, b, c, d;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return levels;
    const int osxsave = (c >> 27) & 1, avx = (c >> 28) & 1, ssse3 = (c >> 9) & 1;
    if (!osxsave || !avx || !ssse3) return levels;
    const uint64_t xcr0 = read_xcr0();
    if ((xcr0 & 0x6u) != 0x6u) return levels; /* XMM and YMM state enabled by the OS */
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return levels;
    const int avx2 = (b >> 5) & 1, avx512f = (b >> 16) & 1, avx512bw = (b >> 30) & 1;
    if (avx2) levels |= BIT(LEVEL_AVX2);
    /* the AVX-512 level also runs AVX2 code; XCR0 bits 5-7: opmask, upper ZMM0-15, ZMM16-31 */
    if (avx2 && avx512f && avx512bw && (xcr0 & 0xE6u) == 0xE6u) levels |= BIT(LEVEL_AVX512);
    return levels;
}
#else
static int detect_levels(void) { return BIT(LEVEL_SCALAR); }
#endif

__attribute__((constructor)) static void vnx_rs_init(void) {
    init_tables();
    CPU_LEVELS = detect_levels();
}

/* ------------------------------------------------------------------------------------------------ dst ^= c * src */
typedef void (*mad_fn)(uint8_t *dst, const uint8_t *src, uint8_t c, size_t len);

static inline __attribute__((always_inline)) void mad_scalar(uint8_t *dst, const uint8_t *src, uint8_t c, size_t len) {
    const uint8_t *m = MUL[c];
    for (size_t i = 0; i < len; i++) dst[i] ^= m[src[i]];
}

#if VNX_X86
__attribute__((target("avx2"))) static inline void mad_avx2(uint8_t *dst, const uint8_t *src, uint8_t c, size_t len) {
    const __m128i lo128 = _mm_loadu_si128((const __m128i *)(const void *)NIB[c]);
    const __m128i hi128 = _mm_loadu_si128((const __m128i *)(const void *)(NIB[c] + 16));
    size_t i = 0;
    if (len >= 32) {
        const __m256i lo = _mm256_broadcastsi128_si256(lo128), hi = _mm256_broadcastsi128_si256(hi128);
        const __m256i mask = _mm256_set1_epi8(0x0f);
        for (; i + 32 <= len; i += 32) {
            const __m256i x = _mm256_loadu_si256((const __m256i *)(const void *)(src + i));
            const __m256i l = _mm256_and_si256(x, mask), h = _mm256_and_si256(_mm256_srli_epi16(x, 4), mask);
            const __m256i p = _mm256_xor_si256(_mm256_shuffle_epi8(lo, l), _mm256_shuffle_epi8(hi, h));
            __m256i *dp = (__m256i *)(void *)(dst + i);
            _mm256_storeu_si256(dp, _mm256_xor_si256(_mm256_loadu_si256(dp), p));
        }
    }
    if (i + 16 <= len) {
        const __m128i mask = _mm_set1_epi8(0x0f);
        const __m128i x = _mm_loadu_si128((const __m128i *)(const void *)(src + i));
        const __m128i l = _mm_and_si128(x, mask), h = _mm_and_si128(_mm_srli_epi16(x, 4), mask);
        const __m128i p = _mm_xor_si128(_mm_shuffle_epi8(lo128, l), _mm_shuffle_epi8(hi128, h));
        __m128i *dp = (__m128i *)(void *)(dst + i);
        _mm_storeu_si128(dp, _mm_xor_si128(_mm_loadu_si128(dp), p));
        i += 16;
    }
    const uint8_t *m = MUL[c];
    for (; i < len; i++) dst[i] ^= m[src[i]];
}

__attribute__((target("avx512f,avx512bw"))) static inline void mad_avx512(uint8_t *dst, const uint8_t *src, uint8_t c,
                                                                          size_t len) {
    const __m512i lo = _mm512_broadcast_i32x4(_mm_loadu_si128((const __m128i *)(const void *)NIB[c]));
    const __m512i hi = _mm512_broadcast_i32x4(_mm_loadu_si128((const __m128i *)(const void *)(NIB[c] + 16)));
    const __m512i mask = _mm512_set1_epi8(0x0f);
    size_t i = 0;
    for (; i + 64 <= len; i += 64) {
        const __m512i x = _mm512_loadu_si512((const void *)(src + i));
        const __m512i l = _mm512_and_si512(x, mask), h = _mm512_and_si512(_mm512_srli_epi16(x, 4), mask);
        const __m512i p = _mm512_xor_si512(_mm512_shuffle_epi8(lo, l), _mm512_shuffle_epi8(hi, h));
        _mm512_storeu_si512((void *)(dst + i), _mm512_xor_si512(_mm512_loadu_si512((const void *)(dst + i)), p));
    }
    if (i < len) { /* masked tail: bytes outside the mask are neither read nor written */
        const __mmask64 k = (__mmask64)((~0ULL) >> (64 - (len - i)));
        const __m512i x = _mm512_maskz_loadu_epi8(k, (const void *)(src + i));
        const __m512i l = _mm512_and_si512(x, mask), h = _mm512_and_si512(_mm512_srli_epi16(x, 4), mask);
        const __m512i p = _mm512_xor_si512(_mm512_shuffle_epi8(lo, l), _mm512_shuffle_epi8(hi, h));
        const __m512i d = _mm512_maskz_loadu_epi8(k, (const void *)(dst + i));
        _mm512_mask_storeu_epi8((void *)(dst + i), k, _mm512_xor_si512(d, p));
    }
}
#endif

/* ------------------------------------------------------------------------------------------------ per-call tables */
typedef struct {
    int n, nsym, width;
    int qs;              /* row stride of Q: 16 when nsym <= 16 (rows zero-padded), else nsym */
    int np;              /* row stride of P: n rounded up to a multiple of 64 (rows zero-padded), <= 256 */
    uint8_t x_of[256];   /* X_j = alpha^(n-1-j) */
    uint8_t lxinv[256];  /* log of X_j^-1 */
    uint8_t *Q;          /* Q[j*qs + i] = alpha^(i(n-1-j)): syndrome contribution of a 1 at position j */
    uint8_t *P;          /* P[k*np + j] = (X_j^-1)^k, k = 0 .. nsym */
} code_t;

static int code_init(code_t *c, int n, int nsym) {
    c->n = n;
    c->nsym = nsym;
    c->width = nsym + 1;
    c->qs = nsym <= 16 ? 16 : nsym;
    c->np = (n + 63) / 64 * 64;
    /* n <= 255, nsym <= 254: at most 255*254 + 255*256 bytes */
    const size_t qsize = (size_t)n * (size_t)c->qs, psize = (size_t)c->width * (size_t)c->np;
    c->Q = (uint8_t *)calloc(qsize + psize, 1);
    if (!c->Q) return VNX_RS_ENOMEM;
    c->P = c->Q + qsize;
    for (int j = 0; j < n; j++) {
        const int e = (n - 1 - j) % 255;
        c->x_of[j] = EXP[e];
        c->lxinv[j] = LOG[INV[c->x_of[j]]];
        for (int i = 0; i < nsym; i++) c->Q[(size_t)j * (size_t)c->qs + (size_t)i] = EXP[(i * e) % 255];
    }
    for (int k = 0; k < c->width; k++) {
        for (int j = 0; j < n; j++) c->P[(size_t)k * (size_t)c->np + (size_t)j] = EXP[(k * c->lxinv[j]) % 255];
    }
    return VNX_RS_OK;
}

/* ------------------------------------------------------------------------------------------------ syndromes */
/* s[0 .. nsym) = sum_j w_j * Q_j (s must hold 256 bytes; bytes nsym .. qs may be written with zeros) */
typedef void (*syn_fn)(const code_t *c, const uint8_t *w, uint8_t *s);

static inline __attribute__((always_inline)) void syn_scalar(const code_t *c, const uint8_t *w, uint8_t *s) {
    memset(s, 0, (size_t)c->nsym);
    for (int j = 0; j < c->n; j++) {
        if (w[j]) mad_scalar(s, c->Q + (size_t)j * (size_t)c->qs, w[j], (size_t)c->nsym);
    }
}

#if VNX_X86
/* nsym <= 16: two positions per 256-bit step, accumulator kept in a register, no branches on the data */
__attribute__((target("avx2"))) static inline void syn_avx2(const code_t *c, const uint8_t *w, uint8_t *s) {
    if (c->qs != 16) {
        memset(s, 0, (size_t)c->nsym);
        for (int j = 0; j < c->n; j++) {
            if (w[j]) mad_avx2(s, c->Q + (size_t)j * (size_t)c->qs, w[j], (size_t)c->nsym);
        }
        return;
    }
    const __m256i mask = _mm256_set1_epi8(0x0f);
    __m256i acc = _mm256_setzero_si256();
    int j = 0;
    for (; j + 2 <= c->n; j += 2) {
        const uint8_t *t0 = NIB[w[j]], *t1 = NIB[w[j + 1]];
        const __m256i lo = _mm256_inserti128_si256(
            _mm256_castsi128_si256(_mm_loadu_si128((const __m128i *)(const void *)t0)),
            _mm_loadu_si128((const __m128i *)(const void *)t1), 1);
        const __m256i hi = _mm256_inserti128_si256(
            _mm256_castsi128_si256(_mm_loadu_si128((const __m128i *)(const void *)(t0 + 16))),
            _mm_loadu_si128((const __m128i *)(const void *)(t1 + 16)), 1);
        const __m256i x = _mm256_loadu_si256((const __m256i *)(const void *)(c->Q + (size_t)j * 16));
        const __m256i l = _mm256_and_si256(x, mask), h = _mm256_and_si256(_mm256_srli_epi16(x, 4), mask);
        acc = _mm256_xor_si256(acc, _mm256_xor_si256(_mm256_shuffle_epi8(lo, l), _mm256_shuffle_epi8(hi, h)));
    }
    __m128i r = _mm_xor_si128(_mm256_castsi256_si128(acc), _mm256_extracti128_si256(acc, 1));
    if (j < c->n) {
        const __m128i m = _mm_set1_epi8(0x0f);
        const __m128i lo = _mm_loadu_si128((const __m128i *)(const void *)NIB[w[j]]);
        const __m128i hi = _mm_loadu_si128((const __m128i *)(const void *)(NIB[w[j]] + 16));
        const __m128i x = _mm_loadu_si128((const __m128i *)(const void *)(c->Q + (size_t)j * 16));
        const __m128i l = _mm_and_si128(x, m), h = _mm_and_si128(_mm_srli_epi16(x, 4), m);
        r = _mm_xor_si128(r, _mm_xor_si128(_mm_shuffle_epi8(lo, l), _mm_shuffle_epi8(hi, h)));
    }
    _mm_storeu_si128((__m128i *)(void *)s, r);
}

__attribute__((target("avx512f,avx512bw"))) static inline void syn_avx512(const code_t *c, const uint8_t *w,
                                                                          uint8_t *s) {
    if (c->qs != 16) {
        memset(s, 0, (size_t)c->nsym);
        for (int j = 0; j < c->n; j++) {
            if (w[j]) mad_avx512(s, c->Q + (size_t)j * (size_t)c->qs, w[j], (size_t)c->nsym);
        }
        return;
    }
    syn_avx2(c, w, s); /* building four per-position tables per 512-bit step costs more than it saves */
}
#endif

/* ------------------------------------------------------------------------------------------------ one codeword */
/* Decodes w (n bytes, modified in place only on success). Returns 1 = ok, 0 = failure; *errata as rs_fast.
 * pad: evaluate the Chien vectors over the zero-padded stride np instead of n (SIMD levels: no scalar tail). */
static inline __attribute__((always_inline)) int decode_row(const code_t *c, uint8_t *w, const uint8_t *er,
                                                             int64_t *errata, const mad_fn mad, const syn_fn syn,
                                                             const int pad) {
    const int n = c->n, nsym = c->nsym, width = c->width;
    if (n < 2 || n > 255 || nsym < 1 || nsym >= n) return 0; /* validated by the caller; states the range for the compiler */
    uint8_t s[256];
    int64_t f = 0;
    *errata = 0;
    if (er) {
        for (int j = 0; j < n; j++) f += er[j] != 0;
    }
    if (f > nsym) return 0;
    syn(c, w, s);
    uint8_t any = 0;
    for (int i = 0; i < nsym; i++) any |= s[i];
    if (!any) return 1;

    /* erasure locator Gamma, low degree first, truncated to width (f <= nsym, so nothing is lost) */
    uint8_t buf[3][256];
    uint8_t *lam = buf[0], *b = buf[1], *t = buf[2];
    memset(lam, 0, (size_t)width);
    lam[0] = 1;
    if (f) {
        int deg = 0;
        for (int j = 0; j < n; j++) {
            if (!er[j]) continue;
            const uint8_t *mx = MUL[c->x_of[j]];
            const int top = deg + 1 < width - 1 ? deg + 1 : width - 1;
            for (int k = top; k >= 1; k--) lam[k] ^= mx[lam[k - 1]];
            deg = top;
        }
    }
    memcpy(b, lam, (size_t)width);

    /* Berlekamp-Massey with erasures (rs_fast semantics, including the truncation of x*B to width) */
    int64_t L = f;
    for (int r = (int)f + 1; r <= nsym; r++) {
        uint8_t delta = 0;
        const int jmax = r - 1 < width - 1 ? r - 1 : width - 1;
        for (int j = 0; j <= jmax; j++) delta ^= MUL[lam[j]][s[r - 1 - j]];
        /* x*B into t (top coefficient dropped) */
        t[0] = 0;
        memcpy(t + 1, b, (size_t)nsym);
        if (delta == 0) {
            uint8_t *tmp = b;
            b = t;
            t = tmp;
            continue;
        }
        if (2 * L <= (int64_t)r - 1 + f) {
            /* B <- delta^-1 * Lambda (old Lambda), Lambda <- Lambda - delta x B, L <- r + f - L */
            memset(b, 0, (size_t)width);
            mad(b, lam, INV[delta], (size_t)width);
            mad(lam, t, delta, (size_t)width);
            L = (int64_t)r + f - L;
        } else {
            mad(lam, t, delta, (size_t)width);
            uint8_t *tmp = b;
            b = t;
            t = tmp;
        }
    }
    int degree = 0;
    for (int k = width - 1; k >= 0; k--) {
        if (lam[k]) {
            degree = k;
            break;
        }
    }
    if (!(degree == L && 2 * (L - f) + f <= nsym && L <= n)) return 0;

    /* Chien search at every codeword position: values = sum_k lam_k * P_k */
    uint8_t val[256];
    const size_t vlen = pad ? (size_t)c->np : (size_t)n;
    memset(val, 0, vlen);
    for (int k = 0; k <= degree; k++) {
        if (lam[k]) mad(val, c->P + (size_t)k * (size_t)c->np, lam[k], vlen);
    }
    uint8_t roots[256];
    int nroots = 0;
    for (int j = 0; j < n; j++) {
        if (val[j] == 0) roots[nroots++] = (uint8_t)j;
        else if (er && er[j]) return 0; /* a flagged erasure that is not a root */
    }
    if (nroots != L) return 0;

    /* Forney: Omega = S * Lambda mod x^nsym */
    uint8_t om[256];
    memset(om, 0, (size_t)nsym);
    for (int k = 0; k < width && k < nsym; k++) {
        if (lam[k]) mad(om + k, s, lam[k], (size_t)(nsym - k));
    }
    int omdeg = nsym - 1;
    while (omdeg > 0 && !om[omdeg]) omdeg--;
    uint8_t mag[256];
    for (int q = 0; q < nroots; q++) {
        const int j = roots[q];
        const uint8_t *mp = MUL[EXP[c->lxinv[j]]]; /* multiply by the point X_j^-1 */
        uint8_t ov = 0, dv = 0;
        for (int k = omdeg; k >= 0; k--) ov = mp[ov] ^ om[k];
        /* Lambda'(x) = sum over odd k of lam_k x^(k-1): coefficient of x^(2m) is lam_(2m+1) */
        const uint8_t *mp2 = MUL[EXP[(2 * c->lxinv[j]) % 255]]; /* (X_j^-1)^2 */
        int top = (width - 2) / 2; /* largest m with 2m + 1 <= nsym */
        for (int m = top; m >= 0; m--) dv = mp2[dv] ^ lam[2 * m + 1];
        if (dv == 0) return 0;
        mag[q] = MUL[MUL[c->x_of[j]][ov]][INV[dv]];
    }
    /* re-check: S(w ^ e) = S(w) ^ sum_j e_j Q_j must vanish */
    for (int q = 0; q < nroots; q++) {
        if (mag[q]) mad(s, c->Q + (size_t)roots[q] * (size_t)c->qs, mag[q], (size_t)nsym);
    }
    any = 0;
    for (int i = 0; i < nsym; i++) any |= s[i];
    if (any) return 0;
    for (int q = 0; q < nroots; q++) w[roots[q]] ^= mag[q];
    *errata = nroots;
    return 1;
}

static inline __attribute__((always_inline)) void decode_rows(const code_t *c, int64_t n_words, const uint8_t *cw,
                                                               const uint8_t *erase, uint8_t *out, uint8_t *ok,
                                                               int64_t *errata, const mad_fn mad, const syn_fn syn,
                                                               const int pad) {
    const size_t n = (size_t)c->n;
    uint8_t w[256];
    for (int64_t r = 0; r < n_words; r++) {
        const size_t off = (size_t)r * n;
        memcpy(w, cw + off, n);
        ok[r] = (uint8_t)decode_row(c, w, erase ? erase + off : NULL, &errata[r], mad, syn, pad);
        memcpy(out + off, w, n);
    }
}

static void decode_rows_scalar(const code_t *c, int64_t nw, const uint8_t *cw, const uint8_t *er, uint8_t *out,
                               uint8_t *ok, int64_t *errata) {
    decode_rows(c, nw, cw, er, out, ok, errata, mad_scalar, syn_scalar, 0);
}

#if VNX_X86
__attribute__((target("avx2"))) static void decode_rows_avx2(const code_t *c, int64_t nw, const uint8_t *cw,
                                                             const uint8_t *er, uint8_t *out, uint8_t *ok,
                                                             int64_t *errata) {
    decode_rows(c, nw, cw, er, out, ok, errata, mad_avx2, syn_avx2, 1);
}

__attribute__((target("avx512f,avx512bw"))) static void decode_rows_avx512(const code_t *c, int64_t nw,
                                                                           const uint8_t *cw, const uint8_t *er,
                                                                           uint8_t *out, uint8_t *ok, int64_t *errata) {
    decode_rows(c, nw, cw, er, out, ok, errata, mad_avx512, syn_avx512, 1);
}
#endif

/* ------------------------------------------------------------------------------------------------ public ABI */
int vnx_rs_abi_version(void) { return VNX_RS_ABI_VERSION; }

/* Bitmask of usable levels (bit 1 scalar, bit 2 avx2, bit 3 avx512): compiled in AND supported by CPU + OS AND not
 * removed by vnx_rs_restrict_levels. */
int vnx_rs_levels(void) { return CPU_LEVELS & LEVEL_MASK & (BIT(LEVEL_SCALAR) | BIT(LEVEL_AVX2) | BIT(LEVEL_AVX512)); }

/* Levels detected on this CPU + OS, ignoring the test restriction. */
int vnx_rs_cpu_levels(void) { return CPU_LEVELS; }

/* Test hook: pretend the CPU only supports the levels in mask (the scalar level can never be removed). */
void vnx_rs_restrict_levels(int mask) { LEVEL_MASK = mask | BIT(LEVEL_SCALAR); }

/* Level used for "auto" (level 0): AVX2 when usable, else scalar. AVX-512 is never chosen automatically: on the
 * development host (Xeon Gold 6240, Cascade Lake) it measured 0.94x the speed of AVX2 on the captured decode workload,
 * i.e. not measurably faster (benchmarks/v6/native_rs/results/bench.json); it stays selectable explicitly. */
int vnx_rs_best_level(void) {
    const int lv = vnx_rs_levels();
    return (lv & BIT(LEVEL_AVX2)) ? LEVEL_AVX2 : LEVEL_SCALAR;
}

static int overlaps(const void *a, size_t na, const void *b, size_t nb) {
    const uintptr_t x = (uintptr_t)a, y = (uintptr_t)b;
    return na && nb && x < y + nb && y < x + na;
}

/*
 * Batch decode. cw: n_words x n bytes (C order); erase: same shape, non-zero = flagged, or NULL; out: n_words x n
 * (may be exactly cw for in-place decoding, must not partially overlap it); ok: n_words bytes (0/1); errata: n_words.
 * Requires 1 <= n <= 255, 1 <= nsym < n (nsym == 0 and n_words == 0 are trivial and handled by the caller, as in
 * rs_fast). level: 0 = best usable, 1 scalar, 2 avx2, 3 avx512. Returns 0 or a negative VNX_RS_E* code.
 */
int vnx_rs_decode_batch(int64_t n_words, int32_t n, int32_t nsym, const uint8_t *cw, const uint8_t *erase,
                        uint8_t *out, uint8_t *ok, int64_t *errata, int32_t level) {
    if (n_words < 0 || n < 1 || n > 255 || nsym < 1 || nsym >= n) return VNX_RS_EINVAL;
    if (level < LEVEL_AUTO || level > LEVEL_AVX512) return VNX_RS_EINVAL;
    if (level == LEVEL_AUTO) level = vnx_rs_best_level();
    if (!(vnx_rs_levels() & BIT(level))) return VNX_RS_EUNSUPPORTED;
    if (n_words == 0) return VNX_RS_OK;
    if (!cw || !out || !ok || !errata) return VNX_RS_EINVAL;
    if ((uint64_t)n_words > (uint64_t)(PTRDIFF_MAX / 256)) return VNX_RS_EINVAL;
    const size_t total = (size_t)n_words * (size_t)n;
    if (out != cw && overlaps(out, total, cw, total)) return VNX_RS_EOVERLAP;
    if (erase && overlaps(out, total, erase, total)) return VNX_RS_EOVERLAP;
    if (overlaps(ok, (size_t)n_words, out, total) || overlaps(errata, (size_t)n_words * sizeof(int64_t), out, total))
        return VNX_RS_EOVERLAP;
    code_t c;
    const int rc = code_init(&c, n, nsym);
    if (rc) return rc;
    switch (level) {
#if VNX_X86
    case LEVEL_AVX512: decode_rows_avx512(&c, n_words, cw, erase, out, ok, errata); break;
    case LEVEL_AVX2: decode_rows_avx2(&c, n_words, cw, erase, out, ok, errata); break;
#endif
    default: decode_rows_scalar(&c, n_words, cw, erase, out, ok, errata); break;
    }
    free(c.Q);
    return VNX_RS_OK;
}
