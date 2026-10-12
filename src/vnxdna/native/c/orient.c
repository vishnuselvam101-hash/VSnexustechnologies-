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
#define VNX_OR_ABI 1
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
