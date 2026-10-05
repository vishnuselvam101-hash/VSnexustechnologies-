/* libFuzzer harness for the V6 native read parser (src/vnxdna/v6/native/reads.c), built by fuzz/run.sh as one
 * translation unit (the kernel is #included, so the harness always tests the source in this tree).
 * Mirrors the feed loop in vnxdna/v6/native_reads.py. Byte 0 picks the format, byte 1 the chunk size; the rest is
 * the file. Invariants: no sanitizer report; consumed/records/used counts within the capacities given; return codes in
 * the documented set; never NEED_MORE on the final chunk; every emitted base code in 0..4 and invalid flags 0/1.
 * info[4] and ctx[40] are sized exactly as the Python binding allocates them (an implicit ABI contract). */
#include "vnxdna/v6/native/reads.c"
#include <stdlib.h>
enum { NEED_INPUT = 0, BATCH_FULL = 1, NEED_SPACE = 2, NEED_MORE = 3, DONE = 4 };

int LLVMFuzzerTestOneInput(const uint8_t *in, size_t size) {
    if (size < 2) return 0;
    const int32_t fmt = 1 + in[0] % 3;               /* fastq, fasta, plain */
    const int64_t chunk = 1 + in[1];                 /* 1..256 byte chunks hit every boundary path */
    const uint8_t *data = in + 2;
    const int64_t len = (int64_t)size - 2;
    const int64_t max_nt = 512, block = 64;

    uint8_t *state = calloc(1, (size_t)vnx_reads_state_size());
    if (vnx_reads_init(state, vnx_reads_state_size(), fmt, max_nt, max_nt * 2 + 4096, block) != 0) abort();

    int64_t cap = max_nt + 64, lcap = 4;
    uint8_t *codes = malloc((size_t)cap), *quals = malloc((size_t)cap), *invalid = malloc((size_t)lcap);
    int64_t *lengths = malloc((size_t)lcap * sizeof(int64_t));
    int64_t info[4];
    uint8_t ctx[40];

    int64_t pos = 0;     /* first byte not yet consumed by the parser */
    int64_t window = 0;  /* bytes offered in the current call, beyond pos */
    for (int guard = 0; guard < 1000000; guard++) {
        if (window == 0) window = chunk < len - pos ? chunk : len - pos;
        const int final = pos + window >= len;
        int rc = vnx_reads_feed(state, data + pos, window, final, codes, fmt == 1 ? quals : NULL, cap,
                                lengths, invalid, lcap, info, ctx);
        if (info[0] < 0 || info[0] > window) abort();      /* consumed out of range */
        if (info[1] < 0 || info[1] > lcap || info[2] < 0 || info[2] > cap) abort();
        for (int64_t i = 0; i < info[2]; i++)
            if (codes[i] > 4) abort();                      /* base codes are 0..4 (N = 4) */
        for (int64_t i = 0; i < info[1]; i++)
            if (invalid[i] > 1) abort();
        pos += info[0];
        window -= info[0];
        if (rc < 0) break;                                  /* structured parse error: fine */
        if (rc == DONE) break;
        if (rc == NEED_INPUT) { window = 0; continue; }       /* at the end this re-feeds 0 bytes, final */
        if (rc == NEED_MORE) {                              /* re-feed the rest plus more input */
            if (final) abort();                             /* contract: never NEED_MORE on the final chunk */
            int64_t more = window + chunk;
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
            if (vnx_reads_new_batch(state) != 0) {          /* open record: grow instead */
                lcap *= 2;
                lengths = realloc(lengths, (size_t)lcap * sizeof(int64_t));
                invalid = realloc(invalid, (size_t)lcap);
            }
            continue;
        }
        abort();                                            /* unknown return code */
    }
    free(state); free(codes); free(quals); free(lengths); free(invalid);
    return 0;
}
