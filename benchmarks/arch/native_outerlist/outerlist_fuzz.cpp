// libFuzzer harness for the C++ outer-list kernel (src/vnxdna/native/cpp/outerlist.cpp, ABI 1).
// The input bytes choose a small code (k, m), P, the known positions, candidate positions/values/validity, the caps
// and an assignment problem; every buffer has exactly the size the C contract states (ASan reports any overrun).
// Properties: valid calls succeed; 0 <= accepted <= P; agreement counts in [-1, candidates]; the call is
// deterministic (a second call gives identical outputs); the known symbols are never modified.
// build: clang++ -std=c++20 -g -O1 -fsanitize=fuzzer,address,undefined benchmarks/arch/native_outerlist/outerlist_fuzz.cpp \
//        src/vnxdna/native/cpp/outerlist.cpp -o outerlist_fuzz
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>

extern "C" int vnx_ol_decode_row(int64_t, int64_t, int64_t, const uint8_t*, const uint8_t*, const uint8_t*,
                                 const uint8_t*, uint8_t*, int64_t, const int32_t*, const uint8_t*, const uint8_t*,
                                 int64_t, int64_t, int64_t, int32_t*, int32_t*);
extern "C" int vnx_ol_assign(int64_t, int64_t, const uint8_t*, int64_t, int64_t, const uint8_t*, int64_t,
                             const int16_t*, int64_t, const uint8_t*, int64_t, int64_t, int32_t*, int16_t*, int8_t*,
                             int8_t*);

namespace {
struct Src {
    const uint8_t* p;
    size_t n;
    uint8_t take() {
        if (!n) return 0;
        n--;
        return *p++;
    }
};
uint8_t gmul(uint8_t a, uint8_t b) {
    uint8_t r = 0;
    while (b) {
        if (b & 1) r ^= a;
        a = static_cast<uint8_t>((a << 1) ^ ((a & 0x80) ? 0x1D : 0));
        b >>= 1;
    }
    return r;
}
uint8_t MUL[256 * 256], INV[256];
}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    static bool init = false;
    if (!init) {
        for (int a = 0; a < 256; a++)
            for (int b = 0; b < 256; b++) MUL[a * 256 + b] = gmul(static_cast<uint8_t>(a), static_cast<uint8_t>(b));
        for (int a = 1; a < 256; a++)
            for (int b = 1; b < 256; b++)
                if (MUL[a * 256 + b] == 1) INV[a] = static_cast<uint8_t>(b);
        init = true;
    }
    Src s{data, size};
    const int k = 1 + s.take() % 12, m = 1 + s.take() % 6, P = 1 + s.take() % 8, n = k + m;
    // Cauchy parity matrix C[i][j] = 1 / ((k + i) ^ j), as vnxdna.codec.cauchy
    std::vector<uint8_t> parity(static_cast<size_t>(m * k));
    for (int i = 0; i < m; i++)
        for (int j = 0; j < k; j++) parity[static_cast<size_t>(i * k + j)] = INV[static_cast<uint8_t>((k + i) ^ j)];
    const int f = 1 + s.take() % 4;
    std::vector<uint8_t> known(static_cast<size_t>(n), 1);
    int unknown = 0;
    for (int p = 0; p < n && unknown < m + f; p++)
        if (s.take() & 1 || n - p <= m + f - unknown) { known[static_cast<size_t>(p)] = 0; unknown++; }
    std::vector<uint8_t> sym(static_cast<size_t>(n * P));
    for (auto& b : sym) b = s.take();
    std::vector<int> upos;
    for (int p = 0; p < n; p++)
        if (!known[static_cast<size_t>(p)]) upos.push_back(p);
    const int nc = s.take() % 12;
    std::vector<int32_t> cpos(static_cast<size_t>(nc) + 1);
    std::vector<uint8_t> cval(static_cast<size_t>(nc * P) + 1), cok(static_cast<size_t>(nc * P) + 1);
    for (int c = 0; c < nc; c++) {
        cpos[static_cast<size_t>(c)] = upos.empty() ? 0 : upos[s.take() % upos.size()];
        for (int j = 0; j < P; j++) {
            cval[static_cast<size_t>(c * P + j)] = s.take();
            cok[static_cast<size_t>(c * P + j)] = s.take() & 1;
        }
    }
    const int64_t min_extra = s.take() % 3, margin = 1 + s.take() % 3, subsets = 1 + s.take() % 200;
    std::vector<uint8_t> s1 = sym, s2 = sym;
    std::vector<int32_t> b1(static_cast<size_t>(P)), c1(static_cast<size_t>(P)), b2(static_cast<size_t>(P)), c2(static_cast<size_t>(P));
    const int rc = vnx_ol_decode_row(k, m, P, parity.data(), MUL, INV, known.data(), s1.data(), nc, cpos.data(),
                                     cval.data(), cok.data(), min_extra, margin, subsets, b1.data(), c1.data());
    const int f_real = k - (n - unknown);
    if (f_real >= 1 && f_real <= 8 && !upos.empty()) {
        if (rc < 0 || rc > P) abort();
        for (int j = 0; j < P; j++)
            if (b1[static_cast<size_t>(j)] < -1 || b1[static_cast<size_t>(j)] > nc) abort();
        const int rc2 = vnx_ol_decode_row(k, m, P, parity.data(), MUL, INV, known.data(), s2.data(), nc, cpos.data(),
                                          cval.data(), cok.data(), min_extra, margin, subsets, b2.data(), c2.data());
        if (rc2 != rc || s1 != s2 || b1 != b2 || c1 != c2) abort();
        for (int p = 0; p < n; p++)
            if (known[static_cast<size_t>(p)] && memcmp(&s1[static_cast<size_t>(p * P)], &sym[static_cast<size_t>(p * P)], static_cast<size_t>(P))) abort();
    } else if (rc >= 0 && f_real < 1) {
        abort();  // a row without unknown dof must be rejected
    }
    // assignment: a few frames against a few targets
    const int nf = s.take() % 6, nt = s.take() % 5, fb = 10 + s.take() % 20;
    std::vector<uint8_t> frames(static_cast<size_t>(nf * fb) + 1), targets(static_cast<size_t>(nt * 9) + 1), ks(4 * 16);
    for (auto& b : frames) b = s.take();
    for (auto& b : targets) b = s.take();
    for (auto& b : ks) b = s.take();
    int16_t vars[4] = {0, 1, 2, 3};
    std::vector<int32_t> ot(static_cast<size_t>(nf) + 1);
    std::vector<int16_t> ov(static_cast<size_t>(nf) + 1);
    std::vector<int8_t> od(static_cast<size_t>(nf) + 1), os(static_cast<size_t>(nf) + 1);
    const int ra = vnx_ol_assign(nf, fb, frames.data(), 4, 16, ks.data(), 4, vars, nt, nt ? targets.data() : nullptr,
                                 s.take() % 11, s.take() % 1024, ot.data(), ov.data(), od.data(), os.data());
    if (ra != 0) abort();
    for (int i = 0; i < nf; i++)
        if (ot[static_cast<size_t>(i)] < -1 || ot[static_cast<size_t>(i)] >= nt || od[static_cast<size_t>(i)] < 0 ||
            od[static_cast<size_t>(i)] > 11)
            abort();
    return 0;
}
