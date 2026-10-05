# VNX-DNA V6 architecture (target) and migration plan

- Status: **DESIGN (V6 directive Phase 1), 2026-10-05.** Nothing in this document is implemented unless it cites file:line
  at 081697b. The normative formats live in [`docs/spec/VNX-DNA-SPEC-V6.md`](spec/VNX-DNA-SPEC-V6.md) (cited as "spec §n").
- Inputs:
  - the V6 Phase 0 static audit (lab `results/v6-audit/INVENTORY.md`, cited as "audit §n");
  - the baseline note of 2026-10-05;
  - the research-gate roadmap and product strategy (branch `work/research-gate`).
- Every channel result referred to here is **SIMULATED**. No DNA has been synthesised, stored or sequenced by VNX-DNA.

## 1. Problem statement (from the audit)

1. **The "V4" package is not a frozen baseline.** V5 and V6 behaviour is patched into it in place, and the cycles v4↔v5 and
   v4↔v6 are broken only by function-level imports (audit §2.2). For example, `v4/decoder.py:40` imports
   `v6.native_reads` at module level, while `v6.native_reads` imports `v4.reads`.
2. **The decoder is one 1,524-line module.** `v4/decoder.py` mixes parsing, sync, inner code, consensus, outer code, V6
   stripes, the planner, events and random access (audit §2.7).
3. **The CLI does pipeline work** (audit §2.7):
   - it archives before encoding (`v4/cli.py:292-300`);
   - it verifies after encoding (`cli.py:303-309`);
   - it extracts after decoding (`cli.py:397-422`);
   - it resolves redundancy profiles (`cli.py:276-286`), duplicating `v6/profiles.dna_options` (`v6/profiles.py:29`).
4. **There is no V4+ Python API.** The package docstring still names the V1 `vnxdna.api` (`src/vnxdna/__init__.py:1-4`).
5. **About 12k lines of V1–V3 code serve only `vnx-dna`.** Their module names (`vnxdna.api`, `vnxdna.channel`,
   `vnxdna.cli`, `vnxdna.sync`, `vnxdna.container`) occupy the names a layered design would use (audit §1.2, §5.2).
6. **Versions, backends and schema versions are missing from outputs** (audit §4, §7.2). The package says 5.0.0
   (`_version.py:2`).
7. **Five channel simulators, four inner-RS implementations and three CLIs exist** (audit §5.3).

## 2. Target package structure

Every package lives under `src/vnxdna/`. A **layer** number constrains imports (§3).

| Layer | Package | Responsibility | Moves in from (081697b) |
|---|---|---|---|
| 0 | `vnxdna.core` | errors and stable codes, version registry (spec §4.1), canonical JSON, hashing, atomic output, CRC-32, resource helpers, schema loader, observability primitives (`Events`, report builder) | `errors.py`, `v4/errors.py`, `v6/errors.py`, `v4/version.py`, `v4/util.py`, `v2/crc.py`, `provenance.py`, `v6/observe.py`, canonical JSON from `v4/container.py` |
| 1 | `vnxdna.native` | the three C kernels, one loader, a backend registry with status and ABI, source-hash checks | `v5/native/align.c` + `v5/native_alignment.py`, `v6/native/reads.c` + `v6/native_reads.py` (binding part), `v6/native/rs.c` + `v6/native_rs.py` |
| 2 | `vnxdna.archive` | VNX4 container read/write, chunking, dedup, compression (bounded zstd), AEAD and KDF, Merkle, extract, list, locate, verify | `v4/archive.py`, `v4/container.py`, `v4/crypto.py`, `v4/merkle.py`, `container/compression.py` (bounded reader only) |
| 2 | `vnxdna.codec` | GF(256); inner RS (one API, backends: native, NumPy, reference); outer Cauchy RS (rows, product/stripes, plan, bounds); LT (EXPERIMENTAL); superblock pack/unpack (v1, v2, later v3); geometry; redundancy profiles | `ecc/gf256.py`, `ecc/cauchy.py`, `ecc/rs_batch.py`, the parity matrix of `ecc/inner_rs.py`, `v4/codecs.py`, `v4/rs_fast.py`, `v6/outer.py`, `v6/profiles.py`, `Superblock` from `v4/encoder.py:110-202` |
| 3 | `vnxdna.dnaenc` | strand profiles and `Layout`, frame 4 (later frame 6), scrambler, 2-bit mapping, markers, primers (V7), constraint screening, strand order, strand and read file I/O | `v4/frame.py`, `v4/constraints.py`, `v2/strandio.py`, `v4/reads.py` (reference parser), `_serialize`/`_labels` (`v4/encoder.py:226-242`), `v6/encoder._records` |
| 3 | `vnxdna.sync` | marker-template alignment, native aligner use, smart indel recovery | `v4/sync.py`, `v5/indel/*` (and keeps the legacy module `vnxdna.sync.indel` as an alias, §7.2) |
| 4 | `vnxdna.recovery` | decode machinery: probe and layout (spec §3.10), pass 1, spill, consensus, soft decoding, recovery schedule, planner and budgets, superblock selection, outer rows, stripes, publish, partial and selective output | `v4/decoder.py` (split), `v5/soft/*`, `v6/decode.py`, `v6/recovery.py` |
| 5 | `vnxdna.pipeline` | the canonical stage graphs E0–E15 and D0–D14 (spec §5) as two orchestrators, `encode` and `decode`; stage timing; events | `v4/encoder.encode_container` (`v4/encoder.py:287`), `v6/encoder.encode_container_v6` (`v6/encoder.py:105`), `decoder.decode_reads/_decode_reads` (`v4/decoder.py:583-720`) |
| 5 | `vnxdna.simulation` | channel models (`vnx.channel-model/1`), stage models (synthesis, storage, amplification, sequencing), loss and bursts, read simulation | `v4/channel.py`, `v6/loss.py`, `experiments/v6/channel/channel.py` + `models/*.json` |
| 5 | `vnxdna.physical` | physical-record schemas and validator; export/import package schemas, writer and reader (spec §9.3) | `experiments/v6/physical/schema/*`, `experiments/v6/physical/validate.py` |
| 6 | `vnxdna.providers` | `DNAWriter`/`DNAReader`/`DNAProvider` protocols, registry, `ReferenceSimulatorProvider` (spec §9) | new |
| 6 | `vnxdna.benchmark` | benchmark and experiment harness, data generator, sweeps, experiment manifest (`vnx.experiment/1`) | `v4/bench.py`, `v4/experiment.py`, `v4/sweep.py`, `v4/datagen.py` |
| 7 | `vnxdna.sdk` | **the stable public API** (§4) | new façade over `pipeline`, `archive`, `simulation`, `providers`, `benchmark` |
| 7 | `vnxdna.conformance` | vector runner (spec §6); a small vector subset as package data | new |
| 8 | `vnxdna.commands` | the `vnx` CLI: argument parsing, one SDK call, JSON output. No pipeline logic | `v4/cli.py`, `v4/config.py` (config parsing moves to `sdk.config`) |
| — | `vnxdna.legacy` | frozen V0.1–V3 code and the `vnx-dna` CLI (§6) | `api.py`, `cli.py`, `cli_v1.py`, `bench.py`, `channel.py`, `container/*`, `dna/*`, `storage/*`, `sync/indel.py`, `legacy/v0_1.py`, `v2/*`, `v3/*` |

