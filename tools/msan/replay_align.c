/* Golden-vector replay for the native aligner (src/vnxdna/v5/native/align.c), built by tools/msan.sh with MemorySanitizer.
 * The kernel is #included, so this binary needs neither CPython nor NumPy.
 *
 * For every record of a vector file from tools/msan/make_vectors.py (inputs exactly as vnxdna.native.align builds them,
 * expected outputs from the NumPy reference TemplateAligner._align; readpos from the uninstrumented native library)
 * it calls vnx_align_batch, vnx_align_batch_path and vnx_align_batch_profiled with freshly malloc'd, never-initialised
 * output buffers and compares every output byte with the expected arrays. Comparing reads every output byte, so an
 * output byte the kernel did not write is reported by MSan as a use of uninitialised memory.
 * Exit 0: all records identical; 1: mismatch or non-zero return code; 3: bad vector file. */
#include "vnxdna/v5/native/align.c"
#include "vecio.h"

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s vectors.bin\n", argv[0]);
        return 2;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) vec_die("cannot open");
    const int64_t records = vec_records(f);
    int64_t reads = 0, calls = 0;
    for (int64_t r = 0; r < records; r++) {
        vec_t sc = vec_read(f, 4);
        if (sc.count != 13) vec_die("align scalars");
        const int64_t *s = sc.data;
        const int64_t n = s[0], total = s[1], flags = s[12];
        const int32_t T = (int32_t)s[2], nseg = (int32_t)s[3], frame_nt = (int32_t)s[4];
        vec_t codes = vec_read(f, 1), offsets = vec_read(f, 4), quals = vec_read(f, 1), has_q = vec_read(f, 1);
        vec_t tpl = vec_read(f, 2), seg_of = vec_read(f, 3), prev_seg = vec_read(f, 3), next_seg = vec_read(f, 3);
        vec_t frame_pos = vec_read(f, 3), seg_frame = vec_read(f, 3);
        vec_t e[8];
        const int32_t et[8] = {1, 1, 1, 4, 4, 4, 4, 2};
        for (int i = 0; i < 8; i++) e[i] = vec_read(f, et[i]);
        const size_t sz[8] = {(size_t)(n * frame_nt), (size_t)(n * frame_nt), (size_t)n, 8 * (size_t)n, 8 * (size_t)n,
                              8 * (size_t)n, 8 * (size_t)n, 2 * (size_t)(n * T)};
        for (int i = 0; i < 8; i++)
            if ((size_t)e[i].count * vec_elem(et[i]) != sz[i]) vec_die("align expected size");
        const uint8_t *pc = (flags & 4) ? codes.data : NULL;
        const uint8_t *pq = (flags & 1) ? quals.data : NULL;
        const uint8_t *ph = (flags & 2) ? has_q.data : NULL;
        static const char *names[8] = {"bases", "erased", "ok", "ins", "del", "mm", "cost", "readpos"};
        for (int mode = 0; mode < 3; mode++) {
            void *o[8];
            for (int i = 0; i < 8; i++) o[i] = malloc(sz[i] ? sz[i] : 1); /* deliberately not initialised */
            double timings[4] = {0, 0, 0, 0};
            int rc;
#define ARGS n, pc, offsets.data, total, pq, ph, T, tpl.data, seg_of.data, prev_seg.data, next_seg.data, nseg, frame_nt, \
             frame_pos.data, seg_frame.data, (int32_t)s[5], (int32_t)s[6], (int32_t)s[7], (int32_t)s[8], (int32_t)s[9], \
             (int32_t)s[10], (int32_t)s[11], o[0], o[1], o[2], o[3], o[4], o[5], o[6]
            if (mode == 0) rc = vnx_align_batch(ARGS);
            else if (mode == 1) rc = vnx_align_batch_path(ARGS, o[7]);
            else rc = vnx_align_batch_profiled(ARGS, timings);
#undef ARGS
            if (rc != 0) {
                fprintf(stderr, "record %lld mode %d: return code %d\n", (long long)r, mode, rc);
                return 1;
            }
            const int outs = mode == 1 ? 8 : 7;
            for (int i = 0; i < outs; i++) {
                char what[48];
                snprintf(what, sizeof what, "%s (mode %d)", names[i], mode);
                if (!vec_same(what, r, o[i], e[i].data, sz[i])) return 1;
            }
            if (mode == 2 && !(timings[0] >= 0 && timings[1] >= 0 && timings[2] >= 0 && timings[3] >= 0)) {
                fprintf(stderr, "record %lld: negative stage time\n", (long long)r);
                return 1;
            }
            for (int i = 0; i < 8; i++) free(o[i]);
            calls++;
        }
        reads += n;
        vec_free(&sc); vec_free(&codes); vec_free(&offsets); vec_free(&quals); vec_free(&has_q); vec_free(&tpl);
        vec_free(&seg_of); vec_free(&prev_seg); vec_free(&next_seg); vec_free(&frame_pos); vec_free(&seg_frame);
        for (int i = 0; i < 8; i++) vec_free(&e[i]);
    }
    fclose(f);
    printf("{\"kernel\": \"align\", \"records\": %lld, \"reads\": %lld, \"calls\": %lld, \"mismatches\": 0}\n",
           (long long)records, (long long)reads, (long long)calls);
    return 0;
}
