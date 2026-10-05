/* Golden-vector replay for the native inner RS decoder (src/vnxdna/v6/native/rs.c), built by tools/msan.sh with
 * MemorySanitizer. The kernel is #included, so this binary needs neither CPython nor NumPy.
 *
 * For every record (a batch of codewords, optional erasure flags, and the expected corrected words / ok flags / errata
 * counts computed by the reference vnxdna.v4.rs_fast.decode_batch) it runs vnx_rs_decode_batch at level 0 (auto) and
 * at every level this CPU supports (scalar, avx2, avx512), out of place into never-initialised buffers and in place,
 * and compares every output byte. Exit 0: identical everywhere; 1: mismatch or error code; 3: bad vector file. */
#include "vnxdna/v6/native/rs.c"
#include "vecio.h"

#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s vectors.bin\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) vec_die("cannot open");
    const int64_t records = vec_records(f);
    const int levels = vnx_rs_levels();
    int64_t words = 0, calls = 0;
    for (int64_t r = 0; r < records; r++) {
        vec_t sc = vec_read(f, 4);
        if (sc.count != 4) vec_die("rs scalars");
        const int64_t *s = sc.data;
        const int64_t nw = s[0];
        const int32_t n = (int32_t)s[1], nsym = (int32_t)s[2];
        vec_t cw = vec_read(f, 1), er = vec_read(f, 1), eo = vec_read(f, 1), eok = vec_read(f, 1), eerr = vec_read(f, 4);
        const size_t total = (size_t)nw * (size_t)n;
        if ((size_t)cw.count != total || (size_t)eo.count != total || eok.count != nw || eerr.count != nw) vec_die("rs sizes");
        const uint8_t *erase = (s[3] & 1) ? er.data : NULL;
        for (int level = 0; level <= 3; level++) {
            if (level && !(levels & (1 << level))) continue;
            for (int inplace = 0; inplace < 2; inplace++) {
                uint8_t *out = malloc(total ? total : 1), *ok = malloc(nw ? (size_t)nw : 1);
                int64_t *errata = malloc(nw ? (size_t)nw * 8 : 8);
                if (inplace) memcpy(out, cw.data, total);
                const int rc = vnx_rs_decode_batch(nw, n, nsym, inplace ? out : cw.data, erase, out, ok, errata, level);
                if (rc != 0) {
                    fprintf(stderr, "record %lld level %d: return code %d\n", (long long)r, level, rc);
                    return 1;
                }
                char w1[40], w2[40], w3[40];
                snprintf(w1, sizeof w1, "out (level %d%s)", level, inplace ? " in place" : "");
                snprintf(w2, sizeof w2, "ok (level %d%s)", level, inplace ? " in place" : "");
                snprintf(w3, sizeof w3, "errata (level %d%s)", level, inplace ? " in place" : "");
                if (!vec_same(w1, r, out, eo.data, total) || !vec_same(w2, r, ok, eok.data, (size_t)nw) ||
                    !vec_same(w3, r, errata, eerr.data, (size_t)nw * 8))
                    return 1;
                free(out); free(ok); free(errata);
                calls++;
            }
        }
        words += nw;
        vec_free(&sc); vec_free(&cw); vec_free(&er); vec_free(&eo); vec_free(&eok); vec_free(&eerr);
    }
    fclose(f);
    printf("{\"kernel\": \"rs\", \"records\": %lld, \"words\": %lld, \"calls\": %lld, \"levels_mask\": %d, \"mismatches\": 0}\n",
           (long long)records, (long long)words, (long long)calls, levels);
    return 0;
}
