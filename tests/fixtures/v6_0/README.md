# V6.0 golden fixtures (SYNTHETIC SOFTWARE TEST data)

These files are **synthetic software test fixtures**. No DNA was synthesised, stored or sequenced. Strands are the output of
the VNX-DNA encoder as it was **before the V6 Phase 2 refactor**; reads are produced by the SIMULATED channel
(`vnxdna/v4/channel.py`: i.i.d. substitutions, insertions and deletions, fixed coverage 4, 10 % reverse-complement strands,
shuffle window 64) at fixed seeds. It is a stress setting, not a fitted sequencing model.

- Generating tree: commit `1309554` (branch `build/v6-sprint`, `src/` unchanged since; package version 5.0.0). The generator
  accepts any later HEAD whose `src/` is identical to that commit and clean.
- Environment: Python 3.12, numpy 2.x, zstandard, gcc native kernels built in the same worktree
  (`python -m vnxdna.v5.native_alignment build`, `vnxdna.v6.native_reads build`, `vnxdna.v6.native_rs build`).
- Regenerate (output must be byte-identical, `SHA256SUMS` must not change):
  `PYTHONPATH=<worktree>/src python generate.py <worktree> [output-dir]`, then check `vnxdna.__file__` is under `<worktree>/src`
  (the generator asserts it). Do **not** regenerate in place with a newer tree; the fixtures are the reference.
- Cases (all containers use the default `zstd` builder at 5.0.0; payloads from `vnxdna.v4.datagen`, recorded in `manifest.json`):

| case | encoder options | files |
|---|---|---|
| `stripes-seq` | v4-balanced, stripe depth D 4, column parity Mc 2, sequential order | random.bin 7800 B (seed 6001) |
| `adaptive-interleaved` | v4-balanced, `outer_plan="adaptive"` (interleaved) | random.bin 4000 B (6002), text.txt 2000 B (6003) |
| `max-recovery` | redundancy profile `maximum-recovery` (v4-archival, D 8, Mc 2, interleaved) | random.bin 8200 B (6004) |
| `encrypted-stripes` | v4-balanced, D 4, Mc 2, interleaved, AES-256-GCM, fixed test-only archive id and salt | random.bin 7800 B (6005) |

- Per case: `inputs/<case>/` payload files, `<case>.vnx` container, `<case>.strands.fasta` clean strands,
  `<case>.reads.fastq.gz` noisy simulated reads (gzip, mtime 0; decompress before decoding). Channel seed per case is
  60001 + case index (60001..60004), listed in `manifest.json` with the container, strand and file SHA-256s.
- The passphrase of `encrypted-stripes` protects nothing real; it is a public test value (see `manifest.json`).
- The containers record `encoder.version` 5.0.0 in their manifest. Tests that rebuild containers compare section by
  section (see `tests/compat/test_byte_identity.py`), so a version bump or an informational `extensions.vnx` block does not
  invalidate the fixtures.
- Used by `tests/compat/test_v6_golden.py` and `tests/compat/test_byte_identity.py`.
