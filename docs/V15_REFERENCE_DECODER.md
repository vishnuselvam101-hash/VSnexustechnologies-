# The V15 reference decoder and the format-freeze compatibility rule

This document is the normative **compatibility rule** required by the V15 finalization plan
(LR-1) and the **version-compatibility contract** of the reference decoder (LR-3). It states
which archives the reference decoder reads, which it refuses, and what every later VNX-DNA
version must do with the archives frozen here.

**Evidence rung: MEASURED for the software behaviour (the golden corpus is decoded and
digested in CI), THEORETICAL for the physical behaviour (no DNA was synthesised, stored or
sequenced; every input in the corpus is a software-produced read set).**

---

## 1. What the reference decoder is

`reference/vnx_ref_decoder.py` is a second, independent implementation of the VNX-DNA
**format-4** read path. It is written for clarity, not speed, so that a future party can
port it to another language without reading the production code.

| property | value |
|---|---|
| language | Python 3.12+ |
| dependencies | **the standard library only** — no `numpy`, no `cryptography`, no `vnxdna` |
| source size | **1 341 lines** (the whole file, including its docstrings) |
| non-blank, non-comment lines | 1 116 |
| public entry points | `decode_reads`, `decode_container`, `restore_reads`, `restore_container` |
| algorithm covered | read parsing, the three nucleotide mappings, de-scrambling, CRC-32, the inner GF(256) Reed–Solomon errors-and-erasures decode, consensus, the outer Cauchy erasure code, the manifest, chunk SHA-256, AES-256-GCM, whole-object SHA-256 |
| test | `tests/v15/test_hardware_lane.py` |

The standard-library-only rule is enforced mechanically: the test parses the module's import
statements and fails if any of them names a module that is not in `sys.stdlib_module_names`.

### Measured runtime

Measured on the lab machine (Python 3.12.15, single core), decoding the whole committed
golden corpus (`reference/golden/`, four archives). The time is the best of five runs after a
warm-up, so the numbers are free of first-call import cost:

| path | work | time |
|---|---|---|
| container (`.vxdna` → original bytes) | 4 archives | **0.0002 s** total (≈ 0.05 ms each) |
| strands (FASTA → original bytes) | 4 archives, 753 strands | **0.099 s** total (≈ 25 ms each) |

Per archive, the strand path costs:

| archive | strands | time |
|---|---|---|
| `plain-2bit` | 171 | 11 ms |
| `zlib-rotation3` | 129 | 31 ms |
| `codebook8-plain` | 249 | 44 ms |
| `encrypted-2bit` | 204 | 12 ms |

The reference decoder is roughly two to three orders of
magnitude slower than the production decoder; that is expected and accepted (LR-3 "risks":
it is slow on large pools, acceptable, documented). The runtime is a property of the
corpus, not a performance claim about any pool size.

The line count and the runtime above are recorded here because LR-3 requires both to be
documented. They are **not** a specification: a later revision of the reference decoder must
update this table in the same commit that changes the number.

---

## 2. What the reference decoder reads

### 2.1 The format it implements

