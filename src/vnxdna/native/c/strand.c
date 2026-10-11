/* VNX-DNA hybrid architecture: native encode kernel (strand builder), ABI 1.
 *
 * The NumPy reference is the specification: vnxdna.dnaenc.frame4.build_strands (plain rows -> scrambler variant ->
 * inner RS parity -> 2-bit mapping -> sync markers -> constraint screen, first variant that passes). For every row this
 * kernel returns exactly the strand and the variant the reference returns; rows that fail every variant get variant -1
 * (the caller raises the reference's error). The constraint screen reproduces constraints.violations_batch for the
 * rules that depend on the sequence (GC content, windowed GC, homopolymer, period-2/3 tandem repeats, forbidden motifs);
 * the length rule depends only on the layout and is checked by the caller.
 *
 *   vnx_st_build   frame4.build_strands (rows c0..c1-1)
 *
 * Inner RS parity: parity[j] = XOR_i MUL[msg[i]][matrix[i][j]] over the k = 1 + span message bytes (GF(256) tables
 * from the caller: the same MUL table and parity matrix as codec.InnerRS.parity, so the field polynomial and the code
 * cannot differ from the reference).
 *
 * Plain C11, loaded with ctypes. No global mutable state; disjoint row ranges may run concurrently. Every size from the
 * caller is validated; products are formed in int64 after range checks (span, r <= 4096, strand <= 65536 nt). */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifndef VNX_ST_ABI /* overridable only so that the tests can build a mismatching library */
#define VNX_ST_ABI 1
#endif

enum { ST_OK = 0, ST_EARG = -1, ST_ENOMEM = -2 };

int vnx_st_abi_version(void) { return VNX_ST_ABI; }

typedef struct {
    int64_t gc_min, gc_max, win, wmin, wmax, hmax, tandem;
    int64_t nmotif;
    const uint8_t *motif_buf;
    const int64_t *motif_off, *motif_len;
} st_rules_t;

/* 1 if the strand of length L satisfies every sequence rule (as constraints.violations_batch); codes are 0..3. */
static int st_ok(const uint8_t *s, int64_t L, const st_rules_t *c, int32_t *csum) {
    if (L == 0) return 1;
    int64_t gc = 0;
    csum[0] = 0;
    for (int64_t i = 0; i < L; i++) {
        gc += (s[i] == 1) | (s[i] == 2);
        csum[i + 1] = (int32_t)gc;
    }
    if (!(c->gc_min * L <= 100 * gc && 100 * gc <= c->gc_max * L)) return 0;
    const int64_t w = c->win;
    if (w && L >= w) {
        for (int64_t i = 0; i + w <= L; i++) {
            const int64_t g = csum[i + w] - csum[i];
            if (!(c->wmin * w <= 100 * g && 100 * g <= c->wmax * w)) return 0;
        }
    }
    const int64_t h = c->hmax;
    if (h && L > h) { /* h consecutive equal neighbours = a run of h + 1 equal bases */
        int64_t run = 0;
        for (int64_t i = 1; i < L; i++) {
            run = s[i] == s[i - 1] ? run + 1 : 0;
            if (run >= h) return 0;
        }
    }
    const int64_t t = c->tandem;
    if (t) {
        for (int64_t p = 2; p <= 3; p++) {
            if (L <= p) continue;
            const int64_t need = t - p + 1; /* consecutive positions with s[i + p] == s[i] */
            if (need <= 0) return 0;
            int64_t run = 0;
            for (int64_t i = 0; i + p < L; i++) {
                run = s[i + p] == s[i] ? run + 1 : 0;
                if (run >= need) return 0;
            }
        }
    }
    for (int64_t m = 0; m < c->nmotif; m++) {
        const uint8_t *pat = c->motif_buf + c->motif_off[m];
        const int64_t ml = c->motif_len[m];
        if (ml > L) continue;
        for (int64_t i = 0; i + ml <= L; i++) {
            if (s[i] != pat[0]) continue;
            int64_t j = 1;
            while (j < ml && s[i + j] == pat[j]) j++;
            if (j == ml) return 0;
        }
    }
    return 1;
}

/* Rows c0..c1-1 of the reference build_strands.
 * plain: n*span unscrambled bytes 1..13+P; ks: nvar*span keystreams; matrix: (1 + span)*r inner RS parity matrix; mul:
 * 256*256 GF(256) product table; tpl: strand_nt int16 (marker code 0..3, or -1 at frame positions, which take the frame
 * bases in order; their count must be 4 * (1 + span + r)). Rules: GC percents 0..100, win/hmax/tandem >= 0, motifs
 * (codes 0..3, length 1..64) at motif_buf[motif_off[m]]. Outputs: out n*strand_nt codes, chosen n (variant or -1;
 * rows that fail keep whatever the last variant produced in out, as the caller discards them). Returns the number
 * of failed rows in the range (>= 0) or ST_EARG / ST_ENOMEM. */
