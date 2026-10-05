# Compatibility and migration

| format | written by | read by current code | how |
|---|---|---|---|
| **archive format 5** + container file v2 + frame format 5 + VXS 1 | VNX-DNA 3.x and 2.x (`vnx-dna store/encode`) | yes | every command |
| **archive format 4** + container file v1 (`.vxdna`) + frame format 4 | VNX-DNA 1.0.0 (still written by `vnx-dna v1 store/encode`) | yes | `restore`, `recover`, `decode`, `verify`, `info`, `extract` (dispatched to the unchanged V1 code), `migrate` |
| V0.1 dataset, `format_version` 1 (VNX1 FASTA headers, no ECC) | V0.1 | yes, read-only | `vnx-dna legacy restore DIR -o FILE` |
| V0.1 dataset, `format_version` 2 (VNX2 headers, Vandermonde shards) | V0.1 | yes, read-only | same |
| V0.1 dataset, `format_version` 3 (constrained-v1 codebook, optional shards) | V0.1 | yes, read-only | same |
| RD-1 JSON archive `VNXDNA` / `0.1` | V0.1 R&D-1 | yes, read-only | `vnx-dna legacy restore FILE.json -o FILE` |
| anything else | – | no | `UNSUPPORTED_FORMAT` (6) or `INVALID_INPUT` (3) |

## VNX-DNA 2 ↔ 3

**Decision: no new format, no migration needed.** VNX-DNA 3 writes archive format 5, as VNX-DNA 2 did
([STORAGE_FORMAT.md](STORAGE_FORMAT.md)).

| direction | result | evidence |
|---|---|---|
| V3 reads V2 containers (plain, encrypted, resumed-encrypted) and V2 strand files | **yes**: restore, verify, random access and DNA decoding | `tests/v3/test_compat_v3.py` on files written by the 2.0.0 release (`tests/fixtures/v2_0/`) |
| V3 writes what V2 wrote | **yes** for fresh stores: same body, indexes and data strands; only `encoder.version` (and the metadata strands carrying the manifest) differ | `test_fresh_v3_stores_equal_v2_output_except_the_encoder_version` |
| V2 reads V3 archives and strands | **yes**, except encrypted archives whose store was **resumed** by V3: they declare `final-seal-epoch-v3` and V2 refuses them cleanly (exit 6, `UNSUPPORTED_FORMAT`) | `research/results/v3/v2-reads-v3.json` (run with the 2.0.0 CLI) |
| V2 store checkpoints resumed by V3 | **no**: refused with a clear message (they are not authenticated); start the store over | `src/vnxdna/v2/archive.py` |
| simulated reads from V2 vs V3 for the same seed | identical for unshuffled output and ordinary channels; shuffled output can differ for large pools, VXS input and very high coverage (V3 fixes the format dependence) | `tests/v3/test_channel_v3.py` |
| Python API | `vnxdna.v2.*` kept; additions only (new `DecodeOptionsV2` fields have defaults) | test suites |

## V1 (format 4) inside V2 and V3

* **Detection.** Containers are recognised by the container file version (1 = V1, 2 = V2); strand/read files by
  geometry discovery (frame format 4 or 5). V1 inputs go to the V1 modules (`vnxdna.api` and friends), which are
  byte-for-byte the 1.0.0 code, so V1 archives are read by exactly the code that wrote them. Reports carry
  `"format_version": 4`.
* **Writing V1** is still possible for anyone who needs it: `vnx-dna v1 store …` is the unchanged 1.0.0 CLI.
* **V1 limits apply to V1 archives**: in-memory processing (4 GiB input cap), 24-bit stripe index.
* The V1 test suite (unit, integration, property, adversarial, CLI via `vnx-dna v1`) still runs unchanged.

## Migration: `vnx-dna migrate`

```bash
vnx-dna migrate old.vxdna -o new.vxdna                      # V1 container → V2 container
vnx-dna migrate old.fasta -o new.vxdna                      # V1 DNA reads → V2 container
vnx-dna migrate old.vxdna -o new.vxdna -k key.txt           # encrypted: same key
vnx-dna migrate old.vxdna -o new.vxdna -k old.key --new-key-file new.key   # and rotate the key
```

1. **Before**: the V1 archive is restored through the complete V1 verification chain (manifest digest/HMAC, every
   stored-chunk SHA-256, AES-GCM, every plaintext-chunk SHA-256, whole-object SHA-256) into a private temporary
   file. Any failure aborts the migration before anything is written.
2. The V2 archive is built from that file (streaming; the original file name is kept).
3. **After**: the V2 archive is verified independently (trailer, manifest, indexes, every chunk, object SHA-256), and
   its object SHA-256 must equal the V1 one. Otherwise the V2 output is deleted and the command fails.

A corrupted V1 archive is never silently converted (`tests/v2/test_compat_v2.py`).

## Rules for legacy V0.1

- **Explicit dispatch.** Legacy archives are identified only by their own magic and version values
  (`format == "VNX-DNA"` with an integer `format_version` in 1–3, or `manifest.format == "VNXDNA"` with
  `format_version == "0.1"`). The format-4 commands refuse them with exit 6 and point to `vnx-dna legacy restore`.
  The legacy decoder never reads format 4.
- **Read-only.** VNX-DNA cannot write V0.1 formats. To migrate, run `vnx-dna legacy restore old -o file`, then
  `vnx-dna store file -o new.vxdna`.
