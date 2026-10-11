/* libFuzzer harness for the V10 W2 polish trial-cost kernel vnx_cl_trial_costs (src/vnxdna/native/c/cluster.c).
 *
 * The input bytes choose candidates, template length, reads (lengths, per-read bands, bytes), per-row dip flags,
 * costs and trial templates (a changed window of the base template, or arbitrary codes). The oracle is independent of
 * the kernel's windowed join: for every (trial, read) row it runs the reference-shaped full forward pass with a shared
 * band of band + dip (consensus._fb: per-read band masks, unmasked insertion run), takes the optimum, clips it to
 * CL_INF and to pen, and sums per trial and per candidate base. The kernel must return exactly these sums. Byte 5 bit
 * 7 makes one argument invalid; the kernel must then return CL_EARG (-1) without writing any output.
 *
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
 *     benchmarks/v7/native_cluster/trial_costs_fuzz.c src/vnxdna/native/c/cluster.c -lm -o trial_costs_fuzz
 *   ./trial_costs_fuzz -max_total_time=600 -max_len=8192 corpus/
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int vnx_cl_trial_costs(int64_t c0, int64_t c1, int64_t ncand, int64_t T, const int16_t *base_tpl, const int32_t *mc,
                       const uint8_t *buf, int64_t nbuf, const int64_t *off, const int64_t *len, const int64_t *band,
                       const int64_t *read_start, const uint8_t *base_dip, int32_t c_indel, int64_t pen,
                       const int16_t *trial_tpl, const int64_t *trial_start, const int64_t *trial_row,
                       int64_t ntrialrows, const uint8_t *trial_dip, int64_t *trial_cost, int64_t *base_cost);

#define CL_INF (1 << 28)
#define CL_EARG (-1)
#define MAXC 3
#define MAXR 4
#define MAXX 4

static const uint8_t *in_p;
static size_t in_left;
static uint8_t next(void) {
    if (!in_left) return 0;
    in_left--;
    return *in_p++;
}

static int64_t mn(int64_t a, int64_t b) { return a < b ? a : b; }

/* the reference-shaped forward pass with shared band Bs (>= b) */
static int64_t oracle_opt(int64_t T, const int16_t *tp, const int32_t *mc, const uint8_t *rd, int64_t L, int64_t b,
                          int64_t Bs, int64_t c) {
    const int64_t W = 2 * Bs + 1;
    int64_t *P = malloc(sizeof(int64_t) * (size_t)W), *Q = malloc(sizeof(int64_t) * (size_t)W);
    for (int64_t x = 0; x < W; x++) {
        const int64_t d = x - Bs, ad = d < 0 ? -d : d;
        P[x] = (d >= 0 && d <= L && ad <= b) ? d * c : CL_INF;
    }
    for (int64_t i = 1; i <= T; i++) {
        int64_t run = CL_INF;
        for (int64_t x = 0; x < W; x++) {
            const int64_t jd = i - 1 + x - Bs, j = jd + 1, ad = x - Bs < 0 ? Bs - x : x - Bs;
            const int okd = jd >= 0 && jd < L;
            const int64_t cost = (tp[i - 1] < 0 || (okd && rd[jd] == tp[i - 1])) ? 0 : mc[i - 1];
            int64_t v = mn(okd ? P[x] + cost : CL_INF, (x + 1 < W ? P[x + 1] : CL_INF) + c);
            v = mn(v, run + c);
            run = v;
            Q[x] = (j >= 0 && j <= L && ad <= b) ? v : CL_INF;
        }
        int64_t *t = P;
        P = Q;
        Q = t;
    }
    const int64_t dl = L - T;
    int64_t o = (dl < 0 ? -dl : dl) <= b ? P[dl + Bs] : CL_INF;
    if (o >= CL_INF) o = CL_INF;
    free(P);
    free(Q);
    return o;
}

