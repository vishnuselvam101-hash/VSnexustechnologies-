/*
 * VNX-DNA V6 native streaming FASTQ / FASTA / plain-sequence read parser.
 *
 * Specification: src/vnxdna/v4/reads.py (iter_reads, _lines, _batch). This kernel reproduces that
 * reference bit for bit, including the order in which errors and batches appear. Python
 * (vnxdna.v6.native_reads) keeps format detection, the max_reads cap and the error messages.
 *
 * Memory model: the kernel never allocates. The caller owns
 *   - the parser state (vnx_reads_state_size() bytes, fixed size),
 *   - every input chunk (read only during one call, never retained),
 *   - every output buffer (codes, quals, lengths, invalid), passed again on every call.
 * Only offsets are kept between calls, so the caller may grow (realloc) the output buffers between
 * calls. All writes are bounded by the capacities given in the same call.
 *
 * Incremental input: chunks may end anywhere (inside a record, a line or a CR LF pair). The
 * reference reads 8 MiB blocks and rejects a block whose trailing partial line exceeds a limit
 * BEFORE it hands out any line of that block. To reproduce that ordering for arbitrary chunking the
 * kernel resolves the check of a block before the first line ending inside that block; if the
 * chunk does not reach far enough to decide, it returns VNX_READS_NEED_MORE and the caller re-feeds
 * the unconsumed bytes together with more data. With chunks aligned to the block size (what the
 * Python binding does) this never happens.
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define VNX_READS_ABI 1
#define VNX_READS_MAGIC 0x564e5852454144ULL /* "VNXREAD" */
#define HDR_KEEP 40

enum { FMT_FASTQ = 1, FMT_FASTA = 2, FMT_PLAIN = 3 };

enum {
    VNX_READS_NEED_INPUT = 0, /* chunk fully consumed, give the next one */
    VNX_READS_BATCH_FULL = 1, /* n == max_records: take the batch (or grow lengths/invalid) */
    VNX_READS_NEED_SPACE = 2, /* codes/quals need cap - used >= max_nt for the open record */
    VNX_READS_NEED_MORE = 3,  /* re-feed data[consumed:] followed by more input */
    VNX_READS_DONE = 4        /* final chunk processed; info[1] records remain in the batch */
};

enum {
    E_ARG = -1,
    E_STATE = -2,
    E_LINE_TOO_LONG = -10, /* _lines: partial line at a block boundary longer than limit */
    E_FQ_HEADER = -11,     /* expected '@' */
    E_FQ_TRUNC = -12,      /* truncated FASTQ record */
    E_FQ_MALFORMED = -13,  /* '+' missing or quality length != sequence length */
    E_FQ_TOO_LONG = -14,   /* read longer than max_nt */
    E_FA_NOHDR = -15,      /* sequence data before the first FASTA header */
    E_FA_TOO_LONG = -16,   /* record longer than max_nt */
    E_PL_TOO_LONG = -17    /* plain read longer than max_nt */
};

/* exported ABI (ctypes, see vnxdna/v6/native_reads.py) */
int vnx_reads_abi_version(void);
int64_t vnx_reads_state_size(void);
int vnx_reads_init(void *state, int64_t state_len, int32_t fmt, int64_t max_nt, int64_t limit, int64_t block);
int vnx_reads_new_batch(void *state);
int vnx_reads_feed(void *state, const uint8_t *data, int64_t len, int32_t final, uint8_t *codes, uint8_t *quals,
                   int64_t cap, int64_t *lengths, uint8_t *invalid, int64_t max_records, int64_t *info, uint8_t *ctx);

enum { R_FQ_HDR = 0, R_FQ_SEQ = 1, R_FQ_PLUS = 2, R_FQ_QUAL = 3, R_FA = 4, R_PL = 5 };
enum { LK_UNDECIDED = 0, LK_HEADER = 1, LK_DATA = 2 };

typedef struct {
    uint64_t magic;
    int32_t fmt, role, line_kind, err;
    int32_t done, plus_first, lead_ws, rec_open;
    int64_t max_nt, limit, block;
    int64_t pos;           /* absolute offset of the next input byte */
    int64_t next_boundary; /* next multiple of block not yet crossed */
    int64_t checked_upto;  /* every block boundary <= this is known to pass the line check */
    int64_t raw_len;       /* raw bytes of the current line so far (CR included) */
    int64_t cr_run;        /* trailing CR bytes of the current line not yet known to be content */
    int64_t content_len;   /* bytes of the current line after rstrip(b"\r") so far */
    int64_t ws_run;        /* pending trailing whitespace of a stripped line */
    int64_t rec_len;       /* FASTQ: sequence length; FASTA / plain: stored (stripped) length */
    int64_t rec_size;      /* FASTA: sum of rstripped line lengths (the reference's size) */
    int64_t qual_len;
    int64_t plus_ok, rec_inv, hdr_len, ctx_len;
    int64_t n, used; /* records and committed bytes in the current batch */
    uint8_t hdr[HDR_KEEP];
    uint8_t ws[256];   /* bytes.strip() whitespace */
} vnx_reads_state;

