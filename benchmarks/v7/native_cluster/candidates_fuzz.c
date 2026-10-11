/* libFuzzer harness for the V10 range-partitioned candidate step vnx_cl_cand_run / _take / _free
 * (src/vnxdna/native/c/cluster.c, ABI 4).
 *
 * The input bytes choose n, s, the hash alphabet, hashes, orientation codes (0, 1, 2 = no sketch), bucket_cap,
 * max_pairs, min_shared and the chunk size. Oracle: the same inputs through the single-buffer form vnx_cl_candidates
 * (one range, the pre-V10 algorithm). Both must return the same status, the same seven counters and, unless the
 * budget stopped the step, the same pairs in the same order at every chunk size. Byte 0 bit 7 makes one argument
 * invalid; the handle API must then return CL_EARG (-1) and leave *handle NULL.
 *
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
 *     benchmarks/v7/native_cluster/candidates_fuzz.c src/vnxdna/native/c/cluster.c -lm -o candidates_fuzz
 *   ./candidates_fuzz -max_total_time=600 -max_len=4096 corpus/
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int vnx_cl_candidates(int64_t n, int32_t s, const uint32_t *hashes, const uint8_t *orient, int64_t bucket_cap,
                      int64_t max_pairs, int64_t min_shared, int64_t *a_out, int64_t *b_out, uint8_t *rel_out,
                      int64_t out_cap, int64_t *stats);
int vnx_cl_cand_run(int64_t n, int32_t s, const uint32_t *hashes, const uint8_t *orient, int64_t bucket_cap,
                    int64_t max_pairs, int64_t min_shared, int64_t chunk_pairs, int64_t *stats, void **handle);
int vnx_cl_cand_take(void *handle, int64_t n, int64_t *a_out, int64_t *b_out, uint8_t *rel_out, int64_t out_cap);
void vnx_cl_cand_free(void *handle);

#define CL_EARG (-1)
#define MAXN 96
#define MAXS 8

static const uint8_t *in_p;
static size_t in_left;
static uint8_t next(void) {
    if (!in_left) return 0;
    in_left--;
    return *in_p++;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    in_p = data;
    in_left = size;
    const uint8_t ctl = next();
    const int64_t n = next() % (MAXN + 1);
    const int32_t s = 1 + next() % MAXS;
    const uint32_t alpha = (uint32_t[]){1, 2, 3, 7, 40, 0xFFFFFFFFu}[next() % 6];
    const int64_t bucket_cap = (int64_t[]){0, 1, 2, 3, 8, 256}[next() % 6];
    const int64_t max_pairs = (int64_t[]){0, 1, 10, 100, 1000, INT64_MAX}[next() % 6];
    const int64_t min_shared = 1 + next() % 4;
    uint32_t hashes[MAXN * MAXS];
    uint8_t orient[MAXN * MAXS];
    for (int64_t i = 0; i < n * s; i++) {
        uint32_t h = (uint32_t)next() | (uint32_t)next() << 8;
        hashes[i] = alpha == 0xFFFFFFFFu ? h * 2654435761u : h % alpha;
        orient[i] = next() % 3;
    }
    int64_t st_ref[7], st[9];
    static int64_t ra[MAXN * MAXN], rb[MAXN * MAXN], a[MAXN * MAXN], b[MAXN * MAXN];
    static uint8_t rr[MAXN * MAXN], r[MAXN * MAXN];
    const int64_t cap = MAXN * MAXN; /* >= any kept count: pairs are distinct (a, b) with a < b < n */
    void *h = (void *)1;
    if (ctl & 0x80) { /* one invalid argument: CL_EARG and no handle */
        int rc;
        switch (ctl % 4) {
        case 0: rc = vnx_cl_cand_run(-1, s, hashes, orient, bucket_cap, max_pairs, min_shared, 1, st, &h); break;
        case 1: rc = vnx_cl_cand_run(n, 0, hashes, orient, bucket_cap, max_pairs, min_shared, 1, st, &h); break;
        case 2: rc = vnx_cl_cand_run(n, s, hashes, orient, bucket_cap, -1, min_shared, 1, st, &h); break;
        default: rc = vnx_cl_cand_run(n, s, hashes, orient, bucket_cap, max_pairs, min_shared, 0, st, &h); break;
        }
        if (rc != CL_EARG || h != NULL) abort();
        return 0;
    }
    const int rc_ref = vnx_cl_candidates(n, s, hashes, orient, bucket_cap, max_pairs, min_shared, ra, rb, rr, cap,
                                         st_ref);
    if (rc_ref != 0) abort(); /* cap is always large enough; the inputs are valid */
    const int64_t chunks[] = {1, 2, 3, 17, 1 + (ctl & 0x3F), INT64_MAX};
    for (size_t c = 0; c < sizeof chunks / sizeof *chunks; c++) {
        if (vnx_cl_cand_run(n, s, hashes, orient, bucket_cap, max_pairs, min_shared, chunks[c], st, &h) != 0) abort();
        if (!h) abort();
        if (memcmp(st, st_ref, sizeof st_ref)) abort();
        const int64_t k = st[2] ? 0 : st[3];
        if (k > 0 && vnx_cl_cand_take(h, n, a, b, r, k - 1) != CL_EARG) abort(); /* too small: refused */
        if (vnx_cl_cand_take(h, n < 1 ? 1 : n, a, b, r, k) != 0) abort();
        vnx_cl_cand_free(h);
        h = NULL;
        if (k && (memcmp(a, ra, (size_t)k * 8) || memcmp(b, rb, (size_t)k * 8) || memcmp(r, rr, (size_t)k))) abort();
    }
    return 0;
}