**Naming decisions.**

- The stable API is `vnxdna.sdk`, not `vnxdna.api`. `vnxdna.api` is the V1 API with different semantics for `encode`,
  `decode`, `verify` and `extract` (`api.py:40`), and it stays importable (§7). The name also matches the product
  component "VNX-DNA SDK" (strategy §5.2).
- The channel layer is `vnxdna.simulation`, because `vnxdna.channel` is the V1 module (`channel.py:41,87`).
- The CLI package is `vnxdna.commands`, because `vnxdna.cli` is the V3 CLI.
- `vnxdna.sync` is reused: today it is a package with an empty `__init__` and one submodule `indel` (audit §2.1), so new
  submodules can sit beside the legacy alias.

## 3. Dependency rules

```
 L8 commands ─► L7 sdk, conformance ─► L6 providers, benchmark ─► L5 pipeline, simulation, physical
 L5 pipeline ─► L4 recovery ─► L3 dnaenc, sync ─► L2 archive, codec ─► L1 native ─► L0 core
```

1. **R1 (downward only).** A module may import only from its own package or from lower layers. Function-level imports count
   (the audit's cycles hide there).
2. **R2 (codec purity).** `core`, `native`, `archive`, `codec`, `dnaenc`, `sync`, `recovery` and `pipeline` MUST NOT import
   `simulation`, `providers`, `physical`, `benchmark`, `sdk`, `commands` or `legacy`. This removes today's
   `v4/config.py:28` import of `ChannelConfig` from the decode configuration path (audit §2.7).
3. **R3 (no cycles).** The package-level import graph is acyclic.
4. **R4 (legacy isolation).** No new-layer module imports `vnxdna.legacy`. `vnxdna.legacy` may import `core`, `codec` and
   `dnaenc` only through the old module paths, which are aliases (§7.2), so frozen legacy code needs no edits.
5. **R5 (native boundary).** Only `vnxdna.native` calls `ctypes`. Other layers ask the backend registry for a callable.
   Every backend choice is reported (§5).
6. **R6 (CLI thinness).** `vnxdna.commands` imports only `vnxdna.sdk` and `vnxdna.core`.

**Enforcement.** A new test, `tests/architecture/test_layers.py`, walks `src/vnxdna` with `ast`, as the audit did (audit
§2.2). It collects module-level and function-level imports and asserts R1–R6. It ships in Phase 2 with an explicit
allow-list for the shim modules of §7, and the allow-list may only shrink.

## 4. Stable public API (`vnxdna.sdk`)

### 4.1 Functions

All functions are keyword-only after the first positional arguments. They return frozen dataclasses with `to_json()`.
Anticipated failures raise `vnxdna.core.errors.VNXError` subclasses that carry a stable `code` (spec §10).

```python
def archive(inputs: Sequence[PathLike], output: PathLike, *, options: ArchiveOptions | None = None,
            key: bytes | None = None, passphrase: str | None = None, overwrite: bool = False,
            observer: Observer | None = None) -> ArchiveResult

def encode(source: PathLike | Sequence[PathLike], output: PathLike, *, dna: DNAOptions | None = None,
           redundancy_profile: str | None = None, archive_options: ArchiveOptions | None = None,
           key: bytes | None = None, passphrase: str | None = None, verify: bool = False,
           fmt: Literal["fasta", "fastq"] | None = None, overwrite: bool = False,
           observer: Observer | None = None) -> EncodeResult
    # source = container -> E7..E14; otherwise E0..E14 with archive_options (never silently ignored, spec §2.3.3)

def decode(reads: PathLike | Sequence[PathLike], output: PathLike | None, *, options: DecodeOptions | None = None,
           select: Sequence[str] | None = None, extract_to: PathLike | None = None, partial_dir: PathLike | None = None,
           key: bytes | None = None, passphrase: str | None = None, allow_unencrypted: bool = False,
           budget: RecoveryBudget | None = None, overwrite: bool = False, observer: Observer | None = None,
           task_id: str | None = None) -> DecodeResult

def inspect(path: PathLike, *, deep: bool = False, key: bytes | None = None,
            passphrase: str | None = None) -> InspectResult        # container or reads; "can I read this?" (spec §4.3)
def verify(container: PathLike, *, key: bytes | None = None, passphrase: str | None = None,
           chunk: int | None = None, full: bool = True) -> VerifyResult
def extract(container: PathLike, output_dir: PathLike, *, files: Sequence[str] | None = None,
            key: bytes | None = None, passphrase: str | None = None, overwrite: bool = False) -> ExtractResult
def list_entries(container: PathLike, *, key: bytes | None = None, passphrase: str | None = None) -> ListResult
def simulate(strands: PathLike, output: PathLike, *, model: str | ChannelModel, seed: int,
             coverage: float | None = None, provider: str = "reference-simulator") -> SimulateResult
def benchmark(spec: BenchmarkSpec | PathLike) -> BenchmarkResult
def conformance(vectors: PathLike | None = None, *, select: Sequence[str] | None = None,
                backend: Literal["auto", "native", "reference"] = "auto") -> ConformanceResult
def version() -> VersionResult                                      # spec §4.2
```

- `list_entries` avoids shadowing the Python built-in `list`. The CLI verb is still `vnx list`.
- `DNAOptions`, `DecodeOptions`, `ArchiveOptions` and `RecoveryBudget` keep their current fields: `v4/encoder.py:49-71`,
  `v4/decoder.py:55-122`, `v4/archive.py:35`, `v6/recovery.py:49`. The SDK re-exports them, so existing call sites keep
  working.

### 4.2 Result envelope `vnx.result/1`

Every result's `to_json()` and every CLI stdout JSON has this shape:

```json
{"schema": "vnx.result/1", "kind": "decode", "status": "SUCCESS",
 "software": {"name": "vnxdna", "version": "6.0.0"}, "spec": "6.0",
 "provenance": {"git_commit": null, "git_dirty": null, "python": "3.12.3", "platform": "linux-x86_64",
                "backends": {"align": {"active": "native", "abi": 2, "lib_sha256": "…"},
                             "reads": {"active": "native", "abi": 1, "lib_sha256": "…"},
                             "rs": {"active": "native", "level": "avx2", "abi": 1, "lib_sha256": "…"}},
                "config_sha256": "…", "seeds": {}},
 "inputs": [{"role": "reads", "path": "…", "bytes": 0, "sha256": "…"}],
 "outputs": [{"role": "container", "path": "…", "bytes": 0, "sha256": "…"}],
 "formats": {"container": [4, 0], "frame_version": 4, "superblock_version": 1, "codec": "f4-sb1-cauchy-rs"},
 "timings": {"seconds": 0.0, "stage_seconds": {"D0": 0.0}},
 "resources": {"peak_rss_bytes": 0, "peak_rss_scope": "parent", "workers": 1},
 "result": {},
 "warnings": [],
 "error": null}
```

- `status` values per kind:
  - decode: `SUCCESS | PARTIAL | FAILURE`, as today (`v4/cli.py:423-424`);
  - other kinds: `SUCCESS | FAILURE`;
  - conformance: `CONFORMANT | NONCONFORMANT` inside `result`.
- `git_commit` and `git_dirty` are filled only when the package runs from a source checkout. Their absence is `null`,
  never a guess.
- `inputs[].sha256` for read files is computed while streaming during D0, with no extra pass. `--no-input-hash` disables it.

Kind-specific `result` bodies:

| Kind | `result` fields |
|---|---|
| encode | `strands`, `strand_nt`, `superblock_strands`, `groups`, `column_parity_groups`, `geometry`, `redundancy_profile`, `strand_profile`, `container_sha256`, `strand_file_sha256`, `verified_after_encode` |
| decode | today's report (`decoder.py:1386-1452`): `superblock`, `reads`, `recovery_plan`, `recovery_schedule`, `indel_recovery`, `soft_decoding`, `outer_v6`, `container_sha256`, `failed_groups`, `lost_ranges`; plus `encrypted`, `content_verified` (spec §2.3.3), `files_published`, `selected` |
| inspect | `vnx.probe/1` body (§4.3) |
| verify | `level` (`L0-container`), `chunks_checked`, `files_checked`, `merkle_root`, `whole_file_sha256_ok`, `warnings` |
| extract/list | entries with path, type, size, sha256 |
| simulate | `model`, `model_version`, `model_schema`, `seed`, `coverage`, `reads`, `evidence_class: "SIMULATED"` |
| benchmark | `vnx.experiment/1` manifest reference and metric table |

### 4.3 Probe body `vnx.probe/1`

```json
{"object": "reads", "readable": "yes", "reason": null,
 "frame": {"version": 4, "domain": "VNX4 scrambler", "share": 0.97, "address_class": null},
 "strand_profile": "v4-balanced", "layout": {"P": 40, "r": 16, "marker_period": 24, "marker_len": 3},
 "primers": null, "sample_reads": 20000,
 "superblock": null, "generated_by": {"answer": "VNX4 frame 4: VNX-DNA ≥ 4.0", "exact": false}}
```

- `readable` ∈ `yes | no | legacy | unknown`.
- `superblock` is filled only with `deep=True`: version, archive ID, geometry, codec, and spec/software where recorded.

### 4.4 Error object `vnx.error/1` (stderr JSON and `result.error`)

```json
{"schema": "vnx.error/1", "code": "FRAME_VERSION_UNSUPPORTED", "category": "UNSUPPORTED_FORMAT", "exit_code": 6,
 "retryable": false, "stage": "D1", "message": "strand frame version 7 is not supported (supported: 4)",
 "hint": "upgrade VNX-DNA; this pool was written by a newer encoder", "details": {"frame_version": 7, "supported": [4]},
 "error_class": "VNXUnsupportedVersionError"}
```

- `code` and `category` are stable. `error_class` is kept for one major version for compatibility (`v4/errors.py:37`),
  then dropped.
- `stage` uses the spec §5 identifiers.
- A decode FAILURE without an exception (exit 5 today, with no `error` field; audit §7.2) MUST now carry `error`, with code
  `INSUFFICIENT_REDUNDANCY` or `NO_SUPERBLOCK`.
- New exit code 10 `PROVIDER_ERROR` (spec §10, open question 6).

## 5. Observability schema

### 5.1 Decode report `vnx.decode-report/1` (`--report FILE`)

It equals the decode `vnx.result/1` envelope (§4.2). The file is written atomically with mode 0600 and never through a
symlink, as today (`b772ce5`).

### 5.2 Events `vnx.event/1` (`--events FILE`, JSON lines)

Today every event has `ts, task_id, event, stage, elapsed, rss_bytes, pid` and `archive_id` once known (`v6/observe.py:45-50`).
Target:

| Field | Every event | Notes |
|---|---|---|
| `schema` | yes | `"vnx.event/1"` |
| `seq` | yes | 0, 1, 2, … per run; detects a lost or truncated tail |
| `ts`, `elapsed`, `task_id`, `pid`, `rss_bytes` | yes | as today |
| `run_id` | yes | random 16 hex per invocation; `task_id` stays caller-chosen |
| `event`, `stage` | yes | stage IDs from spec §5 |
| `archive_id` | once known | as today |
| `software` | `run_start` only | `{name, version}`, `spec` |
| `provenance` | `run_start` only | the `provenance` block of §4.2 (backends with ABI and library hash, config hash, seeds) |
| `inputs` | `run_start`; hashes on `pass1_end` | path, bytes; sha256 when computed |
| `formats` | `superblock` event (new) | frame and superblock version, codec, geometry |
| `status`, `exit_code`, `report_sha256` | `command_end` | as today, plus the report hash |

The event names stay (`decode_start`, `pass1_progress`, `pass1_end`, `recovery_round`, `pass2_end`, `verify`,
`decode_end`, `error`, `command_end`), with `run_start` and `superblock` added. Encode gains the same mechanism (`encode_start`,
`stage_end` per E-stage, `encode_end`). Today encode reports only total seconds (`encoder.py:352,367`).

### 5.3 What observability MUST NOT do

- Change outputs (spec FC-6).
- Record keys, passphrases, plaintext file names of encrypted archives, or payload bytes.
- Fail the command. A failing observer is detached and recorded in the result `warnings`.

## 6. Legacy code: keep or delete (decision)

**Decision.** Keep all V0.1–V3 code, move it physically under `vnxdna.legacy`, and freeze it. Do not delete any of it in V6.
The reasons:

1. It is the only decoder for V1–V3 data (`.vxdna` format 4/5, V1 and V3 frames, VXS, `.vxidx`; audit §4, last row), and
   the directive requires V3/V4/V5 data to stay decodable.
2. About 601 V3 tests and the V1/V2 suites cover it (baseline A.2). Deleting it would drop tests, which the founder's rules
   forbid.
3. Moving it frees the misleading top-level names (`vnxdna.api`, `vnxdna.cli`, `vnxdna.channel`) from looking current
   (audit §5.4, last item). It also makes R4 (§3) checkable by path prefix.
4. The `vnx` path still uses five pieces of it (audit §1.2: `v2.strandio`, `v2.crc`, `v2.encoder.cauchy_parity`,
   `container.compression`, `ecc.*`). Those pieces move **up** into `dnaenc`, `core`, `codec` and `archive`. The legacy
   code keeps importing them through the old paths, which become aliases.

**Packaging consequences.**

- `pydantic` is used only by V1/V2 manifests (audit §1.6). It stays a runtime dependency in 6.x, because `vnx-dna` must
  keep working after `pip install vnxdna`. Moving it to a `[legacy]` extra is a 7.0 decision (open question).
- `reedsolo` leaves the `vnx` import path: the parity-matrix helper moves into `codec`, and `ecc/inner_rs.py:24` (module-top
  `import reedsolo`) stays legacy-only.
- The `Dockerfile` `ENTRYPOINT` becomes `vnx` (today `vnx-dna`, audit §1.3), and CI smoke-tests both `vnx --help` and
  `vnx-dna --help` (today only `vnx-dna`, audit §1.5).

**What may be deleted, and when.**

- Dead code with zero references:
  - `v4/decoder.py:503 consensus_soft`;
  - `v4/util.py:70 sha256_hex`;
  - `v5/soft/frames.py:69 read_posterior` (audit §5.2).
  Delete it in Phase 2 only after a grep over `tests/`, `experiments/` and `benchmarks/` confirms 0 references. Test count
  is unaffected.
- `VNX_RS_REFERENCE` becomes a deprecated alias of `VNXDNA_RS_BACKEND=reference` (audit §3.3). It keeps working with a
  `DeprecationWarning` in 6.x.
- The shims of §7 may be removed no earlier than 8.0, and only with the founder's approval.

## 7. Migration without breaking imports or formats

### 7.1 Order of work (each step is a separate commit set; the full suite passes after every step)

1. **M0 Freeze evidence first.**
   - Generate `tests/fixtures/v6_0` (§8, Phase 2.0) from the **pre-refactor** tree.
   - Add `tests/architecture/test_public_paths.py`. It holds a list, generated once by grepping `tests/`, `experiments/`
     and `benchmarks/` (58 files import `vnxdna.v4` today), of every `vnxdna.*` module path and attribute used outside
     `src/`. It asserts that each one still imports and resolves.
2. **M1 Core and native.** Move errors, versions, util, crc and observe into `core`, and the three kernels into `native`.
   Leave aliases.
3. **M2 Archive and codec.** Move whole modules. Leave aliases.
4. **M3 DNA encoding and sync.** `frame.py` is split into `dnaenc.layout`, `scrambler`, `mapping`, `markers`, `frame4` and
   `constraints`. The old `vnxdna.v4.frame` becomes a façade module that re-exports every public and private name it had.
5. **M4 Recovery and pipeline.** `v4/decoder.py` is split into `recovery.*` modules and `pipeline.decode`. `vnxdna.v4.decoder`
   becomes a façade. `encode_container` and `encode_container_v6` move into `pipeline.encode`.
6. **M5 SDK and commands.** Build the SDK, rewrite the CLI on top of it, and point the `vnx` entry point at
   `vnxdna.commands:main`. `vnxdna.v4.cli:main` stays as an alias.
7. **M6 Simulation and physical.** Move the channel code and the physical code from `experiments/` into the package.
   `experiments/` keep thin scripts that import the package.
8. **M7 Legacy.** Move V0.1–V3 into `vnxdna.legacy` with aliases.

### 7.2 Alias mechanism (normative for the migration)

- **Whole-module moves** (for example `v4/crypto.py` → `archive/crypto.py`).
  - The old path is a three-line module:
    ```python
    import sys, warnings
    from vnxdna.archive import crypto as _m
    sys.modules[__name__] = _m
    ```
  - A `DeprecationWarning` is emitted only when `VNXDNA_WARN_LEGACY_IMPORTS=1`, so test output stays quiet in 6.x.
  - Aliasing through `sys.modules` makes the old and the new name the **same module object**, so `monkeypatch.setattr`
    on either name keeps working. Tests patch module attributes in at least 13 places (`na`, `nr`, `cont`, `ct`, `de`,
    `crypto`, …; grep over `tests/`).
- **Split modules** (`v4/frame.py`, `v4/decoder.py`, `v4/encoder.py`).
  - The old module becomes a façade that imports every name the old module defined, private names included, from the new
    modules.
  - A test that monkeypatches a name on the façade would then patch the façade, not the module that uses it. The Phase 2
    plan therefore lists every such test: the grep shows `setattr(de, …)` once and `setattr(StripeRecovery, …)` once.
  - Those tests are updated to patch the new home in the same commit. The test count and the assertions stay unchanged.
- **Package aliases** (`vnxdna.v2` → `vnxdna.legacy.v2` and so on). The package `__init__` installs aliases for every
  submodule in `sys.modules`, so `import vnxdna.v2.frame` still works.
- **Formats are untouched by the migration.** The goldens (`tests/fixtures/v4_0`, `v5_0`, `v6_0`), the byte-identity test
  for strands (`tests/v6/test_outer_pipeline.py:70-80`) and the native RS golden vectors run after every step.

### 7.3 Deprecation timeline

| Release | Old paths (`vnxdna.v4.*`, `v5.*`, `v6.*`, V1–V3 names) | `error_class` in error JSON | `VNX_RS_REFERENCE` |
|---|---|---|---|
| 6.x | work silently; warnings opt-in | present | works, warns |
| 7.x | work; `DeprecationWarning` by default | present, marked deprecated | works, warns |
| ≥ 8.0 | removal possible with founder approval | may be removed | may be removed |

## 8. Implementation plan, directive Phases 2–8

Each phase ends with the full suite on 6 workers in the foreground, ruff, the layer test, the goldens and a bench-compare
against the phase's base commit. Test count never drops. Every new behaviour is opt-in unless this plan says it changes a
default, and every default change is listed in the phase's acceptance.

### Phase 2: codec/API refactor, version fixes, P1 bugs

| Item | Content | Acceptance criteria |
|---|---|---|
| 2.0 | **V6 golden fixtures** `tests/fixtures/v6_0`, generated **before** any refactor from the then-current tree. Cases: `stripes-seq` (v4-balanced, D 4, Mc 2, sequential), `adaptive-interleaved` (outer-plan adaptive), `max-recovery` (redundancy profile maximum-recovery), `encrypted-stripes` (fixed test-only ID and salt, as `tests/compat`). Each case has `.vnx`, `.strands.fasta`, `.reads.fastq.gz` (SIMULATED channel, fixed seeds), plus `manifest.json`, `SHA256SUMS`, README, `generate.py` | `tests/compat` extended to `v6_0`: exact container and file SHA-256; no false SUCCESS; the README states SYNTHETIC SOFTWARE TEST data. Fixtures never regenerated afterwards |
| 2.1 | Public-path test and layer test (§3, §7.1 M0) | Both green; the allow-list is committed |
| 2.2 | Moves M1–M5 (§7.1) | Full suite green after each M-step; goldens bit-exact; strand byte identity holds; no import cycle |
| 2.3 | SDK (§4) and thin CLI. Profile merge in one place (`codec.profiles`). The `--performance` KeyError becomes `CONFIGURATION_ERROR`, exit 7 (audit §5.4). `vnx encode` archive options are passed through or refused | The CLI tests (`tests/cli`) are unchanged and green. New SDK tests cover every function, with JSON validated against the schemas |
| 2.4 | Version axes (spec §4): `_version.py` becomes `6.0.0.dev0`; `extensions.vnx`; `vnx version` emits `vnx.version/1`. **The container byte-identity test is restated** (needs founder approval): (a) the strand-FASTA identity is asserted by encoding the stored `tests/fixtures/v5_0/balanced.vnx` and `archival.vnx`, so 5.0.0 container bytes go into the encoder and the 5.0.0 FASTA SHA-256s must come out; (b) the archive-builder identity is asserted modulo the informational manifest fields: every section except the manifest and trailer is byte-identical, and the manifest equals 5.0.0's after removing `extensions.vnx` and normalising `encoder.version` | Both new assertions committed **before** the old constant is changed. The old test's intent (frame, superblock and container sections unchanged) is preserved in full |
| 2.5 | Probe and dispatch (spec §3.10) and error codes (spec §10) | Negative vectors: a nibble-7 pool exits 6 not retryable; a V3 frame-5 pool exits 6 `LEGACY_FORMAT`; random reads exit 3; EXP-PROBE-1 passes (§9) |
| 2.6 | Schemas: result, error, report, events (§4–§5); JSON Schema files in `core/schemas/` | Every CLI JSON output validates in tests; reports carry software, spec, backends and input hashes |
| 2.7 | Bugs: job #56 (round S before superblock decode in select mode, audit §6), job #13 (`_FlatReads[-1]`), `locate --dna-profile` refuses superblock-2 archives with `CONFIGURATION_ERROR` instead of printing V4 ranges (audit §5.5), tag-collision detection (spec §3.10 step 7) | The failing test is committed first for each bug; the regression tests pass; #56 is reproduced at D 4, Mc 2, coverage 2–3 |
| 2.8 | Spill-bucket independence (audit §5.6, UNVERIFIED claim) | A test decodes the same reads with 2 different `RLIMIT_NOFILE`-derived bucket counts and gets identical container and report (minus timings) |

**Performance acceptance for Phase 2:** the median of 5 runs of the 1 MiB and 64 MiB clean round trips and of the 4 MiB
noisy decode is within ±5 % of the pre-refactor commit (bench-compare "NO-CHANGE"), at a clean tree (`git_dirty: false`).

### Phase 3: channel-model framework (`vnxdna.simulation`)

| Item | Acceptance |
|---|---|
| `vnx.channel-model/1` schema with stages (synthesis, storage, amplification, sequencing); per-position error profiles; 4×4 substitution matrix; deletion-run length distribution; coverage models (fixed, Poisson, negative binomial, lognormal); `data_source` and `evidence_class` (roadmap V6 table) | Schema committed. The 14 existing models (`experiments/v6/channel/models/*.json`) load as `/0` and as converted `/1`, and give **byte-identical reads for identical seeds** to the current simulator |
| `vnx channel simulate --model NAME[@VERSION] --seed N` uses named models, loss and bursts (today it cannot, audit §2.7) | The old `ChannelConfig` JSON is still accepted, with byte-identical output |
| R2 enforced: no codec layer imports `simulation` | Layer test |

### Phase 4: indel and soft decoding (measurement-driven only)

- No format change.
- A pre-registered design for quality-weighted consensus, compared against the current consensus on the 14 models and, if
  available, public CNR reads. Any comparison with BMA / Trellis-BMA is labelled as such, with datasets cited.
- Acceptance:
  - 0 false SUCCESS;
  - a gain is claimed only where its 95 % interval over seeds excludes 0;
  - the default schedule changes only with founder approval.

### Phase 5: performance and native

| Item | Acceptance |
|---|---|
| `setup.py` builds all three kernels as optional extensions (today only `align.c`, audit §1.3) | `pip install .` with a compiler loads native backends; with `CC=/bin/false` the install still succeeds and reports the reference backends |
| ABI 2 for `reads` and `rs`, and ABI 3 for `align`, with explicit output and context buffer lengths (audit §3.4 item 4); a library source hash embedded and checked (§3.4 item 3) | A stale or mismatched library is refused with a logged reason; differential fuzz ≥ 1 M cases per kernel, native vs reference, 0 mismatches |
| `vnx_rs_restrict_levels` is no longer exported in production builds (test-only build flag) (audit §3.3) | `nm -D` shows no test hook in the production `.so` |
| `-Werror` only in CI builds, not runtime builds (audit §3.4 item 7) | — |
| Native benchmarks re-run at a clean commit, 1 MiB–1 GiB (baseline A.4: earlier runs were dirty-tree) | `git_dirty: false` in every committed result |

### Phase 6: security and fuzzing

| Item | Acceptance |
|---|---|
| `docs/security/V6_SECURITY_MODEL.md`, covering FC-8 (no authenticity for clear archives), tags/primers/Sector One leakage (spec §11), and encrypted SUCCESS semantics | Reviewed by the security reviewer |
| Fuzz harnesses: container parser, manifest, superblock unpack (v1/v2), frame decode, probe, reads parser, RS, aligner, export/import manifests | Each harness ≥ 1 CPU-hour, 0 crashes; findings triaged in `docs/SECURITY.md` |
| `content-v1` archive ID option (spec §2.3.2) | Tests: identical body and tables under both derivations; different content gives different IDs; old readers open it (5.0.0 fixture reader path) |
| CI gains gitleaks (audit §1.7; `docs/SECURITY.md:62` claims it) | Needs a push, i.e. the founder's go |

### Phase 7: interoperability and lab interface

| Item | Acceptance |
|---|---|
| `vnxdna.physical`: the record schemas and validator move from `experiments/` | `tests/v6/physical` unchanged and green |
| `vnxdna.providers`: the protocols and `ReferenceSimulatorProvider` (spec §9) | Round trip `prepare → write → retrieve → read → decode` for v4-balanced and one V6 stripe profile: SUCCESS, deterministic across 1 and 4 workers; every package says SIMULATED. Status table: "interface implemented: yes; provider integration tested: software only" |
| Export/import packages, the pool-composition rule (spec §3.9), the vendor 350-nt check | Negative tests: two archives with one tag give `ARCHIVE_TAG_COLLISION`; a strand of 351 nt with a 350-nt limit gives `VENDOR_MAX_LENGTH` |
| DDSA mapping document (spec §8.1): table only, layouts "to be aligned" | No Sector Zero/One bytes produced |

### Phase 8: conformance and reproducibility

| Item | Acceptance |
|---|---|
| `tests/conformance/` with the required vectors (spec §6.2) and `vnx conformance` (spec §6.3) | `vnx conformance` is CONFORMANT for both `--backend native` and `--backend reference`; each negative vector produces exactly its code and exit code |
| `vnx.experiment/1` manifest; `vnx experiment reproduce` checks software, spec, backends, seeds and input hashes before rerunning | Reproducing one committed Phase 1 experiment cell gives identical decode outcomes |

**Out of V6:** frame 6, superblock 3, primers, short profiles (V7); the wide class, hierarchical index and pool catalogue
(V8); Sector Zero/One writers (V9, needs DDSA IDs); vendor adapters (V11). Spec §3.5–§3.8 fixes their layouts so that this
later work only adds code.

## 9. Experiment plan (all results SIMULATED unless stated)

| ID | Phase | Question | Grid | Seeds | Metrics | Pass criterion |
|---|---|---|---|---|---|---|
| EXP-PROBE-1 | 2 | Does the version probe (spec §3.10) ever refuse a supported pool, or accept an unsupported one? | Pools: VNX4 frame 4 (4 profiles, superblocks 1 and 2), V1 frame 4, V3 frame 5, synthetic nibble-7 pools (frame 4 with the nibble changed), uniformly random sequences. Channels: clean, illumina-like, nanopore-like, mixed-harsh, deletion-heavy, dropout-20. Coverage 1, 3, 10. Sample sizes 64, 1,000, 20,000 | 20 per cell | decision confusion matrix; probe seconds | 0 false refusals of supported pools; 0 acceptances of unsupported ones; unsupported identified correctly in ≥ 99 % of cells with n ≥ 1,000 |
| EXP-REF-1 | 2 | Is the refactor behaviour-preserving? | the goldens v4_0, v5_0, v6_0; the P1-EXP-01 grid subset (3 dropout rates × 2 encoders) | the original seeds | container SHA-256, status, report minus timings; bench medians | identical outcomes; performance within ±5 % |
| EXP-SIM-1 | 3 | Do the migrated channel models reproduce? | 14 models × 3 strand files | 5 | read-file SHA-256 | byte-identical |
| EXP-PROV-1 | 7 | Provider round-trip determinism | 2 profiles × 3 models × workers {1, 4} | 10 | package hashes, decode status | identical across worker counts; 0 false SUCCESS |
| EXP-F6-1 | V7 (designed now) | Freeze the frame-6 candidate profiles (spec §3.7) | f6-s255, f6-s160m, f6-s160d × r ∈ {8 … 16} × marker on/off × 14 models (fitted models once V7 provides them) × Poisson coverage 1–10 | 20 | coverage threshold for ≥ 19/20 SUCCESS; nt per payload byte with outer overhead; encode screening failure rate | pre-registered; 0 false SUCCESS; parameters frozen with golden fixtures |
| EXP-POOL-1 | V7/V8 (designed now) | Multi-archive pools | N ∈ {2, 10, 100, 1,000} small archives; derived vs assigned tags; frame 4 and frame 6 | 10 | separation success; collisions detected | assigned tags: 100 %; derived tags: every collision detected and refused, never a wrong output |

Every result file records software, spec, git commit and dirty flag, backends, seeds and input hashes (`vnx.experiment/1`),
and the label SIMULATED. No result may be described as physical.

## 10. Test plan summary

| Kind | What | Where |
|---|---|---|
| Unit | every stage function per spec §5, against its vector | `tests/unit`, `tests/conformance` |
| Property (hypothesis) | superblock pack/unpack round trip for all valid field ranges, and rejection outside them; frame build/parse round trip with random headers; probe returns the right nibble for any v; alias modules are identical objects (`sys.modules`) | `tests/property`, `tests/architecture` |
| Golden | `tests/fixtures/v0_1, v2_0, v4_0, v5_0` (existing), `v6_0` (Phase 2.0); native RS golden; strand byte identity | `tests/compat`, `tests/v6` |
| Cross-version | the current tree decodes every older golden; stored 5.0.0 answers for SB2 refusal (exit 6) as a negative vector; for V7: 6.x answers for frame 6 / superblock 3 refusal stored and asserted | `tests/compat`, `tests/conformance/negative` |
| Architecture | layer rules R1–R6; public-path resolution | `tests/architecture` |
| Fuzz | §8 Phase 6 harnesses | outside the default suite; CI job optional |

## 11. Risks and open questions

1. **Façade monkeypatch drift** (§7.2). The mitigation is the explicit list of affected tests in Phase 2. Residual risk: a test
   that patches a façade and still passes without exercising the patch. Each touched test therefore asserts that its patch
   was hit (a call counter).
2. **Restating the byte-identity test** (Phase 2.4) needs founder approval. The alternative is to keep `encoder.version`
   frozen at `"5.0.0"`, which perpetuates the audit finding.
3. **Moving `experiments/v6/channel` and `physical` into the package** changes the paths used by committed experiment
   scripts. The scripts keep working through thin wrappers, but reproduction instructions in old result files name the old
   paths. Those files are not edited; `vnx experiment reproduce` maps them.
4. **Exit code 10** extends the stable taxonomy (spec §12, question 6).
5. **The lab venv's editable install points at another worktree** (audit §2.3). Phase 2 benchmarks and tests MUST run with
   an explicit `PYTHONPATH` to the worktree under test.

## Decisions on the open questions (founder-delegated, 2026-10-05)

| # | Question | Decision |
|---|---|---|
| 1 | Version bump to `6.0.0.dev0` and the new manifest block break the pinned 5.0.0 container SHA (`tests/v6/test_outer_pipeline.py:70-80`) | **Approved.** The stronger replacement assertions (Phase 2.4) are committed *before* the bump: re-encoding the stored V5 containers must reproduce the V5 strand SHA-256s, and every non-manifest container section must stay byte-identical. The test count must not drop. |
| 2 | Write `extensions.vnx` by default in 6.x? | **Yes, by default.** It is informational, and it answers "which spec and software produced this archive" (audit §4). Readers ignore it if absent. |
| 3 | New exit code 10 for provider errors | **Approved**, reserved now and implemented in Phase 7. |
| 4 | Sector Zero/One layouts | **Deferred** to V9 / DDSA membership. Field naming is aligned already. |
| 5 | Freezing the short-strand profiles | **Deferred** to V7. Frozen only after experiments on fitted channels. |
| 6 | CRC-16 for the shortest profile | **Deferred.** Revisit with V7 experiment data. |
| 7 | `pydantic` to an optional legacy extra | **Deferred** to 7.0, with the legacy package split. |

Next step before any refactor: Phase 2.0 generates the V6 golden fixtures (`tests/fixtures/v6_0`) from the unrefactored tree.
