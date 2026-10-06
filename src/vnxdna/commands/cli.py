"""``vnx``: the VNX-DNA command-line interface, a thin layer over :mod:`vnxdna.sdk` (V6_ARCHITECTURE §3 R6, layer 8).

    vnx archive ./dataset archive.vnx            files/directories → VNX4 container
    vnx inspect | list | verify | locate | extract archive.vnx ...   (inspect also answers "can I read this?" for reads)
    vnx encode archive.vnx strands.fasta         container (or plain files) → DNA strands
    vnx channel simulate strands.fasta reads.fastq --model illumina-like --seed 7   (or --config channel.json)
    vnx channel models | show NAME | convert old.json new.json | sweep strands.fasta --model M --grid P=[..]
    vnx decode reads.fastq -o recovered.vnx      reads → verified container (or --extract DIR)
    vnx validate strands.fasta                   biological constraint diagnostics (JSON)
    vnx benchmark --profile balanced             machine-readable benchmark suite
    vnx experiment run experiments/EXP-0001/config.json      (also writes manifest.json, vnx.experiment/1)
    vnx experiment reproduce manifest.json       re-run a vnx.experiment/1 manifest; exit 0 iff result_hash matches
    vnx conformance                              run the conformance vectors (spec §6)
    vnx experimental ...                         experimental features (clearly separated)

Each command parses its arguments, makes one SDK call and prints JSON: the ``vnx.result/1`` envelope with the command's
5.x top-level fields mirrored next to it (``vnx version`` prints ``vnx.version/1``). Errors go to stderr as
``vnx.error/1`` (plus the 5.x keys). Exit codes: 0 success, 1 verification failed, 3 invalid input, 4 key/authentication,
5 insufficient redundancy, 6 unsupported format, 7 configuration, 8 output, 9 PARTIAL recovery, 70 internal error
(10 is reserved for provider errors). The V3 tool (``vnx-dna``, format 5) is unchanged.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer

from vnxdna import sdk
from vnxdna.core.errors import VNXConfigurationError, VNXError, VNXOutputError

app = typer.Typer(add_completion=False, no_args_is_help=True, help="VNX-DNA V4: DNA data-storage software (simulation only).")
channel_app = typer.Typer(no_args_is_help=True, help="Simulated DNA storage/sequencing channel.")
experiment_app = typer.Typer(no_args_is_help=True, help="Reproducible experiments.")
experimental_app = typer.Typer(no_args_is_help=True, help="EXPERIMENTAL features: measured, not guaranteed; may change.")
app.add_typer(channel_app, name="channel")
app.add_typer(experiment_app, name="experiment")
app.add_typer(experimental_app, name="experimental")

EXIT_PARTIAL = 9
STATE = {"verbose": False, "progress": False}


def _emit(obj: dict, human: str | None = None, as_json: bool = True) -> None:
    if as_json or human is None:
        typer.echo(json.dumps(obj, indent=2, sort_keys=True, default=str))
    else:
        typer.echo(human)


def _out(res: sdk.Result, human: str | None = None, as_json: bool = True) -> None:
    _emit(res.to_cli(), human, as_json)


def _progress_cb():
    if not STATE["progress"]:
        return None
    last = [0.0]

    def cb(info: dict) -> None:
        now = time.perf_counter()
        if now - last[0] < 0.5:
            return
        last[0] = now
        parts = [f"{k}={v:.1f}" if isinstance(v, float) else f"{k}={v}" for k, v in info.items()]
        sys.stderr.write("\r[vnx] " + " ".join(parts) + " " * 8)
        sys.stderr.flush()
    return cb


def _run(fn):
    try:
        code = fn()
        if STATE["progress"]:
            sys.stderr.write("\n")
        raise typer.Exit(code or 0)
    except VNXError as error:
        typer.echo(json.dumps(sdk.error_json(error), indent=2, default=str), err=True)
        raise typer.Exit(error.exit_code)
    except (typer.Exit, typer.Abort):
        raise
    except KeyboardInterrupt:
        typer.echo("vnx: interrupted", err=True)
        raise typer.Exit(130)
    except Exception as error:  # noqa: BLE001 - mapped to the stable contract
        from vnxdna.core.taxonomy import VNXDNAError
        if isinstance(error, VNXDNAError):
            typer.echo(json.dumps(sdk.error_json(error), indent=2, default=str), err=True)
            raise typer.Exit(error.exit_code)
        if STATE["verbose"]:
            raise
        typer.echo(json.dumps(sdk.error_json(error), default=str), err=True)
        raise typer.Exit(70)


KEY_OPT = typer.Option(None, "--key-file", "-k", help="32-byte key file (raw, 64 hex or 44 base64 characters).")
PW_OPT = typer.Option(None, "--passphrase-env", help="Name of an environment variable holding a passphrase (scrypt).")
JSON_OPT = typer.Option(True, "--json/--human", help="Machine-readable JSON (default) or a short human summary.")
UNENC_OPT = typer.Option(False, "--allow-unencrypted",
                         help="Accept an unencrypted archive although a key or passphrase was given (refused by default).")
FORCE_OPT = typer.Option(False, "--force", "-f", help="Overwrite existing outputs.")
CONFIG_OPT = typer.Option(None, "--config", "-c", help="JSON configuration file (sections archive/dna/constraints/channel/decode).")
PERF_OPT = typer.Option(None, "--performance", help="Performance profile: safe, balanced, maximum-throughput.")
V6_STRIPE = typer.Option(None, "--stripe-depth", help="V6 (opt-in): data groups per stripe (0 = automatic).")
V6_COLUMN = typer.Option(None, "--column-parity", help="V6 (opt-in): column-parity groups per stripe.")
V6_ORDER = typer.Option(None, "--strand-order", help="V6 (opt-in): sequential or interleaved.")
V6_PLAN = typer.Option(None, "--outer-plan", help="V6 (opt-in): fixed (default) or adaptive.")
EXPECT_ID_OPT = typer.Option(None, "--expect-archive-id",
                             help="Refuse any other archive (32 hex characters; exit 1 ARCHIVE_MISMATCH).")
EXPECT_SHA_OPT = typer.Option(None, "--expect-sha256",
                              help="Refuse a container whose SHA-256 differs (64 hex characters; exit 1 ARCHIVE_MISMATCH).")


@app.callback()
def main_callback(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show tracebacks for internal errors."),
                  progress: bool = typer.Option(False, "--progress", help="Progress, stage, throughput on stderr.")) -> None:
    STATE["verbose"] = verbose
    STATE["progress"] = progress


@app.command()
def version() -> None:
    """Print every version axis (vnx.version/1): software, spec, container, frame, superblock, codecs, backends."""
    typer.echo(json.dumps(sdk.version()))


@app.command()
def native() -> None:
    """Show which backend (native C or NumPy reference) each kernel uses, which library is loaded and why not if it is not.

    The top-level fields describe the V5 marker aligner (unchanged since V5); ``kernels`` covers all three kernels."""
    _out(sdk.native())


@app.command()
def keygen(output: Path = typer.Argument(..., help="New key file (mode 0600; refuses to overwrite).")) -> None:
    """Generate a random 32-byte key file."""
    _run(lambda: _out(sdk.keygen(output)))


@app.command()
def archive(inputs: List[Path] = typer.Argument(..., help="Files and/or directories, then the output .vnx (last argument)."),
            chunk_size: int = typer.Option(1 << 20, help="Chunk size in bytes (4 KiB … 64 MiB)."),
            compression: str = typer.Option("zstd", help="zstd or none."), level: int = typer.Option(3, help="zstd level 1–22."),
            no_dedup: bool = typer.Option(False, "--no-dedup"), preserve_metadata: bool = typer.Option(False, "--preserve-metadata"),
            archive_id: str = typer.Option("options", "--archive-id",
                                           help="Unencrypted archive ID: options (options-v1, default) or content (content-v1)."),
            workers: int = typer.Option(1, "--workers", "-w"), performance: Optional[str] = PERF_OPT,
            key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT, force: bool = FORCE_OPT,
            config: Optional[Path] = CONFIG_OPT, as_json: bool = JSON_OPT) -> None:
    """Create a VNX4 archive: vnx archive ./dataset archive.vnx"""
    def go():
        from vnxdna.sdk.config import archive_options_for
        if len(inputs) < 2:
            raise VNXConfigurationError("usage: vnx archive INPUT... OUTPUT.vnx")
        key, pw = sdk.load_keys(key_file, passphrase_env)
        opts = archive_options_for(config, performance, key=key, passphrase=pw,
                                   chunk_size=chunk_size if chunk_size != 1 << 20 else None, workers=workers,
                                   compression=compression, level=level, dedup=not no_dedup, preserve_metadata=preserve_metadata,
                                   archive_id=archive_id)
        res = sdk.archive(list(inputs[:-1]), inputs[-1], options=opts, overwrite=force, progress=_progress_cb())
        d = res.body
        _out(res, f"archived {d['files']} entries, {d['content_bytes']:,} B → {d['container_bytes']:,} B ({d['output']})", as_json)
    _run(go)


@app.command()
def inspect(path: Path = typer.Argument(..., metavar="CONTAINER_OR_READS"), key_file: Optional[Path] = KEY_OPT,
            passphrase_env: Optional[str] = PW_OPT, allow_unencrypted: bool = UNENC_OPT,
            deep: bool = typer.Option(False, "--deep", help="Reads: also decode the superblock (one pass over the reads).")) -> None:
    """Show the manifest and structure of a VNX4 archive, or answer "can I read this?" for a read/strand file."""
    def go():
        key, pw = sdk.load_keys(key_file, passphrase_env)
        _out(sdk.inspect(path, deep=deep, key=key, passphrase=pw, allow_unencrypted=allow_unencrypted))
    _run(go)


@app.command("list")
def list_cmd(container: Path, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
             as_json: bool = JSON_OPT, allow_unencrypted: bool = UNENC_OPT) -> None:
    """List archive entries."""
    def go():
        key, pw = sdk.load_keys(key_file, passphrase_env)
        res = sdk.list_entries(container, key=key, passphrase=pw, allow_unencrypted=allow_unencrypted)
        _out(res, "\n".join(f"{r['size']:>14,}  {r['path']}" for r in res.body["entries"]), as_json)
    _run(go)


@app.command()
def verify(container: Path, chunk: Optional[int] = typer.Option(None, "--chunk", help="Verify one chunk by Merkle proof."),
           key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
           allow_unencrypted: bool = UNENC_OPT) -> None:
    """Verify an archive (trailer, manifest, Merkle root, every chunk and file hash) or one chunk."""
    def go():
        key, pw = sdk.load_keys(key_file, passphrase_env)
        _out(sdk.verify(container, key=key, passphrase=pw, chunk=chunk, allow_unencrypted=allow_unencrypted))
    _run(go)


@app.command()
def locate(container: Path, name: str, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
           profile: Optional[str] = typer.Option(None, "--dna-profile",
                                                 help="Also give strand groups/records for this strand or redundancy profile."),
           data_symbols: Optional[int] = typer.Option(None, "-K"), parity_symbols: Optional[int] = typer.Option(None, "-M"),
           stripe_depth: Optional[int] = V6_STRIPE, column_parity: Optional[int] = V6_COLUMN,
           strand_order: Optional[str] = V6_ORDER, outer_plan: Optional[str] = V6_PLAN,
           allow_unencrypted: bool = UNENC_OPT) -> None:
    """Locate a file: chunk indices, container byte ranges and (with --dna-profile) strand groups and strand records.

    Give the same DNA options as `vnx encode` (V4/V5 layout or V6 outer code): the records are those of that strand file."""
    def go():
        key, pw = sdk.load_keys(key_file, passphrase_env)
        explicit = {k: v for k, v in (("data_symbols", data_symbols), ("parity_symbols", parity_symbols),
                                      ("stripe_depth", stripe_depth), ("column_parity", column_parity),
                                      ("strand_order", strand_order), ("outer_plan", outer_plan)) if v is not None}
        dna = None
        if explicit:
            from vnxdna.sdk.config import encode_options
            redundancy = profile if profile in sdk.profiles().body["redundancy"] else None
            dna, _ = encode_options(None, None, redundancy_profile=redundancy,
                                    **({} if redundancy else {"profile": profile}), **explicit)
        _out(sdk.locate(container, name, dna_profile=profile, dna=dna, key=key, passphrase=pw,
                        allow_unencrypted=allow_unencrypted))
    _run(go)


@app.command()
def extract(container: Path, output_dir: Path, names: Optional[List[str]] = typer.Option(None, "--file", help="Extract only these."),
            key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT, force: bool = FORCE_OPT,
            apply_metadata: bool = typer.Option(False, "--apply-metadata"), allow_unencrypted: bool = UNENC_OPT,
            expect_archive_id: Optional[str] = EXPECT_ID_OPT, expect_sha256: Optional[str] = EXPECT_SHA_OPT) -> None:
    """Extract (every file verified before it is renamed into place)."""
    def go():
        key, pw = sdk.load_keys(key_file, passphrase_env)
        _out(sdk.extract(container, output_dir, files=names or None, key=key, passphrase=pw, overwrite=force,
                         apply_metadata=apply_metadata, allow_unencrypted=allow_unencrypted,
                         expect_archive_id=expect_archive_id, expect_sha256=expect_sha256))
    _run(go)


@app.command()
def encode(source: Path = typer.Argument(..., help="A .vnx container, or a file/directory (archived first)."),
           output: Path = typer.Argument(..., help="Strand file (.fasta or .fastq)."),
           profile: Optional[str] = typer.Option(None, help="v4-balanced (default), v4-dense, v4-indel, v4-archival, v6-high-dropout."),
           outer_code: Optional[str] = typer.Option(None, help="cauchy-rs (default)."),
           data_symbols: Optional[int] = typer.Option(None, "-K"), parity_symbols: Optional[int] = typer.Option(None, "-M"),
           workers: int = typer.Option(0, "--workers", "-w", help="0 = from the performance profile."),
           performance: Optional[str] = PERF_OPT, config: Optional[Path] = CONFIG_OPT, force: bool = FORCE_OPT,
           keep_archive: Optional[Path] = typer.Option(None, help="When SOURCE is not a container: also keep the .vnx here."),
           key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
           stripe_depth: Optional[int] = V6_STRIPE, column_parity: Optional[int] = V6_COLUMN,
           strand_order: Optional[str] = V6_ORDER, outer_plan: Optional[str] = V6_PLAN,
           redundancy_budget: Optional[float] = typer.Option(None, "--redundancy-budget",
                                                             help="V6 adaptive plan: max redundant strands per data strand."),
           redundancy_profile: Optional[str] = typer.Option(
               None, "--redundancy-profile",
               help="V6 preset: maximum-density, balanced, maximum-recovery or high-dropout (explicit options override it)."),
           chunk_size: Optional[int] = typer.Option(None, "--chunk-size", help="Archive option (SOURCE not a container)."),
           compression: Optional[str] = typer.Option(None, "--compression", help="Archive option: zstd or none."),
           level: Optional[int] = typer.Option(None, "--level", help="Archive option: zstd level 1–22."),
           no_dedup: bool = typer.Option(False, "--no-dedup", help="Archive option: no chunk deduplication."),
           preserve_metadata: bool = typer.Option(False, "--preserve-metadata", help="Archive option."),
           allow_unencrypted: bool = UNENC_OPT) -> None:
    """Encode a VNX4 container (or files) into DNA strands. Archive options are passed to the archive builder when SOURCE
    is not a container, and refused (exit 7) when it is one."""
    def go():
        from vnxdna.sdk.config import archive_options_for, encode_options
        opts, perf = encode_options(config, performance, redundancy_profile=redundancy_profile, workers=workers,
                                    profile=profile, outer_code=outer_code, data_symbols=data_symbols,
                                    parity_symbols=parity_symbols, stripe_depth=stripe_depth, column_parity=column_parity,
                                    strand_order=strand_order, outer_plan=outer_plan, redundancy_budget=redundancy_budget)
        if opts.outer_code != "cauchy-rs":
            raise VNXConfigurationError("non-default outer codes are experimental: use `vnx experimental encode`")
        key, pw = sdk.load_keys(key_file, passphrase_env)
        given = {"chunk_size": chunk_size, "compression": compression, "level": level,
                 "dedup": False if no_dedup else None, "preserve_metadata": True if preserve_metadata else None}
        aopts = None
        if any(v is not None for v in given.values()):
            aopts = archive_options_for(config, None, key=key, passphrase=pw, workers=opts.workers,
                                        **{k: v for k, v in given.items() if k != "chunk_size"}, chunk_size=chunk_size)
        _out(sdk.encode(source, output, dna=opts, archive_options=aopts, key=key, passphrase=pw,
                        verify=bool(perf.get("verify_after_encode")), overwrite=force, keep_archive=keep_archive,
                        progress=_progress_cb(), allow_unencrypted=allow_unencrypted))
    _run(go)


def _same_file(a: Path, b: Path) -> bool:
    """True when two paths name one file: the same inode (hardlinks included) or, if absent, the same resolved path."""
    try:
        sa, sb = os.stat(a), os.stat(b)
        if (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino):
            return True
    except OSError:
        pass
    return os.path.realpath(a) == os.path.realpath(b)


def _check_side_files(reads, key_file, output, report, events, force) -> None:
    """Refuse a --report / --events path that is a symlink or names an input or output of this decode (checked before
    anything is opened or decoded), and an existing report without --force."""
    for opt, path in (("--report", report), ("--events", events)):
        if path is None:
            continue
        if os.path.islink(path):
            raise VNXConfigurationError(f"{opt} {path} is a symlink; refusing to follow it")
        others = {"reads": reads, "key file": key_file, "output": output,
                  "report": report if opt == "--events" else None}
        for what, other in others.items():
            if other is not None and _same_file(Path(path), Path(other)):
                raise VNXConfigurationError(f"{opt} {path} is the same file as the {what}")
    if report is not None and os.path.lexists(report) and not force:
        raise VNXOutputError(f"report already exists: {report} (use --force to overwrite)")


def _decode(reads, output, extract_dir, partial_dir, select, opts, force, key_file, passphrase_env, report, events, task_id,
            allow_unencrypted, input_hash):
    _check_side_files(reads, key_file, output, report, events, force)
    key, pw = sdk.load_keys(key_file, passphrase_env)
    if output is None and extract_dir is None and not select:
        raise VNXConfigurationError("give --output (container), --extract DIR, or --select FILE --extract DIR")
    jsonl = cmd = None
    observer = None
    last: dict = {}
    if events is not None:
        import secrets
        from vnxdna.core.observe import Events, JsonlObserver, RunStamp
        from vnxdna.core.version import SPEC_VERSION
        jsonl = JsonlObserver(events)
        stamp = RunStamp(jsonl)
        task_id = task_id or secrets.token_hex(6)

        def observer(event: dict) -> None:
            last["event"] = event["event"]
            stamp(event)
        cmd = Events(observer, task_id)
        cmd.emit("run_start", "command", software=sdk.envelope.SOFTWARE, spec=SPEC_VERSION,
                 provenance=sdk.provenance(), inputs=[sdk.envelope.file_ref("reads", reads)])
    try:
        res = sdk.decode(reads, output, options=opts, select=select or None, extract_to=extract_dir, partial_dir=partial_dir,
                         key=key, passphrase=pw, allow_unencrypted=allow_unencrypted, overwrite=force, observer=observer,
                         task_id=task_id, progress=_progress_cb(), input_hash=input_hash)
        report_sha = None
        if report:
            from vnxdna.core.util import atomic_output
            text = json.dumps(res.to_cli("vnx.decode-report/1"), indent=2, sort_keys=True, default=str) + "\n"
            with atomic_output(report, overwrite=force, mode=0o600) as tmp:
                tmp.write_text(text)
            import hashlib
            report_sha = hashlib.sha256(text.encode()).hexdigest()
        _out(res)
        code = 0 if res.status == "SUCCESS" else EXIT_PARTIAL if res.status == "PARTIAL" else 5
        if cmd is not None:
            cmd.emit("command_end", "command", exit_code=code, status=res.status, extract=res.body.get("extract"),
                     report_sha256=report_sha, inputs=list(res.inputs))
        return code
    except BaseException as error:
        if cmd is not None:
            code = 130 if isinstance(error, KeyboardInterrupt) else getattr(error, "exit_code", 70)
            if last.get("event") != "error":        # the decoder already recorded its own failure
                cmd.emit("error", getattr(error, "stage", "command"), error_class=type(error).__name__,
                         message=str(error)[:500], code=getattr(error, "code", "INTERNAL_ERROR"))
            cmd.emit("command_end", "command", exit_code=code, status="FAILED", error_class=type(error).__name__,
                     report_sha256=None)
        raise
    finally:
        if jsonl is not None:
            jsonl.close()


@app.command()
def decode(reads: Path, output: Optional[Path] = typer.Option(None, "--output", "-o", help="Recovered .vnx container."),
           extract_dir: Optional[Path] = typer.Option(None, "--extract", help="Also extract the verified files here."),
           partial_dir: Optional[Path] = typer.Option(None, "--partial-dir", help="On PARTIAL recovery, extract verified files here."),
           select: Optional[List[str]] = typer.Option(None, "--select", help="Random access: decode only these files."),
           profile: Optional[str] = typer.Option(None, help="Layout profile (default: detected from read lengths)."),
           workers: int = typer.Option(0, "--workers", "-w"), performance: Optional[str] = PERF_OPT,
           config: Optional[Path] = CONFIG_OPT, band: Optional[int] = typer.Option(None, help="Max net indel drift per read."),
           retry_band: Optional[int] = typer.Option(
               None, "--retry-band",
               help="Opt-in: align reads beyond --band (net drift up to this) with this wider band; 0 = off (default)."),
           min_quality: Optional[int] = typer.Option(None, help="Phred below this → erasure."),
           archive_tag: Optional[str] = typer.Option(None, help="Hex archive tag when a pool holds several archives."),
           force: bool = FORCE_OPT, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
           report: Optional[Path] = typer.Option(None, help="Write the JSON report here too."),
           indel_recovery: Optional[str] = typer.Option(None, "--indel-recovery",
                                                        help="segment (V4 default) or smart (V5 bounded local indel recovery)."),
           soft_decoding: Optional[str] = typer.Option(None, "--soft-decoding",
                                                       help="off (default), erasure (GMD), chase or auto: V5 bounded soft decoding."),
           recovery_schedule: Optional[str] = typer.Option(None, "--recovery-schedule",
                                                           help="deferred (default) or eager: when V5 per-read smart/soft recovery runs."),
           consensus_weighting: Optional[str] = typer.Option(
               None, "--consensus-weighting",
               help="count (V4 vote, default) or quality: pass-2 consensus weighted by Phred quality (opt-in)."),
           read_clustering: Optional[str] = typer.Option(
               None, "--read-clustering",
               help="off (default) or fallback: V7 header-independent read clustering + per-cluster consensus whose "
                    "verified frames only fill what the default path left unresolved (opt-in)."),
           max_recovery_reads: Optional[int] = typer.Option(None, "--max-recovery-reads",
                                                            help="V6 budget: reads examined by smart/soft recovery (all rounds)."),
           max_round_b_reads: Optional[int] = typer.Option(None, "--max-round-b-reads",
                                                           help="V6 budget: reads examined by the expensive round B."),
           max_outer_stripes: Optional[int] = typer.Option(None, "--max-outer-stripes",
                                                           help="V6 budget: stripes attempted by column recovery."),
           max_wall_seconds: Optional[float] = typer.Option(None, "--max-wall-seconds",
                                                            help="V6 budget: stop (nothing published) after this many seconds."),
           max_rss_mb: Optional[int] = typer.Option(None, "--max-rss-mb",
                                                    help="V6 budget: stop (nothing published) above this parent peak RSS."),
           events: Optional[Path] = typer.Option(None, "--events", help="Write structured decode events (JSON lines) here."),
           task_id: Optional[str] = typer.Option(None, "--task-id", help="Task ID recorded in every event."),
           allow_unencrypted: bool = UNENC_OPT,
           max_container_bytes: Optional[int] = typer.Option(
               None, "--max-container-bytes",
               help="Refuse a superblock claiming a larger container (default 4 GiB; exit 3 RESOURCE_LIMIT)."),
           expect_archive_id: Optional[str] = EXPECT_ID_OPT, expect_sha256: Optional[str] = EXPECT_SHA_OPT,
           no_input_hash: bool = typer.Option(False, "--no-input-hash", help="Do not compute the SHA-256 of the read file.")
           ) -> None:
    """Reconstruct a verified VNX4 container from DNA reads (FASTA/FASTQ)."""
    budget = {k: v for k, v in (("max_reads_examined", max_recovery_reads), ("max_round_b_reads", max_round_b_reads),
                                ("max_outer_stripes", max_outer_stripes), ("max_wall_seconds", max_wall_seconds),
                                ("max_rss_bytes", None if max_rss_mb is None else max_rss_mb << 20)) if v is not None}

    def go():
        from vnxdna.sdk.config import decode_options_for
        opts = decode_options_for(config, performance, workers=workers, archive_tag=archive_tag, budget=budget,
                                  profile=profile, band=band, retry_band=retry_band, min_quality=min_quality, indel_recovery=indel_recovery,
                                  soft_decoding=soft_decoding, recovery_schedule=recovery_schedule,
                                  consensus_weighting=consensus_weighting, max_container_bytes=max_container_bytes, expect_archive_id=expect_archive_id,
                                  expect_sha256=expect_sha256, read_clustering=read_clustering)
        return _decode(reads, output, extract_dir, partial_dir, select, opts, force, key_file, passphrase_env, report, events,
                       task_id, allow_unencrypted, not no_input_hash)
    _run(go)


@app.command()
def validate(sequences: Path, config: Optional[Path] = typer.Option(None, "--constraints", help="Constraint JSON."),
             gc_min: Optional[int] = typer.Option(None), gc_max: Optional[int] = typer.Option(None),
             max_homopolymer: Optional[int] = typer.Option(None), motif: Optional[List[str]] = typer.Option(None, "--forbid"),
             max_reported: int = typer.Option(100)) -> None:
    """Check DNA sequences against configurable biological constraints (JSON diagnostics; exit 3 if any violate)."""
    def go():
        res = sdk.validate_strands(sequences, constraints=config, forbid=motif, max_reported=max_reported,
                                   overrides={"gc_min_percent": gc_min, "gc_max_percent": gc_max,
                                              "max_homopolymer": max_homopolymer})
        _out(res)
        return 0 if res.body["valid"] else 3
    _run(go)


MODEL_OPT = typer.Option(None, "--model", "-m",
                         help="Channel model: a shipped NAME, NAME@VERSION, or a model JSON (vnx.channel-model/1 or /0).")


@channel_app.command("simulate")
def channel_simulate(strands: Path, output: Path, config: Optional[Path] = typer.Option(None, "--config", "-c",
                     help="V4 ChannelConfig JSON (vnx.channel-config/0); a channel-model file is also accepted."),
                     model: Optional[str] = MODEL_OPT,
                     seed: Optional[int] = typer.Option(None), coverage: Optional[float] = typer.Option(None),
                     substitution_rate: Optional[float] = typer.Option(None), insertion_rate: Optional[float] = typer.Option(None),
                     deletion_rate: Optional[float] = typer.Option(None), dropout_rate: Optional[float] = typer.Option(None),
                     param: List[str] = typer.Option([], "--param", "-p",
                                                     help="PATH=JSON parameter change, e.g. sequencing.substitution.rate=0.01."),
                     metadata: Optional[Path] = typer.Option(None, "--metadata",
                                                             help="Also write the vnx.simulation-metadata/1 JSON here."),
                     manifest: Optional[Path] = typer.Option(None, "--manifest",
                                                             help="Also write the vnx.experiment/1 manifest here "
                                                                  "(re-run with `vnx experiment reproduce`)."),
                     experiment_id: Optional[str] = typer.Option(None, "--experiment-id",
                                                                 help="experiment_id recorded in --manifest."),
                     workers: int = typer.Option(1, "--workers", "-w"), force: bool = FORCE_OPT) -> None:
    """Simulate synthesis, storage, amplification and sequencing (SIMULATION): strands → reads (FASTQ/FASTA)."""
    def go():
        overrides = {"substitution_rate": substitution_rate, "insertion_rate": insertion_rate,
                     "deletion_rate": deletion_rate, "dropout_rate": dropout_rate, **_params(param)}
        _out(sdk.simulate(strands, output, config=config, model=model, seed=seed, coverage=coverage, workers=workers,
                          overwrite=force, progress=_progress_cb(), overrides=overrides, metadata=metadata,
                          manifest=manifest, experiment_id=experiment_id))
    _run(go)


def _params(items: List[str]) -> dict:
    out = {}
    for item in items:
        path, sep, text = item.partition("=")
        if not sep or not path:
            raise VNXConfigurationError(f"--param expects PATH=VALUE, got {item!r}")
        try:
            out[path.strip()] = json.loads(text)
        except ValueError:
            raise VNXConfigurationError(f"--param {path}: the value must be JSON (e.g. 0.01, \"poisson\", [1, 2])") from None
    return out


@channel_app.command("models")
def channel_models() -> None:
    """List the shipped channel models (vnx.channel-model/1; every model is SIMULATED)."""
    _run(lambda: _out(sdk.channel_models()))


@channel_app.command("show")
def channel_show(model: str, schema: str = typer.Option("vnx.channel-model/1", "--schema",
                                                        help="vnx.channel-model/1 (default) or vnx.channel-model/0.")) -> None:
    """Print one channel model as its canonical document."""
    _run(lambda: _out(sdk.channel_model(model, schema=schema)))


@channel_app.command("convert")
def channel_convert(source: Path, output: Path, force: bool = FORCE_OPT) -> None:
    """Convert a /0 model or a ChannelConfig JSON to a canonical vnx.channel-model/1 file."""
    _run(lambda: _out(sdk.channel_convert(source, output, overwrite=force)))


@channel_app.command("sweep")
def channel_sweep(strands: Path, out_dir: Path = typer.Option(..., "--out-dir", help="Directory for the trial reads."),
                  model: Optional[str] = MODEL_OPT,
                  config: Optional[Path] = typer.Option(None, "--config", "-c", help="V4 ChannelConfig JSON instead of --model."),
                  grid: List[str] = typer.Option([], "--grid", "-g",
                                                 help="PATH=JSON-list sweep axis, e.g. sequencing.substitution.rate=[0.001,0.01]."),
                  trials: int = typer.Option(10, "--trials", "-n"), base_seed: int = typer.Option(0, "--base-seed"),
                  keep_reads: bool = typer.Option(False, "--keep-reads"),
                  output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the vnx.channel-sweep/1 JSON here."),
                  workers: int = typer.Option(1, "--workers", "-w")) -> None:
    """Monte Carlo runs (seeds base-seed + i) over a parameter grid; realised rates per point (SIMULATED)."""
    _run(lambda: _out(sdk.channel_sweep(strands, out_dir, model=model, config=config, grid=_params(grid), trials=trials,
                                        base_seed=base_seed, workers=workers, keep_reads=keep_reads, output=output)))


@app.command()
def benchmark(profile: str = typer.Option("balanced", "--profile", help="safe, balanced or maximum-throughput."),
              size: List[str] = typer.Option(["1MB"], "--size"), output: Optional[Path] = typer.Option(None, "--output", "-o"),
              human: bool = typer.Option(False, "--human")) -> None:
    """Run the benchmark suite (stage throughput + clean/noisy end-to-end); JSON (+ .md next to --output)."""
    def go():
        res = sdk.benchmark(profile, tuple(sdk.parse_size(s) for s in size), output)
        if human:
            typer.echo(sdk.benchmark_markdown(res.body["results"]))
        else:
            _out(res)
    _run(go)


@app.command()
def sweep(config: Path, output: Optional[Path] = typer.Option(None, "--output", "-o")) -> None:
    """Run an error sweep (experiment config with a "sweep" section); prints the recovery curve."""
    def go():
        res = sdk.sweep(config, progress=_progress_cb())
        if output:
            output.write_text(json.dumps(res.body, indent=2, sort_keys=True) + "\n")
        typer.echo(sdk.sweep_table(res.body))
    _run(go)


@experiment_app.command("run")
def experiment_run(config: Path) -> None:
    """Run an experiment; writes environment.json, seed.json, input.sha256 and results.json next to the config."""
    _run(lambda: _out(sdk.experiment_run(config, progress=_progress_cb())))


@experiment_app.command("reproduce")
def experiment_reproduce(target: Path = typer.Argument(..., metavar="MANIFEST_OR_DIR",
                                                      help="A vnx.experiment/1 manifest file, or an experiment directory."),
                         input_path: Optional[Path] = typer.Option(None, "--input",
                                                                   help="Manifest only: the strand file to use instead of the recorded path."),
                         workers: Optional[int] = typer.Option(None, "--workers", "-w",
                                                               help="Manifest only: worker processes (results do not depend on it).")) -> None:
    """Re-run an experiment: a manifest (compare result_hash) or a directory (compare all deterministic results).
    Exit 0 reproduced, 1 not reproduced (input or result hash differs), 3 malformed manifest or missing input,
    6 unsupported manifest schema."""
    def go():
        res = sdk.experiment_reproduce(target, input_path=input_path, workers=workers)
        _out(res)
        return 0 if res.body["reproduced"] else 1
    _run(go)


@app.command()
def generate(output: Path, size: str = typer.Option("1MB"), pattern: str = typer.Option("mixed"), seed: int = typer.Option(42)) -> None:
    """Generate deterministic test data (random, text, repetitive, binary, mixed)."""
    _run(lambda: _out(sdk.generate(output, size, pattern, seed)))


@app.command()
def profiles() -> None:
    """List strand layout profiles, performance profiles and redundancy profiles."""
    _out(sdk.profiles())


@app.command()
def conformance(vectors: Optional[Path] = typer.Option(None, "--vectors", help="Vector directory (default: tests/conformance of a source checkout, else the packaged subset)."),
                select: Optional[List[str]] = typer.Option(None, "--select", help="Only these vector IDs."),
                backend: str = typer.Option("auto", "--backend", help="auto, native or reference.")) -> None:
    """Run conformance vectors (spec §6): exit 0 if CONFORMANT, 1 otherwise."""
    def go():
        res = sdk.conformance(vectors, select=select or None, backend=backend)
        _out(res)
        return 0 if res.body["verdict"] == "CONFORMANT" else 1
    _run(go)


# ---------------------------------------------------------------- experimental
@experimental_app.command("encode")
def experimental_encode(source: Path, output: Path, outer_code: str = typer.Option("lt-fountain"),
                        distribution: str = typer.Option("dense", help="dense or robust-soliton"),
                        profile: Optional[str] = typer.Option(None), data_symbols: Optional[int] = typer.Option(None, "-K"),
                        parity_symbols: Optional[int] = typer.Option(None, "-M"), workers: int = typer.Option(1, "--workers", "-w"),
                        force: bool = FORCE_OPT) -> None:
    """EXPERIMENTAL: encode with a non-default outer code (e.g. the GF(2) fountain code)."""
    def go():
        opts = sdk.DNAOptions(profile=profile or "v4-balanced", outer_code=outer_code, lt_distribution=distribution,
                              data_symbols=data_symbols, parity_symbols=parity_symbols, workers=workers, experimental=True)
        res = sdk.encode(source, output, dna=opts, overwrite=force)
        res.body["stability"] = "EXPERIMENTAL"
        _out(res)
    _run(go)


@experimental_app.command("codec-compare")
def experimental_codec_compare(trials: int = typer.Option(100), output: Optional[Path] = typer.Option(None, "--output", "-o")) -> None:
    """EXPERIMENTAL: compare outer codes at the same redundancy budget under i.i.d. loss."""
    def go():
        res = sdk.codec_compare(trials=trials)
        if output:
            output.write_text(json.dumps(res.body, indent=2) + "\n")
        typer.echo(sdk.benchmark_markdown(res.body))
    _run(go)


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
