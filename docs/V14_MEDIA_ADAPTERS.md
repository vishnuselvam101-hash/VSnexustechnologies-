# V14 media adapters (Phase 7)

Master directive §13. The adapters sit behind the stable `MediaAdapter` contract of
[V14_CONTRACTS.md](V14_CONTRACTS.md) §7 (media contract **v1, unchanged** by this phase). Evidence categories:
the image backend is category C (electronic backend); the DNA simulator and the lab simulator are category B/D
(software device over a SIMULATED channel). Nothing in this phase is category E or F: **no DNA was synthesised,
stored, retrieved or sequenced, and no adapter has been tested against physical equipment.**

| adapter | module | `kind` | durability | persists across restarts | what it is |
|---|---|---|---|---|---|
| memory (P1) | `media.rs` | `memory` | volatile | no | in-process test adapter |
| image | `image.rs` | `image` | file-fsync | yes | electronic file-image backend on the host's storage; **not DNA** |
| DNA simulator | `dnasim.rs` | `dna-sim` | simulated-dna | if its pool store does (e.g. over `image`) | VNX-DNA codec + simulated channel |
| fault injection | `fault.rs` | `faulty` | the wrapped adapter's | the wrapped adapter's | deterministic faults over any adapter |
| lab (simulated) | `lab.rs` | `dna-lab-sim` | simulated-dna | no (the simulated lab is in memory) | the future physical-DNA interface over `SimulatedLab` |

Every adapter passes `media::conformance` (unit tests in each module; the DNA adapters also over the real codec in
`tests/media_worker.rs`, run by `tests/v14/test_media_adapters.py`). The rule shared by all: **a `get` returns
exactly the bytes of the successful `put` of that generation, or a typed error; integrity is verified before
anything is returned.**

## 1. Shared pieces

* **Worker pool** (`worker.rs`): the `vnx.codec-ipc/1` process pool of P4, extracted from `seal.rs` so the sealing
  workers and the codec workers share one implementation (spawn, `hello` negotiation, bounded response lines,
  timeouts, replace-on-failure, private staging directory, staging files `<tag>-<pid>-<start ns>-<job>.in|out`
  swept per tag when their process is gone). `WorkerSealer` behaviour is unchanged (tag `seal`).
* **Codec boundary** (`codec.rs`): the `Codec` trait (`encode(at, framed)`, `decode(at, pool, expect_sha256,
  expect_len, channel)`); `WorkerCodec`, the production codec (Python workers, tag `dsim`); `ToyCodec`, a TEST-ONLY
  stand-in (2 bits per base, per-base majority vote, no ECC) so the adapters' own logic is tested without Python —
  never shipped as a codec and nothing measured with it is a codec result.