typedef struct {
    uint8_t *codes, *quals;
    int64_t cap;
    int64_t *lengths;
    uint8_t *invalid;
    int64_t max_records;
} out_t;

static const uint8_t CRS[64] = {
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13,
    13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13};

/* reference _LUT | 8 * ~_VALID: A/a 0, C/c 1, G/g 2, T/t 3, N/n 4, any other byte 4 | 8 */
static const uint8_t CODE_LUT[256] = {
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12,  0, 12,  1, 12, 12, 12,  2, 12, 12, 12, 12, 12, 12,  4, 12,
    12, 12, 12, 12,  3, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12,  0, 12,  1, 12, 12, 12,  2, 12, 12, 12, 12, 12, 12,  4, 12,
    12, 12, 12, 12,  3, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
    12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12,
};

static int64_t sat_add(int64_t a, int64_t b) { /* a, b >= 0 */
    return (a > INT64_MAX - b) ? INT64_MAX : a + b;
}

static int64_t min64(int64_t a, int64_t b) { return a < b ? a : b; }

int vnx_reads_abi_version(void) { return VNX_READS_ABI; }

int64_t vnx_reads_state_size(void) { return (int64_t)sizeof(vnx_reads_state); }

static void reset_line(vnx_reads_state *st) {
    st->raw_len = 0;
    st->cr_run = 0;
    st->content_len = 0;
    st->ws_run = 0;
    st->lead_ws = 1;
    st->line_kind = LK_UNDECIDED;
    st->plus_first = -1;
}

int vnx_reads_init(void *state, int64_t state_len, int32_t fmt, int64_t max_nt, int64_t limit, int64_t block) {
    if (!state || state_len < (int64_t)sizeof(vnx_reads_state)) return E_ARG;
    if (fmt != FMT_FASTQ && fmt != FMT_FASTA && fmt != FMT_PLAIN) return E_ARG;
    if (max_nt < 0 || limit < 0 || block < 1) return E_ARG;
    vnx_reads_state *st = (vnx_reads_state *)state;
    memset(st, 0, sizeof *st);
    st->magic = VNX_READS_MAGIC;
    st->fmt = fmt;
    st->max_nt = max_nt;
    st->limit = limit;
    st->block = block;
    st->next_boundary = block;
    st->role = fmt == FMT_FASTQ ? R_FQ_HDR : (fmt == FMT_FASTA ? R_FA : R_PL);
    st->rec_open = fmt == FMT_PLAIN; /* every plain line may become a record */
    reset_line(st);
    const uint8_t wsb[6] = {9, 10, 11, 12, 13, 32};
    for (int i = 0; i < 6; i++) st->ws[wsb[i]] = 1;
    return 0;
}

int vnx_reads_new_batch(void *state) {
    vnx_reads_state *st = (vnx_reads_state *)state;
    if (!st || st->magic != VNX_READS_MAGIC) return E_ARG;
    if (st->rec_open && st->rec_len != 0) return E_STATE; /* would orphan stored bytes */
    if (st->rec_open && st->fmt == FMT_FASTQ && st->qual_len != 0) return E_STATE;
    st->n = 0;
    st->used = 0;
    return 0;
}

/* ------------------------------------------------------------------ record content writers */
static void put_codes(vnx_reads_state *st, const out_t *o, const uint8_t *s, int64_t m) {
    /* stores at most max_nt bytes per record; cap - used >= max_nt was checked at record open */
    int64_t idx = st->rec_len;
    int64_t w = idx < st->max_nt ? min64(m, st->max_nt - idx) : 0;
    if (w > 0) {
        uint8_t *restrict dst = o->codes + st->used + idx;
        uint8_t acc = 0;
        for (int64_t j = 0; j < w; j++) {
            uint8_t v = CODE_LUT[s[j]];
            dst[j] = v & 7;
            acc |= v;
        }
        uint8_t bad = acc & 8;
        if (bad) st->rec_inv = 1;
    }
    st->rec_len = sat_add(idx, m);
}

