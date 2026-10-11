// VNX-DNA hybrid architecture: outer-code list decoding of short rows (C++20, C ABI 1, loaded with ctypes).
//
// A row of the outer code is a linear [n = k + M, k] MDS code over GF(256), generator [I; C] (C = the Cauchy parity
// matrix from vnxdna.codec.cauchy), applied byte column by byte column to the P-byte symbols. A row is "short" when
// fewer than k symbols were verified (inner RS + CRC-32). Read clustering still leaves failed consensus frames for
// many of the missing symbols: most of their bytes are right, but too many are wrong for the inner code. This kernel
// combines them with the verified symbols through the outer code:
//
//   vnx_ol_assign      candidate frame -> (missing address, scrambler variant) by header distance (bytes 0..9),
//                      rejecting ambiguous and distant matches
//   vnx_ol_decode_row  with h verified symbols the codewords that agree with them form an f = k - h dimensional
//                      affine space; every candidate byte is one linear equation on it. Per column, every choice of f
//                      candidate positions (lexicographic, capped) determines a solution; the solution agreeing with
//                      the most candidate positions is accepted only with an explicit margin over every other
//                      solution and enough agreements beyond the f that determine it.
//
// The Python reference (vnxdna.recovery.outer_list) is the specification; tests check bit-exact equality.
// Contract: the caller owns every buffer; all sizes are validated before use; no global mutable state; every entry
// point catches all C++ exceptions (std::bad_alloc -> -2, anything else -3), so none crosses the C ABI.
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <new>
#include <vector>

#ifndef VNX_OL_ABI  // overridable only so that the tests can build a mismatching library
#define VNX_OL_ABI 1
#endif

