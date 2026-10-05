/* Standalone driver for the libFuzzer harnesses in fuzz/native/<target>_fuzz.c, so they can be built with MemorySanitizer
 * without the libFuzzer runtime (tools/msan.sh). It calls LLVMFuzzerTestOneInput on
 *   1. every file named on the command line (directories are walked one level deep: the seed corpus and regressions);
 *   2. N pseudo-random inputs (MSAN_RANDOM_CASES, default 20000; lengths 0..4096, a few up to 65536), seeded
 *      (MSAN_SEED, default 20261005) with a splitmix64 generator, so a run is reproducible.
 * Each input is copied into a malloc'd buffer of exactly its length. Prints a JSON line with the counts. */
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

static uint64_t sm_state;
static uint64_t sm_next(void) {
    uint64_t z = (sm_state += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static long long n_files;

static void run_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc(len > 0 ? (size_t)len : 1);
    if (len > 0 && fread(buf, 1, (size_t)len, f) != (size_t)len) len = 0;
    fclose(f);
    LLVMFuzzerTestOneInput(buf, (size_t)(len > 0 ? len : 0));
    free(buf);
    n_files++;
}

static void run_path(const char *path) {
    struct stat st;
    if (stat(path, &st) != 0) return;
    if (!S_ISDIR(st.st_mode)) {
        run_file(path);
        return;
    }
    DIR *d = opendir(path);
    if (!d) return;
    struct dirent *e;
    char full[4096];
    while ((e = readdir(d)) != NULL) {
        if (e->d_name[0] == '.') continue;
        snprintf(full, sizeof full, "%s/%s", path, e->d_name);
        if (stat(full, &st) == 0 && S_ISREG(st.st_mode)) run_file(full);
    }
    closedir(d);
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) run_path(argv[i]);
    const char *s = getenv("MSAN_RANDOM_CASES"), *seed = getenv("MSAN_SEED");
    const long long cases = s ? atoll(s) : 20000;
    sm_state = seed ? strtoull(seed, NULL, 10) : 20261005ULL;
    long long bytes = 0;
    for (long long c = 0; c < cases; c++) {
        const uint64_t r = sm_next();
        size_t len = (r & 0xFF) < 4 ? (size_t)(sm_next() % 65537) : (size_t)(sm_next() % 4097);
        uint8_t *buf = malloc(len ? len : 1);
        for (size_t i = 0; i < len; i += 8) {
            uint64_t v = sm_next();
            const size_t k = len - i < 8 ? len - i : 8;
            memcpy(buf + i, &v, k);
        }
        LLVMFuzzerTestOneInput(buf, len);
        free(buf);
        bytes += (long long)len;
    }
    printf("{\"files\": %lld, \"random_cases\": %lld, \"random_bytes\": %lld}\n", n_files, cases, bytes);
    return 0;
}