static void put_ws_layer(vnx_reads_state *st, const out_t *o, const uint8_t *s, int64_t m) {
    /* bytes.strip(): leading whitespace dropped, trailing whitespace held until more content */
    int64_t j = 0;
    if (st->lead_ws) {
        while (j < m && st->ws[s[j]]) j++;
        if (j == m) return;
        st->lead_ws = 0;
    }
    while (j < m) {
        int64_t k = j;
        while (k < m && !st->ws[s[k]]) k++;
        if (k > j) {
            if (st->ws_run) {
                int64_t pend = st->ws_run;
                st->ws_run = 0;
                int64_t idx = st->rec_len;
                int64_t w = idx < st->max_nt ? min64(pend, st->max_nt - idx) : 0;
                if (w > 0) {
                    memset(o->codes + st->used + idx, 4, (size_t)w);
                    st->rec_inv = 1;
                }
                st->rec_len = sat_add(idx, pend);
            }
            put_codes(st, o, s + j, k - j);
        }
        j = k;
        while (k < m && st->ws[s[k]]) k++;
        st->ws_run = sat_add(st->ws_run, k - j);
        j = k;
    }
}

/* content of the current line after the CR layer (rstrip(b"\r") semantics) */
static void emit(vnx_reads_state *st, const out_t *o, const uint8_t *s, int64_t m) {
    if (m <= 0) return;
    switch (st->role) {
    case R_FQ_HDR: {
        int64_t at = st->content_len;
        if (at < HDR_KEEP) {
            int64_t w = min64(m, HDR_KEEP - at);
            memcpy(st->hdr + at, s, (size_t)w);
        }
        break;
    }
    case R_FQ_SEQ:
        put_codes(st, o, s, m);
        break;
    case R_FQ_PLUS:
        if (st->content_len == 0) st->plus_first = s[0];
        break;
    case R_FQ_QUAL: {
        int64_t idx = st->qual_len;
        int64_t w = idx < st->max_nt ? min64(m, st->max_nt - idx) : 0;
        if (w > 0) {
            uint8_t *restrict dst = o->quals + st->used + idx;
            const uint8_t *restrict src = s;
            for (int64_t j = 0; j < w; j++) { /* clip(b - 33, 0, 93) */
                uint8_t v = src[j];
                v = (uint8_t)(v < 33 ? 0 : v - 33);
                dst[j] = (uint8_t)(v > 93 ? 93 : v);
            }
        }
        st->qual_len = sat_add(idx, m);
        break;
    }
    case R_FA:
        if (st->line_kind == LK_UNDECIDED) st->line_kind = s[0] == '>' ? LK_HEADER : LK_DATA;
        if (st->line_kind == LK_DATA && st->rec_open) put_ws_layer(st, o, s, m);
        break;
    case R_PL:
        put_ws_layer(st, o, s, m);
        break;
    default:
        break;
    }
    st->content_len = sat_add(st->content_len, m);
}

/* raw line bytes [s, s+m), no '\n' inside */
static void line_bytes(vnx_reads_state *st, const out_t *o, const uint8_t *s, int64_t m) {
    if (m <= 0) return;
    st->raw_len = sat_add(st->raw_len, m);
    int64_t e = m;
    while (e > 0 && s[e - 1] == '\r') e--;
    if (e == 0) {
        st->cr_run = sat_add(st->cr_run, m);
        return;
    }
    while (st->cr_run > 0) { /* earlier CRs are followed by content: they are content */
        int64_t w = min64(st->cr_run, (int64_t)sizeof CRS);
        emit(st, o, CRS, w);
        st->cr_run -= w;
    }
    emit(st, o, s, e);
    st->cr_run = m - e;
}

static int space_ok(const vnx_reads_state *st, const out_t *o) {
    return !st->rec_open || o->cap - st->used >= st->max_nt;
}

static int set_err(vnx_reads_state *st, int code, int64_t ctx_len) {
    st->err = code;
    st->ctx_len = ctx_len;
    return code;
}

static void commit(vnx_reads_state *st, const out_t *o) {
    o->lengths[st->n] = st->rec_len;
    o->invalid[st->n] = st->rec_inv ? 1 : 0;
    st->used += st->rec_len;
    st->n += 1;
}

static void open_record(vnx_reads_state *st) {
    st->rec_open = 1;
    st->rec_len = 0;
    st->rec_size = 0;
    st->qual_len = 0;
    st->rec_inv = 0;
    st->plus_ok = 0;
}