namespace {

constexpr int OL_EARG = -1, OL_ENOMEM = -2, OL_EINTERNAL = -3;
constexpr int MAX_N = 255, MAX_F = 8, MAX_P = 4096, MAX_PER_POS = 4;

struct GF {
    const uint8_t* mul;  // 256 x 256
    const uint8_t* inv;  // 256
    uint8_t m(uint8_t a, uint8_t b) const { return mul[256 * a + b]; }
};

// Invert an f x f matrix in place (row-major); false if singular.
bool invert(const GF& gf, std::vector<uint8_t>& a, int f) {
    std::vector<uint8_t> b(static_cast<size_t>(f * f), 0);
    for (int i = 0; i < f; i++) b[static_cast<size_t>(i * f + i)] = 1;
    for (int c = 0; c < f; c++) {
        int p = -1;
        for (int r = c; r < f; r++)
            if (a[static_cast<size_t>(r * f + c)]) { p = r; break; }
        if (p < 0) return false;
        if (p != c)
            for (int j = 0; j < f; j++) {
                std::swap(a[static_cast<size_t>(p * f + j)], a[static_cast<size_t>(c * f + j)]);
                std::swap(b[static_cast<size_t>(p * f + j)], b[static_cast<size_t>(c * f + j)]);
            }
        const uint8_t iv = gf.inv[a[static_cast<size_t>(c * f + c)]];
        for (int j = 0; j < f; j++) {
            a[static_cast<size_t>(c * f + j)] = gf.m(a[static_cast<size_t>(c * f + j)], iv);
            b[static_cast<size_t>(c * f + j)] = gf.m(b[static_cast<size_t>(c * f + j)], iv);
        }
        for (int r = 0; r < f; r++) {
            const uint8_t t = a[static_cast<size_t>(r * f + c)];
            if (r == c || !t) continue;
            for (int j = 0; j < f; j++) {
                a[static_cast<size_t>(r * f + j)] ^= gf.m(t, a[static_cast<size_t>(c * f + j)]);
                b[static_cast<size_t>(r * f + j)] ^= gf.m(t, b[static_cast<size_t>(c * f + j)]);
            }
        }
    }
    a.swap(b);
    return true;
}

// Next f-combination of {0..q-1} in lexicographic order; false after the last.
bool next_comb(std::vector<int>& s, int q) {
    const int f = static_cast<int>(s.size());
    int i = f - 1;
    while (i >= 0 && s[static_cast<size_t>(i)] == q - f + i) i--;
    if (i < 0) return false;
    s[static_cast<size_t>(i)]++;
    for (int j = i + 1; j < f; j++) s[static_cast<size_t>(j)] = s[static_cast<size_t>(j - 1)] + 1;
    return true;
}

// Solution space of a short row: unknown positions U, x_U = a(col) + R z (z = values at the f free unknowns).
struct Space {
    int n = 0, K = 0, M = 0, f = 0, u = 0, h = 0;
    std::vector<int> U, Kn, prow, uidx;
    std::vector<uint8_t> R, T;  // R: u x f, T: m x m row operations
    const uint8_t* parity = nullptr;
    uint8_t H(int r, int p) const {
        return p < K ? parity[static_cast<size_t>(r * K + p)] : static_cast<uint8_t>(p - K == r);
    }
    // a for one column (sym: n x P)
    void offsets(const GF& gf, const uint8_t* sym, int64_t P, int64_t col, std::vector<uint8_t>& a,
                 std::vector<uint8_t>& rhs) const {
        for (int r = 0; r < M; r++) {
            uint8_t s = 0;
            for (int i = 0; i < h; i++)
                s ^= gf.m(H(r, Kn[static_cast<size_t>(i)]), sym[static_cast<int64_t>(Kn[static_cast<size_t>(i)]) * P + col]);
            rhs[static_cast<size_t>(r)] = s;
        }
        for (int c = 0; c < u; c++) {
            const int r = prow[static_cast<size_t>(c)];
            uint8_t s = 0;
            if (r >= 0)
                for (int j = 0; j < M; j++) s ^= gf.m(T[static_cast<size_t>(r * M + j)], rhs[static_cast<size_t>(j)]);
            a[static_cast<size_t>(c)] = s;
        }
    }
};

// Build the space; returns 0, OL_EARG (f out of range) or OL_EINTERNAL.
int build_space(const GF& gf, int64_t k, int64_t m, const uint8_t* parity, const uint8_t* known, Space& sp) {
    sp.K = static_cast<int>(k);
    sp.M = static_cast<int>(m);
    sp.n = sp.K + sp.M;
    sp.parity = parity;
    for (int p = 0; p < sp.n; p++) (known[p] ? sp.Kn : sp.U).push_back(p);
    sp.h = static_cast<int>(sp.Kn.size());
    sp.u = static_cast<int>(sp.U.size());
    sp.f = sp.K - sp.h;
    if (sp.f < 1 || sp.f > MAX_F) return OL_EARG;
    sp.uidx.assign(static_cast<size_t>(sp.n), -1);
    for (int i = 0; i < sp.u; i++) sp.uidx[static_cast<size_t>(sp.U[static_cast<size_t>(i)])] = i;
    const int M = sp.M, u = sp.u, f = sp.f;
    std::vector<uint8_t> A(static_cast<size_t>(M * u));
    sp.T.assign(static_cast<size_t>(M * M), 0);
    for (int r = 0; r < M; r++) {
        for (int i = 0; i < u; i++) A[static_cast<size_t>(r * u + i)] = sp.H(r, sp.U[static_cast<size_t>(i)]);
        sp.T[static_cast<size_t>(r * M + r)] = 1;
    }
    std::vector<int> pivcol;
    int row = 0;
    for (int c = 0; c < u && row < M; c++) {
        int p = -1;
        for (int r = row; r < M; r++)
            if (A[static_cast<size_t>(r * u + c)]) { p = r; break; }
        if (p < 0) continue;
        if (p != row) {
            for (int j = 0; j < u; j++) std::swap(A[static_cast<size_t>(p * u + j)], A[static_cast<size_t>(row * u + j)]);
            for (int j = 0; j < M; j++) std::swap(sp.T[static_cast<size_t>(p * M + j)], sp.T[static_cast<size_t>(row * M + j)]);
        }
        const uint8_t iv = gf.inv[A[static_cast<size_t>(row * u + c)]];
        for (int j = 0; j < u; j++) A[static_cast<size_t>(row * u + j)] = gf.m(A[static_cast<size_t>(row * u + j)], iv);
        for (int j = 0; j < M; j++) sp.T[static_cast<size_t>(row * M + j)] = gf.m(sp.T[static_cast<size_t>(row * M + j)], iv);
        for (int r = 0; r < M; r++) {
            const uint8_t q = A[static_cast<size_t>(r * u + c)];
            if (r == row || !q) continue;
            for (int j = 0; j < u; j++) A[static_cast<size_t>(r * u + j)] ^= gf.m(q, A[static_cast<size_t>(row * u + j)]);
            for (int j = 0; j < M; j++) sp.T[static_cast<size_t>(r * M + j)] ^= gf.m(q, sp.T[static_cast<size_t>(row * M + j)]);
        }
        pivcol.push_back(c);
        row++;
    }
    if (row != M || u - row != f) return OL_EINTERNAL;
    std::vector<int> freec, fidx(static_cast<size_t>(u), -1);
    sp.prow.assign(static_cast<size_t>(u), -1);
    for (int r = 0; r < M; r++) sp.prow[static_cast<size_t>(pivcol[static_cast<size_t>(r)])] = r;
    for (int c = 0; c < u; c++)
        if (sp.prow[static_cast<size_t>(c)] < 0) { fidx[static_cast<size_t>(c)] = static_cast<int>(freec.size()); freec.push_back(c); }
    sp.R.assign(static_cast<size_t>(u * f), 0);
    for (int c = 0; c < u; c++) {
        if (fidx[static_cast<size_t>(c)] >= 0) {
            sp.R[static_cast<size_t>(c * f + fidx[static_cast<size_t>(c)])] = 1;
        } else {
            const int r = sp.prow[static_cast<size_t>(c)];
            for (int j = 0; j < f; j++) sp.R[static_cast<size_t>(c * f + j)] = A[static_cast<size_t>(r * u + freec[static_cast<size_t>(j)])];
        }
    }
    return 0;
}

}  // namespace

