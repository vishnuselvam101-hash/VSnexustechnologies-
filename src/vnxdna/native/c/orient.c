/* VNX-DNA V12: native kernel of the pass-1 orientation pre-pass (vnxdna.recovery.pass1._orientation_reference).
 *
 * The NumPy reference is the specification; this kernel returns exactly what it returns (bit-exact, checked by golden
 * hashes and randomized equivalence tests against the reference on every layout):
 *
 *   vnx_or_scan   pass1._orientation_reference   shift-tolerant marker agreement, forward vs reverse complement
 *
 * For every read, each marker block (a run of consecutive marker positions of the layout template) scores its best
 * agreement over shifts s in 0, -1, +1, ..., -max_shift, +max_shift: forward reads compare codes[off + p + s] with the
 * marker code, reverse-complement candidates compare RC(codes[off + len - 1 - (p + s)]). Positions outside the read
 * never score. A read is flipped when sum(best reverse) > sum(best forward) + margin. The reference leaves the scan
 * early for reads whose forward sum already reaches (marker count - margin) after a shift (the reverse sum is then
 * irrelevant, since it never exceeds the marker count), and so does this kernel, in the same shift order: the
 * decision is identical either way, and reproducing the exit keeps the work identical too.
 *
 * Codes are 0..3 (A, C, G, T), 4 = N (its reverse complement is 4, which matches no marker). The reference indexes a
 * five-entry table, so codes above 4 are outside the contract; here they match nothing.
 *
 * Plain C11 shared library, loaded with ctypes (no Python headers). No global mutable state: reentrant, every read
 * depends only on itself; the binding releases the GIL around the call. The per-read block scores live in two small
 * heap buffers (3 x nblocks int64) allocated once per call; nothing else allocates. */
#include <stdint.h>
#include <stdlib.h>

#ifndef VNX_OR_ABI /* overridable only so that the tests can build a mismatching library */
#define VNX_OR_ABI 3
#endif

enum { OR_OK = 0, OR_EARG = -1, OR_ENOMEM = -2 };

int vnx_or_abi_version(void) { return VNX_OR_ABI; }

static inline uint8_t rc_code(uint8_t c) { return c < 4 ? (uint8_t)(3 - c) : (c == 4 ? (uint8_t)4 : (uint8_t)255); }

/* Per-block agreement of one read at shift s (forward when rc == 0, as reverse complement otherwise). */
static void score(const uint8_t *r, int64_t len, int64_t nm, const int64_t *mpos, const uint8_t *want,
                  const int64_t *block, int64_t nb, int64_t s, int rc, int64_t *out)
{
    for (int64_t b = 0; b < nb; b++) out[b] = 0;
    for (int64_t m = 0; m < nm; m++) {
        int64_t p = mpos[m] + s;
        if (p < 0 || p >= len) continue;
        uint8_t c = rc ? rc_code(r[len - 1 - p]) : r[p];
        out[block[m]] += (c == want[m]);
    }
}

static inline int64_t sum(const int64_t *v, int64_t nb)
{
    int64_t t = 0;
    for (int64_t b = 0; b < nb; b++) t += v[b];
    return t;
}

/* flip[i] = 1 where read i is better explained as a reverse complement (pass1._orientation_reference).
 *   codes[ncodes]      flat 2-bit codes of all reads of the batch (0..4)
 *   lengths[n]         read lengths (>= 0); offs[n]: start of read i in codes (offs[i] + lengths[i] <= ncodes)
 *   mpos[nm]           marker positions of the template (ascending); want[nm]: the marker codes (0..3)
 *   block[nm]          block index of each marker (0 <= block[m] < nblocks, non-decreasing)
 *   margin             flip only when the reverse sum exceeds the forward sum by more than this (>= 0)
 *   max_shift          shifts tried: 0, then -k and +k for k = 1..max_shift (>= 0)
 * Returns OR_OK, OR_EARG for an inconsistent argument (nothing written), OR_ENOMEM. */
