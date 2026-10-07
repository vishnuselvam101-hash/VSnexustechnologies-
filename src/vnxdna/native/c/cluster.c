/* VNX-DNA V7 item A: native kernels of the read-clustering stage (V7_ARCHITECTURE §5.2-5.3).
 *
 * The NumPy reference in vnxdna/recovery/cluster/ is the specification; every function here returns exactly what the
 * corresponding reference function returns (bit-exact, checked by golden hashes and randomized equivalence tests):
 *
 *   vnx_cl_sketch      sketch.sketch_reads_reference        canonical k-mer MinHash (splitmix64), orientation bits
 *   vnx_cl_candidates  graph.candidate_pairs_reference      buckets, pairs sharing >= min_shared slots, sorted
 *   vnx_cl_banded      editdist.banded_distance_reference   banded unit-cost edit distance of read pairs
 *   vnx_cl_verify      the verification loop of graph.cluster_reads_reference (chunks, union-find with parity)
 *   vnx_cl_fb          consensus.fb_calls_reference         forward-backward certain calls against templates
 *
 * Banded edit distance. The reference computes the global distance restricted to the diagonals
 * [min(0, dL) - slack, max(0, dL) + slack]. A path that leaves this band needs at least 2 * slack + 2 + |dL| indels, so
 * when the unbanded distance d* is <= 2 * slack + 1 + |dL| the banded value equals d*. d* is computed with the
 * bit-parallel algorithm of Myers (1999) (multi-word blocks); only when d* exceeds that bound is the banded dynamic
 * program run (and in vnx_cl_verify not even then when d* already exceeds the acceptance threshold, because the banded
 * value is >= d*).
 *
 * Forward-backward. The reference vectorises over the reads of a chunk with one shared band B (the chunk's largest
 * band) and per-read band masks; this kernel takes the same B and evaluates the same cells over d in [-B, B] (8 reads
 * per group, structure of arrays), so it reproduces the reference even where cells just outside a read's own band take
 * part in the insertion/deletion recurrences. An AVX2 build of the group kernel is selected at run time (cpuid +
 * xgetbv); the library itself is built without -march. All arithmetic is int32 on values below 2^30 (the Python side
 * checks the cost domain), so it never overflows.
 *
 * Plain C11 shared library, loaded with ctypes (no Python headers). No global mutable state: the functions are
 * reentrant and may run concurrently on disjoint outputs (the binding releases the GIL around every call). */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(__x86_64__) || defined(__i386__)
#include <cpuid.h>
#define VNX_CL_X86 1
#endif

#ifndef VNX_CL_ABI /* overridable only so that the tests can build a mismatching library */
#define VNX_CL_ABI 1
#endif
#define CL_INF (1 << 28)

enum { CL_OK = 0, CL_EARG = -1, CL_ENOMEM = -2 };

int vnx_cl_abi_version(void) { return VNX_CL_ABI; }

