/* MSan canary (tools/msan.sh): decodes a codeword whose bytes were never initialised with the real RS kernel. The
 * kernel branches on syndromes computed from those bytes, so an MSan build must stop with a use-of-uninitialised-value
 * report (exit 86). If this program exits 0, the build is not instrumented and the MSan gate must fail. */
#include "vnxdna/v6/native/rs.c"

#include <stdio.h>

int main(void) {
    enum { N = 70, NSYM = 16 };
    uint8_t *cw = malloc(N); /* deliberately not initialised */
    uint8_t out[N], ok[1];
    int64_t errata[1];
    vnx_rs_decode_batch(1, N, NSYM, cw, NULL, out, ok, errata, 1);
    printf("canary decoded ok=%d: MSan did not fire\n", ok[0]);
    free(cw);
    return 0;
}
