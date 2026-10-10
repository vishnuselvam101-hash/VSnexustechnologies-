/* libFuzzer harness for the fused polish kernel vnx_cl_polish_deltas (cluster ABI 5, src/vnxdna/native/c/cluster.c).
 *
 * Differential: the input bytes choose a batch (reads n, template length T, band B, costs, pen) and fill one template
 * per read, the shared mismatch costs, read lengths, per-read bands and read bytes (any byte, not only 0..3). The batch
 * is evaluated as one edit_costs chunk with shared band B (vnx_cl_edit_costs) and folded per read exactly as
 * consensus._polish folds it (min(cost, pen) - opt for reads with opt < CL_INF); vnx_cl_polish_deltas, with every read
 * its own candidate and dip = band < B, must return the same sums at the baseline level and (when the CPU has it) at
 * AVX2. Byte 6 bit 7 makes one argument invalid; the kernel must then return CL_EARG (-1). Any crash, sanitizer report,
 * unexpected return code or differing sum is a bug.
 *
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
 *     benchmarks/v7/native_cluster/polish_deltas_fuzz.c src/vnxdna/native/c/cluster.c -lm -o polish_deltas_fuzz
 *   ./polish_deltas_fuzz -max_total_time=600 -max_len=8192 corpus/
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int vnx_cl_edit_costs(int64_t n, int64_t T, int64_t B, const int16_t *tpl, const int32_t *mc, const uint8_t *buf,
                      int64_t nbuf, const int64_t *off, const int64_t *len, const int64_t *band, int32_t c_indel,
                      int32_t c_sub, int64_t *opt, int64_t *sub, int64_t *dele, int64_t *ins);
int vnx_cl_polish_deltas(int64_t ncand, int64_t c0, int64_t c1, int64_t T, const int16_t *tpl, const int32_t *mc,
                         const uint8_t *buf, int64_t nbuf, const int64_t *off, const int64_t *len,
                         const int64_t *read_start, const int64_t *band, const uint8_t *dip, int32_t c_indel,
                         int32_t c_sub, int64_t pen, int64_t *dsub, int64_t *ddel, int64_t *dins, int64_t *live,
                         int level);
int vnx_cl_fb_level(void);

#define CL_INF (1 << 28)
#define CL_EARG (-1)

static const uint8_t *in_p;
static size_t in_left;
static uint8_t next(void) {
    if (!in_left) return 0;
    in_left--;
    return *in_p++;
}

static int64_t fold(int64_t v, int64_t pen, int64_t o) { return (v < pen ? v : pen) - o; }

int LLVMFuzzerTestOneInput(const uint8_t *in, size_t size) {
    if (size < 7) return 0;
    in_p = in;
    in_left = size;
    const int64_t n = 1 + next() % 5, T = next() % 65, B = next() % 17;
    const int32_t c_indel = 4 * next(), c_sub = 4 * next();
    const int64_t pen = 37 * (int64_t)next();
    const uint8_t flags = next();

    int16_t *tpl = malloc(sizeof(int16_t) * (size_t)(n * T + 1));
    int16_t *tpl_rows = malloc(sizeof(int16_t) * (size_t)(n * T + 1));
    int32_t *mc = malloc(sizeof(int32_t) * (size_t)(T + 1));
    int32_t *mc_rows = malloc(sizeof(int32_t) * (size_t)(n * T + 1));
    int64_t off[5], len[5], band[5], rs[6], nbuf = 0;
    uint8_t dip[5];
    for (int64_t i = 0; i < n * T; i++) tpl[i] = (int16_t)(flags & 1 ? (int8_t)next() : next() % 4);
    for (int64_t t = 0; t < T; t++) mc[t] = 4 * next();
    for (int64_t r = 0; r < n; r++) {
        memcpy(tpl_rows + r * T, tpl + r * T, sizeof(int16_t) * (size_t)T);
        memcpy(mc_rows + r * T, mc, sizeof(int32_t) * (size_t)T);
        len[r] = next() % (T + 2 * B + 8);
        band[r] = next() % (B + 1);
        off[r] = nbuf;
        nbuf += len[r];
        rs[r] = r;
    }
    rs[n] = n;
    int64_t Bmax = 0;
    for (int64_t r = 0; r < n; r++) Bmax = band[r] > Bmax ? band[r] : Bmax;
    for (int64_t r = 0; r < n; r++) dip[r] = band[r] < Bmax;
    uint8_t *buf = malloc((size_t)nbuf + 1);
    for (int64_t i = 0; i < nbuf; i++) buf[i] = flags & 2 ? next() % 8 : next() % 4;

    int expect = 0;
    if (flags & 0x80) { /* one invalid argument, chosen by the next byte */
        const uint8_t which = next() % 5;
        if (which == 0) band[0] = 513;
        else if (which == 1) off[n - 1] = nbuf - len[n - 1] + 1;
        else if (which == 2 && T > 0) mc[0] = 1025;
        else if (which == 3) dip[0] = 2;
        else len[0] = -1;
        expect = CL_EARG;
    }

    int64_t *dsub = malloc(sizeof(int64_t) * (size_t)(n * T * 4 + 1)), *ddel = malloc(sizeof(int64_t) * (size_t)(n * T + 1));
    int64_t *dins = malloc(sizeof(int64_t) * (size_t)(n * T * 4 + 1)), live[5];
    const int levels = vnx_cl_fb_level() >= 2 ? 2 : 1;
    int64_t *opt = malloc(sizeof(int64_t) * (size_t)n), *sub = malloc(sizeof(int64_t) * (size_t)(n * T * 4 + 1));
    int64_t *dele = malloc(sizeof(int64_t) * (size_t)(n * T + 1)), *ins = malloc(sizeof(int64_t) * (size_t)(n * T * 4 + 1));
    int ref_ok = 0;
    if (expect == 0)
        ref_ok = vnx_cl_edit_costs(n, T, Bmax, tpl_rows, mc_rows, buf, nbuf, off, len, band, c_indel, c_sub, opt, sub,
                                   dele, ins) == 0;
    for (int level = 1; level <= levels; level++) {
        const int rc = vnx_cl_polish_deltas(n, 0, n, T, tpl, mc, buf, nbuf, off, len, rs, band, dip, c_indel, c_sub,
                                            pen, dsub, ddel, dins, live, level);
        if (rc != expect) abort();
        if (rc != 0) continue;
        if (!ref_ok) abort();
        for (int64_t r = 0; r < n; r++) {
            const int on = opt[r] < CL_INF;
            if (live[r] != on) abort();
            for (int64_t i = 0; i < T; i++) {
                if (ddel[r * T + i] != (on ? fold(dele[r * T + i], pen, opt[r]) : 0)) abort();
                for (int k = 0; k < 4; k++) {
                    const int64_t x = (r * T + i) * 4 + k;
                    if (dsub[x] != (on ? fold(sub[x], pen, opt[r]) : 0)) abort();
                    if (dins[x] != (on ? fold(ins[x], pen, opt[r]) : 0)) abort();
                }
            }
        }
    }
    free(tpl);
    free(tpl_rows);
    free(mc);
    free(mc_rows);
    free(buf);
    free(dsub);
    free(ddel);
    free(dins);
    free(opt);
    free(sub);
    free(dele);
    free(ins);
    return 0;
}
