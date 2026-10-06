/* Golden-vector replay for the native read parser (src/vnxdna/v6/native/reads.c), built by tools/msan.sh with
 * MemorySanitizer. The kernel is #included, so this binary needs neither CPython nor NumPy.
 *
 * Every record holds one input file (FASTQ / FASTA / plain), the parser settings the binding uses (format, MAX_READ_NT,
 * line limit, block size), a feed chunk size, and the reference's result (vnxdna.v4.reads: all records concatenated,
 * or "error"). The file is parsed twice, mirroring the feed loop of vnxdna.native.reads.iter_reads_native:
 *   mode 0: one growing batch (BATCH_FULL -> grow lengths/invalid, NEED_SPACE -> grow codes/quals);
 *   mode 1: batches of at most 3 records (BATCH_FULL -> copy the batch out, vnx_reads_new_batch; grow on E_STATE).
 * Output buffers are grown with realloc and never initialised, so a byte the kernel reports but did not write is an
 * MSan finding when it is compared. Exit 0: identical; 1: mismatch / contract violation; 3: bad vector file. */
#include "vnxdna/v6/native/reads.c"
#include "vecio.h"

enum { NEED_INPUT = 0, BATCH_FULL = 1, NEED_SPACE = 2, NEED_MORE = 3, DONE = 4 };

typedef struct {
    uint8_t *codes, *quals, *invalid;
    int64_t *lengths;
    int64_t n, used, ncap, ucap;
} acc_t;

static void acc_add(acc_t *a, const uint8_t *codes, const uint8_t *quals, const int64_t *lengths, const uint8_t *invalid,
                    int64_t n, int64_t used) {
    while (a->used + used > a->ucap) {
        a->ucap = a->ucap * 2 + 64;
        a->codes = realloc(a->codes, (size_t)a->ucap);
        a->quals = realloc(a->quals, (size_t)a->ucap);
    }
    while (a->n + n > a->ncap) {
        a->ncap = a->ncap * 2 + 16;
        a->lengths = realloc(a->lengths, (size_t)a->ncap * 8);
        a->invalid = realloc(a->invalid, (size_t)a->ncap);
    }
    if (used) memcpy(a->codes + a->used, codes, (size_t)used);
    if (used && quals) memcpy(a->quals + a->used, quals, (size_t)used);
    if (n) memcpy(a->lengths + a->n, lengths, (size_t)n * 8);
    if (n) memcpy(a->invalid + a->n, invalid, (size_t)n);
    a->n += n;
    a->used += used;
}

