# CLI reference

`vnx-dna --help` shows the workflow, and `vnx-dna <command> --help` lists every option. Each command prints a short
human summary. Add `--json` for the full machine-readable report, or `--report FILE` to save it. Outputs are never
overwritten unless `--force` is given.

## Commands

| command | input → output | key needed? |
|---|---|---|
| `store FILE -o A.vxdna` (alias `pack`) | file → container: chunking, compression, optional AES-256-GCM, manifest. Sets the ECC/DNA parameters. | only to encrypt |
| `encode A.vxdna -o P.fasta` | container → DNA strand pool (outer RS, frames, inner RS, mapping, metadata strands) | no |
| `simulate P.fasta -o R.fasta [rates] --seed N` | strand pool → damaged reads, with a report of actual events (`--events` logs each one) | no |
| `decode R.fasta -o A.vxdna` | reads → container, byte-identical to the original | no |
| `restore A.vxdna\|R.fasta -o FILE` | container or reads → verified original file | if encrypted |
| `recover R.fasta -o FILE` | reads → verified original file in one step | if encrypted |
| `verify A.vxdna\|R.fasta` | independent PASS/FAIL report; writes nothing | for plaintext-level checks |
| `info A.vxdna\|R.fasta` | parameters, content description, efficiency | to see sealed name/size |
| `extract SRC -o OUT --chunk N` / `--start S [--end E]` | random access: one chunk or a byte range | if encrypted |
| `pipeline FILE -o OUT [rates]` | runs store → encode → simulate → decode → restore → verify with real files | optional |
| `keygen [-o key.txt]` | new 256-bit key (the file is mode 0600) | – |
| `benchmark [--sizes 1K,1M] [--repeats 3]` | stage-by-stage timings on this machine | – |
| `version [--json]` | version and supported formats | – |
| `legacy info\|restore` | explicit read-only V0.1 decoder | the Fernet key for encrypted V0.1 |

**Keys.** Pass `--key-file FILE` (base64url or hex, 32 bytes), or set the `VNXDNA_KEY` environment variable. `store`
encrypts whenever a key is available. `--encrypt` makes a missing key an error, and `--no-encrypt` ignores the key.

**Store parameters.** `--chunk-size`, `--compression none|zlib|zstd`, `--level`, `--data-shards K`,
`--parity-shards M`, `--mapping 2bit|rotation3|codebook8`, `--payload-bytes`, `--inner-parity`, `--gc-min`, `--gc-max`,
`--max-homopolymer`, `--gc-window`, `--forbid-motif` (repeatable), `--max-strand-nt`, `--no-name`, `--timestamp`.

**Channel parameters** (`simulate`, `pipeline`): `--substitution-rate`, `--insertion-rate`, `--deletion-rate`,
`--dropout-rate`, `--exact-dropout`, `--burst-rate`, `--burst-min/max`, `--burst-kind`, `--coverage`,
`--coverage-model fixed|poisson`, `--reverse-complement-rate`, `--shuffle/--no-shuffle`, `--seed`.

**Decode options:** `--experimental-indel-repair` and `--max-indel 1..3` (see [CHANNEL_MODEL.md](CHANNEL_MODEL.md)).

## Exit codes (stable)

| code | category | examples |
|---|---|---|
| 0 | SUCCESS | done. Also 0 when repair was needed; the report then says `"status": "RECOVERED"`. |
| 1 | VERIFICATION_FAILED | recomputed SHA-256 mismatch; `verify` found a failed check |
| 2 | USAGE_ERROR | unknown command or option, missing argument, value out of range |
| 3 | INVALID_INPUT | missing file, corrupt container, malformed manifest, invalid DNA, no strands identifiable |
| 4 | AUTHENTICATION_FAILED | key missing, wrong key, HMAC or GCM failure |
| 5 | INSUFFICIENT_REDUNDANCY | a stripe lost more than M strands (or the metadata stripes more than 8) |
| 6 | UNSUPPORTED_FORMAT | unknown version or feature, or a legacy archive given to a format-4 command |
| 7 | CONFIGURATION_ERROR | invalid parameters, unsatisfiable constraints |
| 8 | OUTPUT_ERROR | output exists (no `--force`) or is not writable |
| 70 | INTERNAL_ERROR | a bug; set `VNXDNA_DEBUG=1` for a traceback |

Errors are printed as one line, `vnx-dna: error [CATEGORY]: message`, on stderr. Normal operation never prints a
Python traceback.