- **Integrity.** The legacy decoder recomputes the SHA-256 recorded in the legacy manifest and refuses a mismatch.
  Fernet-encrypted legacy archives need the original Fernet key (`--key-file`).
- **Fixtures.** `tests/fixtures/v0_1/` holds ten archives generated by the original `v0.1-baseline` code (commit
  `cf7d1f5`). Every one decodes to its recorded SHA-256 (`tests/integration/test_legacy_compat.py`).

## Behavioural differences from the V0.1 decoder

All of these are strictly safer:
1. Every duplicate strand is validated, so a corrupt first copy no longer hides a valid copy.
2. For the non-MDS V0.1 shard code (formats 2 and 3), recovery tries *every* invertible K-subset of the surviving
   shards instead of only the first K. Loss patterns that are singular for every subset remain unrecoverable. That is
   a property of the V0.1 code and cannot be fixed on read.
3. Malformed metadata raises a structured error instead of `KeyError` or `TypeError`.

## Unsupported legacy cases

- V0.1 archives whose shard loss pattern is singular under the V0.1 generator.
- V0.1 `inspect`/`simulate` workflows. The V0.1 `simulate` command never worked (infinite recursion).
- The V0.1 HTTP API (archived under `research/legacy/v0_1_src/vnxdna/api`).

## Forward compatibility (formats 4 and 5)

A decoder rejects:
- an unknown `format_version` (it reads 4 and 5);
- an unknown `required_features` entry;
- nonzero container flags or reserved fields;
- a container file version other than 1 or 2, a VXS version other than 1, an unknown frame format nibble.

A future format must change one of these rather than reinterpret an existing field. `extensions` is the only place
where optional, ignorable data may be added, and it is still covered by the digest and HMAC.

## VNX4 archive IDs in 6.x (`content-v1`, V6 Phase 6)

The opt-in `content-v1` archive ID (`vnx archive --archive-id content`, spec V6 §2.3.2) changes neither the VNX4 format
nor any default output. Only the 16-byte `archive_id` value and `extensions.vnx.archive_id_derivation` differ; the
header, body, chunk table, file table and reference table are byte-identical to an `options-v1` build of the same input
(`tests/compat/test_archive_id_content_v1.py::test_identical_body_and_tables_under_both_derivations`). The released 5.0.0
reader (6aef3f4) opens, verifies and extracts such an archive and decodes its strands
(`::test_released_5_0_0_reader_opens_a_content_v1_archive`). The default stays `options-v1`.

## VNX-DNA 5 to 6

**Decision: no format change.** 6.x writes and reads the VNX4 container, strand frame 4 and superblocks 1 and 2.
Frame 6 and superblock 3 are specified for V7 and are not implemented ([V6_DEFERRED.md](V6_DEFERRED.md)).

| direction | result | evidence |
|---|---|---|
| 6.x decodes archives and reads written by 4.0 and 5.0 | yes: the golden fixtures `tests/fixtures/v4_0`, `v5_0` and `v6_0` are decoded, and the current encoder reproduces their strands byte for byte | `tests/compat/test_v4_v5_archives.py`, `tests/compat/test_v6_golden.py` |
| a 6.x container differs from a 5.0.0 container of the same input | yes by default: 6.x adds an `extensions.vnx` block (software and specification version); `ArchiveOptions(writer_provenance=False)` writes the 5.x layout. The block sits in the ignorable `extensions` object | spec §2.3.1; CHANGELOG |
| 5.0 and 4.0 read 6.x containers | specified to stay readable because `extensions` is ignorable; the committed test covers the opt-in `content-v1` archive ID with the released 5.0.0 reader | spec §2.3.1; `tests/compat/test_archive_id_content_v1.py` |
| 6.x on a pool with an unknown frame version | refused: exit 6 `FRAME_VERSION_UNSUPPORTED`, not retryable | spec §3.10 |
| 6.x on a V1 or V3 pool | refused: exit 6 `LEGACY_FORMAT`; use `vnx-dna` | spec §3.10 |
| 6.x on reads whose layout cannot be detected | refused: exit 3 `LAYOUT_UNDETECTED` | spec §3.10 |
| opt-in redundancy profile `high-dropout` (strand profile `v6-high-dropout`, 256 nt) | frame 4 and superblock 1 with existing layout options, so the format is unchanged. It is never selected by default. 6.x detects the layout from read length. Readers before 6.0 do not know the profile name and cannot auto-detect the layout. Decoding such strands with an earlier reader and an explicit layout has not been tested | `tests/v6/test_redundancy_profiles.py`; spec §3.7, §7.2 |
| Python imports | every `vnxdna.v4.*`, `v5.*`, `v6.*`, `errors`, `provenance` and `native` path still imports (module aliases and facades) | `tests/architecture/test_public_paths.py` |
| CLI | the 5.x JSON fields stay at the top level of every output; `vnx.result/1` envelope added; `--report` files are `vnx.decode-report/1` with the 5.x fields at the top level; exit codes 9 and 10 | CHANGELOG; [CLI.md](CLI.md) |

The one test that pinned the 5.0.0 container SHA-256 was replaced by section-wise assertions before the version bump
(decision 1 in [V6_ARCHITECTURE.md](V6_ARCHITECTURE.md)).
