/* libFuzzer harness for the V9 polish kernel vnx_cl_edit_costs (src/vnxdna/native/c/cluster.c).
 *
 * The input bytes choose the batch shape (reads n, template length T, shared band B, costs) and then fill the
 * templates, per-position mismatch costs, read lengths, per-read bands and read bytes. Values are not restricted to
 * the 0..3 alphabet: the kernel must stay memory-safe for any byte. Byte 5 bit 7 makes one argument invalid, and the
 * kernel must then return CL_EARG (-1) without touching the outputs. Any crash, sanitizer report, unexpected return
 * code, or an output outside [0, CL_INF] is a bug.
 *
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
 *     benchmarks/v7/native_cluster/edit_costs_fuzz.c src/vnxdna/native/c/cluster.c -lm -o edit_costs_fuzz
 *   ./edit_costs_fuzz -max_total_time=600 -max_len=8192 corpus/
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int vnx_cl_edit_costs(int64_t n, int64_t T, int64_t B, const int16_t *tpl, const int32_t *mc, const uint8_t *buf,
                      int64_t nbuf, const int64_t *off, const int64_t *len, const int64_t *band, int32_t c_indel,
                      int32_t c_sub, int64_t *opt, int64_t *sub, int64_t *dele, int64_t *ins);

#define CL_INF (1 << 28)
#define CL_EARG (-1)

static const uint8_t *in_p;
static size_t in_left;
static uint8_t next(void) {
    if (!in_left) return 0;
    in_left--;
    return *in_p++;
}

int LLVMFuzzerTestOneInput(const uint8_t *in, size_t size) {
    if (size < 6) return 0;
    in_p = in;
    in_left = size;
    const int64_t n = 1 + next() % 4, T = next() % 65, B = next() % 17;
    const int32_t c_indel = 4 * next(), c_sub = 4 * next();
    const uint8_t flags = next();

    int16_t *tpl = malloc(sizeof(int16_t) * (size_t)(n * T + 1));
    int32_t *mc = malloc(sizeof(int32_t) * (size_t)(n * T + 1));
    int64_t off[4], len[4], band[4], nbuf = 0;
    for (int64_t i = 0; i < n * T; i++) tpl[i] = (int16_t)(flags & 1 ? next() : next() % 4);
    for (int64_t i = 0; i < n * T; i++) mc[i] = 4 * next();
    for (int64_t r = 0; r < n; r++) {
        len[r] = next() % (T + 2 * B + 8);
        band[r] = next() % (B + 1);
        off[r] = nbuf;
        nbuf += len[r];
    }
    uint8_t *buf = malloc((size_t)nbuf + 1);
    for (int64_t i = 0; i < nbuf; i++) buf[i] = flags & 2 ? next() : next() % 4;

    int expect = 0;
    if (flags & 0x80) {  /* one invalid argument, chosen by the next byte */
        const uint8_t which = next() % 4;
        if (which == 0) band[0] = B + 1;
        else if (which == 1) off[n - 1] = nbuf - len[n - 1] + 1;
        else if (which == 2 && T > 0) mc[0] = 1025;
        else len[0] = -1;
        expect = CL_EARG;
    }

    int64_t *opt = malloc(sizeof(int64_t) * (size_t)n);
    int64_t *sub = malloc(sizeof(int64_t) * (size_t)(n * T * 4 + 1));
    int64_t *dele = malloc(sizeof(int64_t) * (size_t)(n * T + 1));
    int64_t *ins = malloc(sizeof(int64_t) * (size_t)(n * T * 4 + 1));
    for (int64_t r = 0; r < n; r++) opt[r] = -7;

    const int rc = vnx_cl_edit_costs(n, T, B, tpl, mc, buf, nbuf, off, len, band, c_indel, c_sub, opt, sub, dele, ins);
    if (rc != expect) abort();
    if (rc == 0) {
        for (int64_t r = 0; r < n; r++)
            if (opt[r] < 0 || opt[r] > CL_INF) abort();
        for (int64_t i = 0; i < n * T; i++) {
            if (dele[i] < 0 || dele[i] > CL_INF) abort();
            for (int k = 0; k < 4; k++)
                if (sub[i * 4 + k] < 0 || sub[i * 4 + k] > CL_INF || ins[i * 4 + k] < 0 || ins[i * 4 + k] > CL_INF)
                    abort();
        }
    } else {
        for (int64_t r = 0; r < n; r++)
            if (opt[r] != -7) abort();
    }
    free(tpl);
    free(mc);
    free(buf);
    free(opt);
    free(sub);
    free(dele);
    free(ins);
    return 0;
}