* **Frame**: what a DNA adapter encodes is `VNXFRAM1 | extent u64 | generation u64 | length u64 | bytes`
  (big-endian). A decode is accepted only if its SHA-256 and length equal those recorded at `put` (checked by the
  adapter itself, `codec::verify_decoded`, whatever the codec reports) and its frame names the generation asked for. An empty extent is a 32-byte frame (the protocol's `output_bytes >= 1` holds).
* **Envelope** (what a DNA adapter keeps in its electronic store per generation): `VNXDENV1 | kind | sha256 |
  length | payload`. Kind `D`: the frame's SHA-256 and length, then the pool file (`dna-sim`) or the catalogue entry
  (`dna-lab-sim`). Kind `M`: electronic metadata only, used for the volume's binding marker (extent `u64::MAX`,
  beyond the codec protocol's signed 64-bit range); its bytes are checked against its SHA-256 on every read. The
  marker is therefore **never presented as DNA** (directive §17.1: a small electronic metadata region).

## 2. A — DNA software simulator (`dna-sim`)

`DnaSimMedia::new(store, codec, DnaSimConfig)`.

* `put`: frame → `Codec::encode` → `vnx.dna-pool/1` file → envelope `D` in `store`. The real codec is the Python
  worker `python -m vnxdna.device.dnasim --staging DIR`, which serves `vnx.codec-ipc/1` `encode_extent` /
  `decode_extent` with the **existing** codec: `vnxdna.pipeline.stream.encode_extent` (unchanged archive container,
  encoder, inner/outer ECC; content archive IDs) and `decode_extent` (unchanged decoder, verified against the
  record's SHA-256). The protocol schemas are those of P1 (golden files unchanged); this phase defines the content of
  their `options`.
* `get`: per attempt one simulated sequencing run: `decode_extent` with options
  `{"channel": {"model": NAME, "seed": N}}` passes the pool's strands through the **existing** staged channel
  simulator (`vnxdna.simulation.engine.simulate_file`, a shipped `vnx.channel-model/1` NAME; paths are refused) and
  decodes the simulated reads. The seed of attempt n is derived from (seed, extent, generation, n), so every run of a
  pool differs and the sequence is reproducible. A decode that fails is retried with a fresh run up to
  `read_attempts`, then CORRUPTION. `channel: None` decodes the synthesised strands as they are.
* **Failure model** (seeded, ppm): `synthesis_failure_ppm` (put fails BACKEND_UNAVAILABLE, nothing stored),
  `sequencing_failure_ppm` (get fails BACKEND_UNAVAILABLE). Both retryable; `ResilientMedia` retries them.
* **Latency model**: modelled synthesis/sequencing times (fixed + per KiB). Accounted in `report()`
  (`modelled_*_ns`) and slept only if `sleep` is set (scheduler experiments). They are model inputs, not
  measurements, and the defaults are zero.
* `report()`: counters with `"evidence": "SIMULATED"`.

Pool file `vnx.dna-pool/1` (owned by `vnxdna.device.dnasim`): `VNXPOOL/1\n`, a canonical-JSON header line with
exactly `format`, `evidence` (`SIMULATED`), `extent`, `generation`, `body` (`strands` | `reads`), `body_sha256`,
`record` (the stream extent record), then the body (FASTA strands, or FASTA/FASTQ reads). Both sides parse it
strictly; any damage is CORRUPTION. Worker rules: no key (sealing ops are PROTOCOL); private staging directory
checked at start; inputs opened without following symlinks; outputs created exclusively 0600; scratch directories
`.dsimw-<pid>-*` inside the staging directory (names a request cannot use), swept when their worker is gone; a
channel is never applied to reads; channel models are loaded from the shipped catalogue only (never a file of that
name); the decoded bytes must match `expect_bytes`/`expect_sha256` or nothing is written. Error classes: a decode
that does not verify is CORRUPTION; a refusal of the request's options is INVALID_ARGUMENT with the message prefix
`options: ` (the only INVALID_ARGUMENT `vnxd` passes on as permanent); the worker's own resource failures (disk
full, output or OS errors) are BACKEND_UNAVAILABLE (retryable), never damage.

## 3. B — electronic image backend (`image`)

Format `vnx.media-image/1`: `IMAGE` (format line + "electronic file-image backend of vnxd: host storage, NOT DNA"),
`LOCK` (owner pid), `objects/<extent hex16>-<generation hex16>.obj` = 64-byte header (`VNXIMG01`, extent, generation,
length (u64 LE), SHA-256) + bytes.

* **Crash-safe put**: temporary file `objects/.tmp-<pid>-<n>` (exclusive, 0600) → write → `fsync` → `link(2)` to the
  final name (atomic; fails if the name exists, so a generation can never be replaced, not even by another process)
  → `fsync` of the objects directory → unlink the temporary name. A crash leaves no object or the whole object;
  temporary files are swept at open.
* **fsync discipline**: a receipt is returned only after the data and the directory entry are durable; `remove`
  fsyncs the directory before returning; `sync` fsyncs it again.
* **Integrity-checked reads**: magic, the header's generation = the one asked for (a file copied under another name
  is refused), length = file size, SHA-256; else CORRUPTION; a missing object is NOT_FOUND. `scrub()` checks every
  object.
* One process per image (advisory `LOCK` = "<pid> <token>", created by linking a complete fsynced file so it is
  never seen empty; a lock whose pid no longer runs, or that names this process while no instance of this process
  holds the image — an earlier incarnation with the same pid — is taken over; a failed open releases it); host I/O
  errors are BACKEND_UNAVAILABLE; a second opener is BACKEND_BUSY.
