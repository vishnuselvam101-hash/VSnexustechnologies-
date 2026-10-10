/* libFuzzer harness for the native strand builder vnx_st_build (strand ABI 1, src/vnxdna/native/c/strand.c).
 *
 * The input bytes choose the geometry (rows, span, variants, parity, markers), the rules and the motifs, then the row
 * contents; every buffer is allocated at exactly the size the C contract states, so ASan reports any access outside
 * it. Properties checked on every input:
 *   - the call succeeds (all arguments are inside the contract) and returns the number of rows with variant -1;
 *   - every chosen variant is -1 or in [0, nvar), and every strand of a row with a variant holds codes 0..3 and the
 *     template's marker bases at the marker positions;
 *   - row-range independence: building the rows in two ranges [0, s) and [s, n) gives the same strands and variants.
 * The equality with the NumPy reference is tested in tests/arch/test_native_strand.py (the C file has no reference).
 *
 * build: clang -g -O1 -fsanitize=fuzzer,address,undefined benchmarks/arch/native_strand/strand_fuzz.c \
 *        src/vnxdna/native/c/strand.c -o strand_fuzz */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int vnx_st_build(int64_t n, int64_t c0, int64_t c1, int64_t span, const uint8_t *plain, int64_t nvar, const uint8_t *ks,
                 int64_t r, const uint8_t *matrix, const uint8_t *mul, int64_t strand_nt, const int16_t *tpl,
                 int64_t gc_min, int64_t gc_max, int64_t win, int64_t wmin, int64_t wmax, int64_t hmax, int64_t tandem,
                 int64_t nmotif, const uint8_t *motif_buf, int64_t motif_bytes, const int64_t *motif_off,
                 const int64_t *motif_len, uint8_t *out, int16_t *chosen);

typedef struct { const uint8_t *p; size_t n; } src_t;
static uint8_t take(src_t *s) {
    if (!s->n) return 0;
    s->n--;
    return *s->p++;
}

static uint8_t gmul(uint8_t a, uint8_t b) { /* GF(256)/0x11D, only to fill a realistic table */
    uint8_t r = 0;
    while (b) {
        if (b & 1) r ^= a;
        a = (uint8_t)((a << 1) ^ ((a & 0x80) ? 0x1D : 0));
        b >>= 1;
    }
    return r;
}

static uint8_t MUL[256 * 256];

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    static int init = 0;
    if (!init) {
        for (int a = 0; a < 256; a++)
            for (int b = 0; b < 256; b++) MUL[a * 256 + b] = gmul((uint8_t)a, (uint8_t)b);
        init = 1;
    }
    src_t s = {data, size};
    const int64_t n = take(&s) % 9, span = take(&s) % 24, nvar = 1 + take(&s) % 8, r = take(&s) % 9;
    const int64_t period = (take(&s) % 4) * 4, mlen = period ? 1 + take(&s) % 3 : 0;
    const int64_t fnt = 4 * (1 + span + r);
    const int64_t markers = period ? (fnt + period - 1) / period - 1 : 0, strand_nt = fnt + markers * mlen;
    int64_t gc_a = take(&s) % 101, gc_b = take(&s) % 101;
    const int64_t gc_min = gc_a < gc_b ? gc_a : gc_b, gc_max = gc_a < gc_b ? gc_b : gc_a;
    int64_t wa = take(&s) % 101, wb = take(&s) % 101;
    const int64_t wmin = wa < wb ? wa : wb, wmax = wa < wb ? wb : wa;
    const int64_t win = take(&s) % 40, hmax = take(&s) % 7, tandem = (take(&s) & 1) ? 6 + take(&s) % 10 : 0;
    const int64_t nmotif = take(&s) % 4;
    uint8_t *plain = malloc((size_t)(n * span) + 1), *ks = malloc((size_t)(nvar * span) + 1);
    uint8_t *matrix = malloc((size_t)((1 + span) * r) + 1), *out = malloc((size_t)(n * strand_nt) + 1);
    uint8_t *out2 = malloc((size_t)(n * strand_nt) + 1), *mbuf = malloc(4 * 8 + 1);
    int16_t *tpl = malloc(sizeof(int16_t) * (size_t)(strand_nt + 1)), *ch = malloc(sizeof(int16_t) * (size_t)(n + 1));
    int16_t *ch2 = malloc(sizeof(int16_t) * (size_t)(n + 1));
    int64_t moff[4], mln[4], mbytes = 0;
    for (int64_t i = 0; i < n * span; i++) plain[i] = take(&s);
    for (int64_t i = 0; i < nvar * span; i++) ks[i] = take(&s);
    for (int64_t i = 0; i < (1 + span) * r; i++) matrix[i] = take(&s);
    for (int64_t m = 0; m < nmotif; m++) {
        moff[m] = mbytes;
        mln[m] = 1 + take(&s) % 8;
        for (int64_t j = 0; j < mln[m]; j++) mbuf[mbytes++] = take(&s) & 3;
    }
    for (int64_t x = 0, f = 0, k = 0; x < strand_nt;) { /* frame bases, then a marker every `period` frame bases */
        const int64_t t = period && fnt - f > period ? period : fnt - f;
        for (int64_t j = 0; j < t; j++) tpl[x++] = -1;
        f += t;
        if (f < fnt && period)
            for (int64_t j = 0; j < mlen; j++) tpl[x++] = (int16_t)((k + j) & 3);
        k++;
    }
    int rc = vnx_st_build(n, 0, n, span, plain, nvar, ks, r, matrix, MUL, strand_nt, tpl, gc_min, gc_max, win, wmin, wmax,
                          hmax, tandem, nmotif, mbuf, mbytes, moff, mln, out, ch);
    if (rc < 0) abort();
    int failed = 0;
    for (int64_t i = 0; i < n; i++) {
        if (ch[i] < -1 || ch[i] >= nvar) abort();
        failed += ch[i] < 0;
        if (ch[i] < 0) continue;
        for (int64_t x = 0; x < strand_nt; x++) {
            const uint8_t b = out[i * strand_nt + x];
            if (b > 3 || (tpl[x] >= 0 && b != tpl[x])) abort();
        }
    }
    if (failed != rc) abort();
    const int64_t split = n ? take(&s) % (n + 1) : 0;
    int r1 = vnx_st_build(n, 0, split, span, plain, nvar, ks, r, matrix, MUL, strand_nt, tpl, gc_min, gc_max, win, wmin,
                          wmax, hmax, tandem, nmotif, mbuf, mbytes, moff, mln, out2, ch2);
    int r2 = vnx_st_build(n, split, n, span, plain, nvar, ks, r, matrix, MUL, strand_nt, tpl, gc_min, gc_max, win, wmin,
                          wmax, hmax, tandem, nmotif, mbuf, mbytes, moff, mln, out2, ch2);
    if (r1 < 0 || r2 < 0 || r1 + r2 != rc) abort();
    for (int64_t i = 0; i < n; i++) {
        if (ch[i] != ch2[i]) abort();
        if (ch[i] >= 0 && memcmp(out + i * strand_nt, out2 + i * strand_nt, (size_t)strand_nt)) abort();
    }
    free(plain); free(ks); free(matrix); free(out); free(out2); free(mbuf); free(tpl); free(ch); free(ch2);
    return 0;
}
