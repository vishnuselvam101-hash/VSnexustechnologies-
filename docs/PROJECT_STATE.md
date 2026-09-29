# VNX-DNA project state

_Last updated: 2026-09-29_

**Current version:** 1.0.0. The only version source is `src/vnxdna/_version.py`.
**Release status:** v1.0.0 is a stable research-grade *software* release. There is **no wet-lab validation**.
**Branch:** `vnx-dna/v0.2`. The V0.1 baseline remains at tag `v0.1-baseline` (`cf7d1f5`).
**Measurements:** experiments and benchmarks were run from a fresh clone at commit `bb33372`. Later commits change only
docs, tests, research scripts and results (`git diff bb33372 -- src` is empty).

## Architecture

The layers are store (container) → encode (outer Cauchy RS + in-band strand frames + inner RS + DNA mapping) →
simulate → decode → restore/verify. See [ARCHITECTURE.md](ARCHITECTURE.md). There is one implementation. The V0.1
code, tests, docs and results are archived unchanged under `research/legacy/`.

## Supported commands

`store` (`pack`), `encode`, `simulate`, `decode`, `restore`, `recover`, `verify`, `info`, `extract`, `pipeline`,
`keygen`, `benchmark`, `version`, `legacy info|restore`. Exit codes 0–8 and 70 ([CLI.md](CLI.md)).

## Configuration and guarantees

| aspect | state |
|---|---|
| outer ECC | Cauchy Reed–Solomon over GF(2⁸)/0x11D, default 64+16, configurable K+M ≤ 256. Any M lost strands per stripe are recoverable: proven MDS and exhaustively verified on small codes, including {4,5,7,11} for 8+4. |
| inner ECC | RS per strand, default 8 parity bytes (corrects 4 byte errors), CRC-32 re-checked after every correction |
| DNA codec | 2bit (default), rotation3, codebook8; GC / window / homopolymer / motif screening with 256 scrambler variants; fails loudly if unsatisfiable |
| strand | 244 nt default (61-byte frame: 9 header + 40 payload + 4 CRC + 8 parity); identity inside the DNA |
| compression | zstd (default, level 9), zlib, none; per chunk; bounded decompression |
| encryption | AES-256-GCM per chunk, HKDF-SHA256 subkeys, HMAC-SHA256 manifest, key check; name, size and hashes sealed |
| container | `.vxdna` v1 (magic, version, canonical manifest, body, SHA-256 trailer). `decode(encode(c)) == c` byte for byte |
| simulator | seeded dropout, coverage (fixed/Poisson), substitutions, insertions, deletions, bursts, reverse complement, shuffle; observed events plus an event log |
| synchronization | experimental opt-in RS-assisted realignment (single indel per read guaranteed) |
| random access | per chunk or byte range; only that chunk's stripes are decoded |
| compatibility | explicit read-only V0.1 decoder (dataset formats 1–3, RD-1 0.1). All 10 baseline fixtures decode. |

## Test status (fresh clone, fresh venv, Python 3.12.3)

- `pytest`: **269 tests, 0 failures** (163 unit, 62 integration, 5 property with Hypothesis, 11 adversarial/fuzz,
  28 CLI including the clean-room §35 test and the README-executes test). About 60 s wall time on 8 CPUs.
- ECC exhaustive: 8+4 (794 patterns) and 16 other configurations; sampled up to 200+56; 192+64 worst case.
- Extended fuzz (`research/experiments/extended_fuzz.py`, 3,000 seeds): 24,000 container mutations in raw,
  body-resealed, bit-flip and manifest-resealed modes, and 1,586 manifest-field cases. Result: **0 unstructured
  exceptions, 0 wrong outputs** (`research/results/extended_fuzz.json`).
- Package install: `pip install -e '.[dev]'` from a fresh clone succeeds, and `vnx-dna` is on PATH.

## Measured results (simulation only; [CHANNEL_MODEL.md](CHANNEL_MODEL.md), `research/results/`)

These come from 10 seeds per point with the default 64+16 profile on 60 kB:
- **Dropout:** 100 % recovered up to 5 %, 9/10 at 10 %, 2/10 at 15 %. The 96+48 profile gives 100 % up to 25 %.
- **Substitutions:** 100 % recovered up to 1.0 % per base, 0/10 at 1.5 %.
- **Deletions:** 7/10 at 0.05 % and 0/10 at 0.1 % without repair; 10/10 at 0.2 % with the experimental repair.
- **Wrong data returned: 0** in every trial.

## Benchmarks ([BENCHMARKS.md](BENCHMARKS.md))

At 10 MB (encrypted, 50 % compressible, 8-CPU x86-64, single-threaded):
- storage: 157,537 strands, 38.4 M nt, 3.84 nt per input byte (2.08 net bits/nt, all overheads included);
- speed: DNA encode 2.7 s; clean scan 1.7 s; damaged scan 16.8 s (the bottleneck is per-read RS);
- end to end: 24 s with about 270 MiB peak traced memory.

## Security review (at release)

- **Secret scan** of all tracked files (AWS/GitHub/Slack/OpenAI-style tokens, private keys, password/api_key/secret
  assignments; no gitleaks available): no findings. No `.env` or key files are tracked. The test keys are derived
  from public strings, and the V0.1 fixture Fernet key is labelled test-only.
- **Unsafe code:** no `eval`, `exec`, `pickle`, `shell=True` or `os.system`. The only subprocess is `git` with an
  argument list.
- **Files:** outputs are atomic and never written before verification. Stored names are sanitized and never used as
  output paths.
- **Hygiene:** no debug prints or TODO placeholders in `src/`.

## Known limitations

- Software only: no synthesis or sequencing, no primer design, and no secondary-structure or melting-temperature
  screening. The channel is an i.i.d. model with bursts, not fitted to any platform.
- Indel handling is experimental. It is slow, validated for one indel per read, and uses no consensus across copies.
- Everything runs in memory (4 GiB input limit, about 27× peak traced memory). Damaged-read decoding runs at about
  0.3 MB/s.
- Unencrypted archives have integrity digests but no authenticity. Encrypted archives still reveal the approximate
  size and compressibility ([SECURITY.md](SECURITY.md)).
- A tiny file costs about 97 strands, because the manifest and a minimum stripe dominate.
- If every copy of a metadata stripe is lost beyond 8 of 16 strands, decoding from reads alone fails. The `.vxdna`
  container still works.

## Future research

See [ROADMAP.md](ROADMAP.md): streaming, a vectorized inner decoder, padding, marker/consensus synchronization,
secondary-structure constraints, fitted channel models, fountain codes and wet-lab validation.

## Git / release

- Commits are logical and in `git log` from `204d8a8` onward.
- Tag `v1.0.0` marks the release commit.
- Publishing (pushing the branch and tag to `origin` without force, see [RELEASE_PROCESS.md](RELEASE_PROCESS.md))
  happens after tagging, so this tagged tree cannot record its outcome. No credentials are stored in the repository.