int vnx_or_scan(int64_t n, const uint8_t *codes, int64_t ncodes, const int64_t *lengths, const int64_t *offs,
                int64_t nm, const int64_t *mpos, const uint8_t *want, const int64_t *block, int64_t nblocks,
                int64_t margin, int64_t max_shift, uint8_t *flip)
{
    if (n < 0 || ncodes < 0 || nm < 0 || nblocks < 0 || nblocks > nm || margin < 0 || max_shift < 0) return OR_EARG;
    if (max_shift > INT32_MAX) return OR_EARG;
    if ((n > 0 && (!lengths || !offs || !flip)) || (ncodes > 0 && !codes)) return OR_EARG;
    if (nm > 0 && (!mpos || !want || !block)) return OR_EARG;
    for (int64_t m = 0; m < nm; m++) {
        if (block[m] < 0 || block[m] >= nblocks || (m > 0 && block[m] < block[m - 1])) return OR_EARG;
        if (mpos[m] < 0 || mpos[m] > INT32_MAX || (m > 0 && mpos[m] <= mpos[m - 1])) return OR_EARG;
    }
    for (int64_t i = 0; i < n; i++)
        if (lengths[i] < 0 || offs[i] < 0 || offs[i] > ncodes - lengths[i]) return OR_EARG;
    if (n == 0) return OR_OK;
    if (nm == 0) {
        for (int64_t i = 0; i < n; i++) flip[i] = 0;
        return OR_OK;
    }
    int64_t *buf = (int64_t *)malloc((size_t)(3 * nblocks) * sizeof(int64_t));
    if (!buf) return OR_ENOMEM;
    int64_t *bf = buf, *br = buf + nblocks, *tmp = buf + 2 * nblocks;
    for (int64_t i = 0; i < n; i++) {
        const uint8_t *r = codes + offs[i];
        int64_t len = lengths[i];
        score(r, len, nm, mpos, want, block, nblocks, 0, 0, bf);
        int64_t sf = sum(bf, nblocks), sr = 0;
        if (sf + margin < nm) {
            score(r, len, nm, mpos, want, block, nblocks, 0, 1, br);
            for (int64_t k = 1; k <= max_shift && sf + margin < nm; k++) {
                for (int side = 0; side < 2 && sf + margin < nm; side++) {
                    int64_t s = side ? k : -k;
                    score(r, len, nm, mpos, want, block, nblocks, s, 0, tmp);
                    for (int64_t b = 0; b < nblocks; b++) if (tmp[b] > bf[b]) bf[b] = tmp[b];
                    score(r, len, nm, mpos, want, block, nblocks, s, 1, tmp);
                    for (int64_t b = 0; b < nblocks; b++) if (tmp[b] > br[b]) br[b] = tmp[b];
                    sf = sum(bf, nblocks);
                }
            }
            sr = sum(br, nblocks);
        }
        flip[i] = sr > sf + margin;
    }
    free(buf);
    return OR_OK;
}

/* ABI 2 (V13 D1): lower bound on the marker-template alignment cost of each read taken as a reverse complement.
 * For every marker block (consecutive marker positions) any alignment of band B either keeps one shift s, |s| <= B,
 * for the whole block (cost >= c_mm * mismatches at that shift) or places an insertion or deletion inside the block
 * (cost >= min(c_ins, c_del)); blocks and the ops inside them are disjoint, so
 *   out[i] = sum over blocks b of min(c_mm * (size_b - max_{|s|<=B} hits_b(s)), c_ins, c_del)
 * never exceeds the cost of any alignment the DP of vnxdna.sync.template can return for the reverse complement.
 * Same arguments as vnx_or_scan (offs may select any reads of the buffer); band, c_mm, c_ins, c_del >= 0.
 * Reference: vnxdna.recovery.pass1._rc_bound_reference. */
