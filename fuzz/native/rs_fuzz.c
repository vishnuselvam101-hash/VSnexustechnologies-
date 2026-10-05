/* libFuzzer harness for the V6 native inner Reed-Solomon decoder (src/vnxdna/v6/native/rs.c), built by fuzz/run.sh as
 * one translation unit (the kernel is #included).
 *
 * Input: byte 0 -> n (2..255 if bits 6-7 are set, else 2..49), byte 1 -> nsym (1..n-1), byte 2 -> mode, byte 3 -> words (1..4); the rest drives the
 * content. Mode 0 ("raw"): codewords and erasure flags are input bytes. Mode 1 ("coded"): every word is a valid
 * codeword (encoded here) with e random errors and f erasures chosen by the input.
 *
 * Invariants checked (all are properties of the specification in the rs.c header, not of the implementation):
 *   - valid arguments return VNX_RS_OK; invalid ones (nsym >= n, n > 255, bad level) return VNX_RS_EINVAL;
 *   - ok is 0/1; a failed word is left unchanged with errata 0;
 *   - a word whose syndromes are all zero (and f <= nsym) is returned unchanged, ok, errata 0;
 *   - more flagged erasures than nsym -> fail;
 *   - an ok word is a codeword (all nsym syndromes zero, computed here with an independent GF(2^8)/0x11D, generator 2,
 *     first root 0), changes at most errata positions, and the u changed unflagged positions satisfy 2u + f <= nsym;
 *   - coded mode with 2e + f <= nsym: ok, and the original codeword is recovered exactly (unique decoding radius);
 *   - every usable SIMD level gives byte-identical out / ok / errata to the scalar level, NULL erase equals an all-zero
 *     mask, and in-place decoding (out == cw) equals out-of-place decoding.
 * Buffers are allocated with exactly the sizes the Python binding (native_rs.py) passes, so ASan sees any overrun. */
#include "vnxdna/v6/native/rs.c"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* the reference arithmetic is excluded from coverage instrumentation: it is the oracle, not the target, and its
 * comparisons would otherwise dominate the run time */
#define ORACLE __attribute__((no_sanitize("coverage")))

static uint8_t H_EXP[512], H_LOG[256];

ORACLE static void h_init(void) {
    static int done;
    if (done) return;
    unsigned x = 1;
    for (int i = 0; i < 255; i++) {
        H_EXP[i] = (uint8_t)x;
        H_LOG[x] = (uint8_t)i;
        x <<= 1;
        if (x & 0x100u) x ^= 0x11Du;
    }
    for (int i = 255; i < 512; i++) H_EXP[i] = H_EXP[i - 255];
    done = 1;
}

ORACLE static uint8_t h_mul(uint8_t a, uint8_t b) { return (a && b) ? H_EXP[H_LOG[a] + H_LOG[b]] : 0; }

/* S_i = sum_j c_j alpha^(i (n-1-j)), Horner over j */
ORACLE static int h_syndromes_zero(const uint8_t *c, int n, int nsym) {
    for (int i = 0; i < nsym; i++) {
        const uint8_t a = H_EXP[i % 255];
        uint8_t acc = 0;
        for (int j = 0; j < n; j++) acc = (uint8_t)(h_mul(acc, a) ^ c[j]);
        if (acc) return 0;
    }
    return 1;
}

/* systematic encoding: msg (k = n - nsym bytes) || remainder of msg(x) x^nsym mod g(x), g = prod (x + alpha^i) */
ORACLE static void h_encode(uint8_t *cw, int n, int nsym) {
    uint8_t g[256] = {1};
    int glen = 1;
    for (int i = 0; i < nsym; i++) {
        uint8_t ng[256] = {0};
        for (int j = 0; j <= glen; j++) {
            uint8_t v = j < glen ? g[j] : 0;
            if (j >= 1) v ^= h_mul(g[j - 1], H_EXP[i]);
            ng[j] = v;
        }
        glen++;
        memcpy(g, ng, (size_t)glen);
    }
    const int k = n - nsym;
    uint8_t buf[256];
    memcpy(buf, cw, (size_t)k);
    memset(buf + k, 0, (size_t)nsym);
    for (int i = 0; i < k; i++) {
        const uint8_t coef = buf[i];
        if (!coef) continue;
        for (int j = 1; j < glen; j++) buf[i + j] ^= h_mul(g[j], coef);
    }
    memcpy(cw + k, buf + k, (size_t)nsym);
}

