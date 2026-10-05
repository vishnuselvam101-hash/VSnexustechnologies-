# Conformance and reproducibility

In this project, conformance means that an implementation reproduces a set of frozen, byte-level golden vectors derived from
the specification ([VNX-DNA-SPEC-V6 §6](spec/VNX-DNA-SPEC-V6.md)). Each vector names an operation over byte strings, its
parameters and inputs, and either the exact expected outputs or the exact expected error (code, category, exit code,
retryable flag). An independent implementation needs only the vector files and the specification.

All vectors are SYNTHETIC SOFTWARE TEST data. No DNA was synthesised, stored or sequenced, and passing the vectors says
nothing about wet-lab behaviour. It shows that this software agrees with the written format on these inputs. It does not
prove the specification is complete, and it does not cover frame 6, superblock 3 or any other format the specification
marks as not implemented.

## What the 222 vectors cover

The full set is `tests/conformance/index.json` (schema `vnx.conformance-index/1`). The counts below were computed from that
file: 129 positive and 93 negative vectors, 222 in total. By directory: 105 under `stage/`, 24 under `e2e/` (all positive)
and 93 under `negative/`. Stage IDs are the pipeline stages of spec §5 (E = encode, D = decode); a range such as
`E10-E13` marks a vector that exercises several stages together.

| Stage ID | Stage name (spec §5) | Positive | Negative | Total |
|---|---|---:|---:|---:|
| version | version report | 1 | 0 | 1 |
| E5 | seal | 3 | 0 | 3 |
| E5-E6 | seal, container | 1 | 0 | 1 |
| E6 | container | 8 | 8 | 16 |
| E7-E14 | plan to write (whole encode) | 12 | 0 | 12 |
| E8 | outer | 10 | 0 | 10 |
| E9 | superblock | 4 | 0 | 4 |
| E10 | frame | 3 | 0 | 3 |
| E10-E13 | frame build (E10 to E13) | 8 | 0 | 8 |
| E11 | scramble, inner RS | 10 | 0 | 10 |
| E12 | map, markers | 11 | 0 | 11 |
| E13 | screen | 1 | 1 | 2 |
| E14 | order, write | 2 | 0 | 2 |
| D0 | ingest | 0 | 1 | 1 |
| D0-D14 | whole decode | 12 | 0 | 12 |
| D1 | probe, layout | 1 | 6 | 7 |
| D5 | inner | 26 | 9 | 35 |
| D8 | superblock | 2 | 20 | 22 |
| D10 | outer | 6 | 3 | 9 |
| D11 | stripe | 2 | 2 | 4 |
| D12 | verify (container rules) | 1 | 36 | 37 |
| D14 | extract | 5 | 7 | 12 |
| all | | 129 | 93 | 222 |

Operation families, with the count per family (positive/negative) taken from the vector IDs:

| Family | What the vectors check |
|---|---|
| `crc32` (3) | CRC-32 of fixed rows, and the standard check value |
| `scrambler` (6) | keystream bytes for variants 0, 1 and 255; byte 0 of the keystream for all 256 variants in each of the three scrambler domains (spec §3.2) |
| `mapping`, `markers` (5, 6) | bytes to nucleotides for the four frame-4 profiles; marker tables for ℓ = 1 to 6 |
| `rs` (21) | inner Reed-Solomon encode (4 vectors), errata decode (16) and a golden vector (1); parity is checked against an independent implementation in the generator |
| `frame4` (9 build, 9 parse, 10 negative) | frame-4 build per profile for kind 0 and 1 and a variant greater than 0; parse of the built strands; negatives for the reserved version nibbles, kind 2, a constraint failure, and errors beyond the RS bound (these must be reported, never silently miscorrected) |
| `superblock` (6 positive, 19 negative) | pack and unpack of versions 1 and 2; negatives for versions 0, 3 and 255, version 4, bad CRC, truncation, invalid version-2 fields (depth 0, depth plus parity above 256, order 2 and 255, unsupported LT code) and CRC-valid forged fields |
| `outer` (18 positive, 5 negative) | Cauchy row encode and decode for (K, M) in {(64,16), (32,32), (48,16)}, a short last group, column parity for (D, Mc) in {(4,2), (8,2)}, iterative stripe decode; negatives for erasure patterns beyond the bound |
| `strand.order` (2) | sequential and interleaved record order for one small geometry |
| `probe` (1 positive, 3 negative) | layout detection on a frame-4 sample; refusal for a version-7 nibble, a V3 frame-5 pool and random reads |
| `container` (5 positive, 36 negative), `manifest` (1, 8) | container build with fixed archive ID and salt, container read (clear, zstd, encrypted); one or more negatives for each of container validation rules 1 to 9; canonical-manifest acceptance and eight rejections (duplicate key, float, NaN, non-ASCII, not an object, pretty-printed, trailing newline, unsorted keys) |
| `merkle.proofs` (6) | Merkle tree roots and proofs (checked against a recursive RFC 6962 tree in the generator) |
| `aead` (6 positive, 5 negative) | chunk seal and open in domains 0, 1 and 2; negatives for tampering, truncation, wrong archive ID, wrong index and wrong count |
| `version.report` (1) | the `vnx.version/1` answer (needs the SDK service, see below) |
| `e2e.encode`, `e2e.decode` (12, 12 positive and 7 negative) | encode: fixture files to container and strand SHA-256; decode: reads to container SHA-256 and extracted file hashes, over the `v4_0`, `v5_0` and `v6_0` fixture sets (four cases each) in `tests/fixtures/`; negatives for decoder refusals and failures (the full list is in `tests/conformance/negative/e2e.*`) |

Each negative vector fixes the exact error. The 93 negatives use these error codes (code, category, exit code, retryable):

| Code | Category | Exit code | Retryable | Vectors |
|---|---|---:|---|---:|
| ARCHIVE_TAG_AMBIGUOUS | INVALID_INPUT | 3 | false | 1 |
| CONSTRAINT_ERROR | CONFIGURATION_ERROR | 7 | false | 1 |
| CONTAINER_VERSION_UNSUPPORTED | UNSUPPORTED_FORMAT | 6 | false | 4 |
| FEATURE_UNSUPPORTED | UNSUPPORTED_FORMAT | 6 | false | 1 |
| FORMAT_ERROR | INVALID_INPUT | 3 | false | 43 |
| FRAME_VERSION_UNSUPPORTED | UNSUPPORTED_FORMAT | 6 | false | 5 |
| INSUFFICIENT_REDUNDANCY | INSUFFICIENT_REDUNDANCY | 5 | true | 10 |
| INTEGRITY_ERROR | VERIFICATION_FAILED | 1 | false | 15 |
| KEY_FOR_UNENCRYPTED | AUTHENTICATION_FAILED | 4 | false | 1 |
| LAYOUT_UNDETECTED | INVALID_INPUT | 3 | false | 2 |
| LEGACY_FORMAT | UNSUPPORTED_FORMAT | 6 | false | 3 |
| RESOURCE_LIMIT | INVALID_INPUT | 3 | false | 1 |
| SUPERBLOCK_VERSION_UNSUPPORTED | UNSUPPORTED_FORMAT | 6 | false | 4 |
| WRONG_KEY | AUTHENTICATION_FAILED | 4 | false | 2 |

The error table itself is spec §10 (see [VNX-DNA-SPEC-V6](spec/VNX-DNA-SPEC-V6.md)).