int vnx_or_rc_bound(int64_t n, const uint8_t *codes, int64_t ncodes, const int64_t *lengths, const int64_t *offs,
                    int64_t nm, const int64_t *mpos, const uint8_t *want, const int64_t *block, int64_t nblocks,
                    int64_t band, int64_t c_mm, int64_t c_ins, int64_t c_del, int64_t *out)
{
    if (n < 0 || ncodes < 0 || nm < 0 || nblocks < 0 || nblocks > nm || band < 0 || band > INT32_MAX) return OR_EARG;
    if (c_mm < 0 || c_ins < 0 || c_del < 0 || c_mm > INT32_MAX || c_ins > INT32_MAX || c_del > INT32_MAX) return OR_EARG;
    if ((n > 0 && (!lengths || !offs || !out)) || (ncodes > 0 && !codes)) return OR_EARG;
    if (nm > 0 && (!mpos || !want || !block)) return OR_EARG;
    for (int64_t m = 0; m < nm; m++) {
        if (block[m] < 0 || block[m] >= nblocks || (m > 0 && block[m] < block[m - 1])) return OR_EARG;
        if (mpos[m] < 0 || mpos[m] > INT32_MAX || (m > 0 && mpos[m] <= mpos[m - 1])) return OR_EARG;
    }
    for (int64_t i = 0; i < n; i++)
        if (lengths[i] < 0 || offs[i] < 0 || offs[i] > ncodes - lengths[i]) return OR_EARG;
    if (n == 0) return OR_OK;
    if (nm == 0) {
        for (int64_t i = 0; i < n; i++) out[i] = 0;
        return OR_OK;
    }
    const int64_t c_indel = c_ins < c_del ? c_ins : c_del;
    int64_t *buf = (int64_t *)malloc((size_t)(3 * nblocks) * sizeof(int64_t));
    if (!buf) return OR_ENOMEM;
    int64_t *best = buf, *tmp = buf + nblocks, *size = buf + 2 * nblocks;
    for (int64_t b = 0; b < nblocks; b++) size[b] = 0;
    for (int64_t m = 0; m < nm; m++) size[block[m]]++;
    for (int64_t i = 0; i < n; i++) {
        const uint8_t *r = codes + offs[i];
        int64_t len = lengths[i];
        for (int64_t b = 0; b < nblocks; b++) best[b] = 0;
        for (int64_t s = -band; s <= band; s++) {
            score(r, len, nm, mpos, want, block, nblocks, s, 1, tmp);
            for (int64_t b = 0; b < nblocks; b++) if (tmp[b] > best[b]) best[b] = tmp[b];
        }
        int64_t total = 0;
        for (int64_t b = 0; b < nblocks; b++) {
            int64_t mm = c_mm * (size[b] - best[b]);
            total += mm < c_indel ? mm : c_indel;
        }
        out[i] = total;
    }
    free(buf);
    return OR_OK;
}

/* ABI 3 (V13 B1): marker-anchored speculative projection of selected reads onto frame coordinates, without the DP.
 * Reference: vnxdna.recovery.pass1._spec_project_reference (the specification; this kernel is bit-exact with it).
 *
 * For read i of length L (delta = L - T) the marker blocks are visited left to right with a running shift s_prev
 * (0 at the start). Block b takes the first shift s of the sequence s_prev, s_prev - 1, s_prev + 1, s_prev - 2, ...
 * (|s| <= band, the whole block inside the read) at which every marker of the block matches; if no shift matches
 * exactly, it keeps s_prev when the block has at most one mismatch there (counted in mm); otherwise the read is not
 * projected (ok = 0, outputs 4 / erased / 0). Template position p lies in region r = number of blocks whose first
 * marker is <= p; its left shift is the shift of block r - 1 (0 for r = 0) and its right shift the shift of block r
 * (delta after the last block). The base at p is codes[p + left shift] (4 when outside the read); it is erased when it
 * is not A/C/G/T, when its quality is below min_q (quals given), or when the two shifts differ (an indel lies in the
 * region). indels = sum of |shift changes| including the final step to delta. Only frame positions are written.
 *   quals: NULL or a buffer of ncodes entries; T >= 1; mpos ascending in [0, T); frame_pos in [0, T).
 *   out_ok[n], out_bases/out_erased[n * frame_nt], out_mm[n], out_indels[n]. */