/* a complete line (reference: one item from _lines). Returns 0 to continue, a status or an error. */
static int line_end(vnx_reads_state *st, const out_t *o) {
    int64_t clen = st->content_len;
    int rc = 0;
    switch (st->role) {
    case R_FQ_HDR:
        if (clen == 0) break; /* empty header line: skipped */
        if (st->hdr[0] != '@') return set_err(st, E_FQ_HEADER, clen);
        st->hdr_len = clen;
        open_record(st);
        st->role = R_FQ_SEQ;
        if (!space_ok(st, o)) rc = VNX_READS_NEED_SPACE;
        break;
    case R_FQ_SEQ:
        st->role = R_FQ_PLUS;
        break;
    case R_FQ_PLUS:
        st->plus_ok = clen > 0 && st->plus_first == '+';
        st->role = R_FQ_QUAL;
        break;
    case R_FQ_QUAL:
        if (!st->plus_ok || st->qual_len != st->rec_len) return set_err(st, E_FQ_MALFORMED, st->hdr_len);
        if (st->rec_len > st->max_nt) return set_err(st, E_FQ_TOO_LONG, st->hdr_len);
        commit(st, o);
        st->rec_open = 0;
        st->role = R_FQ_HDR;
        if (st->n >= o->max_records) rc = VNX_READS_BATCH_FULL;
        break;
    case R_FA:
        if (st->line_kind == LK_HEADER) {
            int had = st->rec_open;
            if (had) commit(st, o);
            open_record(st);
            if (had && st->n >= o->max_records) rc = VNX_READS_BATCH_FULL;
            else if (!space_ok(st, o)) rc = VNX_READS_NEED_SPACE;
        } else if (st->line_kind == LK_DATA) {
            if (!st->rec_open) return set_err(st, E_FA_NOHDR, 0);
            st->rec_size = sat_add(st->rec_size, clen);
            if (st->rec_size > st->max_nt) return set_err(st, E_FA_TOO_LONG, 0);
        }
        break;
    case R_PL:
        if (st->rec_len == 0) break; /* blank after strip(): skipped */
        if (st->rec_len > st->max_nt) return set_err(st, E_PL_TOO_LONG, 0);
        commit(st, o);
        open_record(st);
        if (st->n >= o->max_records) rc = VNX_READS_BATCH_FULL;
        else if (!space_ok(st, o)) rc = VNX_READS_NEED_SPACE;
        break;
    default:
        return set_err(st, E_STATE, 0);
    }
    reset_line(st);
    return rc;
}

/*
 * Before the line ending at absolute offset p (rest[0] == '\n', rest_len bytes available) is handed
 * out, the reference has already checked the partial line at the end of the block containing p.
 * Returns 0 (pass), 1 (fail) or 2 (undecidable with the bytes available).
 */
static int resolve_block_check(vnx_reads_state *st, int64_t p, const uint8_t *rest, int64_t rest_len, int final) {
    int64_t q = p / st->block + 1;
    int64_t jb = q > INT64_MAX / st->block ? INT64_MAX : q * st->block;
    if (st->checked_upto >= jb) return 0;
    int64_t chunk_end = p + rest_len; /* p + rest_len <= INT64_MAX: checked at feed entry */
    int64_t bound = jb;
    if (final && chunk_end < bound) bound = chunk_end;
    int64_t avail_end = min64(chunk_end, bound);
    int64_t line_start = p + 1;
    for (int64_t k = avail_end - 1; k > p; k--) {
        if (rest[k - p] == '\n') {
            line_start = k + 1;
            break;
        }
    }
    if (avail_end == bound) {
        if (bound - line_start > st->limit) return 1;
        st->checked_upto = jb;
        return 0;
    }
    if (bound - line_start <= st->limit) { /* the tail at the boundary can only be shorter */
        st->checked_upto = jb;
        return 0;
    }
    return 2;
}

static int cross_boundaries(vnx_reads_state *st) {
    while (st->pos >= st->next_boundary) {
        if (st->raw_len > st->limit) return set_err(st, E_LINE_TOO_LONG, 0);
        if (st->checked_upto < st->next_boundary) st->checked_upto = st->next_boundary;
        st->next_boundary = sat_add(st->next_boundary, st->block);
        if (st->next_boundary == INT64_MAX) break;
    }
    return 0;
}