int LLVMFuzzerTestOneInput(const uint8_t *in, size_t size) {
    if (size < 6) return 0;
    in_p = in;
    in_left = size;
    const int64_t ncand = 1 + next() % MAXC, T = next() % 49;
    const int32_t c_indel = (int32_t)(next() % 8);
    const int64_t pen = (int64_t)next() * 64;
    const uint8_t mode = next(), flags = next();

    int16_t base[MAXC * 48 + 1], trial[MAXC * MAXX * 48 + 1];
    int32_t mc[49];
    int64_t rs[MAXC + 1], ts[MAXC + 1], off[MAXC * MAXR], len[MAXC * MAXR], band[MAXC * MAXR], trow[MAXC * MAXX];
    uint8_t bdip[MAXC * MAXR], tdip[MAXC * MAXX * MAXR + 1];
    for (int64_t t = 0; t < T; t++) mc[t] = mode & 1 ? 4 * next() : next() % 10;
    for (int64_t i = 0; i < ncand * T; i++) base[i] = (int16_t)(mode & 2 ? (int8_t)next() : (int)(next() % 5) - 1);
    int64_t nreads = 0, ntrial = 0, nrows = 0, nbuf = 0;
    rs[0] = ts[0] = 0;
    for (int64_t k = 0; k < ncand; k++) {
        const int64_t nr = next() % (MAXR + 1), nx = next() % (MAXX + 1);
        for (int64_t q = 0; q < nr; q++, nreads++) {
            band[nreads] = next() % 13;
            len[nreads] = next() % (T + 2 * band[nreads] + 3);
            off[nreads] = nbuf;
            nbuf += len[nreads];
            bdip[nreads] = next() & 1;
        }
        rs[k + 1] = nreads;
        for (int64_t x = 0; x < nx; x++, ntrial++) {
            int16_t *u = trial + ntrial * T;
            memcpy(u, base + k * T, sizeof(int16_t) * (size_t)T);
            if (T > 0) {
                const int64_t a = next() % T, e = mn(T, a + next() % 9);
                for (int64_t i = a; i < e; i++) u[i] = (int16_t)(mode & 4 ? (int8_t)next() : (int)(next() % 5) - 1);
            }
            trow[ntrial] = nrows;
            for (int64_t q = 0; q < nr; q++) tdip[nrows++] = next() & 1;
        }
        ts[k + 1] = ntrial;
    }
    uint8_t *buf = malloc((size_t)nbuf + 1);
    for (int64_t i = 0; i < nbuf; i++) buf[i] = mode & 8 ? next() : next() % 5;

    int expect = 0;
    if (flags & 0x80) { /* one invalid argument, chosen by the next byte */
        const uint8_t which = next() % 6;
        int64_t kk = -1; /* a candidate with reads and trials: its first trial's rows can be pushed past the end */
        for (int64_t k = 0; k < ncand && kk < 0; k++)
            if (rs[k + 1] > rs[k] && ts[k + 1] > ts[k]) kk = k;
        if (which == 0 && nreads) band[0] = 513;
        else if (which == 1 && nreads) off[nreads - 1] = nbuf - len[nreads - 1] + 1;
        else if (which == 2 && T > 0) mc[0] = 1025;
        else if (which == 3 && nreads) len[0] = -1;
        else if (which == 4 && kk >= 0) trow[ts[kk]] = nrows - (rs[kk + 1] - rs[kk]) + 1;
        else rs[0] = 1;
        expect = CL_EARG;
    }

    int64_t cost[MAXC * MAXX + 1], basec[MAXC];
    for (int64_t i = 0; i <= ntrial; i++) cost[i] = -7;
    for (int64_t k = 0; k < ncand; k++) basec[k] = -7;
    const int rc = vnx_cl_trial_costs(0, ncand, ncand, T, base, mc, buf, nbuf, off, len, band, rs, bdip, c_indel, pen,
                                      trial, ts, trow, nrows, tdip, cost, basec);
    if (rc != expect) abort();
    if (rc != 0) {
        for (int64_t i = 0; i <= ntrial; i++)
            if (cost[i] != -7) abort();
        for (int64_t k = 0; k < ncand; k++)
            if (basec[k] != -7) abort();
        free(buf);
        return 0;
    }
    for (int64_t k = 0; k < ncand; k++) {
        int64_t bs = 0;
        for (int64_t r = rs[k]; r < rs[k + 1]; r++)
            bs += mn(pen, oracle_opt(T, base + k * T, mc, buf + off[r], len[r], band[r], band[r] + bdip[r], c_indel));
        if (basec[k] != bs) abort();
        for (int64_t x = ts[k]; x < ts[k + 1]; x++) {
            int64_t s = 0;
            for (int64_t r = rs[k]; r < rs[k + 1]; r++) {
                const int64_t bsh = band[r] + tdip[trow[x] + r - rs[k]];
                s += mn(pen, oracle_opt(T, trial + x * T, mc, buf + off[r], len[r], band[r], bsh, c_indel));
            }
            if (cost[x] != s) abort();
        }
    }
    if (cost[ntrial] != -7) abort(); /* nothing past the last trial is written */
    free(buf);
    return 0;
}