int vnx_or_spec_project(int64_t n, const uint8_t *codes, int64_t ncodes, const int64_t *lengths, const int64_t *offs,
                        const uint8_t *quals, int64_t min_q, int64_t T, int64_t nm, const int64_t *mpos,
                        const uint8_t *want, const int64_t *block, int64_t nblocks, int64_t frame_nt,
                        const int64_t *frame_pos, int64_t band, uint8_t *out_ok, uint8_t *out_bases,
                        uint8_t *out_erased, int64_t *out_mm, int64_t *out_indels)
{
    if (n < 0 || ncodes < 0 || nm < 0 || nblocks < 0 || nblocks > nm || band < 0 || band > INT32_MAX) return OR_EARG;
    if (T < 1 || T > INT32_MAX || frame_nt < 0 || frame_nt > T) return OR_EARG;
    if ((n > 0 && (!lengths || !offs || !out_ok || !out_mm || !out_indels)) || (ncodes > 0 && !codes)) return OR_EARG;
    if (n > 0 && frame_nt > 0 && (!out_bases || !out_erased || !frame_pos)) return OR_EARG;
    if (nm > 0 && (!mpos || !want || !block)) return OR_EARG;
    for (int64_t m = 0; m < nm; m++) {
        if (block[m] < 0 || block[m] >= nblocks || (m > 0 && block[m] < block[m - 1])) return OR_EARG;
        if (mpos[m] < 0 || mpos[m] >= T || (m > 0 && mpos[m] <= mpos[m - 1])) return OR_EARG;
    }
    if (nm > 0 && (block[0] != 0 || block[nm - 1] != nblocks - 1)) return OR_EARG;   /* every block has a marker */
    for (int64_t f = 0; f < frame_nt; f++)
        if (frame_pos[f] < 0 || frame_pos[f] >= T) return OR_EARG;
    for (int64_t i = 0; i < n; i++)
        if (lengths[i] < 0 || offs[i] < 0 || offs[i] > ncodes - lengths[i]) return OR_EARG;
    if (n == 0) return OR_OK;
    if ((uint64_t)n > SIZE_MAX / (size_t)(frame_nt > 0 ? frame_nt : 1)) return OR_EARG;
    /* first marker index and size of every block, the chosen shift per block */
    if ((uint64_t)(3 * nblocks) > SIZE_MAX / sizeof(int64_t) - (uint64_t)T) return OR_EARG;
    int64_t *buf = (int64_t *)malloc((size_t)(3 * nblocks + T) * sizeof(int64_t));
    if (!buf) return OR_ENOMEM;
    int64_t *first = buf, *size = buf + nblocks, *shift = buf + 2 * nblocks, *region = buf + 3 * nblocks;
    for (int64_t b = 0; b < nblocks; b++) size[b] = 0;
    for (int64_t m = 0; m < nm; m++) {
        if (size[block[m]] == 0) first[block[m]] = m;
        size[block[m]]++;
    }
    /* region of every template position: the number of blocks whose first marker is <= p */
    for (int64_t p = 0, reg = 0; p < T; p++) {
        while (reg < nblocks && mpos[first[reg]] <= p) reg++;
        region[p] = reg;
    }
    for (int64_t i = 0; i < n; i++) {
        const uint8_t *r = codes + offs[i];
        const uint8_t *q = quals ? quals + offs[i] : NULL;
        const int64_t L = lengths[i];
        const int64_t delta = L - T;
        uint8_t *ob = out_bases + (size_t)i * (size_t)frame_nt;
        uint8_t *oe = out_erased + (size_t)i * (size_t)frame_nt;
        int ok = delta >= -band && delta <= band;
        int64_t mm = 0, indels = 0, s_prev = 0;
        for (int64_t b = 0; ok && b < nblocks; b++) {
            const int64_t m0 = first[b], sz = size[b];
            const int64_t lo = mpos[m0], hi = mpos[m0 + sz - 1];
            int found = 0;
            for (int64_t d = 0; d <= 2 * band && !found; d++) {
                /* candidate order: s_prev, s_prev - 1, s_prev + 1, s_prev - 2, s_prev + 2, ... */
                const int64_t k = (d + 1) / 2;
                const int64_t s = (d & 1) ? s_prev - k : s_prev + k;
                if (s < -band || s > band || lo + s < 0 || hi + s >= L) continue;
                int all = 1;
                for (int64_t m = m0; m < m0 + sz && all; m++) all = r[mpos[m] + s] == want[m];
                if (all) {
                    indels += s >= s_prev ? s - s_prev : s_prev - s;
                    s_prev = s;
                    found = 1;
                }
            }
            if (!found) {
                int64_t bad = 0;
                if (lo + s_prev >= 0 && hi + s_prev < L)
                    for (int64_t m = m0; m < m0 + sz; m++) bad += r[mpos[m] + s_prev] != want[m];
                else
                    bad = sz + 1;
                if (bad <= 1) mm += bad;
                else ok = 0;
            }
            shift[b] = s_prev;
        }
        if (ok) indels += delta >= s_prev ? delta - s_prev : s_prev - delta;
        out_ok[i] = (uint8_t)ok;
        out_mm[i] = ok ? mm : 0;
        out_indels[i] = ok ? indels : 0;
        if (!ok) {
            for (int64_t f = 0; f < frame_nt; f++) { ob[f] = 4; oe[f] = 1; }
            continue;
        }
        for (int64_t f = 0; f < frame_nt; f++) {
            const int64_t p = frame_pos[f];
            const int64_t reg = region[p];
            const int64_t sl = reg == 0 ? 0 : shift[reg - 1];
            const int64_t sr = reg == nblocks ? delta : shift[reg];
            const int64_t j = p + sl;
            uint8_t base = 4;
            int er = sl != sr;
            if (j >= 0 && j < L) {
                base = r[j] < 4 ? r[j] : (uint8_t)4;
                if (q && (int64_t)q[j] < min_q) er = 1;
            }
            if (base > 3) er = 1;
            ob[f] = base;
            oe[f] = (uint8_t)er;
        }
    }
    free(buf);
    return OR_OK;
}