* Object size limit: 65 MiB by default (`with_max_object` changes it). A pool store of the DNA simulator needs
  several times the extent size (strands and ECC), so it must be opened with a larger limit for large extents.

Not a block device (that is P8) and never a physical disk: images live in directories the caller names (tests use
temporary directories).

## 4. C — fault-injection backend (`faulty`)

`FaultMedia` (P4) is extended, not duplicated:

* `FaultMedia::over(inner)` injects into any adapter (image, DNA simulator); `info().durability` is the inner one
  (the default inner is still `MemoryMedia`, so P4 behaviour is unchanged).
* `FaultPlan { seed, entries: [(op, fault, ppm)] }`: the decision for the n-th call of an operation kind is
  `SplitMix64::keyed(seed, kind, n, entry)`; same seed + same call sequence = same faults (`trace()` records them).
  `FaultPlan::honest` (unavailable, busy, timeout, lost acknowledgement, slow reads) and `FaultPlan::lying` (+ torn
  puts, wrong receipts, silently corrupted writes, corrupted writes with lost acknowledgement, bit rot, short reads,
  lost objects). Explicit `Rule`s are checked first.
* `Fault::Delay { millis }` (slow medium), `set_offline(true|false)` (the whole medium unavailable, then recovery).

Recorded outcomes (`tests/media_adapters.rs`), in addition to the P4 matrix:

| scenario | expected and observed outcome |
|---|---|
| same seed twice / another seed, honest and lying plans | identical outcomes and traces / different traces |
| volume + `BlockIo` + `ResilientMedia` over honest faults (4 % per entry), 6 seeds x 300 random requests | > 200 of 300 succeed; every read equals the byte model |
| the same over lying faults (3 %), over memory and image backends | every lying fault kind fires across the seeds; no read ever returns other bytes; failed writes change nothing; final reads equal the model or are CORRUPTION/NOT_FOUND |
| medium offline | writes BACKEND_UNAVAILABLE then READ_ONLY (device read-only), reads BACKEND_UNAVAILABLE; back online: a probe write succeeds, health HEALTHY, data intact |
| image object with a flipped bit under a volume | `read_extent` and `BlockIo::read` CORRUPTION; other extents unaffected |
| DNA simulator with seeded run failures behind the fallback layer | every read is the last completed write, zeros, or a retryable error; retries eventually write every extent |

## 5. D — future physical-DNA adapter interface `vnx.dna-lab/1`

**Status: interface + simulator + conformance tests + this document. Not connected to any provider or instrument;
no physical validation; no vendor command or vendor API is modelled or invented.** The names in the interface are
ours. A real adapter would map them onto what a provider actually offers (order portal or upload template, LIMS,
instrument control software) from that provider's documentation.

| concern | interface (`lab.rs`) |
|---|---|
| synthesis job submit / ids / status | `submit_synthesis(SynthesisOrder) -> JobId`; `status(JobId) -> JobStatus` |
| strand and archive ids | `SynthesisOrder { order_ref, archive_id, strands: [Strand { name, sequence }] }`, validated against `LabCapabilities` (length range, A/C/G/T only, unique names, order size) before anything is submitted |
| physical location + preservation metadata | `PoolRecord { location: { site, container, position }, preservation: { method, storage_condition, stored_at } }` — free text as the lab records it, never inferred |
| retrieval jobs | `submit_retrieval(RetrievalRequest { pool, selection: All | PrimerPair })` → `SampleId` |
| sequencing jobs | `submit_sequencing(SequencingRequest { sample, platform, coverage, format })` |
| read-data delivery | `read_data(job) -> ReadDelivery { run_id, files: [DeliveredFile { name, format, sha256, bytes }], quality }`; `fetch(job, file)`; formats `fastq`, `fastq.gz`, `bam`, `pod5`, `bcl-run-folder` (only FASTQ is decoded directly; the others need basecalling or conversion first and are refused as BACKEND_UNAVAILABLE by `LabMedia`) |
| quality metrics | `QualityMetrics { reads, bases, mean_phred_x100, q30_permille, simulated }`, computed from the delivered reads |
| reconstruction handoff | `LabMedia::get`: every delivered file is checked against its announced SHA-256 and size, the reads are wrapped in a `vnx.dna-pool/1` file (`body: reads`) and handed to `Codec::decode`; only bytes that decode to the recorded SHA-256 return |
| retry / cancel | `cancel(job)` until `finishing`; `retry(job)` only for a failed job with a retryable error, giving a new job whose `retry_of` names the old one |
| equipment errors | `EquipmentError { kind, detail }`: instrument-fault, consumable-exhausted, qc-failed, environment-out-of-range, communication-lost, operator-abort (retryable); sample-lost, rejected, unknown (not). Mapped to BACKEND_UNAVAILABLE / CORRUPTION / INVALID_ARGUMENT / INTERNAL |
| media health / lifecycle | `PoolLifecycle`: ordered, stored, in-retrieval, depleted, destroyed; `retrievals`, `aliquots_left` (as the lab reports); `destroy_pool`; `health() -> LabHealth { online | degraded | offline, per instrument }` |