static int finish_out(vnx_reads_state *st, int64_t *info, uint8_t *ctx, int64_t consumed, int rc) {
    info[0] = consumed;
    info[1] = st->n;
    info[2] = st->used;
    info[3] = st->ctx_len;
    if (ctx) {
        int64_t w = min64(st->ctx_len, HDR_KEEP);
        if (w > 0) memcpy(ctx, st->hdr, (size_t)w);
    }
    return rc;
}

/*
 * Feed data[0:len). final != 0: end of input is at data + len.
 * Outputs: codes (and quals for FASTQ) of capacity cap bytes; lengths/invalid of capacity max_records.
 * info[4] = {consumed, n, used, ctx_len}; ctx[40] = header bytes for error messages.
 */
int vnx_reads_feed(void *state, const uint8_t *data, int64_t len, int32_t final, uint8_t *codes, uint8_t *quals,
                   int64_t cap, int64_t *lengths, uint8_t *invalid, int64_t max_records, int64_t *info, uint8_t *ctx) {
    vnx_reads_state *st = (vnx_reads_state *)state;
    if (!st || st->magic != VNX_READS_MAGIC || !info) return E_ARG;
    if (len < 0 || (len > 0 && !data) || cap < 0 || max_records < 0) return finish_out(st, info, ctx, 0, E_ARG);
    if (cap > 0 && (!codes || (st->fmt == FMT_FASTQ && !quals))) return finish_out(st, info, ctx, 0, E_ARG);
    if (max_records > 0 && (!lengths || !invalid)) return finish_out(st, info, ctx, 0, E_ARG);
    if (len > INT64_MAX - st->pos) return finish_out(st, info, ctx, 0, E_ARG);
    if (st->err) return finish_out(st, info, ctx, 0, st->err);
    if (st->done) return finish_out(st, info, ctx, 0, VNX_READS_DONE);
    if (st->used > cap) return finish_out(st, info, ctx, 0, VNX_READS_NEED_SPACE);
    out_t o = {codes, quals, cap, lengths, invalid, max_records};
    if (st->n >= max_records) return finish_out(st, info, ctx, 0, VNX_READS_BATCH_FULL);
    if (!space_ok(st, &o)) return finish_out(st, info, ctx, 0, VNX_READS_NEED_SPACE);

    int64_t i = 0;
    while (i < len) {
        int rc = cross_boundaries(st);
        if (rc) return finish_out(st, info, ctx, i, rc);
        int64_t seg_end = min64(len, i + (st->next_boundary - st->pos));
        const uint8_t *nl = memchr(data + i, '\n', (size_t)(seg_end - i));
        if (!nl) {
            line_bytes(st, &o, data + i, seg_end - i);
            st->pos += seg_end - i;
            i = seg_end;
            continue;
        }
        int64_t k = (int64_t)(nl - data);
        line_bytes(st, &o, data + i, k - i);
        st->pos += k - i;
        i = k;
        int chk = resolve_block_check(st, st->pos, data + k, len - k, final);
        if (chk == 1) return finish_out(st, info, ctx, i, set_err(st, E_LINE_TOO_LONG, 0));
        if (chk == 2) return finish_out(st, info, ctx, i, VNX_READS_NEED_MORE);
        st->raw_len = sat_add(st->raw_len, 1); /* the '\n' (reset by line_end) */
        st->pos += 1;
        i += 1;
        rc = line_end(st, &o);
        if (rc) return finish_out(st, info, ctx, i, rc);
    }
    int rc = cross_boundaries(st);
    if (rc) return finish_out(st, info, ctx, i, rc);
    if (!final) return finish_out(st, info, ctx, i, VNX_READS_NEED_INPUT);

    /* end of input: the reference checks the last partial block, then yields the final partial line */
    if (st->raw_len > st->limit) return finish_out(st, info, ctx, i, set_err(st, E_LINE_TOO_LONG, 0));
    if (st->raw_len > 0) {
        rc = line_end(st, &o);
        if (rc) return finish_out(st, info, ctx, i, rc);
    }
    if (st->fmt == FMT_FASTQ && st->role != R_FQ_HDR) return finish_out(st, info, ctx, i, set_err(st, E_FQ_TRUNC, 0));
    if (st->fmt == FMT_FASTA && st->rec_open) {
        if (st->n >= max_records) return finish_out(st, info, ctx, i, VNX_READS_BATCH_FULL);
        commit(st, &o);
        st->rec_open = 0;
    }
    st->rec_open = 0;
    st->done = 1;
    return finish_out(st, info, ctx, i, VNX_READS_DONE);
}