/* returns the final parser code (DONE or a negative error); the records parsed so far are in *a */
static int parse(const int64_t *s, const uint8_t *data, int64_t len, int mode, acc_t *a) {
    const int32_t fmt = (int32_t)s[0];
    const int64_t max_nt = s[1], limit = s[2], block = s[3], chunk = s[4];
    uint8_t *state = malloc((size_t)vnx_reads_state_size());
    if (vnx_reads_init(state, vnx_reads_state_size(), fmt, max_nt, limit, block) != 0) {
        fprintf(stderr, "init failed\n");
        exit(1);
    }
    int64_t cap = 64 + max_nt, lcap = mode == 0 ? 2 : 3;
    uint8_t *codes = malloc((size_t)cap), *quals = malloc((size_t)cap), *invalid = malloc((size_t)lcap);
    int64_t *lengths = malloc((size_t)lcap * 8);
    int64_t info[4];
    uint8_t ctx[40];
    int64_t pos = 0, window = 0;
    int rc = 0;
    for (;;) {
        if (window == 0) window = chunk < len - pos ? chunk : len - pos;
        const int final = pos + window >= len;
        rc = vnx_reads_feed(state, data + pos, window, final, codes, fmt == 1 ? quals : NULL, cap, lengths, invalid, lcap,
                            info, ctx);
        if (info[0] < 0 || info[0] > window || info[1] < 0 || info[1] > lcap || info[2] < 0 || info[2] > cap) {
            fprintf(stderr, "info out of range\n");
            exit(1);
        }
        if (rc < 0 || rc == DONE) {
            /* ctx holds header bytes for error messages: read them (info[3] of them) like the binding does */
            volatile uint8_t sink = 0;
            for (int64_t i = 0; i < info[3] && i < 40; i++) sink ^= ctx[i];
            (void)sink;
            if (rc == DONE) acc_add(a, codes, fmt == 1 ? quals : NULL, lengths, invalid, info[1], info[2]);
            break;
        }
        pos += info[0];
        window -= info[0];
        if (rc == NEED_INPUT) { window = 0; continue; }
        if (rc == NEED_MORE) {
            if (final) { fprintf(stderr, "NEED_MORE on the final chunk\n"); exit(1); }
            const int64_t more = window + chunk;
            window = more < len - pos ? more : len - pos;
            continue;
        }
        if (rc == NEED_SPACE) {
            cap = cap * 2 + max_nt;
            codes = realloc(codes, (size_t)cap);
            quals = realloc(quals, (size_t)cap);
            continue;
        }
        if (rc == BATCH_FULL) {
            if (mode == 1 && vnx_reads_new_batch(state) == 0) {
                acc_add(a, codes, fmt == 1 ? quals : NULL, lengths, invalid, info[1], info[2]);
                continue;
            }
            lcap *= 2;
            lengths = realloc(lengths, (size_t)lcap * 8);
            invalid = realloc(invalid, (size_t)lcap);
            continue;
        }
        fprintf(stderr, "unknown return code %d\n", rc);
        exit(1);
    }
    free(state); free(codes); free(quals); free(lengths); free(invalid);
    return rc;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s vectors.bin\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) vec_die("cannot open");
    const int64_t records = vec_records(f);
    int64_t parsed = 0, errors = 0, bytes = 0;
    for (int64_t r = 0; r < records; r++) {
        vec_t sc = vec_read(f, 4);
        if (sc.count != 6) vec_die("reads scalars");
        const int64_t *s = sc.data;
        vec_t data = vec_read(f, 1), ec = vec_read(f, 1), eq = vec_read(f, 1), el = vec_read(f, 4), ei = vec_read(f, 1);
        const int expect_error = (int)s[5];
        for (int mode = 0; mode < 2; mode++) {
            acc_t a = {0};
            const int rc = parse(s, data.data, data.count, mode, &a);
            if (expect_error) {
                if (rc >= 0) {
                    fprintf(stderr, "record %lld mode %d: reference raised, native returned %d\n", (long long)r, mode, rc);
                    return 1;
                }
            } else {
                if (rc != DONE) {
                    fprintf(stderr, "record %lld mode %d: native error %d, reference parsed\n", (long long)r, mode, rc);
                    return 1;
                }
                if (a.n != el.count || a.used != ec.count) {
                    fprintf(stderr, "record %lld mode %d: %lld records / %lld nt, expected %lld / %lld\n", (long long)r, mode,
                            (long long)a.n, (long long)a.used, (long long)el.count, (long long)ec.count);
                    return 1;
                }
                if (!vec_same("codes", r, a.codes, ec.data, (size_t)a.used) ||
                    !vec_same("lengths", r, a.lengths, el.data, (size_t)a.n * 8) ||
                    !vec_same("invalid", r, a.invalid, ei.data, (size_t)a.n) ||
                    (s[0] == 1 && !vec_same("quals", r, a.quals, eq.data, (size_t)a.used)))
                    return 1;
            }
            free(a.codes); free(a.quals); free(a.lengths); free(a.invalid);
        }
        parsed += !expect_error;
        errors += expect_error;
        bytes += data.count;
        vec_free(&sc); vec_free(&data); vec_free(&ec); vec_free(&eq); vec_free(&el); vec_free(&ei);
    }
    fclose(f);
    printf("{\"kernel\": \"reads\", \"records\": %lld, \"parsed\": %lld, \"errors\": %lld, \"bytes\": %lld, \"mismatches\": 0}\n",
           (long long)records, (long long)parsed, (long long)errors, (long long)bytes);
    return 0;
}