| axis | value the reference decoder accepts | value it refuses |
|---|---|---|
| archive `format_version` | **4** | anything else (`1`, `2`, `3`, `5`, unknown) |
| container file version (`.vxdna`) | **1** (magic `"\x89VXDNA\r\n"`) | anything else |
| strand frame format | **4** (the high nibble of the frame's first byte) | 5, 6, unknown |
| nucleotide mapping | `2bit`, `rotation3`, `codebook8` | any other name |
| outer code | `cauchy-rs-gf256` (K data + M parity shards) | any other algorithm name |
| inner code | `reed-solomon-gf256` | any other algorithm name |
| compression | `none`, `zlib`; `zstd` **only** on an interpreter that provides `compression.zstd` (Python 3.14+) | `zstd` on an older interpreter: a typed `ReferenceUnsupportedError`, never a silent wrong answer |
| encryption | `aes-256-gcm` with HKDF-SHA256 key derivation | any other algorithm: a typed `ReferenceUnsupportedError` |

Every refusal is a typed exception (`ReferenceFormatError`, `ReferenceIntegrityError`,
`ReferenceUnsupportedError`, `ReferenceRedundancyError`, `ReferenceKeyError`), all of them
subclasses of `ReferenceDecodeError`. The decoder never returns bytes and never returns a
"best effort" result: on any failure it raises and returns nothing.

### 2.2 The archives of the V14 and V15 lines

**Read by the reference decoder: none of them, and that is by design.**

The V14 and V15 lines write the **VNX4 container** (`.vnx`, magic `"\x89VNX4\r\n\x1a"`,
manifest `format_version` `[4, 0]`, `format: "VNX4"`) and **frame-4 strands with marker
nucleotides**. The format-4 *archive* format (`.vxdna`, manifest `format_version: 4`,
`format: "VNX-DNA"`) is a **different format** that shares the number 4 and the frame
number 4 with the VNX4 container, but has a different container layout, a different
manifest schema and a different strand address space.

So the honest statement of the contract is:

| archive family | written by | read by the reference decoder | read by the production decoder |
|---|---|---|---|
| format-4 archive, `.vxdna` container file version 1, frame 4 | VNX-DNA 1.0.0 (`vnx-dna v1 store/encode`) | **yes** — this is what it implements | yes |
| VNX4 container `.vnx`, manifest `format_version` `[4, 0]`, frame 4 with markers | VNX-DNA 4.x, 5.x, 6.x, 14.x, 15.x | **no** — typed refusal | yes |
| archive format 5, container file version 2, frame 5 | VNX-DNA 2.x, 3.x | **no** — typed refusal | yes |
| V0.1 datasets (`format_version` 1/2/3) and the RD-1 JSON archive | V0.1 | **no** — typed refusal | yes, read-only, via `vnx-dna legacy restore` |

The V14/V15 archives are covered by the **production** decoder, which is exercised by the
committed compatibility suite `tests/compat/test_v4_v5_archives.py` against the released
V4.0 (`d235239`) and V5.0 (`6aef3f4`) fixtures, and by `tests/compat/test_v6_golden.py`
against the V6.0 fixtures. The reference decoder is not a second implementation of the
VNX4 container: it is a second implementation of the format-4 archive read path, which is
the format whose byte layout the V15 specification freezes (§3).

**This is a limitation, stated plainly: a port of the reference decoder to another
language does not by itself give that language a VNX4-container reader.** The VNX4
container is a separate piece of work with its own specification
(`docs/VNX4_FORMAT.md`). The reference decoder covers the strand layer (the part that
crosses into the physical medium) and the format-4 archive layer; it does not cover the
VNX4 container layer.

### 2.3 What the reference decoder is checked against

| check | where | what it proves |
|---|---|---|
| golden corpus, container path | `tests/v15/test_hardware_lane.py` | each of the four committed golden archives decodes to exactly the bytes it was made from (SHA-256 equality) |
| golden corpus, strand path | same | the same, starting from the committed FASTA strand pool |
| production agreement | same | the production decoder and the reference decoder produce the **same SHA-256** for every golden archive |
| digest pinning | `reference/make_golden.py --check` | the committed container, FASTA and payload digests in `reference/golden/index.json` still match the files on disk |
| standard-library-only | same | the reference decoder imports nothing outside the standard library |
| round trip | same | the FASTA pool decodes to the same bytes as the container it was made from |

---

## 3. The compatibility rule (normative)

This is the rule that LR-1 requires to be in the specification and in
`docs/RELEASE_PROCESS.md`. It is written here in RFC 2119 keywords.

**C-1 (read the past).** Every later VNX-DNA version **MUST** decode every archive written
by an earlier version to the same bytes. "The same bytes" means the byte-for-byte
identical original file, verified by the digest the archive records.

**C-2 (one breaking change, once).** The V15 freeze happens once. After it, a change that
would make an existing archive unreadable **MUST NOT** be made. A change that alters the
meaning of an existing field, the layout of an existing structure, the definition of an
existing digest, or the acceptance criteria of an existing code **MUST** be treated as a
breaking change and **MUST** raise the archive format version.

**C-3 (extensions, not reinterpretations).** New information **MUST** be added only
through the declared extension fields (`extensions` in the manifest) and only with a new
entry in `required_features`. An existing field **MUST NOT** be given a new meaning.

**C-4 (refuse, never ignore).** A reader **MUST** refuse an archive that declares a
`required_features` entry, a format version, a container file version, a frame format
nibble, a reserved field or a flag bit it does not know. A reader **MUST NOT** decode
"as best it can" and **MUST NOT** skip an unknown field.

**C-5 (the frozen corpus is the test).** The golden archives under `reference/golden/`
**MUST** decode bit-exactly with the production decoder and with the reference decoder in
every later release. The digests in `reference/golden/index.json` **MUST NOT** change
unless the archive format version changes with them.

**C-6 (the reference decoder travels with the release).** A copy of the reference decoder
as it stood at the freeze **MUST** be archived with every later release, together with the
specification text it implements, so that a future reader can be built from the spec
alone (LR-5).

**C-7 (a new format is a new number).** A future format **MUST** change the archive
format version, the container file version or the frame format nibble. It **MUST NOT**
reinterpret an existing one.

**C-8 (the rule for future versions).** When a new archive format version `N+1` is
introduced, a reader that implements `N` **MUST** keep reading `N` archives to the same
bytes (C-1) and **MUST** refuse `N+1` archives with a typed "unsupported version"
refusal (C-4) until it is taught `N+1`. A reader **MUST NOT** accept a format version it
does not know, even if the archive would otherwise parse.

### 3.1 Where the rule lives

| location | what it holds |
|---|---|
| this document | the compatibility contract of the reference decoder and the rule in full |
| `docs/COMPATIBILITY.md` | the format-by-format matrix of what the current code reads |
| `docs/RELEASE_PROCESS.md` step 2 | the release gate: the full suite, including the golden-corpus test, must pass before a release is cut |
| `docs/V15_FINALIZATION_PLAN.md` §LR-1 | the plan item this rule discharges |
| `reference/golden/index.json` | the frozen digests that make the rule testable |

---

## 4. The golden corpus

`reference/golden/` holds four archives, generated by `reference/make_golden.py` with the
production encoder from a fixed payload and fixed options. The corpus is small on purpose:
each archive carries a few hundred bytes, and the whole corpus is 189 442 bytes, so it can
be reviewed by a human and re-generated by anyone.

| profile | mapping | compression | encryption | container bytes | strand bytes | strands |
|---|---|---|---|---|---|---|
| `plain-2bit` | `2bit` | none | no | 1 769 | 28 790 | 171 |
| `zlib-rotation3` | `rotation3` | zlib (level 9) | no | 1 933 | 43 648 | 129 |
| `codebook8-plain` | `codebook8` | none | no | 2 052 | 74 834 | 249 |
| `encrypted-2bit` | `2bit` | none | yes (AES-256-GCM) | 2 038 | 34 378 | 204 |

Each profile is stored twice: as the `.vxdna` container file and as the FASTA strand pool
it encodes to. The two are two routes to the same bytes, and the test checks that they
agree.

The corpus covers the three nucleotide mappings, both the compressed and the uncompressed
path, and the encrypted path with its own AES-256-GCM and HKDF-SHA256 key derivation
implemented in the reference decoder from the standard primitives.

**The corpus does not cover `zstd`.** A zstd archive needs a zstd decoder, which the
standard library only provides from Python 3.14; a reference decoder restricted to the
standard library therefore cannot read one on an older interpreter. The corpus uses `none`
and `zlib` so that it is fully covered on every supported interpreter, and the reference
decoder raises a typed `ReferenceUnsupportedError` for `zstd` rather than guessing.

**The corpus is synthetic.** Every byte in it was produced by the production encoder on
this machine. No DNA was synthesised, stored or sequenced, and no physical read was
involved.

---

## 5. Regenerating the corpus

```bash
python3 reference/make_golden.py            # write the corpus
python3 reference/make_golden.py --check    # verify the committed corpus without writing
```

The script is deterministic for three of the four archives: the same production encoder and the
same fixed payloads produce byte-identical files. The encrypted entry is the exception —
the container builder draws a fresh random archive id per archive, and the AEAD key and
the nonces are derived from it, so `encrypted-2bit` regenerates to different bytes every
run while still decoding to the same payload. Its **payload** digest is the stable part
of that entry; the container and FASTA digests pin the committed files, not the generator.
This is why `--check` verifies the committed corpus against the recorded digests rather
than regenerating and comparing.

Regenerating the corpus is **not** part of a normal change.
If a change to the encoder alters a golden archive, the format has changed and the
freeze (C-2) applies: the format version must change with the corpus, in the same commit
that changes the digests.

---

## 6. Known limits of the reference decoder

Stated so that nobody over-reads it:

1. **It does not read the VNX4 container** (`.vnx`), so it does not read V14/V15
   archives. See §2.2. This is the largest gap and it is a scope decision, not a defect.
2. **It does not read format 5, frame 5, or the V0.1 formats.** It refuses them with a
   typed error.
3. **It does not do `zstd`** on an interpreter without `compression.zstd`.
4. **It is slow** on large pools: it is a clarity implementation. See the measured
   runtime in §1.
5. **It reads FASTA/FASTQ/one-sequence-per-line**, not the packed `.vxs` strand file, and
   it has no streaming mode: a whole read set is held in memory.
6. **It has no random access.** It decodes a whole archive; it cannot extract one chunk
   or one byte range the way `vnx-dna extract` does.
7. **It has been checked against the corpus, not against every archive ever written.**
   C-1 is enforced by the production decoder's compatibility suite; the reference decoder
   is checked on the corpus and on the round trip, and by construction on the format-4
   archives it implements.
