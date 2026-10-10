# VNX-DNA web layer (TypeScript)

Typed access to the JSON contracts VNX-DNA publishes, for web applications, dashboards and Node tooling. No DNA
encoding, alignment or decoding runs here: those stay in Python and the native (C, Rust) kernels.

| module | what |
|---|---|
| `src/contracts.ts` | types and runtime guards for `vnx.result/1`, `vnx.event/1`, `vnx.error/1` and the native kernel status; the enums are checked against `src/vnxdna/core/schemas/*.schema.json` by the tests |
| `src/parse.ts` | event-log line parser (no Node APIs; shared with the browser) |
| `src/events.ts` | `followEvents(path)`: tails a `vnx decode --events` log while the decode runs (partial lines held, malformed lines reported, bounded buffer, `AbortSignal`) |
| `src/progress.ts` | pure reduction of events to a progress view; SUCCESS, PARTIAL and FAILURE stay distinct, an unfinished log is `running` |
| `src/client.ts` | `vnx(args)` runs the CLI without a shell and returns the validated envelope or throws `VnxCommandError` with the `vnx.error/1` object; `nativeStatus()` |
| `dashboard/` | static page that renders a decode report and/or an event log chosen by the user; no network, `textContent` only, strict CSP |

```bash
npm ci                 # TypeScript 5.9.3 and @types/node (dev only); Node >= 22.18 runs the .ts sources directly
npm run typecheck      # tsc --strict, no emit
npm run build          # dashboard/dist/*.js for dashboard/index.html
VNX_TEST_PYTHON=python npm test     # node:test; the client tests run this repository's CLI (PYTHONPATH=../src)
```

Not implemented: a network service (HTTP/WebSocket) for live progress. VNX-DNA has no server today; adding one is a
security-relevant change (VNX-Secure deny-by-default) and needs its own design and review. Until then, live progress
comes from following the `--events` file.
