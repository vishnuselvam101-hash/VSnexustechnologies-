# CLI reference (V3)

`vnx-dna --help` shows the workflow, and `vnx-dna <command> --help` lists every option. Commands print a short
summary. `--json` prints the full machine-readable report, and `--report FILE` saves it. Outputs, including
`--report` files, are never overwritten without `--force` (since V3; VNX-DNA 2.0 overwrote report files, even the
command's own input). No output and no report may be one of the command's inputs, key files and DNA indexes
included, not even with `--force` (V3 release review: `--report key.txt --force` replaced the archive's key file, and
`extract a.vxdna -o a.vxdna --force` replaced the archive). Outputs are written to private, uniquely named temporary
files (mode 0600) and published atomically, without replacing a file that appeared while the command ran (V3). Every reading command accepts V1 (format 4) inputs as
well. The V1 CLI is available unchanged as `vnx-dna v1 …`.

## Commands

| command | input → output | key? |
|---|---|---|
| `store FILE -o A.vxdna` | file → streaming format-5 container (chunks, per-chunk zstd, optional AES-256-GCM, index). `--profile`, `--resume`, `--checkpoint-interval`, `--workers` | only to encrypt |
| `encode A.vxdna -o S.fasta\|S.vxs` | container → strands (ECC groups, frames, constraint screening) + DNA index `S.*.vxidx`. `--format`, `--no-index`, `--workers` | no |
| `simulate S -o R` | storage channel (synthesis errors, dropout, abundance); default 1 read per strand | no |
| `sequence S -o R.fastq` | sequencing channel: coverage, errors, duplicates, truncation, N, junk, contamination, qualities (all options below) | no |
| `reads R [-o F]` | read validation, statistics and filtering (`--min-length`, `--max-length`, `--min-mean-quality`, `--keep-invalid`) | no |
| `cluster R -o C.jsonl` | reads → clusters (address indexing; minimizer index for unaddressed reads) | no |
| `consensus C.jsonl -o X.fasta` | clusters → one consensus per cluster, `N` where ambiguous (`--band`, `--max-edit-fraction`, `--min-winner-share`) | no |
| `decode R -o A.vxdna` | reads/strands/consensus → the original container, byte for byte | no |
| `recover R -o FILE` | reads/strands → verified original file | if encrypted |
| `restore A.vxdna\|R -o FILE` | container (V1/V2) or reads → verified original file | if encrypted |
| `verify A.vxdna\|R [--file F]` | independent PASS/FAIL report; with `--file`, also compares a recovered file chunk by chunk | for plaintext checks |
| `info A.vxdna\|R` | parameters, ECC guarantee, content description, DNA size | to see sealed fields |
| `extract SRC -o OUT --offset N [--length L]` / `--chunk N` | random access (container, or DNA through the DNA index); V1 `--start/--end` also accepted. Conflicting selections (`--chunk` with a range, `--length` with `--end`, `--offset` with `--start`) are refused with exit 3 | if encrypted |
| `migrate V1 -o A.vxdna` | V1 container or V1 reads → V2 container, verified before and after (`--new-key-file` rotates the key) | if encrypted |
| `pipeline FILE -o OUT` | store → encode → [sequence → cluster → consensus] → decode → restore → verify. With `--work-dir`, existing intermediate files (`FILE.vxdna`, `FILE.fasta`, …) are refused unless `--force` or `--resume` is given, and `--cleanup` deletes only files this run wrote. `--work-dir`, `--temp-dir`, `--chunk-size`, `--workers`, `--resume`, `--cleanup keep\|outputs\|all`, `--report`. Default: consensus when coverage > 1 and the reads are FASTQ. Large inputs with substitution-only channels use VXS reads, which are decoded directly (the inner code and the duplicate vote handle substitutions); force either way with `--consensus/--no-consensus` | optional |
| `experiment run --input F --output DIR` | reproducible channel experiment; `--trials N` for Monte Carlo | optional |
| `simulate-errors FILE -o DIR --sweep TYPE=R1,R2 …` | **V3** error-channel sweep: recovery statistics per error type and rate (`sweep.json`, `sweep.csv`, `sweep.md`); `--trials`, `--coverage`, `--coverage-model`, `--burst-length`, `--consensus/--no-consensus`, `--indel-repair`, `--max-indel`, `--burst-repair`; exit 70 if any trial produced wrong output ([ERROR_MODEL.md](ERROR_MODEL.md)) | optional |
| `benchmark generate --size 10GB --pattern mixed --seed 42 -o F` | reproducible test data; reports exact size and SHA-256 | – |
| `benchmark scale --sizes … --work-dir D` | large-file scalability benchmark with the real CLI (time, CPU, peak RAM, swap, disk) | optional |
| `benchmark corruption --size 1GB --work-dir D` | large-file damage acceptance (inside vs beyond the ECC guarantee) | – |
| `benchmark stages` / `benchmark v1` | per-stage benchmark A–H / the V1 benchmark | – |
| `keygen [-o key.txt]` | new 256-bit key (file mode 0600) | – |
| `version [--json]` | version and formats read and written | – |
| `legacy info\|restore` | explicit read-only V0.1 decoder | Fernet key for encrypted V0.1 |
| `v1 …` | the V1 (1.0.0) CLI, unchanged; writes format 4 | – |

**Keys:** `--key-file FILE` (32 bytes, base64url or hex) or `VNXDNA_KEY`. A key file must be a regular file of at most
4 KiB; one that group or others can access is used with a warning. A malformed `VNXDNA_KEY` is an error (exit 7) even
for commands that turn out not to need a key, so a misconfigured environment never goes unnoticed. `store` encrypts whenever a key is available.
`--encrypt` makes a missing key an error, and `--no-encrypt` ignores it.

**Store parameters** (override the profile): `--chunk-size 1MiB`, `--compression zstd|zlib|none`, `--level`,
`--data-shards K`, `--parity-shards M`, `--mapping 2bit|rotation3|codebook8`, `--payload-bytes`, `--inner-parity`,
`--gc-min`, `--gc-max`, `--max-homopolymer`, `--gc-window`, `--max-tandem-repeat`, `--forbid-motif` (repeatable),
`--max-strand-nt`, `--no-name`, `--timestamp`.

**Channel parameters** (`sequence`, `simulate`; a subset on `pipeline` and `experiment run`): `--seed`, `--coverage`,
`--coverage-model fixed|poisson|lognormal`, `--abundance-sigma`, `--dropout-rate`,
`--synthesis-{substitution,insertion,deletion}-rate`, `--{substitution,insertion,deletion}-rate`,
`--duplication-rate`, `--truncation-rate`, `--n-rate`, `--invalid-read-rate`, `--contamination-rate`,
`--reverse-complement-rate`, `--shuffle/--no-shuffle`, `--quality-model informative|flat`,
`--quality-informativeness`, `--burst-rate`, `--burst-length`, `--burst-kind substitution|deletion|insertion|mixed` (V3),
`--format fastq|fasta|vxs`. Output names ending in a compression or alignment suffix (`.gz`, `.bam`, …) are refused
unless `--format` is given, because the output is never compressed (V3).

**Decode options** (`decode`, `recover`, `restore`, `verify`): `--quality-erasure-below Q`,
`--experimental-indel-repair`, `--max-indel 1..3`, `--burst-repair N` (V3: single-read resynchronisation of one
contiguous run of up to N lost or extra bases), `--archive-tag HEX8` (V3: choose one archive from a pool of several),
`--workers`, `--temp-dir`.

## Exit codes (stable)

| code | category | examples |
|---|---|---|
| 0 | SUCCESS | done. Also when repair was needed; the report then says `"status": "RECOVERED"` |
| 1 | VERIFICATION_FAILED | recomputed SHA-256 mismatch; `verify` found a failed check |
| 2 | USAGE_ERROR | unknown command or option, missing argument, value out of range |
| 3 | INVALID_INPUT | missing file, truncated/unfinished container, malformed manifest or index, invalid DNA, `--resume` refused |
| 4 | AUTHENTICATION_FAILED | key missing, wrong key, HMAC or GCM failure; `verify` of an encrypted archive without a key when every check possible without the key passed (V3; V2 exited 1) |
| 5 | INSUFFICIENT_REDUNDANCY | an ECC group lost more than M strands (metadata: more than 8 of 16) |
| 6 | UNSUPPORTED_FORMAT | unknown container/format version or required feature; a legacy archive given to a main command |
| 7 | CONFIGURATION_ERROR | invalid parameters, unsatisfiable constraints |
| 8 | OUTPUT_ERROR | output or report exists (no `--force`), not writable, disk full, file too large, read-only file system (V3: these OS errors were exit 70) |
| 70 | INTERNAL_ERROR | a bug; set `VNXDNA_DEBUG=1` for a traceback |
| 130 | interrupted | Ctrl-C, SIGTERM or SIGHUP (`kill`, `timeout`, `docker stop`, a closed terminal): worker processes are stopped, partial outputs and temporary directories are removed (`store` leaves a resumable checkpoint). Worker processes also exit by themselves if the command is killed with SIGKILL (V3 release review) |
| 141 | output closed | standard output was closed early (e.g. `vnx-dna … | head -1`); V3 exits quietly instead of reporting a BrokenPipe as an internal error |

Errors are one line on stderr: `vnx-dna: error [CATEGORY]: message`. Normal operation never prints a traceback.
