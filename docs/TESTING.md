# Testing

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m pytest            # whole suite (≈ 1–2 min on 8 cores)
python -m pytest tests/unit # fast subset
```

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
| `tests/cli/test_cli.py` | the installed CLI as a subprocess: help for every command, usage errors = 2, each documented exit code, no tracebacks, key via env, JSON reports, reproducible simulation with event log, pipeline, extract, legacy |
| `tests/cli/test_clean_room.py` | **§35 acceptance**: the exact documented command sequence on a 1 MB mixed binary file (plain and encrypted), `cmp` and `sha256sum`; > capacity dropout exits 5 and writes nothing |

The CLI tests use `vnx-dna` from `PATH` if it is installed, and fall back to `python -m vnxdna`.

`research/experiments/extended_fuzz.py` runs a longer fuzz campaign: 2 × 3,000 container mutations, and every
manifest field × 16 hostile values or deletion. Its first development run found one unstructured `TypeError` (a
non-list `required_features`), which is fixed and covered by `tests/adversarial`. Current results are recorded in
[PROJECT_STATE.md](PROJECT_STATE.md).