typedef struct {
    const uint8_t *p;
    size_t n, i;
    uint64_t s;
} src_t;

static uint8_t next_byte(src_t *s) {
    if (s->i < s->n) return s->p[s->i++];
    s->s = s->s * 6364136223846793005ULL + 1442695040888963407ULL; /* deterministic tail once input runs out */
    return (uint8_t)(s->s >> 56);
}

static void fail(const char *what) {
    fprintf(stderr, "rs_fuzz invariant violated: %s\n", what);
    abort();
}

int LLVMFuzzerTestOneInput(const uint8_t *in, size_t size) {
    if (size < 4) return 0;
    h_init();
    const int n = (in[0] & 0xC0) == 0xC0 ? 2 + in[0] % 254 : 2 + in[0] % 48; /* 3/4 of the inputs use short words */
    const int nsym = 1 + in[1] % (n - 1);
    const int mode = in[2] & 1;
    const int use_null_erase = (in[2] >> 1) & 1;
    const int64_t nw = 1 + in[3] % 4;
    src_t s = {in + 4, size - 4, 0, size};
    const size_t total = (size_t)nw * (size_t)n;

    uint8_t *cw = malloc(total), *er = calloc(total, 1), *orig = malloc(total);
    int *expect = calloc((size_t)nw, sizeof(int)); /* coded mode: word must decode to orig */
    if (mode == 0) {
        for (size_t i = 0; i < total; i++) cw[i] = next_byte(&s);
        if (!use_null_erase)
            for (size_t i = 0; i < total; i++) {
                const uint8_t b = next_byte(&s);
                er[i] = b < 24 ? (uint8_t)(b * 11 + 1) : 0; /* any non-zero byte flags an erasure */
            }
        memcpy(orig, cw, total);
    } else {
        for (int64_t w = 0; w < nw; w++) {
            uint8_t *c = cw + w * n, *e = er + w * n;
            for (int j = 0; j < n - nsym; j++) c[j] = next_byte(&s);
            h_encode(c, n, nsym);
            if (!h_syndromes_zero(c, n, nsym)) fail("harness encoder produced a non-codeword");
            memcpy(orig + w * n, c, (size_t)n);
            /* f erasures, e errors; sometimes beyond capacity */
            const int over = next_byte(&s) < 32;
            int f = use_null_erase ? 0 : next_byte(&s) % (nsym + 1);
            int ne = next_byte(&s) % ((nsym - f) / 2 + 1);
            if (over) ne += 1 + next_byte(&s) % 4;
            if (f + ne > n) ne = n - f;
            uint8_t used[256] = {0};
            for (int t = 0; t < f + ne; t++) {
                int pos = next_byte(&s) % n;
                while (used[pos]) pos = (pos + 1) % n;
                used[pos] = 1;
                if (t < f) {
                    e[pos] = 1;
                    c[pos] ^= next_byte(&s); /* an erasure may or may not change the byte */
                } else {
                    c[pos] ^= (uint8_t)(1 + next_byte(&s) % 255);
                }
            }
            expect[w] = 2 * ne + f <= nsym;
        }
    }

    const int levels = vnx_rs_levels();
    uint8_t *ref_out = malloc(total), *ref_ok = malloc((size_t)nw);
    int64_t *ref_er = malloc((size_t)nw * sizeof(int64_t));
    int have_ref = 0;
    for (int lv = LEVEL_SCALAR; lv <= LEVEL_AVX512; lv++) {
        if (!(levels & BIT(lv))) continue;
        for (int inplace = 0; inplace < (lv == LEVEL_SCALAR ? 2 : 1); inplace++) {
            uint8_t *out = malloc(total), *ok = malloc((size_t)nw);
            int64_t *errata = malloc((size_t)nw * sizeof(int64_t));
            memset(out, 0xA5, total);
            memset(ok, 0xA5, (size_t)nw);
            if (inplace) memcpy(out, cw, total);
            const uint8_t *erp = use_null_erase ? NULL : er;
            const int rc = vnx_rs_decode_batch(nw, n, nsym, inplace ? out : cw, erp, out, ok, errata, lv);
            if (rc != VNX_RS_OK) fail("valid arguments not accepted");
            if (!have_ref) {
                memcpy(ref_out, out, total);
                memcpy(ref_ok, ok, (size_t)nw);
                memcpy(ref_er, errata, (size_t)nw * sizeof(int64_t));
                have_ref = 1;
            } else if (memcmp(ref_out, out, total) || memcmp(ref_ok, ok, (size_t)nw) ||
                       memcmp(ref_er, errata, (size_t)nw * sizeof(int64_t))) {
                fail("levels / in-place variants disagree");
            }
            free(out);
            free(ok);
            free(errata);
        }
    }
    /* NULL erase pointer == all-zero mask */
    if (use_null_erase) {
        uint8_t *out = malloc(total), *ok = malloc((size_t)nw);
        int64_t *errata = malloc((size_t)nw * sizeof(int64_t));
        if (vnx_rs_decode_batch(nw, n, nsym, cw, er, out, ok, errata, LEVEL_SCALAR) != VNX_RS_OK) fail("rc (zero mask)");
        if (memcmp(ref_out, out, total) || memcmp(ref_ok, ok, (size_t)nw) ||
            memcmp(ref_er, errata, (size_t)nw * sizeof(int64_t)))
            fail("NULL erase differs from an all-zero mask");
        free(out);
        free(ok);
        free(errata);
    }

    for (int64_t w = 0; w < nw; w++) {
        const uint8_t *c = cw + w * n, *o = ref_out + w * n, *e = er + w * n;
        const int ok = ref_ok[w];
        const int64_t L = ref_er[w];
        int f = 0;
        for (int j = 0; j < n; j++) f += e[j] != 0;
        if (ok != 0 && ok != 1) fail("ok flag not 0/1");
        if (L < 0 || L > n) fail("errata out of range");
        if (!ok) {
            if (memcmp(c, o, (size_t)n) || L != 0) fail("failed word modified or errata != 0");
        } else {
            if (!h_syndromes_zero(o, n, nsym)) fail("ok word is not a codeword");
            int changed = 0, u = 0;
            for (int j = 0; j < n; j++)
                if (c[j] != o[j]) {
                    changed++;
                    if (!e[j]) u++;
                }
            if (changed > L) fail("more positions changed than errata");
            if (2 * u + f > nsym) fail("correction beyond the decoding radius (2u + f > nsym)");
        }
        if (f <= nsym && h_syndromes_zero(c, n, nsym) && !(ok && L == 0 && !memcmp(c, o, (size_t)n))) fail("codeword not passed through");
        if (f > nsym && ok) fail("ok with more erasures than nsym");
        if (mode == 1 && expect[w] && !(ok && !memcmp(o, orig + w * n, (size_t)n)))
            fail("correctable word (2e + f <= nsym) not recovered exactly");
    }

    /* argument validation */
    uint8_t b1[1] = {0}, okb[1];
    int64_t erb[1];
    if (vnx_rs_decode_batch(1, n, n, cw, NULL, b1, okb, erb, LEVEL_SCALAR) != VNX_RS_EINVAL) fail("nsym == n accepted");
    if (vnx_rs_decode_batch(1, 256, 1, cw, NULL, b1, okb, erb, LEVEL_SCALAR) != VNX_RS_EINVAL) fail("n == 256 accepted");
    if (vnx_rs_decode_batch(1, n, nsym, cw, NULL, b1, okb, erb, 4) != VNX_RS_EINVAL) fail("level 4 accepted");

    free(cw);
    free(er);
    free(orig);
    free(expect);
    free(ref_out);
    free(ref_ok);
    free(ref_er);
    return 0;
}