Job state machine (`JobState::can_move_to`): `submitted → queued → running → finishing → completed`; any
non-terminal state → `failed`; `submitted | queued | running → cancelled`; `completed`, `failed`, `cancelled` are
terminal. The tests walk a job through it, check the recorded history against the machine, and check cancel, retry
and the refusals.

`SimulatedLab` (SIMULATED): virtual time (each `status` poll advances a job one state), seeded equipment failures per
job kind, aliquot accounting (a depleted pool is reported as sample-lost → CORRUPTION, never invented data), reads
from `codec::simple_channel` (dropout, i.i.d. substitutions; not fitted to any instrument — the realistic channel is
adapter A's), location `site: SIMULATED`. `LabMedia` is the media adapter over any `DnaLab`; it **refuses a lab
whose capabilities say `simulated: false`**, because media contract v1 has no durability class for physical DNA.
Connecting real equipment requires a versioned contract change (a new durability class and its semantics), a real
provider adapter, and physical validation — none of which exist.

### 5.1 Sources consulted (2026-10-18) and what was taken from each

| source | used for |
|---|---|
| SNIA, *DNA Data Storage Sector Zero* v1.0 (SNIA standard, DNA Data Storage Alliance / DARS working group), https://www.snia.org/standards/technology-standards-software/standards-portfolio/dna-data-storage-sector-zero ; summary of Sector Zero/One: https://www.storagenewsletter.com/2024/03/22/dna-data-storage-alliance-releases-first-specs-for-storage-of-digital-data-in-dna/ | an archive must be identifiable without the writer: Sector Zero carries the synthesis vendor and codec identity, Sector One the archive metadata (contents description, file table, parameters for the sequencer). Reflected as `archive_id`/`order_ref` on every order and pool and the codec identity in the pool header. VNX-DNA does not implement Sector Zero/One (the PDF was not retrievable here: HTTP 403) |
| Twist Bioscience, *Twist Oligo Pools Amplification Protocol* (DOC 4060), https://www.twistbioscience.com/content/dam/DOC_4060_Twist_Oligo_Pools_Amplification_Protocol_REV1_singles.pdf ; Twist FAQ on degenerate bases, https://www.twistbioscience.com/faq/oligo-pools/can-i-submit-oligos-twist-degenerate-bases | oligo pools of 20–350 nt; N is the only degenerate base; order = named sequences. → `min_strand_nt`/`max_strand_nt` capabilities (simulator default 20–350), A/C/G/T-only orders |
| IDT oPools entry page, https://eu.idtdna.com/site/order/poolentry | 40–350 bases; spreadsheet upload; pool size limits depend on synthesis scale; delivery in business days. → per-provider length limits and order-size capability; jobs measured in days (polling, not blocking calls) |
| Illumina, BCL Convert / DRAGEN BCL conversion guide, https://help.dragen.illumina.com/product-guides/dragen-v4.5/bcl-conversion | instruments write a run folder (BCL files, `RunInfo.xml`, `SampleSheet.csv`); FASTQ is produced by a conversion step. → `bcl-run-folder` format that needs conversion before decoding; `run_id` in deliveries |
| Oxford Nanopore, POD5 specification, https://software-docs.nanoporetech.com/pod5/latest/specification/ and output specifications, https://software-docs.nanoporetech.com/output-specifications/latest/read_formats/pod5/ | raw signal in Apache Arrow tables (Reads, Signal, Run Info; `read_id` UUID per read); basecalled output is separate. → `pod5` format that needs basecalling before decoding |
| Oxford Nanopore, MinKNOW API protobuf definitions, https://github.com/nanoporetech/minknow_api (`acquisition.proto`) | run states starting / running / finishing / completed / paused and stop reasons (user request, device error, bad temperature, no disk space, ...). → the job state machine and the `EquipmentErrorKind` list (our names; no MinKNOW call is used) |
| Cock et al., *The Sanger FASTQ file format...*, Nucleic Acids Research 38(6):1767–1771 (2010), doi:10.1093/nar/gkp1137 (standard reference, not re-fetched) | FASTQ with Phred+33 qualities. → `fastq_quality` (mean Phred, Q30 fraction) |
| SAM/BAM specification, https://github.com/samtools/hts-specs (standard reference, not re-fetched) | `bam` as a possible delivery format |

## 6. Known limits (recorded, not fixed in P7)

* **Determinism scope.** Seeded decisions are keyed by per-adapter call counters (`FaultMedia`: per operation kind;
  `dna-sim`: process-wide put/read counters, which restart at 0 with the process; `SimulatedLab`: its poll clock).
  They are reproducible for the same sequence of calls in one thread; concurrent I/O changes the order and therefore
  the faults. The tests that assert determinism are single-threaded.
* **Ambiguous put retries over DNA adapters.** When a put attempt fails ambiguously, `ResilientMedia` (P4) checks
  what was stored with a `get`. Over `dna-sim` that is a full simulated sequencing run and decode (under a noisy
  channel it can fail and turn a stored put into a reported failure; never wrong data); over `dna-lab-sim` it
  consumes an aliquot. A cheap "stored digest" query would avoid it but is an addition to media contract v1, left to
  a versioned contract change.
* **Unowned lab jobs.** `LabMedia` cancels a job it stops waiting for (status error or timeout), but cancellation is
  refused once a job is finishing, and a real lab keeps working while nobody polls. A real adapter must persist the
  outstanding job ids and reconcile them at start (a pool no catalogue entry references is destroyed or adopted).
  The simulator advances jobs only when polled, so it cannot show this.
* **Process liveness via `/proc/<pid>`** (staging sweeps, image lock): a process in another pid namespace sharing
  the directory looks dead. Consequence: a retryable failure of that process, never wrong data.
* The pre-existing P2 test `io::tests::backpressure_cancellation_timeout_and_barrier` relies on a 50 ms sleep; under
  heavy host load its debug build hung twice in this phase (once before any P7 change). It passes alone and in the
  release suite; it is reported, not changed here.

## 7. Tests

| test | what |
|---|---|
| `image::tests`, `dnasim::tests`, `codec::tests`, `lab::tests` | conformance of every adapter; image name parsing and locking; DNA-sim channel retries, moved/damaged pools, seeded run failures, modelled latency; frame/envelope damage; job state machine, lifecycle, order validation, cancel/retry, depletion, offline lab, quality metrics, refusal of a real lab |
| `tests/media_adapters.rs` | restart persistence (image, DNA-sim over image), crash sweep and damaged objects, seeded fault determinism, volume/`BlockIo`/fallback under honest and lying faults, offline + recovery, DNA-sim and lab under a volume |
| `tests/media_worker.rs` (ignored; run by `tests/v14/test_media_adapters.py`) | the DNA adapters over the real VNX-DNA codec: round trip, failure modes, conformance with a perfect channel and with `illumina-like`, a volume over pools kept in an image across a restart, a lossy channel never returning other bytes, `LabMedia` conformance |
| `tests/v14/test_media_adapters.py` | the Python codec worker: round trip with and without a channel, moved/tampered/mismatched pools, strict options, no sealing, seeded channel, strict pool parsing, staging checks, scratch sweep |