int vnx_st_build(int64_t n, int64_t c0, int64_t c1, int64_t span, const uint8_t *plain, int64_t nvar, const uint8_t *ks,
                 int64_t r, const uint8_t *matrix, const uint8_t *mul, int64_t strand_nt, const int16_t *tpl,
                 int64_t gc_min, int64_t gc_max, int64_t win, int64_t wmin, int64_t wmax, int64_t hmax, int64_t tandem,
                 int64_t nmotif, const uint8_t *motif_buf, int64_t motif_bytes, const int64_t *motif_off,
                 const int64_t *motif_len, uint8_t *out, int16_t *chosen) {
    if (n < 0 || c0 < 0 || c1 < c0 || c1 > n || span < 0 || span > 4096 || nvar < 0 || nvar > 256 || r < 0 ||
        r > 4096 || strand_nt < 0 || strand_nt > 65536 || gc_min < 0 || gc_min > gc_max || gc_max > 100 || win < 0 ||
        win > 65536 || wmin < 0 || wmin > wmax || wmax > 100 || hmax < 0 || hmax > 65536 || tandem < 0 ||
        tandem > 65536 || nmotif < 0 || nmotif > 4096 || motif_bytes < 0)
        return ST_EARG;
    if (c1 == c0) return 0;
    if (!plain || !ks || !matrix || !mul || !tpl || !out || !chosen || (nmotif && (!motif_buf || !motif_off || !motif_len)))
        return ST_EARG;
    if (tandem && tandem < 6) return ST_EARG; /* the configuration validator's domain */
    const int64_t k = 1 + span, fbytes = k + r, fnt = 4 * fbytes;
    int64_t nframe = 0;
    for (int64_t x = 0; x < strand_nt; x++) {
        if (tpl[x] < -1 || tpl[x] > 3) return ST_EARG;
        nframe += tpl[x] < 0;
    }
    if (nframe != fnt) return ST_EARG;
    for (int64_t m = 0; m < nmotif; m++) {
        if (motif_len[m] < 1 || motif_len[m] > 64 || motif_off[m] < 0 || motif_off[m] > motif_bytes - motif_len[m])
            return ST_EARG;
        for (int64_t j = 0; j < motif_len[m]; j++)
            if (motif_buf[motif_off[m] + j] > 3) return ST_EARG;
    }
    const st_rules_t rules = {gc_min, gc_max, win, wmin, wmax, hmax, tandem, nmotif, motif_buf, motif_off, motif_len};
    uint8_t *msg = malloc((size_t)fbytes);
    int32_t *csum = malloc(sizeof(int32_t) * (size_t)(strand_nt + 1));
    if (!msg || !csum) {
        free(msg);
        free(csum);
        return ST_ENOMEM;
    }
    int64_t failed = 0;
    for (int64_t row = c0; row < c1; row++) {
        const uint8_t *pl = plain + row * span;
        uint8_t *s = out + row * strand_nt;
        int16_t pick = -1;
        for (int64_t v = 0; v < nvar && pick < 0; v++) {
            const uint8_t *kv = ks + v * span;
            msg[0] = (uint8_t)v;
            for (int64_t i = 0; i < span; i++) msg[1 + i] = pl[i] ^ kv[i];
            uint8_t *par = msg + k;
            memset(par, 0, (size_t)r);
            for (int64_t i = 0; i < k; i++) {
                const uint8_t *mrow = mul + 256 * (int64_t)msg[i], *coef = matrix + i * r;
                for (int64_t j = 0; j < r; j++) par[j] ^= mrow[coef[j]];
            }
            int64_t f = 0;
            for (int64_t x = 0; x < strand_nt; x++) {
                if (tpl[x] >= 0) {
                    s[x] = (uint8_t)tpl[x];
                } else {
                    s[x] = (msg[f >> 2] >> (6 - 2 * (f & 3))) & 3;
                    f++;
                }
            }
            if (st_ok(s, strand_nt, &rules, csum)) pick = (int16_t)v;
        }
        chosen[row] = pick;
        failed += pick < 0;
    }
    free(msg);
    free(csum);
    return (int)(failed > 0x7fffffff ? 0x7fffffff : failed);
}