**Packaged subset.** The installed package carries 22 of the 222 vectors in `src/vnxdna/conformance/vectors/` (counted from
that directory's `index.json`): CRC-32, scrambler, mapping, frame-4 build for the four profiles, superblock unpack and
version negatives, one forged-superblock negative, and layout probes. `default_vectors()` in `vnxdna.conformance` returns `tests/conformance` when the
package sits inside a source checkout that has `tests/conformance/index.json`, and the packaged subset otherwise. A run
against the subset is a smaller test than a run against the full set; the `vectors_dir` and `index_sha256` fields of the
answer record which one was used.

## Running the vectors

```bash
vnx conformance                               # full set in a source checkout, packaged subset otherwise
vnx conformance --vectors tests/conformance   # an explicit vector directory
vnx conformance --select crc32.rows.001       # only these vector IDs (repeat --select for several)
vnx conformance --backend reference           # force every kernel to its NumPy/Python reference
vnx conformance --backend native              # require every kernel to run natively
pytest tests/conformance                      # the vector set plus the runner's own strictness tests
```

`--backend` takes `auto` (default), `native` or `reference`; any other value is a configuration error (exit code 7).

- `auto` leaves the environment alone; each kernel uses its native library if it loads and its reference otherwise.
- `reference` and `native` set `VNXDNA_RS_BACKEND`, `VNXDNA_READS_BACKEND` and `VNXDNA_ALIGN_BACKEND` to that value for the
  duration of the run and restore the previous values afterwards (see [NATIVE_KERNELS.md](NATIVE_KERNELS.md)).
- A forced backend that a kernel does not provide (for example `native` when the library is not built) adds a result
  `backend.native` (or `backend.reference`) with status SKIP that names the missing kernels. A skipped result is not a
  pass, so the verdict is NONCONFORMANT.

Build the native kernels before using `--backend native`:

```bash
python -m vnxdna.native build
python -m vnxdna.native --require-native      # exits 1 unless every kernel runs natively
```

Spec §6.3 requires both backends to pass. `tests/conformance/test_vectors.py` runs the set once per backend (building the
kernels first if the tree has none) and also checks that both backends give identical observations.

**Exit codes.** 0 if the verdict is CONFORMANT; 1 if it is NONCONFORMANT (a FAIL or SKIP, or no vector selected); other
codes follow the error scheme of [CLI.md](CLI.md), for example 7 for an invalid `--backend` and 6 (`SCHEMA_UNSUPPORTED`) for an index that is not
a `vnx.conformance-index/1` document.

**Output.** The command prints the `vnx.result/1` envelope; its `result` field (also mirrored at top level) is the
`vnx.conformance/1` document with these fields:

| Field | Content |
|---|---|
| `schema`, `software`, `spec` | `vnx.conformance/1`, the software version, the specification version |
| `vectors_dir`, `index_sha256` | the vector directory used and the SHA-256 of its `index.json` |
| `backend`, `backends` | the requested backend, and per kernel the RS SIMD level or backend actually in use |
| `summary` | `total`, `passed`, `failed`, `skipped`, `positive`, `negative` |
| `by_stage` | `passed` and `failed` counts per stage ID (a SKIP counts as failed here) |
| `results` | per vector: `id`, `stage`, `kind`, `status` (PASS, FAIL or SKIP), `expected`, `observed`, `seconds` |
| `verdict` | `CONFORMANT` only if at least one vector ran and every result is PASS; otherwise `NONCONFORMANT` |

A vector is FAIL if its observed outputs or error differ from the expected ones in any field, if a recorded input does not
match its SHA-256, if `vector.json` disagrees with its index entry, if the vector schema is unknown, or if an ID is
duplicated in the index. It is SKIP if its operation is not implemented by the runner. A negative vector that unexpectedly
succeeds is a FAIL. The `version.report` vector needs the SDK's version service, which `vnx conformance` and the test
suite supply; `run()` called directly without `services` cannot answer it.

## Adding a vector

**Layout.** A vector is a directory containing `vector.json` and its small input files. It lives under `tests/conformance/`
in `stage/<vector-id>/` (operation-level), `e2e/<vector-id>/` (whole pipeline; inputs are references to
`tests/fixtures/` files by relative path and SHA-256, not copies) or `negative/<vector-id>/` (malformed or unsupported
input with the expected error). `vector.json` has schema `vnx.conformance-vector/1` and these fields:

| Field | Content |
|---|---|
| `schema`, `id`, `stage`, `kind` | schema string, unique vector ID, spec §5 stage ID, `positive` or `negative` |
| `formats` | `{"frame": n|null, "superblock": n|null, "container": [major, minor]|null}` |
| `operation` | a key of `OPERATIONS` in `src/vnxdna/conformance/operations.py` |
| `params` | operation parameters (hex strings, integers, profile names) |
| `inputs` | name to `{"path": ..., "sha256": ...}`; the SHA-256 is checked before the input is used |
| `expected` | `{"outputs": {...}}`, or `{"error": {"code", "category", "exit_code", "retryable"}}` for a negative |
| `since_spec`, `evidence` | `"6.0"` and `"SYNTHETIC SOFTWARE TEST"` |

The index entry in `index.json` repeats `id`, `stage`, `kind` and `formats` and adds `path` (for example
`stage/crc32.rows.001`). The runner fails a vector whose own `id`, `stage` or `kind` differs from its entry.

**Steps.**

1. If the vector needs a new operation, add a function `op_<name>(vec, vdir)` to `operations.py` that takes the parsed
   vector and its directory and returns a JSON-able dict of outputs (raise a `VNXError` for an anticipated failure), and
   register it in the `OPERATIONS` dict. An operation without an entry is a SKIP, which makes the run NONCONFORMANT.
2. Add the vector to the matching function in `tests/conformance/generate_vectors.py` using its `vector(...)` helper (stage,
   operation, params, inputs, `kind`, `formats`, and `error=` for a negative). For a positive vector, pass `expect=` with a
   value computed independently of the product wherever one exists; the helper asserts the product agrees before it freezes
   the value.
3. Regenerate. Without `VNX_CONFORMANCE_OUT` the generator rewrites `tests/conformance/stage`, `e2e`, `negative` and
   `index.json` in place: `PYTHONPATH=src python tests/conformance/generate_vectors.py`. To review first, write to a scratch
   tree whose `tests/conformance` is the output and whose `tests/fixtures` and `tests/v6` are symlinks to the real ones (the
   e2e vectors reference the fixtures by relative path; the determinism test builds exactly this layout), then compare it
   with the committed files.
4. Update the counts in this document and in `tests/conformance/test_vectors.py` (`REQUIRED`, the size bounds) if the
   set changes.

**Rules.**

- Expected values are frozen. The vectors are committed and are never regenerated to make a failing implementation pass. If
  a vector fails, the implementation or the specification is examined first; a committed vector is changed only with a
  stated reason.
- The generator checks against independent computations where they exist: `zlib` CRC-32, `hashlib` SHAKE-128, `reedsolo`,
  a small GF(256) written in the generator for the Cauchy rows, the `cryptography` primitives for the AEAD, and a recursive
  RFC 6962 tree. Where none exists the value is the product's own output, frozen; such a vector detects change, not
  correctness. This applies to the packaged subset, whose expected values were computed by the implementation
  (`generate_packaged_vectors.py`) and frozen; the full-set generator re-checks some of them independently, for example the keystreams.
- The packaged subset is copied byte for byte into the full set by `generate_vectors.py`. Do not edit one copy: the test
  `test_packaged_subset_is_a_byte_identical_part_of_the_full_set` fails if they differ. Changing the subset means running
  `tests/conformance/generate_packaged_vectors.py` and then `generate_vectors.py`, and is an exception, not routine.
- Negative vectors name a code from the spec §10 error table with its category and exit code; `INSUFFICIENT_REDUNDANCY` is
  the only retryable code.

**Tests that must pass.** `pytest tests/conformance` covers, among others: the index matches the directories without
orphans or duplicates; every negative uses a code of the error table; every row of spec §6.2 has its vectors; every
recorded input hash matches its file; the generator reproduces the committed vectors byte for byte; frozen values agree
with the independent computations; both backends are CONFORMANT with identical observations; and the runner rejects a
wrong expectation, a swapped input, a disagreeing index, a duplicate ID, an unknown schema, an empty set and a skipped
vector.

## Experiment manifests and `vnx experiment reproduce`

An experiment manifest (`vnx.experiment/1`, V6 directive §24) records what one SIMULATED experiment depends on and the
SHA-256 of its result, so that anyone with the same software can re-run it and check the result. Code:
[`src/vnxdna/benchmark/manifest.py`](../src/vnxdna/benchmark/manifest.py). JSON Schema:
`src/vnxdna/core/schemas/experiment.schema.json` (`vnxdna.core.schema.load("vnx.experiment/1")`). Tests:
`tests/reproducibility/test_experiment_manifest.py`.

### Fields

| field | type | meaning |
|---|---|---|
| `schema` | `"vnx.experiment/1"` | another `vnx.experiment/N` is `SCHEMA_UNSUPPORTED` (exit 6) |
| `experiment_id` | string, `[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}` | a name for the experiment |
| `kind` | `channel-simulation` or `experiment` | what to re-run (below) |
| `source_class` | `SIMULATED` | the data source (`SIMULATED`, `SYNTHETIC`, `LABORATORY`, `PHYSICAL_VALIDATION`). Software re-runs simulations only, so both kinds must say `SIMULATED` |
| `input_hash` | 64 lowercase hex | SHA-256 of the input (per kind, below) |
| `codec_version` | `{software, spec, backends?}` | package version (`vnxdna._version`), spec version (`SPEC_VERSION`) and the native/reference backend of each kernel |
| `commit` | 40 hex (64 for SHA-256 repositories), optional `-dirty`, or `null` | git commit of the code (`vnxdna.core.util.git_commit`); `-dirty` when `src/` or `pyproject.toml` differ from it; `null` outside a git checkout |
| `simulator_version` | `{simulator, version, model_schema, model}` | `vnxdna.simulation.engine` and its `SIMULATOR_VERSION`, the channel-model schema `vnx.channel-model/1`, and the model's `{name, version, sha256}` (`null` for `experiment`, whose configurations use the V4 channel parameters) |
| `seed` | integer 0 … 2^63−1 | the simulation seed (`experiment`: the configuration's `channel.seed`, else its `seed`, else 0) |
| `parameters` | object | everything else the run needs (per kind, below) |
| `hardware` | object with `cpu_model`, `logical_cpus` (+ `ram_bytes`, `os`, `platform`, `python`, `packages`) | the machine part of `vnxdna.core.util.environment()`; informational |
| `workers` | integer 1 … 1024 | worker processes used. Results do not depend on it |
| `result_hash` | 64 lowercase hex | SHA-256 of the result (per kind, below) |
| `result_hash_of`, `created_utc`, `statement` | string, optional | what was hashed, when the manifest was written (UTC), the SIMULATED statement |

The two kinds:

| kind | `parameters` | `input_hash` | `result_hash` | runs |
|---|---|---|---|---|
| `channel-simulation` | `model`: the complete `vnx.channel-model/1` document (overrides already applied); `input.file`: the strand file, relative to the manifest's directory unless absolute; `format`: `fasta` or `fastq` | SHA-256 of the strand file | SHA-256 of the read file (`output.sha256` of `vnx.simulation-metadata/1`) | `vnxdna.simulation.engine.simulate_file` |
| `experiment` | `config`: a `vnx experiment run` configuration (`type` one of `vnxdna.benchmark.experiment.TYPES`) | SHA-256 of the canonical JSON of `config` (its inputs are generated from it) | SHA-256 of the canonical JSON of the deterministic results (`vnxdna.benchmark.experiment.deterministic`: the fields `vnx experiment reproduce DIR` compares; timings and memory removed). The generated input's own SHA-256 is one of those fields | `vnxdna.benchmark.experiment.execute` (encode → SIMULATED channel → decode) |

Canonical JSON is `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded as UTF-8.
The model is embedded rather than named, so a manifest does not depend on the models a later release ships.

### Validation

`manifest.validate`, `loads` and `read` are strict and raise typed errors, never assertions. A malformed or
inconsistent manifest is a `ManifestError` (`FORMAT_ERROR`, exit 3, `details.field` names the field):

- the file is larger than 16 MiB, or isn't UTF-8 JSON. Duplicate keys, `NaN` and `Infinity` are rejected, and so is nesting deeper than 32 levels;
- a required field is missing or an unknown field is present (top level, `codec_version`, `simulator_version`, `parameters`);
- a type or range is wrong. A boolean is not an integer; hashes are lowercase hex; NUL is refused in strings;
- `source_class` is not `SIMULATED`;
- `channel-simulation`: `parameters.model` is not a valid `vnx.channel-model/1` document, or its name, version or
  canonical SHA-256 differs from `simulator_version.model`;
- `experiment`: `input_hash`, `seed` or `workers` disagree with `parameters.config`.

`manifest.write` validates first and writes atomically, so an invalid manifest is never written.

### Writing a manifest

```bash
vnx channel simulate strands.fasta reads.fastq --model illumina-like --seed 7 --manifest run.manifest.json \
    [--experiment-id my-run]          # SIMULATED; --force also overwrites an existing manifest
vnx experiment run experiments/EXP-XXXX/config.json   # also writes experiments/EXP-XXXX/manifest.json
```

In Python: `vnxdna.sdk.simulate(..., manifest=PATH)`, `manifest.simulation_manifest(metadata, model, strands)` and
`manifest.experiment_manifest(config, results)`. `vnx experiment run` still writes `results.json` and the other files
as before. If the configuration can't be expressed as a manifest (for example a non-integer seed), the run still
succeeds; its return value says `manifest.written: false` and gives the reason.

### Reproducing

```bash
vnx experiment reproduce run.manifest.json [--input strands.fasta] [--workers N]
vnx experiment reproduce experiments/EXP-XXXX            # a directory: the V4 comparison of every deterministic field
```

When given a manifest file, `reproduce` validates it and then checks the input:

- `channel-simulation`: the strand file is hashed. `--input` replaces the recorded path.
- `experiment`: the configuration is hashed.

If `input_hash` differs, the experiment isn't re-run and `checks.result_hash` is `NOT_RUN`. Otherwise `reproduce` re-runs the experiment in a temporary directory
with the recorded seed (and `--workers` if given) and compares `result_hash`. It prints `vnx.result/1` (kind
`experiment`) with a `vnx.experiment-reproduction/1` body:

- `reproduced`: true only if `checks.input_hash` and `checks.result_hash` are both `MATCH`;
- `recorded` and `observed` hashes, the seed, the workers;
- `context`: `SAME`, `DIFFERENT` or `UNKNOWN` for `codec_version.software`, `.spec`, `.backends`, `commit` and
  `simulator_version`. These differences are reported, not judged: the result hash is the verdict. A software
  version that changes encoding or simulation can change results legitimately, and the commit identifies the code.

Exit codes (the scheme of [CLI.md](CLI.md)):

| exit | when |
|---|---|
| 0 | reproduced (`status` SUCCESS) |
| 1 | not reproduced: the input hash or the result hash differs (`status` FAILURE) |
| 3 | malformed or inconsistent manifest, unreadable manifest, input file not found (`FORMAT_ERROR`) |
| 6 | unsupported manifest schema (`SCHEMA_UNSUPPORTED`) |
| 7 | `--input`/`--workers` given with a directory, or `--workers` out of range (`CONFIGURATION_ERROR`) |
| 70 | internal error |

### Committed example

`tests/fixtures/experiment/exp-sim-1.clean.s9100.manifest.json` is one cell of EXP-SIM-1
(`experiments/v6/phase3/EXP-SIM-1`): model `clean@1.0.0`, strand file `tests/fixtures/v6_0/max-recovery.strands.fasta`,
seed 9100, written by `manifest.simulation_manifest` from a working tree based on e27bf2e (hence `-dirty`). Its `result_hash`
(`cc92a2cef18af20dcbf581d6d545e54dcdf8773a5fc76939122afd3f714a5d9b`) is the read-file SHA-256 recorded for that cell in
`experiments/v6/phase3/EXP-SIM-1/results.json`. In a source checkout:

```bash
vnx experiment reproduce tests/fixtures/experiment/exp-sim-1.clean.s9100.manifest.json   # exit 0, reproduced: true
```

The test suite checks this, and checks that a changed result hash, seed or input file does not reproduce. SIMULATED:
no DNA was synthesised, stored, amplified or sequenced.