extern "C" {

int vnx_ol_abi_version(void) { return VNX_OL_ABI; }

// Candidate frames (n x fb bytes: variant byte, scrambled header 1..9, ...) against t target headers (t x 9 plain bytes
// 1..9 of the frame) and nv trial variants (values < nks; keystreams ks: nks x span, span >= 9). Bytes whose bit is set
// in exact_mask (bit b = frame byte b, 0..9) must match exactly, or the pair is not considered. For each candidate:
// out_target = best target index or -1 (best distance > max_dist, or another target as close), out_variant,
// out_dist (best distance, bytes 0..9) and out_second (best distance to a different target, 11 if none).
int vnx_ol_assign(int64_t n, int64_t fb, const uint8_t* frames, int64_t nks, int64_t span, const uint8_t* ks,
                  int64_t nv, const int16_t* variants, int64_t t, const uint8_t* targets, int64_t max_dist,
                  int64_t exact_mask, int32_t* out_target, int16_t* out_variant, int8_t* out_dist,
                  int8_t* out_second) noexcept {
    try {
        if (n < 0 || fb < 10 || fb > 65536 || nks < 1 || nks > 256 || span < 9 || span > 65536 || nv < 1 || nv > 256 ||
            t < 0 || t > (int64_t{1} << 24) || max_dist < 0 || max_dist > 10 || exact_mask < 0 || exact_mask > 1023)
            return OL_EARG;
        if (n == 0) return 0;
        if (!frames || !ks || !variants || (t && !targets) || !out_target || !out_variant || !out_dist || !out_second)
            return OL_EARG;
        for (int64_t j = 0; j < nv; j++)
            if (variants[j] < 0 || variants[j] >= nks) return OL_EARG;
        for (int64_t i = 0; i < n; i++) {
            const uint8_t* fr = frames + i * fb;
            int best = 11, second = 11;
            int32_t bt = -1;
            int16_t bv = -1;
            for (int64_t j = 0; j < nv; j++) {
                const int16_t v = variants[j];
                const uint8_t* k = ks + static_cast<int64_t>(v) * span;
                uint8_t h[9];
                for (int b = 0; b < 9; b++) h[b] = static_cast<uint8_t>(fr[1 + b] ^ k[b]);
                const int d0 = fr[0] != static_cast<uint8_t>(v);
                if (d0 && (exact_mask & 1)) continue;
                for (int64_t x = 0; x < t; x++) {
                    const uint8_t* tg = targets + x * 9;
                    bool exact_ok = true;
                    for (int b = 0; b < 9 && exact_ok; b++)
                        if ((exact_mask >> (b + 1)) & 1) exact_ok = h[b] == tg[b];
                    if (!exact_ok) continue;
                    int d = d0;
                    for (int b = 0; b < 9 && d <= second; b++) d += h[b] != tg[b];
                    if (d < best) {
                        if (bt != static_cast<int32_t>(x)) second = best;
                        best = d;
                        bt = static_cast<int32_t>(x);
                        bv = v;
                    } else if (static_cast<int32_t>(x) != bt && d < second) {
                        second = d;
                    }
                }
            }
            const bool ok = bt >= 0 && best <= max_dist && second > best;
            out_target[i] = ok ? bt : -1;
            out_variant[i] = ok ? bv : static_cast<int16_t>(-1);
            out_dist[i] = static_cast<int8_t>(best);
            out_second[i] = static_cast<int8_t>(second);
        }
        return 0;
    } catch (const std::bad_alloc&) {
        return OL_ENOMEM;
    } catch (...) {
        return OL_EINTERNAL;
    }
}

// One short row. k data + m parity positions (n = k + m <= 255), P bytes per symbol. parity: m x k matrix C (row i =
// parity symbol i); mul (256 x 256) and inv (256): the GF(256) tables of vnxdna.codec.gf256. known: n flags; known
// payloads in sym (n x P; other rows ignored). Candidates: nc of them, position cpos (an unknown position), payload
// cval (nc x P) and byte validity cok (nc x P, 0 = erased byte). At most MAX_PER_POS candidates per position are
// used (the first ones). min_extra: agreements required beyond the f that determine a solution; margin: required lead
// over every other solution; max_subsets: cap on the f-subsets of candidate positions tried (lexicographic order).
// Outputs: sym gets, in every column with a solution, the unknown symbols of the best solution (a proposal; accepted
// columns are those with best >= f + min_extra and best - max(second, 0) >= margin); col_best / col_second (P) the
// agreement counts (-1 if the column has no full subset). Returns the number of accepted columns (0..P) or an error.
int vnx_ol_decode_row(int64_t k, int64_t m, int64_t P, const uint8_t* parity, const uint8_t* mul, const uint8_t* inv,
                      const uint8_t* known, uint8_t* sym, int64_t nc, const int32_t* cpos, const uint8_t* cval,
                      const uint8_t* cok, int64_t min_extra, int64_t margin, int64_t max_subsets, int32_t* col_best,
                      int32_t* col_second) noexcept {
    try {
        if (k < 1 || m < 1 || k + m > MAX_N || P < 1 || P > MAX_P || nc < 0 || nc > 65536 || min_extra < 0 ||
            min_extra > 255 || margin < 1 || margin > 255 || max_subsets < 1 || max_subsets > (int64_t{1} << 32))
            return OL_EARG;
        if (!parity || !mul || !inv || !known || !sym || !col_best || !col_second || (nc && (!cpos || !cval || !cok)))
            return OL_EARG;
        const int n = static_cast<int>(k + m), K = static_cast<int>(k), M = static_cast<int>(m);
        const GF gf{mul, inv};
        std::vector<int> U, Kn;
        for (int p = 0; p < n; p++) (known[p] ? Kn : U).push_back(p);
        const int h = static_cast<int>(Kn.size()), u = static_cast<int>(U.size()), f = K - h;
        if (f < 1 || f > MAX_F) return OL_EARG;
        std::vector<int> uidx(static_cast<size_t>(n), -1);
        for (int i = 0; i < u; i++) uidx[static_cast<size_t>(U[static_cast<size_t>(i)])] = i;
        for (int64_t c = 0; c < nc; c++)
            if (cpos[c] < 0 || cpos[c] >= n || known[cpos[c]]) return OL_EARG;
        // parity-check matrix H = [C | I] (m x n); reduce H_U (m x u) to RREF, recording the row operations in T
        auto H = [&](int r, int p) -> uint8_t {
            return p < K ? parity[static_cast<size_t>(r * K + p)] : static_cast<uint8_t>(p - K == r);
        };
        std::vector<uint8_t> A(static_cast<size_t>(M * u)), T(static_cast<size_t>(M * M), 0);
        for (int r = 0; r < M; r++) {
            for (int i = 0; i < u; i++) A[static_cast<size_t>(r * u + i)] = H(r, U[static_cast<size_t>(i)]);
            T[static_cast<size_t>(r * M + r)] = 1;
        }
        std::vector<int> pivcol;  // pivot column (index into U) of each RREF row
        int row = 0;
        for (int c = 0; c < u && row < M; c++) {
            int p = -1;
            for (int r = row; r < M; r++)
                if (A[static_cast<size_t>(r * u + c)]) { p = r; break; }
            if (p < 0) continue;
            if (p != row) {
                for (int j = 0; j < u; j++) std::swap(A[static_cast<size_t>(p * u + j)], A[static_cast<size_t>(row * u + j)]);
                for (int j = 0; j < M; j++) std::swap(T[static_cast<size_t>(p * M + j)], T[static_cast<size_t>(row * M + j)]);
            }
            const uint8_t iv = gf.inv[A[static_cast<size_t>(row * u + c)]];
            for (int j = 0; j < u; j++) A[static_cast<size_t>(row * u + j)] = gf.m(A[static_cast<size_t>(row * u + j)], iv);
            for (int j = 0; j < M; j++) T[static_cast<size_t>(row * M + j)] = gf.m(T[static_cast<size_t>(row * M + j)], iv);
            for (int r = 0; r < M; r++) {
                const uint8_t q = A[static_cast<size_t>(r * u + c)];
                if (r == row || !q) continue;
                for (int j = 0; j < u; j++) A[static_cast<size_t>(r * u + j)] ^= gf.m(q, A[static_cast<size_t>(row * u + j)]);
                for (int j = 0; j < M; j++) T[static_cast<size_t>(r * M + j)] ^= gf.m(q, T[static_cast<size_t>(row * M + j)]);
            }
            pivcol.push_back(c);
            row++;
        }
        if (row != M || u - row != f) return OL_EINTERNAL;  // impossible for an MDS code: rank M, f free unknowns
        std::vector<int> freec, prow(static_cast<size_t>(u), -1), fidx(static_cast<size_t>(u), -1);
        for (int r = 0; r < M; r++) prow[static_cast<size_t>(pivcol[static_cast<size_t>(r)])] = r;
        for (int c = 0; c < u; c++)
            if (prow[static_cast<size_t>(c)] < 0) { fidx[static_cast<size_t>(c)] = static_cast<int>(freec.size()); freec.push_back(c); }
        // x_c = a_c(col) + sum_j R[c][j] z_j, z = values at the free unknowns
        std::vector<uint8_t> R(static_cast<size_t>(u * f), 0);
        for (int c = 0; c < u; c++) {
            if (fidx[static_cast<size_t>(c)] >= 0) {
                R[static_cast<size_t>(c * f + fidx[static_cast<size_t>(c)])] = 1;
            } else {
                const int r = prow[static_cast<size_t>(c)];
                for (int j = 0; j < f; j++) R[static_cast<size_t>(c * f + j)] = A[static_cast<size_t>(r * u + freec[static_cast<size_t>(j)])];
            }
        }
        // candidate positions (unknown index), first MAX_PER_POS candidates each, in order of first appearance
        std::vector<int> qpos;
        std::vector<std::vector<int64_t>> qc;
        std::vector<int> qof(static_cast<size_t>(u), -1);
        for (int64_t c = 0; c < nc; c++) {
            const int ui = uidx[static_cast<size_t>(cpos[c])];
            if (qof[static_cast<size_t>(ui)] < 0) {
                qof[static_cast<size_t>(ui)] = static_cast<int>(qpos.size());
                qpos.push_back(ui);
                qc.emplace_back();
            }
            auto& v = qc[static_cast<size_t>(qof[static_cast<size_t>(ui)])];
            if (static_cast<int>(v.size()) < MAX_PER_POS) v.push_back(c);
        }
        const int Q = static_cast<int>(qpos.size());
        std::vector<uint8_t> a(static_cast<size_t>(u)), rhs(static_cast<size_t>(M)), sub(static_cast<size_t>(f * f)),
            z(static_cast<size_t>(f)), bz(static_cast<size_t>(f)), y(static_cast<size_t>(f));
        int accepted = 0;
        for (int64_t col = 0; col < P; col++) {
            col_best[col] = -1;
            col_second[col] = -1;
            // rhs = H_known * c_known; a = T * rhs at pivot unknowns, 0 at free unknowns
            for (int r = 0; r < M; r++) {
                uint8_t s = 0;
                for (int i = 0; i < h; i++) s ^= gf.m(H(r, Kn[static_cast<size_t>(i)]), sym[static_cast<int64_t>(Kn[static_cast<size_t>(i)]) * P + col]);
                rhs[static_cast<size_t>(r)] = s;
            }
            for (int c = 0; c < u; c++) {
                const int r = prow[static_cast<size_t>(c)];
                uint8_t s = 0;
                if (r >= 0)
                    for (int j = 0; j < M; j++) s ^= gf.m(T[static_cast<size_t>(r * M + j)], rhs[static_cast<size_t>(j)]);
                a[static_cast<size_t>(c)] = s;
            }
            if (Q < f) continue;
            int best = -1, second = -1;
            std::vector<int> S(static_cast<size_t>(f));
            for (int i = 0; i < f; i++) S[static_cast<size_t>(i)] = i;
            int64_t tried = 0;
            do {
                if (tried++ >= max_subsets) break;
                // candidate choice per position of S (odometer over at most MAX_PER_POS^f choices)
                std::vector<int> pick(static_cast<size_t>(f), 0);
                for (;;) {
                    bool valid = true;
                    for (int i = 0; i < f && valid; i++) {
                        const int64_t c = qc[static_cast<size_t>(S[static_cast<size_t>(i)])][static_cast<size_t>(pick[static_cast<size_t>(i)])];
                        valid = cok[c * P + col] != 0;
                    }
                    if (valid) {
                        for (int i = 0; i < f; i++) {
                            const int ui = qpos[static_cast<size_t>(S[static_cast<size_t>(i)])];
                            for (int j = 0; j < f; j++) sub[static_cast<size_t>(i * f + j)] = R[static_cast<size_t>(ui * f + j)];
                            const int64_t c = qc[static_cast<size_t>(S[static_cast<size_t>(i)])][static_cast<size_t>(pick[static_cast<size_t>(i)])];
                            y[static_cast<size_t>(i)] = static_cast<uint8_t>(cval[c * P + col] ^ a[static_cast<size_t>(ui)]);
                        }
                        if (invert(gf, sub, f)) {
                            for (int i = 0; i < f; i++) {
                                uint8_t s = 0;
                                for (int j = 0; j < f; j++) s ^= gf.m(sub[static_cast<size_t>(i * f + j)], y[static_cast<size_t>(j)]);
                                z[static_cast<size_t>(i)] = s;
                            }
                            int agree = 0;
                            for (int q = 0; q < Q; q++) {
                                const int ui = qpos[static_cast<size_t>(q)];
                                uint8_t x = a[static_cast<size_t>(ui)];
                                for (int j = 0; j < f; j++) x ^= gf.m(R[static_cast<size_t>(ui * f + j)], z[static_cast<size_t>(j)]);
                                for (const int64_t c : qc[static_cast<size_t>(q)])
                                    if (cok[c * P + col] && cval[c * P + col] == x) { agree++; break; }
                            }
                            if (best >= 0 && std::equal(z.begin(), z.end(), bz.begin())) {
                                // the same solution again: nothing changes
                            } else if (agree > best) {
                                second = best;
                                best = agree;
                                bz = z;
                            } else if (agree > second) {
                                second = agree;
                            }
                        }
                    }
                    int i = f - 1;
                    while (i >= 0) {
                        const size_t si = static_cast<size_t>(i);
                        if (++pick[si] < static_cast<int>(qc[static_cast<size_t>(S[si])].size())) break;
                        pick[si] = 0;
                        i--;
                    }
                    if (i < 0) break;
                }
            } while (next_comb(S, Q));
            col_best[col] = best;
            col_second[col] = second;
            if (best >= 0)
                for (int c = 0; c < u; c++) {
                    uint8_t x = a[static_cast<size_t>(c)];
                    for (int j = 0; j < f; j++) x ^= gf.m(R[static_cast<size_t>(c * f + j)], bz[static_cast<size_t>(j)]);
                    sym[static_cast<int64_t>(U[static_cast<size_t>(c)]) * P + col] = x;
                }
            if (best >= f + min_extra && best - std::max(second, 0) >= margin) accepted++;
        }
        return accepted;
    } catch (const std::bad_alloc&) {
        return OL_ENOMEM;
    } catch (...) {
        return OL_EINTERNAL;
    }
}

// Frame-level hypothesis selection for one short row (same inputs as vnx_ol_decode_row for the code and the known
// symbols). nh hypotheses: position hpos (unknown), payload hval (nh x P). Every f-tuple of hypotheses at distinct
// positions determines a codeword per column. Tuples are tried best first: by their largest index, then
// lexicographic, so that every tuple within the first m hypotheses precedes any tuple using hypothesis m + 1 (the
// caller ranks the hypotheses); at most max_tuples tuples with distinct positions are tried. Each hypothesis
// scores the number of columns where it agrees with that codeword. The tuple supported by the most hypotheses with at
// least tau agreeing columns wins (first in order on ties). Outputs: agree (nh) = the winning codeword's agreement per
// hypothesis (0 if no tuple). Returns the winning support count (0 if none) or an error code.
int vnx_ol_ransac(int64_t k, int64_t m, int64_t P, const uint8_t* parity, const uint8_t* mul, const uint8_t* inv,
                  const uint8_t* known, const uint8_t* sym, int64_t nh, const int32_t* hpos, const uint8_t* hval,
                  int64_t tau, int64_t max_tuples, int32_t* agree) noexcept {
    try {
        if (k < 1 || m < 1 || k + m > MAX_N || P < 1 || P > MAX_P || nh < 0 || nh > 4096 || tau < 1 || tau > P ||
            max_tuples < 1 || max_tuples > (int64_t{1} << 32))
            return OL_EARG;
        if (!parity || !mul || !inv || !known || !sym || !agree || (nh && (!hpos || !hval))) return OL_EARG;
        const GF gf{mul, inv};
        Space sp;
        const int rc = build_space(gf, k, m, parity, known, sp);
        if (rc) return rc;
        for (int64_t i = 0; i < nh; i++) {
            if (hpos[i] < 0 || hpos[i] >= sp.n || known[hpos[i]]) return OL_EARG;
            agree[i] = 0;
        }
        const int f = sp.f, u = sp.u, H = static_cast<int>(nh);
        if (H < f) return 0;
        // per column offsets a (u x P)
        std::vector<uint8_t> acol(static_cast<size_t>(u) * static_cast<size_t>(P)), a(static_cast<size_t>(u)),
            rhs(static_cast<size_t>(sp.M));
        for (int64_t col = 0; col < P; col++) {
            sp.offsets(gf, sym, P, col, a, rhs);
            for (int c = 0; c < u; c++) acol[static_cast<size_t>(c) * static_cast<size_t>(P) + static_cast<size_t>(col)] = a[static_cast<size_t>(c)];
        }
        std::vector<int> hu(static_cast<size_t>(H));
        for (int i = 0; i < H; i++) hu[static_cast<size_t>(i)] = sp.uidx[static_cast<size_t>(hpos[i])];
        std::vector<int> S(static_cast<size_t>(f)), head(static_cast<size_t>(f - 1));
        std::vector<uint8_t> sub(static_cast<size_t>(f * f)), y(static_cast<size_t>(f)), z(static_cast<size_t>(f));
        std::vector<int32_t> cnt(static_cast<size_t>(H)), bestcnt(static_cast<size_t>(H), 0);
        int best = 0;
        int64_t tried = 0;
        bool capped = false;
        // tuple = (f - 1)-combination "head" of {0..m-1} followed by m, for m = f-1 .. H-1 (best-first order)
        for (int m = f - 1; m < H && !capped; m++) {
          for (int i = 0; i < f - 1; i++) head[static_cast<size_t>(i)] = i;
          do {
            for (int i = 0; i < f - 1; i++) S[static_cast<size_t>(i)] = head[static_cast<size_t>(i)];
            S[static_cast<size_t>(f - 1)] = m;
            bool distinct = true;
            for (int i = 0; i < f && distinct; i++)
                for (int j = 0; j < i && distinct; j++)
                    distinct = hu[static_cast<size_t>(S[static_cast<size_t>(i)])] != hu[static_cast<size_t>(S[static_cast<size_t>(j)])];
            if (!distinct) continue;
            if (tried++ >= max_tuples) { capped = true; break; }
            for (int i = 0; i < f; i++)
                for (int j = 0; j < f; j++)
                    sub[static_cast<size_t>(i * f + j)] = sp.R[static_cast<size_t>(hu[static_cast<size_t>(S[static_cast<size_t>(i)])] * f + j)];
            if (!invert(gf, sub, f)) continue;
            std::fill(cnt.begin(), cnt.end(), 0);
            for (int64_t col = 0; col < P; col++) {
                for (int i = 0; i < f; i++) {
                    const int h = S[static_cast<size_t>(i)];
                    y[static_cast<size_t>(i)] = static_cast<uint8_t>(hval[static_cast<int64_t>(h) * P + col] ^
                                                acol[static_cast<size_t>(hu[static_cast<size_t>(h)]) * static_cast<size_t>(P) + static_cast<size_t>(col)]);
                }
                for (int i = 0; i < f; i++) {
                    uint8_t sacc = 0;
                    for (int j = 0; j < f; j++) sacc ^= gf.m(sub[static_cast<size_t>(i * f + j)], y[static_cast<size_t>(j)]);
                    z[static_cast<size_t>(i)] = sacc;
                }
                for (int h = 0; h < H; h++) {
                    const int c = hu[static_cast<size_t>(h)];
                    uint8_t x = acol[static_cast<size_t>(c) * static_cast<size_t>(P) + static_cast<size_t>(col)];
                    for (int j = 0; j < f; j++) x ^= gf.m(sp.R[static_cast<size_t>(c * f + j)], z[static_cast<size_t>(j)]);
                    cnt[static_cast<size_t>(h)] += hval[static_cast<int64_t>(h) * P + col] == x;
                }
            }
            int support = 0;
            for (int h = 0; h < H; h++) support += cnt[static_cast<size_t>(h)] >= tau;
            if (support > best) {
                best = support;
                bestcnt = cnt;
            }
          } while (next_comb(head, m));
        }
        for (int h = 0; h < H; h++) agree[h] = bestcnt[static_cast<size_t>(h)];
        return best;
    } catch (const std::bad_alloc&) {
        return OL_ENOMEM;
    } catch (...) {
        return OL_EINTERNAL;
    }
}

}  // extern "C"