/* ------------------------------------------------------------------------------------------------ sketch */
static inline uint64_t splitmix64(uint64_t x) {
    uint64_t z = x + 0x9E3779B97F4A7C15ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/* raw: n rows of `width` codes (raw_len bytes available); lengths: n read lengths (windows end at min(length, width)); out: n*s hashes and
 * orientation bits (0xFFFFFFFF / 2 when the read has no k-mer without N). */
int vnx_cl_sketch(int64_t n, int64_t width, const uint8_t *raw, int64_t raw_len, const int64_t *lengths, int32_t k,
                  int32_t s, uint32_t *hashes, uint8_t *orient) {
    if (n < 0 || width < 0 || raw_len < 0 || k < 1 || k > 31 || s < 1 || s > 256) return CL_EARG;
    if (n > 0 && (!raw || !lengths || !hashes || !orient)) return CL_EARG;
    if (width > 0 && n > raw_len / width) return CL_EARG; /* n rows of width codes must lie inside raw */
    uint64_t seeds[256];
    for (int i = 0; i < s; i++) seeds[i] = splitmix64((uint64_t)0x56584E37u + (uint64_t)i);
    const uint64_t mask = (k == 32) ? ~0ULL : ((1ULL << (2 * k)) - 1);
    uint64_t best[256];
    for (int64_t r = 0; r < n; r++) {
        const uint8_t *row = raw + r * width;
        int64_t len = lengths[r] < width ? lengths[r] : width;
        int has = 0;
        for (int i = 0; i < s; i++) best[i] = ~0ULL;
        uint64_t fwd = 0, rev = 0;
        int run = 0; /* number of consecutive valid bases ending here */
        for (int64_t p = 0; p < len; p++) {
            uint8_t c = row[p];
            if (c > 3) {
                run = 0;
                fwd = rev = 0;
                continue;
            }
            fwd = ((fwd << 2) | c) & mask;
            rev = (rev >> 2) | ((uint64_t)(3 - c) << (2 * (k - 1)));
            if (++run < k) continue;
            const uint64_t ob = rev < fwd;
            const uint64_t canon = ob ? rev : fwd;
            has = 1;
            for (int i = 0; i < s; i++) {
                const uint64_t key = (splitmix64(canon ^ seeds[i]) & ~1ULL) | ob;
                if (key < best[i]) best[i] = key;
            }
        }
        for (int i = 0; i < s; i++) {
            hashes[r * s + i] = has ? (uint32_t)(best[i] >> 32) : 0xFFFFFFFFu;
            orient[r * s + i] = has ? (uint8_t)(best[i] & 1) : 2;
        }
    }
    return CL_OK;
}

/* ------------------------------------------------------------------------------------------------ candidate pairs */
typedef struct {
    uint32_t h;
    int64_t idx;
} hent_t;

static int cmp_hent(const void *x, const void *y) {
    const hent_t *a = x, *b = y;
    if (a->h != b->h) return a->h < b->h ? -1 : 1;
    return (a->idx > b->idx) - (a->idx < b->idx);
}

static int cmp_u64(const void *x, const void *y) {
    const uint64_t a = *(const uint64_t *)x, b = *(const uint64_t *)y;
    return (a > b) - (a < b);
}

typedef struct {
    int64_t key, shared;
    uint8_t rel;
} pent_t;

static int cmp_pent(const void *x, const void *y) {
    const pent_t *a = x, *b = y;
    if (a->shared != b->shared) return a->shared > b->shared ? -1 : 1; /* more shared slots first */
    return (a->key > b->key) - (a->key < b->key);
}

/* stats (7 entries): [0] buckets_over_cap, [1] slot pairs counted (candidate_pairs_slots), [2] 1 = max_pairs exceeded,
 *        [3] pairs kept (>= min_shared), [4] pairs below min_shared, [5] slots with >= 2 sketched reads, [6] retained
 *        buckets (size in [2, bucket_cap]); [5] and [6] tell which counters the reference creates.
 * Returns CL_OK (outputs written when stats[3] <= out_cap), 1 when out_cap is too small (nothing written; call again
 * with out_cap >= stats[3]), or a negative error. */
int vnx_cl_candidates(int64_t n, int32_t s, const uint32_t *hashes, const uint8_t *orient, int64_t bucket_cap,
                      int64_t max_pairs, int64_t min_shared, int64_t *a_out, int64_t *b_out, uint8_t *rel_out,
                      int64_t out_cap, int64_t *stats) {
    if (n < 0 || s < 1 || bucket_cap < 0 || max_pairs < 0 || out_cap < 0 || !stats) return CL_EARG;
    if (n > ((int64_t)1 << 31)) return CL_EARG; /* key = a * n + b must fit in 63 bits */
    if (n > 0 && (!hashes || !orient)) return CL_EARG;
    for (int i = 0; i < 7; i++) stats[i] = 0;
    if (n < 2) return CL_OK;
    hent_t *ent = malloc((size_t)n * sizeof(hent_t));
    size_t cap = 1024, cnt = 0;
    uint64_t *pairs = malloc(cap * sizeof(uint64_t)); /* (key << 1) | xor bit */
    if (!ent || !pairs) {
        free(ent);
        free(pairs);
        return CL_ENOMEM;
    }
    int64_t total = 0;
    for (int i = 0; i < s; i++) {
        int64_t m = 0;
        for (int64_t r = 0; r < n; r++)
            if (orient[r * s + i] < 2) {
                ent[m].h = hashes[r * s + i];
                ent[m].idx = r;
                m++;
            }
        if (m < 2) continue;
        stats[5]++;
        qsort(ent, (size_t)m, sizeof(hent_t), cmp_hent);
        for (int64_t st = 0; st < m;) {
            int64_t en = st + 1;
            while (en < m && ent[en].h == ent[st].h) en++;
            if (en - st > bucket_cap) stats[0]++;
            st = en;
        }
        for (int64_t st = 0; st < m;) {
            int64_t en = st + 1;
            while (en < m && ent[en].h == ent[st].h) en++;
            const int64_t sz = en - st;
            if (sz >= 2 && sz <= bucket_cap) {
                stats[6]++;
                total += sz * (sz - 1) / 2;
                if (total > max_pairs) {
                    stats[1] = total;
                    stats[2] = 1;
                    free(ent);
                    free(pairs);
                    return CL_OK;
                }
                if (cnt + (size_t)(sz * (sz - 1) / 2) > cap) {
                    size_t nc = cap;
                    while (cnt + (size_t)(sz * (sz - 1) / 2) > nc) nc *= 2;
                    uint64_t *np_ = realloc(pairs, nc * sizeof(uint64_t));
                    if (!np_) {
                        free(ent);
                        free(pairs);
                        return CL_ENOMEM;
                    }
                    pairs = np_;
                    cap = nc;
                }
                for (int64_t p = st; p < en; p++)
                    for (int64_t q = p + 1; q < en; q++) {
                        const int64_t a = ent[p].idx, b = ent[q].idx; /* sorted by index within a bucket */
                        const uint64_t x = (uint64_t)(orient[a * s + i] ^ orient[b * s + i]) & 1;
                        pairs[cnt++] = ((uint64_t)(a * n + b) << 1) | x;
                    }
            }
            st = en;
        }
    }
    free(ent);
    stats[1] = total;
    qsort(pairs, cnt, sizeof(uint64_t), cmp_u64);
    int64_t kept = 0, below = 0;
    for (size_t p = 0; p < cnt;) {
        size_t q = p;
        while (q < cnt && (pairs[q] >> 1) == (pairs[p] >> 1)) q++;
        if ((int64_t)(q - p) >= min_shared) kept++;
        else below++;
        p = q;
    }
    stats[3] = kept;
    stats[4] = below;
    if (kept > out_cap) {
        free(pairs);
        return 1;
    }
    if (kept > 0 && (!a_out || !b_out || !rel_out)) {
        free(pairs);
        return CL_EARG;
    }
    pent_t *pe = malloc((size_t)(kept ? kept : 1) * sizeof(pent_t));
    if (!pe) {
        free(pairs);
        return CL_ENOMEM;
    }
    int64_t t = 0;
    for (size_t p = 0; p < cnt;) {
        size_t q = p;
        int64_t xs = 0;
        while (q < cnt && (pairs[q] >> 1) == (pairs[p] >> 1)) xs += (int64_t)(pairs[q++] & 1);
        const int64_t sh = (int64_t)(q - p);
        if (sh >= min_shared) {
            pe[t].key = (int64_t)(pairs[p] >> 1);
            pe[t].shared = sh;
            pe[t].rel = 2 * xs > sh;
            t++;
        }
        p = q;
    }
    free(pairs);
    qsort(pe, (size_t)kept, sizeof(pent_t), cmp_pent);
    for (int64_t i = 0; i < kept; i++) {
        a_out[i] = pe[i].key / n;
        b_out[i] = pe[i].key % n;
        rel_out[i] = pe[i].rel;
    }
    free(pe);
    return CL_OK;
}

/* ------------------------------------------------------------------------------------------------ edit distance */
static const uint8_t RC[8] = {3, 2, 1, 0, 4, 5, 6, 7};

/* workspace of one distance computation; grown on demand */
typedef struct {
    uint64_t *peq, *pv, *mv; /* 4*words, words, words */
    int64_t words_cap;
    int32_t *row0, *row1; /* banded DP rows */
    int64_t row_cap;
    uint8_t *tmp;        /* reverse-complemented text */
    int64_t tmp_cap;
} ws_t;

static void ws_free(ws_t *w) {
    free(w->peq);
    free(w->pv);
    free(w->mv);
    free(w->row0);
    free(w->row1);
    free(w->tmp);
    memset(w, 0, sizeof(*w));
}

static int ws_words(ws_t *w, int64_t words) {
    if (words <= w->words_cap) return 0;
    free(w->peq);
    free(w->pv);
    free(w->mv);
    w->peq = malloc((size_t)words * 5 * sizeof(uint64_t));
    w->pv = malloc((size_t)words * sizeof(uint64_t));
    w->mv = malloc((size_t)words * sizeof(uint64_t));
    w->words_cap = (w->peq && w->pv && w->mv) ? words : 0;
    return w->words_cap ? 0 : -1;
}

static int ws_rows(ws_t *w, int64_t width) {
    if (width <= w->row_cap) return 0;
    free(w->row0);
    free(w->row1);
    w->row0 = malloc((size_t)width * sizeof(int32_t));
    w->row1 = malloc((size_t)width * sizeof(int32_t));
    w->row_cap = (w->row0 && w->row1) ? width : 0;
    return w->row_cap ? 0 : -1;
}

static int ws_tmp(ws_t *w, int64_t len) {
    if (len <= w->tmp_cap) return 0;
    free(w->tmp);
    w->tmp = malloc((size_t)(len ? len : 1));
    w->tmp_cap = w->tmp ? len : 0;
    return w->tmp ? 0 : -1;
}

/* Unbanded Levenshtein distance, Myers (1999) bit-vector with blocks of 64 rows (Hyyrö's block carry hin/hout in
 * {-1, 0, +1}, branch-free); code c matches iff equal and < 4. With `stop` >= 0 the computation ends as soon as the
 * bottom-row score minus the columns left exceeds `stop` (each column changes the bottom row by at most 1, so the
 * distance is then > stop) and that lower bound is returned. */
static int64_t myers(ws_t *w, const uint8_t *a, int64_t m, const uint8_t *b, int64_t nb, int64_t stop) {
    if (m == 0) return nb;
    if (nb == 0) return m;
    const int64_t words = (m + 63) / 64;
    uint64_t *peq = w->peq, *pv = w->pv, *mv = w->mv;
    memset(peq, 0, (size_t)(words * 5) * sizeof(uint64_t)); /* 4 codes + an all-zero row for codes >= 4 */
    for (int64_t p = 0; p < m; p++)
        if (a[p] < 4) peq[a[p] * words + p / 64] |= 1ULL << (p % 64);
    for (int64_t x = 0; x < words; x++) {
        pv[x] = ~0ULL;
        mv[x] = 0;
    }
    const int lastbit = (int)((m - 1) % 64);
    int64_t score = m;
    for (int64_t j = 0; j < nb; j++) {
        const uint64_t *eqv = peq + (b[j] < 4 ? b[j] : 4) * words;
        uint64_t hp = 1, hm = 0; /* horizontal delta entering the block: +1 at the top row (D[0][j] = j) */
        for (int64_t x = 0; x < words; x++) {
            const uint64_t P = pv[x], M = mv[x];
            uint64_t eq = eqv[x];
            const uint64_t xv = eq | M;
            eq |= hm;
            const uint64_t xh = (((eq & P) + P) ^ P) | eq;
            const uint64_t ph = M | ~(xh | P);
            const uint64_t mh = P & xh;
            const int hb = (x == words - 1) ? lastbit : 63;
            const uint64_t op = (ph >> hb) & 1, om = (mh >> hb) & 1;
            const uint64_t phs = (ph << 1) | hp, mhs = (mh << 1) | hm;
            pv[x] = mhs | ~(xv | phs);
            mv[x] = phs & xv;
            hp = op;
            hm = om;
        }
        score += (int64_t)hp - (int64_t)hm;
        if (stop >= 0 && score - (nb - 1 - j) > stop) return score - (nb - 1 - j);
    }
    return score;
}

/* The reference's banded dynamic program (rows a, columns b), diagonals [min(0, dL) - slack, max(0, dL) + slack]. */
static int64_t banded_dp(ws_t *w, const uint8_t *a, int64_t la, const uint8_t *b, int64_t lb, int64_t slack) {
    const int64_t delta = lb - la;
    const int64_t lo = (delta < 0 ? delta : 0) - slack, hi = (delta > 0 ? delta : 0) + slack;
    const int64_t W = hi - lo + 1;
    int32_t *prev = w->row0, *cur = w->row1; /* index = d - lo */
    for (int64_t x = 0; x < W; x++) {
        const int64_t d = lo + x;
        prev[x] = (d >= 0 && d <= lb) ? (int32_t)d : CL_INF;
    }
    for (int64_t i = 1; i <= la; i++) {
        const uint8_t ai = a[i - 1];
        int32_t run = CL_INF;
        for (int64_t x = 0; x < W; x++) {
            const int64_t d = lo + x, j = i + d;
            int32_t v = CL_INF;
            if (j >= 0 && j <= lb) {
                if (j >= 1) {
                    const int32_t s = (ai < 4 && b[j - 1] == ai) ? 0 : 1;
                    if (prev[x] + s < v) v = prev[x] + s;
                }
                if (x + 1 < W && prev[x + 1] + 1 < v) v = prev[x + 1] + 1;
                if (run + 1 < v) v = run + 1;
            }
            cur[x] = v;
            run = v;
        }
        int32_t *t = prev;
        prev = cur;
        cur = t;
    }
    return prev[delta - lo];
}

/* exact banded distance; when `cap` >= 0 any value > cap may be returned for pairs whose banded value exceeds cap */
static int64_t banded_one(ws_t *w, const uint8_t *a, int64_t la, const uint8_t *b, int64_t lb, int64_t slack,
                          double thr, int use_thr) {
    const uint8_t *p = a, *t = b;
    int64_t m = la, nt = lb;
    if (lb < la) { /* the shorter sequence is the pattern (the unbanded distance is symmetric) */
        p = b;
        t = a;
        m = lb;
        nt = la;
    }
    /* stop = floor(thr), the largest integer distance that can still be accepted; -1 = compute the exact distance
     * (no threshold, or a threshold that is negative, NaN or too large for int64) */
    const int64_t stop = (use_thr && thr >= 0.0 && thr < 9.0e18) ? (int64_t)thr : -1;
    const int64_t dstar = myers(w, p, m, t, nt, stop);
    const int64_t ad = la > lb ? la - lb : lb - la;
    /* exact d* (or, after an early stop, a lower bound > floor(thr) that the caller rejects like the exact value) */
    if (dstar <= 2 * slack + 1 + ad) return dstar;
    if (use_thr && (double)dstar > thr) return dstar; /* banded >= d* > threshold: rejected either way */
    return banded_dp(w, a, la, b, lb, slack);
}

static int prep_ws(ws_t *w, int64_t maxlen, int64_t slack) {
    if (ws_words(w, (maxlen + 63) / 64 + 1)) return -1;
    if (ws_rows(w, maxlen + 2 * slack + 2)) return -1;
    if (ws_tmp(w, maxlen)) return -1;
    return 0;
}

/* every sequence [off, off + len) lies inside a buffer of nbuf bytes; lengths at most maxlen */
static int check_seqs(int64_t nseq, int64_t nbuf, const int64_t *off, const int64_t *len, int64_t maxlen_cap,
                      int64_t *maxlen) {
    *maxlen = 0;
    if (nbuf < 0) return -1;
    for (int64_t s = 0; s < nseq; s++) {
        if (len[s] < 0 || len[s] > maxlen_cap || off[s] < 0 || off[s] > nbuf - len[s]) return -1;
        if (len[s] > *maxlen) *maxlen = len[s];
    }
    return 0;
}

/* Banded distances of npairs pairs (buf[off[ia[p]]], buf[off[ib[p]]]); brc[p] = 1 reverse-complements b first
 * (codes must be < 8 then). Returns CL_OK or an error. */
int vnx_cl_banded(int64_t nseq, const uint8_t *buf, int64_t nbuf, const int64_t *off, const int64_t *len,
                  int64_t npairs, const int64_t *ia, const int64_t *ib, const uint8_t *brc, int64_t slack, int64_t *out) {
    if (nseq < 0 || npairs < 0 || slack < 0 || slack > (1 << 20)) return CL_EARG;
    if (nseq > 0 && (!off || !len)) return CL_EARG;
    if (npairs > 0 && (!buf || !ia || !ib || !out)) return CL_EARG;
    int64_t maxlen;
    if (check_seqs(nseq, nbuf, off, len, (int64_t)1 << 24, &maxlen)) return CL_EARG;
    for (int64_t p = 0; p < npairs; p++)
        if (ia[p] < 0 || ia[p] >= nseq || ib[p] < 0 || ib[p] >= nseq) return CL_EARG;
    ws_t w = {0};
    if (prep_ws(&w, maxlen, slack)) {
        ws_free(&w);
        return CL_ENOMEM;
    }
    for (int64_t p = 0; p < npairs; p++) {
        const uint8_t *a = buf + off[ia[p]], *b = buf + off[ib[p]];
        const int64_t la = len[ia[p]], lb = len[ib[p]];
        if (brc && brc[p]) {
            for (int64_t j = 0; j < lb; j++) w.tmp[j] = RC[b[lb - 1 - j] & 7];
            b = w.tmp;
        }
        out[p] = banded_one(&w, a, la, b, lb, slack, 0.0, 0);
    }
    ws_free(&w);
    return CL_OK;
}

/* ------------------------------------------------------------------------------------------------ verification */
static int64_t uf_find(int64_t *parent, uint8_t *par, int64_t *stack, int64_t x, int *parity) {
    int64_t depth = 0;
    while (parent[x] != x) {
        stack[depth++] = x;
        x = parent[x];
    }
    const int64_t root = x;
    int acc = 0;
    for (int64_t t = depth - 1; t >= 0; t--) {
        const int64_t node = stack[t];
        acc ^= par[node];
        par[node] = (uint8_t)acc;
        parent[node] = root;
    }
    *parity = depth ? par[stack[0]] : 0;
    return root;
}

/* The verification loop of graph.cluster_reads: pairs (pa, pb, prel, pmax) in verification order, processed in chunks of
 * `chunk`; a pair whose reads are in one component at the start of its chunk is skipped, the others are verified
 * (banded distance of read a and read b, reverse-complemented when prel = 1) and accepted iff d <= theta * pmax; the
 * accepted edges of a chunk are then united in order. Outputs: root/orient of every read (union-find with parity, the
 * root is the smallest index), best (in/out: the best score 1 - d / pmax of an accepted edge per read) and counters
 * [skipped, verified, accepted, rejected, orientation conflicts]. Codes must be < 8. */
int vnx_cl_verify(int64_t n, const uint8_t *buf, int64_t nbuf, const int64_t *off, const int64_t *len, int64_t npairs,
                  const int64_t *pa, const int64_t *pb, const uint8_t *prel, const int64_t *pmax, double theta,
                  int64_t slack, int64_t chunk, int64_t *root_out, uint8_t *orient_out, double *best,
                  int64_t *counters) {
    if (n < 0 || npairs < 0 || slack < 0 || slack > (1 << 20) || chunk < 1 || !counters) return CL_EARG;
    if (n > 0 && (!buf || !off || !len || !root_out || !orient_out || !best)) return CL_EARG;
    if (npairs > 0 && (!pa || !pb || !prel || !pmax)) return CL_EARG;
    int64_t maxlen;
    if (check_seqs(n, nbuf, off, len, (int64_t)1 << 24, &maxlen)) return CL_EARG;
    for (int64_t p = 0; p < npairs; p++)
        if (pa[p] < 0 || pa[p] >= n || pb[p] < 0 || pb[p] >= n || pmax[p] < 1) return CL_EARG;
    for (int i = 0; i < 5; i++) counters[i] = 0;
    int64_t *parent = malloc((size_t)(n ? n : 1) * sizeof(int64_t));
    int64_t *stack = malloc((size_t)(n ? n : 1) * sizeof(int64_t));
    uint8_t *par = calloc((size_t)(n ? n : 1), 1);
    int64_t *dist = malloc((size_t)chunk * sizeof(int64_t));
    uint8_t *todo = malloc((size_t)chunk);
    ws_t w = {0};
    if (!parent || !stack || !par || !dist || !todo || prep_ws(&w, maxlen, slack)) {
        free(parent);
        free(stack);
        free(par);
        free(dist);
        free(todo);
        ws_free(&w);
        return CL_ENOMEM;
    }
    for (int64_t x = 0; x < n; x++) parent[x] = x;
    int parity;
    for (int64_t c0 = 0; c0 < npairs; c0 += chunk) {
        const int64_t c1 = c0 + chunk < npairs ? c0 + chunk : npairs;
        int64_t ntodo = 0;
        for (int64_t p = c0; p < c1; p++) {
            const int64_t ra = uf_find(parent, par, stack, pa[p], &parity);
            const int64_t rb = uf_find(parent, par, stack, pb[p], &parity);
            todo[p - c0] = ra != rb;
            ntodo += ra != rb;
        }
        counters[0] += (c1 - c0) - ntodo;
        if (!ntodo) continue;
        for (int64_t p = c0; p < c1; p++) {
            if (!todo[p - c0]) continue;
            const uint8_t *a = buf + off[pa[p]], *b = buf + off[pb[p]];
            const int64_t la = len[pa[p]], lb = len[pb[p]];
            if (prel[p]) {
                for (int64_t j = 0; j < lb; j++) w.tmp[j] = RC[b[lb - 1 - j] & 7];
                b = w.tmp;
            }
            dist[p - c0] = banded_one(&w, a, la, b, lb, slack, theta * (double)pmax[p], 1);
        }
        counters[1] += ntodo;
        for (int64_t p = c0; p < c1; p++) {
            if (!todo[p - c0]) continue;
            const int64_t d = dist[p - c0];
            if (!((double)d <= theta * (double)pmax[p])) {
                counters[3]++;
                continue;
            }
            counters[2]++;
            const int64_t i = pa[p], j = pb[p];
            const double sc = 1.0 - (double)d / (double)pmax[p];
            if (sc > best[i]) best[i] = sc;
            if (sc > best[j]) best[j] = sc;
            int pi, pj;
            const int64_t ri = uf_find(parent, par, stack, i, &pi);
            const int64_t rj = uf_find(parent, par, stack, j, &pj);
            if (ri == rj) {
                if ((pi ^ pj) != prel[p]) counters[4]++;
                continue;
            }
            const int64_t lo = ri < rj ? ri : rj, hi = ri < rj ? rj : ri;
            parent[hi] = lo;
            par[hi] = (uint8_t)(pi ^ pj ^ prel[p]);
        }
    }
    for (int64_t x = 0; x < n; x++) {
        root_out[x] = uf_find(parent, par, stack, x, &parity);
        orient_out[x] = (uint8_t)parity;
    }
    free(parent);
    free(stack);
    free(par);
    free(dist);
    free(todo);
    ws_free(&w);
    return CL_OK;
}

/* ------------------------------------------------------------------------------------------------ forward-backward */
/* Forward-backward group state. Element arrays are int32 (8 lanes) or int16 (16 lanes) depending on the variant. */
#define LANES_MAX 16
#define FB16_INF 15000
typedef struct {
    int64_t T, B, W, width, ncols; /* ncols = T + 2B + 1 read columns per lane (j = -B .. T + B) */
    int32_t c_indel, slack;
    void *F;       /* (T+1) rows of (W+1) * lanes; column W is an INF pad (the "d + 1 beyond the band" cell) */
    void *G, *G2;  /* (W+1) * lanes; column 0 is an INF pad, column x+1 holds d = x - B */
    void *bm;      /* W * lanes: -1 where |d| <= band of the lane */
    void *rb;      /* ncols * lanes read bases (5 = no base) */
    void *tc, *mc; /* T * lanes */
    int32_t len[LANES_MAX], band[LANES_MAX];
    int64_t opt[LANES_MAX];
    uint8_t *calls; /* lanes * T */
} fb_t;

/* One group of FL reads (one vector of element type ET per row cell: 32 bytes in the AVX2 clones, 16 bytes in the
 * baseline functions; VT is the vector type, FINF the infinity). Every cell is the reference's cell (same recurrences over d in [-B, B], same masks), computed for all
 * lanes at once with GCC/clang vector extensions (AVX2 in the target("avx2") clones, SSE2 on baseline x86-64).
 * int32 (FINF = 2^28): all values stay below 2^30. int16 (FINF = 15000): used only when every finite path cost plus
 * the slack is below FINF ((T + max read length + 2) * max(c_indel, mc) + slack < FINF), so values derived from FINF
 * (at most 2 * FINF + 2 * 1024 < 2^15) compare exactly as the reference's INF-derived values do: above every finite
 * value and every live threshold. Only the calls and the finite optimal costs leave the kernel. */
typedef int32_t v8si __attribute__((vector_size(32)));
typedef int16_t v16hi __attribute__((vector_size(32)));
typedef int32_t v4si __attribute__((vector_size(16))); /* baseline (SSE2-sized) variants */
typedef int16_t v8hi __attribute__((vector_size(16)));
#define VLD(VT, p) __extension__({ VT v_; memcpy(&v_, (p), sizeof(VT)); v_; })
#define VST(p, v) memcpy((p), &(v), sizeof(v))
#define VSEL(m, a, b) (((m) & (a)) | (~(m) & (b)))
#define VMIN(a, b) VSEL((a) < (b), (a), (b))
#define VMAX(a, b) VSEL((a) > (b), (a), (b))

#define FB_BODY(VT, ET, FL, FINF)                                                                                    \
    const int64_t T = g->T, B = g->B, W = g->W, S = (g->W + 1) * FL;                                                  \
    ET *F = (ET *)g->F, *bm = (ET *)g->bm;                                                                            \
    const ET *rb = (const ET *)g->rb, *tc = (const ET *)g->tc, *mc = (const ET *)g->mc;                               \
    const VT zero = {0};                                                                                              \
    const VT inf = zero + (ET)(FINF), cv = zero + (ET)g->c_indel;                                                     \
    ET tmp[FL];                                                                                                       \
    for (int l = 0; l < FL; l++) tmp[l] = (ET)g->len[l];                                                              \
    const VT lenv = VLD(VT, tmp);                                                                                     \
    for (int64_t x = 0; x < W; x++) {                                                                                 \
        const int32_t d = (int32_t)(x - B), ad = d < 0 ? -d : d;                                                      \
        for (int l = 0; l < FL; l++) bm[x * FL + l] = (ET)(ad <= g->band[l] ? -1 : 0);                                \
        for (int l = 0; l < FL; l++)                                                                                  \
            F[x * FL + l] = (ET)((d >= 0 && d <= g->len[l] && ad <= g->band[l]) ? d * g->c_indel : (FINF));           \
    }                                                                                                                 \
    for (int64_t i = 0; i <= T; i++) VST(F + i * S + W * FL, inf);                                                    \
    for (int64_t i = 1; i <= T; i++) {                                                                                \
        const ET *prev = F + (i - 1) * S;                                                                             \
        ET *cur = F + i * S;                                                                                          \
        const VT tci = VLD(VT, tc + (i - 1) * FL), mci = VLD(VT, mc + (i - 1) * FL);                                  \
        const VT tcneg = tci < zero;                                                                                  \
        VT run = inf;                                                                                                 \
        for (int64_t x = 0; x < W; x++) {                                                                             \
            const int64_t jd = i - 1 + x - B, j = jd + 1;                                                             \
            const VT rbv = VLD(VT, rb + (i - 1 + x) * FL);                                                            \
            const VT cost = ~(tcneg | (rbv == tci)) & mci;                                                            \
            const VT okd = jd >= 0 ? ((zero + (ET)jd) < lenv) : zero;                                                 \
            const VT pd = VLD(VT, prev + x * FL), pn = VLD(VT, prev + (x + 1) * FL);                                  \
            const VT diag = VSEL(okd, pd + cost, inf);                                                                \
            const VT dele = pn + cv;                                                                                  \
            VT v = VMIN(diag, dele);                                                                                  \
            const VT r = run + cv;                                                                                    \
            v = VMIN(v, r);                                                                                           \
            run = v;                                                                                                  \
            const VT ok = j >= 0 ? (~(lenv < (zero + (ET)j)) & VLD(VT, bm + x * FL)) : zero;                          \
            const VT out = VSEL(ok, v, inf);                                                                          \
            VST(cur + x * FL, out);                                                                                   \
        }                                                                                                             \
    }                                                                                                                 \
    int32_t live[FL];                                                                                                 \
    for (int l = 0; l < FL; l++) {                                                                                    \
        const int64_t dl = (int64_t)g->len[l] - T;                                                                    \
        const int fit = (dl < 0 ? -dl : dl) <= g->band[l];                                                            \
        int64_t we = dl + B;                                                                                          \
        if (we < 0) we = 0;                                                                                           \
        if (we > W - 1) we = W - 1;                                                                                   \
        int64_t o = fit ? (int64_t)F[T * S + we * FL + l] : CL_INF;                                                   \
        if (o >= (FINF)) o = CL_INF; /* the reference's INF (2^28) in either width */                                \
        g->opt[l] = o;                                                                                                \
        live[l] = o < CL_INF;                                                                                         \
        tmp[l] = (ET)(live[l] ? o + g->slack : (FINF)); /* a dead lane's calls are 4 whatever its threshold */       \
    }                                                                                                                 \
    const VT thr = VLD(VT, tmp);                                                                                      \
    ET *G = (ET *)g->G + FL, *Gn = (ET *)g->G2 + FL; /* G[-1] is the INF pad */                                       \
    VST(G - FL, inf);                                                                                                 \
    VST(Gn - FL, inf);                                                                                                \
    for (int64_t x = 0; x < W; x++) {                                                                                 \
        const int32_t d = (int32_t)(x - B), ad = d < 0 ? -d : d;                                                      \
        const int64_t j = T + d;                                                                                      \
        for (int l = 0; l < FL; l++)                                                                                  \
            G[x * FL + l] = (ET)((j >= 0 && j <= g->len[l] && ad <= g->band[l]) ? (g->len[l] - j) * g->c_indel       \
                                                                                 : (FINF));                          \
    }                                                                                                                 \
    const VT b99 = zero + (ET)99, bm1 = zero - (ET)1;                                                                 \
    for (int64_t i = T - 1; i >= 0; i--) {                                                                            \
        const ET *Fi = F + i * S;                                                                                     \
        const VT tci = VLD(VT, tc + i * FL), mci = VLD(VT, mc + i * FL);                                              \
        const VT tcneg = tci < zero;                                                                                  \
        VT bmin = b99, bmax = bm1, any = zero, del = zero, run = inf;                                                 \
        /* descending d: the certain-call reductions (order free) and the new G row (suffix recurrence) in one pass */ \
        for (int64_t x = W - 1; x >= 0; x--) {                                                                        \
            const int64_t jd = i + x - B;                                                                             \
            const VT rbv = VLD(VT, rb + (i + x) * FL);                                                                \
            const VT cost = ~(tcneg | (rbv == tci)) & mci;                                                            \
            const VT okd = jd >= 0 ? ((zero + (ET)jd) < lenv) : zero;                                                 \
            const VT g0 = VLD(VT, G + x * FL), gm = VLD(VT, G + (x - 1) * FL), f = VLD(VT, Fi + x * FL);              \
            const VT match = VSEL(okd, f + cost + g0, inf);                                                           \
            const VT m = ~(thr < match);                                                                              \
            any |= m;                                                                                                 \
            bmin = VMIN(bmin, VSEL(m, rbv, b99));                                                                     \
            bmax = VMAX(bmax, VSEL(m, rbv, bm1));                                                                     \
            del |= ~(thr < f + cv + gm);                                                                              \
            const VT diag = VSEL(okd, cost + g0, inf);                                                                \
            const VT dele = gm + cv;                                                                                  \
            VT v = VMIN(diag, dele);                                                                                  \
            const VT r = run + cv;                                                                                    \
            v = VMIN(v, r);                                                                                           \
            run = v;                                                                                                  \
            const VT ok = jd >= 0 ? (~(lenv < (zero + (ET)jd)) & VLD(VT, bm + x * FL)) : zero;                        \
            const VT out = VSEL(ok, v, inf);                                                                          \
            VST(Gn + x * FL, out);                                                                                    \
        }                                                                                                             \
        ET an[FL], de[FL], mn[FL], mx[FL];                                                                            \
        VST(an, any);                                                                                                 \
        VST(de, del);                                                                                                 \
        VST(mn, bmin);                                                                                                \
        VST(mx, bmax);                                                                                                \
        for (int l = 0; l < FL; l++) {                                                                                \
            const int cert = live[l] && !de[l] && an[l] && mn[l] == mx[l] && mn[l] < 4;                               \
            g->calls[l * T + i] = cert ? (uint8_t)mn[l] : 4;                                                          \
        }                                                                                                             \
        ET *t_ = G;                                                                                                   \
        G = Gn;                                                                                                       \
        Gn = t_;                                                                                                      \
    }

static void fb32_scalar(fb_t *g) { FB_BODY(v4si, int32_t, 4, CL_INF) }
static void fb16_scalar(fb_t *g) { FB_BODY(v8hi, int16_t, 8, FB16_INF) }

#ifdef VNX_CL_X86
__attribute__((target("avx2"))) static void fb32_avx2(fb_t *g) { FB_BODY(v8si, int32_t, 8, CL_INF) }
__attribute__((target("avx2"))) static void fb16_avx2(fb_t *g) { FB_BODY(v16hi, int16_t, 16, FB16_INF) }

static int cpu_has_avx2(void) {
    unsigned a, b, c, d;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return 0;
    if (!(c & (1u << 27)) || !(c & (1u << 28))) return 0; /* OSXSAVE, AVX */
    unsigned lo, hi;
    __asm__ volatile(".byte 0x0f, 0x01, 0xd0" /* xgetbv */ : "=a"(lo), "=d"(hi) : "c"(0));
    if ((lo & 6) != 6) return 0; /* XMM and YMM state saved by the OS */
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return 0;
    return (b >> 5) & 1;
}
#endif

/* 1 = scalar, 2 = avx2 */
int vnx_cl_fb_level(void) {
#ifdef VNX_CL_X86
    return cpu_has_avx2() ? 2 : 1;
#else
    return 1;
#endif
}

/* Certain calls of n reads against per-read templates (consensus.fb_calls on one chunk with shared band B).
 * tpl: n*T int16 (negative = wildcard); mc: n*T int32 mismatch costs; reads at buf[off[r]] with len[r] bases, inside
 * the nbuf bytes of buf;
 * band: n per-read bands (0 <= band <= B); calls: n*T output (4 = not certain); opt: n output (2^28 = does not fit).
 * level: 0 = best available, 1 = scalar, 2 = avx2 (error if unsupported). width: 0 = int16 when its bound holds else
 * int32, 32 = int32, 16 = int16 (error if the bound does not hold). Cost domain (checked): 0 <= mc, c_indel <= 1024,
 * T <= 8192, B <= 512, slack <= 2^20, read length <= 2^20. Results are identical for every level and width. */
int vnx_cl_fb(int64_t n, int64_t T, int64_t B, const int16_t *tpl, const int32_t *mc, const uint8_t *buf, int64_t nbuf,
              const int64_t *off, const int64_t *len, const int64_t *band, int32_t c_indel, int32_t slack,
              uint8_t *calls, int64_t *opt, int32_t level, int32_t width) {
    if (n < 0 || T < 0 || T > 8192 || B < 0 || B > 512 || c_indel < 0 || c_indel > 1024 || slack < 0 ||
        slack > (1 << 20) || (level != 0 && level != 1 && level != 2) || (width != 0 && width != 16 && width != 32))
        return CL_EARG;
    if (n > 0 && (!tpl || !mc || !buf || !off || !len || !band || !calls || !opt || nbuf < 0)) return CL_EARG;
    int64_t maxlen = 0, maxstep = c_indel;
    for (int64_t r = 0; r < n; r++) {
        if (len[r] < 0 || len[r] > (1 << 20) || off[r] < 0 || off[r] > nbuf - len[r] || band[r] < 0 || band[r] > B)
            return CL_EARG;
        if (len[r] > maxlen) maxlen = len[r];
        for (int64_t t = 0; t < T; t++) {
            const int32_t v = mc[r * T + t];
            if (v < 0 || v > 1024) return CL_EARG;
            if (v > maxstep) maxstep = v;
        }
    }
    if (n == 0) return CL_OK;
    /* (maxstep >= 1 in the bound: with all costs 0 the lane lengths and indices must still fit the int16 lanes) */
    const int fits16 = (T + maxlen + 2) * (maxstep > 0 ? maxstep : 1) + slack < FB16_INF;
    if (width == 16 && !fits16) return CL_EARG;
    const int w16 = width == 16 || (width == 0 && fits16);
    int use_avx2 = 0;
#ifdef VNX_CL_X86
    if (level == 0 || level == 2) use_avx2 = cpu_has_avx2();
    if (level == 2 && !use_avx2) return CL_EARG;
#else
    if (level == 2) return CL_EARG;
#endif
    const int FLn = (w16 ? 8 : 4) * (use_avx2 ? 2 : 1); /* lanes of the selected variant */
    const size_t es = w16 ? sizeof(int16_t) : sizeof(int32_t);
    fb_t g;
    memset(&g, 0, sizeof(g));
    g.T = T;
    g.B = B;
    g.W = 2 * B + 1;
    g.width = T + B + 2;
    g.ncols = T + 2 * B + 1;
    g.c_indel = c_indel;
    g.slack = slack;
    const size_t Tn = (size_t)(T ? T : 1);
    g.F = malloc((size_t)(T + 1) * (size_t)(g.W + 1) * (size_t)FLn * es);
    g.G = malloc((size_t)(g.W + 1) * (size_t)FLn * es);
    g.G2 = malloc((size_t)(g.W + 1) * (size_t)FLn * es);
    g.bm = malloc((size_t)g.W * (size_t)FLn * es);
    g.rb = malloc((size_t)g.ncols * (size_t)FLn * es);
    g.tc = malloc(Tn * (size_t)FLn * es);
    g.mc = malloc(Tn * (size_t)FLn * es);
    g.calls = malloc(Tn * (size_t)FLn);
    int rc = CL_OK;
    if (!g.F || !g.G || !g.G2 || !g.bm || !g.rb || !g.tc || !g.mc || !g.calls) {
        rc = CL_ENOMEM;
        goto done;
    }
    for (int64_t r0 = 0; r0 < n; r0 += FLn) {
        const int nl = (int)(n - r0 < FLn ? n - r0 : FLn);
        for (int l = 0; l < FLn; l++) {
            const int64_t r = r0 + (l < nl ? l : 0); /* unused lanes repeat the first read; their output is dropped */
            g.len[l] = (int32_t)len[r];
            g.band[l] = (int32_t)band[r];
            const uint8_t *rd = buf + off[r];
            const int64_t keep = len[r] < g.width ? len[r] : g.width;
            for (int64_t col = 0; col < g.ncols; col++) {
                const int64_t j = col - B;
                const int32_t v = (j >= 0 && j < keep) ? (int32_t)rd[j] : 5;
                if (w16) ((int16_t *)g.rb)[col * FLn + l] = (int16_t)v;
                else ((int32_t *)g.rb)[col * FLn + l] = v;
            }
            for (int64_t t = 0; t < T; t++) {
                if (w16) {
                    ((int16_t *)g.tc)[t * FLn + l] = tpl[r * T + t];
                    ((int16_t *)g.mc)[t * FLn + l] = (int16_t)mc[r * T + t];
                } else {
                    ((int32_t *)g.tc)[t * FLn + l] = tpl[r * T + t];
                    ((int32_t *)g.mc)[t * FLn + l] = mc[r * T + t];
                }
            }
        }
#ifdef VNX_CL_X86
        if (use_avx2) {
            if (w16) fb16_avx2(&g);
            else fb32_avx2(&g);
        } else if (w16) fb16_scalar(&g);
        else fb32_scalar(&g);
#else
        if (w16) fb16_scalar(&g);
        else fb32_scalar(&g);
#endif
        for (int l = 0; l < nl; l++) {
            memcpy(calls + (r0 + l) * T, g.calls + (int64_t)l * T, (size_t)T);
            opt[r0 + l] = g.opt[l];
        }
    }
done:
    free(g.F);
    free(g.G);
    free(g.G2);
    free(g.bm);
    free(g.rb);
    free(g.tc);
    free(g.mc);
    free(g.calls);
    return rc;
}
