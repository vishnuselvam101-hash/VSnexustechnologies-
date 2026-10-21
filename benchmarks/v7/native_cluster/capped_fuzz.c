/* libFuzzer harness for the capped candidate step vnx_cl_cand_run_capped (src/vnxdna/native/c/cluster.c, ABI 6).
 *
 * The input bytes choose n, s, the hash alphabet, hashes, orientation codes, bucket_cap, max_pairs, min_shared,
 * cap_floor and the chunk size. Oracles, all against the floor-less forms:
 *   - not lowered (stats[9] == 0): the first nine counters and the pairs equal vnx_cl_cand_run's (stop included);
 *   - lowered to c: max(2, cap_floor) <= c < bucket_cap, the pairs and counters [1], [3], [4] equal
 *     vnx_cl_candidates at bucket cap c without a budget, [1] <= max_pairs, cap c + 1 does not fit, and [11] is the
 *     slot-pair total at bucket_cap;
 *   - every chunk size gives the same result.
 *
 *   clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
 *     benchmarks/v7/native_cluster/capped_fuzz.c src/vnxdna/native/c/cluster.c -lm -o capped_fuzz
 *   ./capped_fuzz -max_total_time=600 -max_len=4096 corpus/
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

int vnx_cl_candidates(int64_t n, int32_t s, const uint32_t *hashes, const uint8_t *orient, int64_t bucket_cap,
                      int64_t max_pairs, int64_t min_shared, int64_t *a_out, int64_t *b_out, uint8_t *rel_out,
                      int64_t out_cap, int64_t *stats);
int vnx_cl_cand_run(int64_t n, int32_t s, const uint32_t *hashes, const uint8_t *orient, int64_t bucket_cap,
                    int64_t max_pairs, int64_t min_shared, int64_t chunk_pairs, int64_t *stats, void **handle);
int vnx_cl_cand_run_capped(int64_t n, int32_t s, const uint32_t *hashes, const uint8_t *orient, int64_t bucket_cap,
                           int64_t max_pairs, int64_t min_shared, int64_t chunk_pairs, int64_t cap_floor,
                           int64_t *stats, void **handle);
int vnx_cl_cand_take(void *handle, int64_t n, int64_t *a_out, int64_t *b_out, uint8_t *rel_out, int64_t out_cap);
void vnx_cl_cand_free(void *handle);

#define MAXN 96
#define MAXS 8
#define CAP (MAXN * MAXN)

static const uint8_t *in_p;
static size_t in_left;
static uint8_t next(void) {
    if (!in_left) return 0;
    in_left--;
    return *in_p++;
}

static int64_t ra[CAP], rb[CAP], a[CAP], b[CAP];
static uint8_t rr[CAP], r[CAP];

/* run + take; returns the kept count (0 after a stop) */
static int64_t run_take(int capped, int64_t n, int32_t s, const uint32_t *h, const uint8_t *o, int64_t bc, int64_t mp,
                        int64_t ms, int64_t chunk, int64_t floor, int64_t *st, int64_t *oa, int64_t *ob, uint8_t *orr) {
    void *hd = NULL;
    const int rc = capped ? vnx_cl_cand_run_capped(n, s, h, o, bc, mp, ms, chunk, floor, st, &hd)
                          : vnx_cl_cand_run(n, s, h, o, bc, mp, ms, chunk, st, &hd);
    if (rc != 0 || !hd) abort();
    const int64_t k = st[2] ? 0 : st[3];
    if (vnx_cl_cand_take(hd, n > 0 ? n : 1, oa, ob, orr, k) != 0) abort();
    vnx_cl_cand_free(hd);
    return k;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    in_p = data;
    in_left = size;
    const uint8_t ctl = next();
    const int64_t n = next() % (MAXN + 1);
    const int32_t s = 1 + next() % MAXS;
    const uint32_t alpha = (uint32_t[]){1, 2, 3, 7, 40, 0xFFFFFFFFu}[next() % 6];
    const int64_t bucket_cap = (int64_t[]){0, 1, 2, 3, 8, 30, 96, 256}[next() % 8];
    const int64_t max_pairs = (int64_t[]){0, 1, 10, 100, 1000, INT64_MAX}[next() % 6];
    const int64_t min_shared = 1 + next() % 4;
    const int64_t floor = (int64_t[]){INT64_MIN + 1, 0, 1, 2, 3, 5, 64, INT64_MAX}[next() % 8];
    static uint32_t hashes[MAXN * MAXS];
    static uint8_t orient[MAXN * MAXS];
    for (int64_t i = 0; i < n * s; i++) {
        uint32_t h = (uint32_t)next() | (uint32_t)next() << 8;
        hashes[i] = alpha == 0xFFFFFFFFu ? h * 2654435761u : h % alpha;
        orient[i] = next() % 3;
    }
    int64_t st0[9], st[12], first[12], sref[7];
    const int64_t chunks[] = {1, 3, 1 + (ctl & 0x3F), INT64_MAX};
    const int64_t k = run_take(1, n, s, hashes, orient, bucket_cap, max_pairs, min_shared, INT64_MAX, floor, first, a,
                               b, r);
    for (size_t c = 0; c < sizeof chunks / sizeof *chunks; c++) { /* chunking never changes the result */
        static int64_t ca[CAP], cb[CAP];
        static uint8_t cr[CAP];
        const int64_t kc = run_take(1, n, s, hashes, orient, bucket_cap, max_pairs, min_shared, chunks[c], floor, st,
                                    ca, cb, cr);
        if (kc != k || memcmp(st, first, 7 * sizeof *st) || memcmp(st + 9, first + 9, 3 * sizeof *st)) abort();
        if (memcmp(ca, a, (size_t)k * sizeof *a) || memcmp(cb, b, (size_t)k * sizeof *b) || memcmp(cr, r, (size_t)k))
            abort();
    }
    const int64_t cap = first[9];
    if (cap == 0) { /* not lowered: the floor-less result */
        const int64_t k0 = run_take(0, n, s, hashes, orient, bucket_cap, max_pairs, min_shared, INT64_MAX, 0, st0, ra,
                                    rb, rr);
        if (first[10] || first[11] || k0 != k || memcmp(st0, first, 7 * sizeof *st0)) abort();
        if (memcmp(ra, a, (size_t)k * sizeof *a) || memcmp(rb, b, (size_t)k * sizeof *b) || memcmp(rr, r, (size_t)k))
            abort();
        return 0;
    }
    const int64_t lo = floor > 2 ? floor : 2;
    if (cap < lo || cap >= bucket_cap || first[2] || first[1] > max_pairs || first[10] < 1) abort();
    if (vnx_cl_candidates(n, s, hashes, orient, cap, INT64_MAX, min_shared, ra, rb, rr, CAP, sref) != 0) abort();
    if (sref[1] != first[1] || sref[3] != first[3] || sref[4] != first[4] || sref[3] != k) abort();
    if (memcmp(ra, a, (size_t)k * sizeof *a) || memcmp(rb, b, (size_t)k * sizeof *b) || memcmp(rr, r, (size_t)k))
        abort();
    if (vnx_cl_candidates(n, s, hashes, orient, cap + 1, INT64_MAX, min_shared, ra, rb, rr, CAP, sref) != 0) abort();
    if (sref[1] <= max_pairs) abort(); /* the largest fitting cap */
    if (vnx_cl_candidates(n, s, hashes, orient, bucket_cap, INT64_MAX, min_shared, ra, rb, rr, CAP, sref) != 0) abort();
    if (sref[1] != first[11]) abort();
    return 0;
}
