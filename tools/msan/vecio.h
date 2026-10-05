/* Minimal reader for the vector files written by tools/msan/make_vectors.py (no CPython, no NumPy).
 *
 * File:   int64 n_records, then n_records records; a record is a fixed, kernel-specific sequence of arrays.
 * Array:  int32 type (1 uint8, 2 int16, 3 int32, 4 int64), int64 count, count * sizeof(type) bytes (little endian).
 * Every array is copied into its own malloc'd buffer of exactly count elements (at least 1 byte), so the kernels see
 * buffers sized as the Python bindings size them. */
#ifndef VNX_MSAN_VECIO_H
#define VNX_MSAN_VECIO_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int32_t type;
    int64_t count;
    void *data;
} vec_t;

static size_t vec_elem(int32_t type) {
    switch (type) {
    case 1: return 1;
    case 2: return 2;
    case 3: return 4;
    case 4: return 8;
    default: return 0;
    }
}

static void vec_die(const char *what) {
    fprintf(stderr, "vector file: %s\n", what);
    exit(3);
}

static vec_t vec_read(FILE *f, int32_t want_type) {
    vec_t v;
    if (fread(&v.type, sizeof v.type, 1, f) != 1 || fread(&v.count, sizeof v.count, 1, f) != 1) vec_die("truncated header");
    if (v.type != want_type) vec_die("unexpected array type");
    if (v.count < 0 || v.count > ((int64_t)1 << 32)) vec_die("bad count");
    const size_t bytes = (size_t)v.count * vec_elem(v.type);
    v.data = malloc(bytes ? bytes : 1);
    if (!v.data) vec_die("out of memory");
    if (bytes && fread(v.data, 1, bytes, f) != bytes) vec_die("truncated data");
    return v;
}

static int64_t vec_records(FILE *f) {
    int64_t n;
    if (fread(&n, sizeof n, 1, f) != 1 || n < 0) vec_die("bad record count");
    return n;
}

static void vec_free(vec_t *v) {
    free(v->data);
    v->data = NULL;
}

/* 1 if the first `bytes` bytes of a and b are equal; on a difference print the first differing byte offset */
static int vec_same(const char *what, int64_t record, const void *a, const void *b, size_t bytes) {
    const uint8_t *x = a, *y = b;
    for (size_t i = 0; i < bytes; i++)
        if (x[i] != y[i]) {
            fprintf(stderr, "MISMATCH record %lld %s: byte %zu (%u != %u)\n", (long long)record, what, i, x[i], y[i]);
            return 0;
        }
    return 1;
}
#endif
