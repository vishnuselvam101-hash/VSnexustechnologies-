# Testing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
PATH=$PWD/.venv/bin:$PATH python -m pytest   # whole suite, V1 + V2 + V3 (≈ 4 min on 8 cores); the README tests need vnx-dna on PATH
python -m pytest tests/v3     # V3 only
python -m pytest -m "not slow" # skip the bounded-memory measurement
ruff check src tests research # lint (pyflakes rules), also run in CI
```

Test helpers are imported from `tests/v1_support.py` and `tests/v2_support.py`, never from `conftest` (since V3:
`from conftest import …` was ambiguous when several suites were collected together, so `pytest tests/v2 tests/unit`
failed at collection).

## V3 suites (`tests/v3/`)

Every test for a defect found in the V2 audit or in the review of V3 fails on the 2.0.0 release and passes on V3.
This was checked by running the files against a checkout of tag `v2.0.0`.

| suite | what it proves |
|---|---|
| `test_container_security_v3.py` | no AES-GCM nonce reuse across two resumes or at re-finalisation; checkpoint epoch cannot be rolled back; fresh archives stay V2-compatible; padded bodies rejected; `stored_sha256` verified; truncated bodies give FAIL reports; no descriptor leak; non-UTF-8 names; profile names validated up front; trailer chunk limit up front; no silent clobbering; CLI: `--compression none`, human `info`, `verify` exit 4 without a key, disk full → exit 8 with no unresumable partial |
| `test_ecc_decoder_v3.py` | vectorised RS decoder at the `2e + f ≤ r` boundary for eight (r, n); agreement with `reedsolo` inside the bound and strictness beyond it (E1); CRC-gated batch correction; one/two-indel repair; repair of reads with `N`; long junk reads (D1); forged metadata lengths (D2); bounded line reads (D3); metadata repairs reported (D4); IUPAC reads decoded (D5); two-archive pools by tag (D6) |
| `test_v3_features.py` | burst resynchronisation for L = 1…29, deletions and insertions, both orientations, and its bounds; a coverage-1 pool with deletion bursts fails without and recovers with `burst_repair`; sweep parsing and validation; sweeps reproducible across worker counts with 0 undetected corruption; `simulate-errors` CLI contract; ECC registry |
| `test_compat_v3.py` | 2.0.0 fixtures intact; V2 plain, encrypted and resumed containers restore and verify; V2 strands decode; random access on V2 containers; fresh V3 stores equal V2 output except `encoder.version` |
| `test_roundtrip_v3.py` | empty, 1-byte, tiny, Unicode, repetitive, incompressible and chunk-boundary inputs through container and DNA (FASTA and VXS, plain and encrypted) |
| `test_channel_v3.py` | channel fixes B1–B3, B5, B11–B13 (truncation, format-independent shuffling, coverage-bounded batches, partial files, observed rates, orphan cap) and burst errors: contiguity, counts, determinism, zero-rate byte identity with v2.0.0 outputs, end-to-end recovery |
| `test_cli_v3.py` | CLI fixes B4, B6–B10, B14, B15: reports never clobber, clean exit codes for every bad path, closed stdout, consensus option validation, pipeline checks and cleanup, experiment statistics, explicit DNA index, refused compressed output names |
| `test_release_review_v3.py` | release-review findings H1–H2, M1–M3, L1–L2, L4–L7: `pipeline --work-dir` never overwrites or deletes the user's files; no report or output may be an input (key files and DNA indexes included), even with `--force`; symlinks planted at the old fixed temporary names are never written through; inputs named `<output>.partial` survive; atomic, symlink-safe `keygen` that keeps the old key when a write fails (`RLIMIT_FSIZE`); stale DNA index removed; conflicting `extract` selections and missing inputs give exit 3; bounded key-file reads; SIGTERM cleans up (exit 130, no partials, no temp dirs, no live workers); workers exit when the parent is killed with SIGKILL |
| `test_review_regressions_v3.py` | review findings R1–R8: replayed checkpoints, one-strand metadata bound bypass, `verify --report --force`, insertion and rotation3 burst spans, re-tagged metadata, hard-link fallback, sweep option validation |

## V2 suites (`tests/v2/`)

| suite | what it proves |
|---|---|
| `test_frame_crc_constraints.py` | vectorised CRC-32 = `zlib.crc32`; table RS parity = `reedsolo`; linear screening = direct screening (byte-identical); frame format 5 round trip with 32-bit addresses for all mappings; every strand satisfies the recorded constraints; unsatisfiable constraints fail; inner RS corrects ≤ r/2 and the CRC rejects the rest; reverse complements; tentative headers; V2 constraint checks = V1 and a scalar reference |
| `test_container_v2.py` | store/restore round trips (0 B … 50 KB, chunk boundaries, plain/encrypted, every profile); determinism across worker counts; per-chunk compression decision; random access reads only needed chunks; any single bit flip detected, never wrong output; truncated/unfinished archives rejected; wrong/missing key; streaming AEAD detects modified, reordered, duplicated, missing/truncated and spliced chunks; `verify --file` names damaged chunks; **SIGKILL during store → no archive published → `--resume` gives a byte-identical archive**; resume refuses a changed input |
| `test_dna_v2.py` | encode/decode gives the identical container (FASTA and VXS, plain and encrypted); encoding deterministic across worker counts; shuffled + duplicated strands; **exactly M strands lost in every ECC group → exact; M + 1 in one group → exit-5 error, no output, `verify` names the chunk**; substitutions in addresses corrected; majority/tie resolution; junk, invalid, truncated, foreign reads; mixed-archive pools refused; malformed FASTQ/VXS; quality-based erasures; random access through the DNA index reads only needed strands; atomic strand writer |
| `test_channel_cluster_consensus.py` | sequencing determinism; zero-error mode gives exact copies; event counts match rates statistically; Bernoulli sampler distribution; edit bookkeeping equals a scalar replay; reverse complements exact; informative qualities mark errors; VXS refuses length-changing channels; batched alignment = reference edit distance; consensus writes `N` for split evidence; cluster + consensus recover under indels, and read order does not change the outcome; truncated cluster files rejected |
| `test_compat_v2.py` | V1 containers and V1 reads through every V2 command; migrate from container and from reads; encrypted migration with the same or a rotated key; corrupted V1 archives refused with no output; V0.1 legacy still refused by the main decoder |
| `test_cli_v2.py` | the installed CLI: help for every command; the canonical workflow with `cmp`/`sha256sum`; every documented exit code (4, 3, 5, 6, 7, 8, 2) with no tracebacks; one-command pipeline; `experiment run` writes generated results; generator reproducibility; extract and info from containers and VXS |
| `test_properties_adversarial_v2.py` | Hypothesis: `restore(store(x)) == x`, `decode(encode(c)) == c`, and `restore(decode(consensus(cluster(sequence(encode(c)))))) == x` under a supported channel; fuzz: manifest fields and index entries mutated **with valid digests and trailer resealed** never yield wrong output; garbage read files never crash; extreme channels (60 % dropout, 8 % substitutions, 3 % indels, 0.3× coverage) fail detectably or recover exactly |
| `test_streaming_scale_v2.py` | size parsing; generator determinism and pattern compressibility; process-tree measurement harness; large-file corruption logic inside/beyond the guarantee; **peak RSS of store/encode/recover at 64 MB ≤ 1.25 × that at 8 MB + 48 MiB** (`slow`) |

## V1 suites (unchanged, still run)

| suite | what it proves |
|---|---|
| `tests/unit/test_ecc_cauchy.py` | GF(2⁸) axioms (exhaustive); MDS: every K-row subset invertible; **every** loss pattern ≤ M for 8+4 (794, including {4,5,7,11}) and 16 other configurations; sampled large codes; 192+64 worst case; M+1 losses raise |
| `tests/unit/test_inner_rs.py` | inner parity bit-identical to reedsolo; corrects r/2 errors and r erasures; refuses more |
| `tests/unit/test_dna_mapping.py` | every byte round-trips for all mappings; determinism; rotation/codebook properties; N erasures; vectorized constraint checks equal the scalar reference |
| `tests/unit/test_strand.py` | in-band identity; constraints met by every strand; substitutions inside capacity corrected, beyond it never a wrong payload; reverse complement; unsatisfiable constraints fail |
| `tests/unit/test_manifest_and_crypto.py` | schema/semantic violations (removed, added and retyped fields, floats, versions, features, sizes, names) give structured errors; digest and HMAC tampering; wrong key; fresh salts; AEAD binding (index, count, archive, domain, truncation); key parsing; bounded decompression |
| `tests/unit/test_container_file.py` | .vxdna layout, determinism, truncation, trailing data, magic/version/flags, trailer, atomic write |
| `tests/unit/test_duplicates.py` | corrupt→valid, valid→corrupt, reversed, many valid, all corrupt, conflicting valid-looking copies (tie → erasure; forged majority → caught by SHA-256) |
| `tests/unit/test_channel.py` | identity at zero rates; seeded reproducibility; dropout really drops; event log matches counts and positions |
| `tests/unit/test_indel.py` | single deletion/insertion at many positions repaired (plus a substitution); off by default |
| `tests/integration/test_full_dna_storage.py` | **the full lifecycle** with files: none/substitution/dropout/reorder/duplicate/RC/mixed damage × plain/encrypted; exact M erasures in every stripe recovered; M+1 fails with no output; heavy damage either recovers exactly or fails cleanly |
| `tests/integration/test_matrix_and_random_access.py` | 5 configurations × 4 sizes (incl. 0 and 1 byte) through DNA; 2.5 MB file; Unicode/long names; random access decodes only needed stripes and survives loss of other chunks; container tampering; output safety; determinism |
| `tests/integration/test_legacy_compat.py` | all 10 V0.1 fixtures; legacy never read by the format-4 decoder; the V0.1 generator really is singular on {4,5,7,11} |
| `tests/property/test_properties.py` | Hypothesis: `restore(store(x)) == x`, `decode(encode(c)) == c`, deterministic encoding, recovery under guaranteed damage (≤ r/2 substitutions per read, ≤ M adversarial erasures per stripe, shuffle, RC), any K of N, metadata round trip |
| `tests/adversarial/test_fuzz.py` | mutated containers (bit flips, deletions, insertions, truncation) never yield wrong data; mutated manifest fields give structured errors; arbitrary read text; randomly corrupted reads; junk inputs |
| `tests/cli/test_cli_v1.py` | the installed V1 CLI (`vnx-dna v1 …`) as a subprocess: help for every command, usage errors = 2, each documented exit code, no tracebacks, key via env, JSON reports, reproducible simulation with event log, pipeline, extract, legacy |
| `tests/cli/test_clean_room_v1.py` | V1 clean-room acceptance (`vnx-dna v1`): the exact documented command sequence on a 1 MB mixed binary file (plain and encrypted), `cmp` and `sha256sum`; > capacity dropout exits 5 and writes nothing |

The CLI tests use `vnx-dna` from `PATH` if it is installed, and fall back to `python -m vnxdna`.

`research/experiments/extended_fuzz.py` runs a longer fuzz campaign: 2 × 3,000 container mutations, and every
manifest field × 16 hostile values or deletion. Its first development run found one unstructured `TypeError` (a
non-list `required_features`), which is fixed and covered by `tests/adversarial`. Current results are recorded in
[PROJECT_STATE.md](PROJECT_STATE.md).

`tests/cli/test_readme.py` runs the README's quick-start and encryption blocks verbatim with the installed CLI.

The large-scale acceptance runs (1–10 GB, corruption at 1 GB) are not part of `pytest`. They take about an hour and
need tens of GB of disk. They are the `vnx-dna benchmark scale` / `benchmark corruption` commands, whose results are
in [LARGE_FILES.md](LARGE_FILES.md).
