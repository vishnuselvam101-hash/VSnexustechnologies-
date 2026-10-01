"""VNX-DNA V1 command-line interface (archive format 4), kept unchanged and mounted as ``vnx-dna v1``.

The V2 CLI is :mod:`vnxdna.cli`. This module is the V1 CLI exactly as released in 1.0.0; it writes format-4
archives for users who need them. V2 reads format 4 through the main commands as well.

Workflow::

    vnx-dna store    FILE      -o archive.vxdna     # compress, encrypt, chunk, manifest
    vnx-dna encode   archive.vxdna -o pool.fasta    # redundancy + ECC + DNA strands
    vnx-dna simulate pool.fasta -o reads.fasta      # optional synthetic channel damage
    vnx-dna decode   reads.fasta -o archive.vxdna   # DNA → container (byte-identical)
    vnx-dna restore  archive.vxdna -o FILE          # container (or reads) → verified file
    vnx-dna verify   archive.vxdna|reads.fasta      # independent integrity report

Exit codes are stable and documented in ``docs/CLI.md`` and :mod:`vnxdna.errors`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Optional

import typer

from . import __version__, api
from .channel import ChannelConfig
from .container import crypto
from .container.builder import StoreOptions
from .dna.constraints import ConstraintSpec
from .errors import EXIT_CODES, EXIT_INTERNAL, ConfigurationError, InvalidInputError, OutputError, VNXDNAError
from .storage.decoder import DecodeOptions

HELP = """VNX-DNA: computational DNA data storage (software simulation; not wet-lab validated).

\b
Typical workflow:
  vnx-dna store input.bin -o archive.vxdna
  vnx-dna encode archive.vxdna -o pool.fasta
  vnx-dna simulate pool.fasta -o reads.fasta --dropout-rate 0.05 --substitution-rate 0.001 --seed 42
  vnx-dna decode reads.fasta -o recovered.vxdna
  vnx-dna restore recovered.vxdna -o recovered.bin
  vnx-dna verify recovered.vxdna
\b
One-shot research run:  vnx-dna pipeline input.bin -o recovered.bin --dropout-rate 0.02
Encryption:             vnx-dna keygen -o key.txt ; then add --key-file key.txt
\b
Exit codes: 0 ok, 1 verification failed, 2 usage, 3 invalid input, 4 authentication,
5 insufficient redundancy, 6 unsupported format, 7 configuration, 8 output, 70 internal.
"""

app = typer.Typer(help=HELP, no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False,
                  context_settings={"help_option_names": ["-h", "--help"]})
legacy_app = typer.Typer(help="Read-only access to V0.1 archives (datasets and RD-1 JSON archives).", no_args_is_help=True)
app.add_typer(legacy_app, name="legacy")

# ---------------------------------------------------------------- shared options
KeyFile = typer.Option(None, "--key-file", "-k", help="File containing a 32-byte key (from `vnx-dna keygen`). "
                       "Defaults to the VNXDNA_KEY environment variable.", dir_okay=False)
JsonOut = typer.Option(False, "--json", help="Print the full machine-readable JSON report.")
ReportOut = typer.Option(None, "--report", help="Also write the JSON report to this file.", dir_okay=False)
Force = typer.Option(False, "--force", "-f", help="Overwrite existing output files.")
IndelRepair = typer.Option(False, "--experimental-indel-repair", help="EXPERIMENTAL: try to resynchronize reads whose "
                           "length is off by 1..--max-indel bases (slow; see docs/CHANNEL_MODEL.md).")
MaxIndel = typer.Option(1, "--max-indel", min=1, max=3, help="Largest length difference tried by indel repair.")


def _key(key_file: Optional[Path]) -> bytes | None:
    if key_file is not None:
        try:
            text = Path(key_file).read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError) as error:
            raise InvalidInputError(f"cannot read key file {key_file}: {getattr(error, 'strerror', None) or error}") from None
        return crypto.parse_key(text)
    env = os.environ.get("VNXDNA_KEY")
    return crypto.parse_key(env) if env else None


def _decode_options(indel: bool, max_indel: int) -> DecodeOptions:
    return DecodeOptions(indel_repair=indel, max_indel=max_indel)


def _emit(report: dict[str, Any], as_json: bool, report_path: Optional[Path], summary: list[str]) -> None:
    if report_path is not None:
        try:
            Path(report_path).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        except OSError as error:
            raise OutputError(f"cannot write report {report_path}: {error.strerror or error}") from None
    if as_json:
        typer.echo(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        for line in summary:
            typer.echo(line)


def _run(fn, *args, **kwargs) -> None:
    """Execute a command body with the structured error contract (no raw tracebacks)."""
    try:
        fn(*args, **kwargs)
    except VNXDNAError as error:
        typer.echo(f"vnx-dna: error [{error.category}]: {error}", err=True)
        if error.details and os.environ.get("VNXDNA_DEBUG"):
            typer.echo(json.dumps(error.details, indent=2, default=str), err=True)
        raise typer.Exit(error.exit_code)
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        typer.echo("vnx-dna: interrupted", err=True)
        raise typer.Exit(130)
    except MemoryError:
        typer.echo("vnx-dna: error [INTERNAL_ERROR]: out of memory", err=True)
        raise typer.Exit(EXIT_INTERNAL)
    except Exception as error:  # noqa: BLE001 - last-resort guard: report, never dump a traceback by default
        typer.echo(f"vnx-dna: error [INTERNAL_ERROR]: unexpected {type(error).__name__}: {error} "
                   "(this is a bug; set VNXDNA_DEBUG=1 for a traceback)", err=True)
        if os.environ.get("VNXDNA_DEBUG"):
            traceback.print_exc()
        raise typer.Exit(EXIT_INTERNAL)


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "hidden (encrypted)"
    return f"{n:,} B"


# ---------------------------------------------------------------- store
def _store_options(chunk_size, compression, level, data_shards, parity_shards, mapping, payload_bytes, inner_parity,
                   gc_min, gc_max, max_homopolymer, gc_window, forbid, no_name, timestamp, max_strand_nt) -> StoreOptions:
    if level is None:
        level = {"none": 0, "zlib": 6, "zstd": 9}.get(compression, 0)
    options = StoreOptions(chunk_size=chunk_size, compression=compression, compression_level=level, data_shards=data_shards,
                           parity_shards=parity_shards, mapping=mapping, payload_bytes=payload_bytes,
                           inner_parity_bytes=inner_parity,
                           constraints=ConstraintSpec(gc_min_percent=gc_min, gc_max_percent=gc_max, max_homopolymer=max_homopolymer,
                                                      gc_window_nt=gc_window, forbidden_motifs=tuple(forbid or ())),
                           store_name=not no_name, timestamp=timestamp)
    geometry = options.validate()
    if max_strand_nt is not None and geometry.strand_nt > max_strand_nt:
        raise ConfigurationError(f"strands would be {geometry.strand_nt} nt, above --max-strand-nt {max_strand_nt}; "
                                 "reduce --payload-bytes/--inner-parity or use a denser --mapping")
    return options


def _store_command(name: str):
    def command(
        input_file: Path = typer.Argument(..., help="File to archive.", exists=False, dir_okay=False),
        output: Path = typer.Option(..., "--output", "-o", help="Container file to create (.vxdna).", dir_okay=False),
        key_file: Optional[Path] = KeyFile,
        encrypt: Optional[bool] = typer.Option(None, "--encrypt/--no-encrypt", help="Encrypt with AES-256-GCM. "
                                               "Default: encrypt iff a key is available."),
        chunk_size: int = typer.Option(256 * 1024, "--chunk-size", help="Plaintext bytes per independently recoverable chunk."),
        compression: str = typer.Option("zstd", "--compression", help="none | zlib | zstd"),
        level: Optional[int] = typer.Option(None, "--level", help="Compression level (zlib 0-9, zstd 1-22)."),
        data_shards: int = typer.Option(64, "--data-shards", help="Outer code K: data strands per stripe."),
        parity_shards: int = typer.Option(16, "--parity-shards", help="Outer code M: parity strands per stripe "
                                          "(any M lost strands per stripe are recoverable)."),
        mapping: str = typer.Option("2bit", "--mapping", help="2bit (2 bits/nt) | rotation3 (no homopolymers) | codebook8 (50% GC words)"),
        payload_bytes: int = typer.Option(40, "--payload-bytes", help="Payload bytes per strand."),
        inner_parity: int = typer.Option(8, "--inner-parity", help="Inner RS parity bytes per strand (even; corrects inner/2 byte errors)."),
        gc_min: int = typer.Option(40, "--gc-min", help="Minimum GC content per strand (%)."),
        gc_max: int = typer.Option(60, "--gc-max", help="Maximum GC content per strand (%)."),
        max_homopolymer: int = typer.Option(4, "--max-homopolymer", help="Maximum homopolymer run (0 disables)."),
        gc_window: int = typer.Option(0, "--gc-window", help="Also enforce GC bounds in every window of this many nt (0 = off)."),
        forbid: Optional[list[str]] = typer.Option(None, "--forbid-motif", help="Forbidden motif (repeatable; reverse complement also checked)."),
        max_strand_nt: Optional[int] = typer.Option(None, "--max-strand-nt", help="Fail if strands would exceed this length."),
        no_name: bool = typer.Option(False, "--no-name", help="Do not record the input file name."),
        timestamp: Optional[str] = typer.Option(None, "--timestamp", help="Record this creation time (omitted by default for determinism)."),
        force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut,
    ) -> None:
        def body() -> None:
            key = _key(key_file)
            if encrypt is True and key is None:
                raise ConfigurationError("--encrypt requires a key (--key-file or VNXDNA_KEY); create one with `vnx-dna keygen`")
            use_key = key if (encrypt is not False) else None
            options = _store_options(chunk_size, compression, level, data_shards, parity_shards, mapping, payload_bytes,
                                     inner_parity, gc_min, gc_max, max_homopolymer, gc_window, forbid, no_name, timestamp,
                                     max_strand_nt)
            r = api.store(input_file, output, options=options, key=use_key, overwrite=force)
            _emit(r, as_json, report_path, [
                f"stored {input_file} -> {output}",
                f"  archive id   {r['archive_id']}",
                f"  original     {r['original_bytes']:,} B  sha256 {r['original_sha256']}",
                f"  stored       {r['stored_bytes']:,} B in {r['chunks']} chunk(s), compression {r['compression']}",
                f"  encryption   {'AES-256-GCM' if r['encrypted'] else 'none (NOT encrypted; use --key-file for confidentiality)'}",
                f"next: vnx-dna encode {output} -o {Path(output).with_suffix('.fasta')}"])
        _run(body)
    command.__name__ = name
    command.__doc__ = ("Archive a file into a self-describing .vxdna container (compression, optional AES-256-GCM, "
                       "chunking, authenticated manifest). ECC and DNA parameters are fixed here." +
                       (" Alias of `store`." if name == "pack" else ""))
    return command


app.command("store")(_store_command("store"))
app.command("pack", hidden=False)(_store_command("pack"))


# ---------------------------------------------------------------- encode
@app.command("encode")
def encode_cmd(container: Path = typer.Argument(..., help="Container from `vnx-dna store`."),
               output: Path = typer.Option(..., "--output", "-o", help="FASTA file to write."),
               force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut) -> None:
    """Encode a container into DNA strands (outer Cauchy RS + per-strand CRC and inner RS + A/C/G/T mapping).

    FASTA headers are labels only. Every address and check is inside the DNA,
    and the manifest is also stored as metadata strands, so the FASTA alone is a
    complete archive. No key is needed.
    """
    def body() -> None:
        r = api.encode(container, output, overwrite=force)
        e = r["efficiency"]
        _emit(r, as_json, report_path, [
            f"encoded {container} -> {output}",
            f"  strands      {r['strands']:,} x {r['strand_nt']} nt = {e['dna_bases_total']:,} nt "
            f"({e['parity_strands']:,} parity, {e['metadata_strands']} metadata)",
            f"  outer code   {e['outer_code']}, {e['stripes']} stripe(s)",
            f"  density      {e['bases_per_original_byte']:.3f} nt per original byte" if e["bases_per_original_byte"] else
            "  density      n/a (original size hidden or empty)",
            f"next: vnx-dna simulate {output} -o damaged.fasta --dropout-rate 0.05 --seed 1   (optional)",
            f"      vnx-dna decode {output} -o decoded.vxdna"])
    _run(body)


# ---------------------------------------------------------------- simulate
@app.command("simulate")
def simulate_cmd(
    reads: Path = typer.Argument(..., help="Strand pool (FASTA from `vnx-dna encode`)."),
    output: Path = typer.Option(..., "--output", "-o", help="Damaged read file to write (FASTA)."),
    seed: int = typer.Option(0, "--seed", help="PRNG seed (numpy PCG64); same inputs + seed = same output."),
    substitution_rate: float = typer.Option(0.0, "--substitution-rate", help="Per-base substitution probability."),
    insertion_rate: float = typer.Option(0.0, "--insertion-rate", help="Per-base insertion probability."),
    deletion_rate: float = typer.Option(0.0, "--deletion-rate", help="Per-base deletion probability."),
    dropout_rate: float = typer.Option(0.0, "--dropout-rate", help="Per-strand loss probability (molecule dropout)."),
    exact_dropout: bool = typer.Option(False, "--exact-dropout", help="Drop exactly round(rate*N) strands."),
    burst_rate: float = typer.Option(0.0, "--burst-rate", help="Per-read probability of one burst error."),
    burst_min: int = typer.Option(2, "--burst-min", help="Minimum burst length (nt)."),
    burst_max: int = typer.Option(8, "--burst-max", help="Maximum burst length (nt)."),
    burst_kind: str = typer.Option("substitution", "--burst-kind", help="substitution | deletion | mixed"),
    coverage: float = typer.Option(1, "--coverage", help="Reads per surviving strand (fixed) or mean (poisson)."),
    coverage_model: str = typer.Option("fixed", "--coverage-model", help="fixed | poisson (poisson can yield zero reads)"),
    reverse_complement_rate: float = typer.Option(0.0, "--reverse-complement-rate", help="Probability a read is reverse-complemented."),
    shuffle: bool = typer.Option(True, "--shuffle/--no-shuffle", help="Shuffle read order."),
    events: Optional[Path] = typer.Option(None, "--events", help="Write every injected event (JSON lines)."),
    force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut,
) -> None:
    """Pass strands through a synthetic DNA channel. Reports the events that actually happened."""
    def body() -> None:
        config = ChannelConfig(seed=seed, substitution_rate=substitution_rate, insertion_rate=insertion_rate,
                               deletion_rate=deletion_rate, dropout_rate=dropout_rate, exact_dropout=exact_dropout,
                               burst_rate=burst_rate, burst_length_min=burst_min, burst_length_max=burst_max, burst_kind=burst_kind,
                               coverage=coverage, coverage_model=coverage_model, reverse_complement_rate=reverse_complement_rate,
                               shuffle=shuffle)
        r = api.simulate(reads, output, config, report_path=report_path, events_path=events, overwrite=force)
        c = r["channel"]
        _emit(r, as_json, None, [
            f"simulated {reads} -> {output} (seed {seed})",
            f"  strands      {c['strands_in']:,} in, {c['strands_dropped']:,} dropped (observed {c['observed_dropout_rate']:.4f}), "
            f"{c['strands_surviving']:,} surviving, {c['strands_with_zero_reads']:,} with zero reads",
            f"  reads        {c['reads_out']:,} out, {c['reads_altered']:,} altered, {c['reads_reverse_complemented']:,} reverse-complemented",
            f"  events       {c['substitutions']:,} substitutions, {c['insertions']:,} insertions, {c['deletions']:,} deletions, "
            f"{c['bursts']:,} bursts",
            f"  observed     sub {c['observed_substitution_rate']:.5f}  ins {c['observed_insertion_rate']:.5f}  "
            f"del {c['observed_deletion_rate']:.5f} per base"])
    _run(body)


# ---------------------------------------------------------------- decode / restore / recover
def _recovery_lines(r: dict[str, Any]) -> list[str]:
    lines = []
    reads = r.get("reads")
    if reads:
        lines.append(f"  reads        {reads.get('reads_total', 0):,} total, {reads.get('reads_valid', 0):,} valid "
                     f"({reads.get('reads_inner_corrected', 0):,} inner-corrected, {reads.get('reads_reverse_complement', 0):,} rev-comp, "
                     f"{reads.get('reads_indel_repaired', 0):,} indel-repaired), {reads.get('reads_rejected', 0):,} rejected, "
                     f"{reads.get('reads_length_mismatch', 0):,} wrong length")
    rec = r.get("recovery", {})
    if "stripes_decoded" in rec:
        lines.append(f"  outer code   {rec.get('stripes_decoded', 0):,} stripe(s), {rec.get('shards_erased', 0):,} strands erased, "
                     f"{rec.get('stripes_outer_recovered', 0):,} stripe(s) repaired, worst stripe lost {rec.get('max_erasures_in_a_stripe', 0)}")
    return lines


@app.command("decode")
def decode_cmd(reads: Path = typer.Argument(..., help="DNA reads (FASTA, FASTQ or one sequence per line)."),
               output: Path = typer.Option(..., "--output", "-o", help="Container file to write (.vxdna)."),
               indel: bool = IndelRepair, max_indel: int = MaxIndel,
               force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut) -> None:
    """Decode DNA reads back into the .vxdna container (inner RS, duplicate resolution, outer erasure decoding).

    No key is needed. Every stored chunk is checked against its SHA-256 before the
    container is written, so the output is either exact or not written at all.
    """
    def body() -> None:
        r = api.decode(reads, output, options=_decode_options(indel, max_indel), overwrite=force)
        _emit(r, as_json, report_path, [f"decoded {reads} -> {output}: {r['status']}", *_recovery_lines(r),
                                        f"  container    sha256 {r['container_sha256']}",
                                        f"next: vnx-dna restore {output} -o <file>"])
    _run(body)


def _restore_like(op: str):
    source_help = "Container (.vxdna) or DNA reads." if op == "restore" else "DNA reads (FASTA, FASTQ or plain)."

    def command(source: Path = typer.Argument(..., help=source_help),
                output: Path = typer.Option(..., "--output", "-o", help="File to write (only after full verification)."),
                key_file: Optional[Path] = KeyFile, indel: bool = IndelRepair, max_indel: int = MaxIndel,
                force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut) -> None:
        def body() -> None:
            fn = api.restore if op == "restore" else api.recover
            r = fn(source, output, key=_key(key_file), options=_decode_options(indel, max_indel), overwrite=force)
            _emit(r, as_json, report_path, [f"{op}d {source} -> {output}: {r['status']}", *_recovery_lines(r),
                                            f"  size         {r['size']:,} B",
                                            f"  sha256       {r['recovered_sha256']} (recomputed; matches manifest)",
                                            f"  auth         {r['manifest_authentication']}"])
        _run(body)
    command.__name__ = op
    command.__doc__ = ("Restore the original file from a container or DNA reads; decrypts, decompresses and recomputes SHA-256."
                       if op == "restore" else "Recover the original file directly from DNA reads (decode + restore in one step).")
    return command


app.command("restore")(_restore_like("restore"))
app.command("recover")(_restore_like("recover"))


# ---------------------------------------------------------------- verify / info / extract
@app.command("verify")
def verify_cmd(source: Path = typer.Argument(..., help="Container (.vxdna) or DNA reads."),
               key_file: Optional[Path] = KeyFile, indel: bool = IndelRepair, max_indel: int = MaxIndel,
               as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut) -> None:
    """Verify structure, authentication, ECC recovery and a freshly recomputed SHA-256. Writes nothing.

    Exit 0 = PASS, 1 = integrity failure, 4 = authentication failure, 5 = insufficient redundancy.
    """
    def body() -> None:
        r = api.verify(source, key=_key(key_file), options=_decode_options(indel, max_indel))
        lines = [f"verify {source}: {r['status']}" + (f" ({r.get('recovery_status')})" if r["status"] == "PASS" else "")]
        lines += [f"  [{c['result']}] {c['check']}" + (f": {c['detail']}" if c.get("detail") else "") for c in r["checks"]]
        if "expected_sha256" in r:
            lines.append(f"  size         original {r['original_size']:,} B, recovered {r['recovered_size']:,} B")
            lines.append(f"  sha256       expected  {r['expected_sha256']}")
            lines.append(f"               recovered {r['recovered_sha256']}")
        if r.get("note"):
            lines.append(f"  note         {r['note']}")
        for f in r.get("failures", [])[:10]:
            lines.append(f"  failure      chunk {f['chunk']}: {f['error']}: {f['message']}")
        _emit(r, as_json, report_path, lines)
        if r["exit_code"]:
            raise typer.Exit(r["exit_code"])
    _run(body)


@app.command("info")
def info_cmd(source: Path = typer.Argument(..., help="Container (.vxdna), DNA reads, or legacy archive."),
             key_file: Optional[Path] = KeyFile, as_json: bool = JsonOut) -> None:
    """Show how an archive was built (format, crypto, ECC, strand layout, storage efficiency)."""
    def body() -> None:
        r = api.info(source, key=_key(key_file))
        if "note" in r:
            _emit(r, as_json, None, [f"{source}: {r['input_kind']}", f"  {r['note']}"])
            return
        e = r["efficiency"]
        content = r["content"]
        lines = [f"{source}: {r['input_kind']}, {r['format']}, encoder {r['encoder']}",
                 f"  archive id   {r['archive_id']}",
                 f"  content      " + (f"{content['name']!r}, {content['size']:,} B, sha256 {content['sha256']}" if isinstance(content, dict) else content),
                 f"  encryption   {r['encryption']} (manifest: {r['manifest_authentication']})",
                 f"  compression  {r['compression']}, chunk size {r['chunk_size']:,} B, {r['chunks']} chunk(s)",
                 f"  outer ECC    {r['erasure_code']['algorithm']} {r['erasure_code']['data_shards']}+{r['erasure_code']['parity_shards']}, "
                 f"{r['erasure_code']['stripes']} stripe(s): {r['erasure_code']['guarantee']}",
                 f"  strand       {r['strand']['strand_nt']} nt, mapping {r['strand']['mapping']}, payload {r['strand']['payload_bytes']} B, "
                 f"inner RS {r['strand']['inner_parity_bytes']} B, CRC-32",
                 f"  constraints  GC {r['constraints']['gc_min_percent']}-{r['constraints']['gc_max_percent']}%, "
                 f"max homopolymer {r['constraints']['max_homopolymer'] or 'off'}, motifs {r['constraints']['forbidden_motifs'] or 'none'}",
                 f"  size         original {_fmt_bytes(e['original_bytes'])}, stored {e['stored_bytes']:,} B, "
                 f"outer parity {e['outer_parity_bytes']:,} B, frame overhead {e['frame_overhead_bytes']:,} B",
                 f"  DNA          {e['strands_total']:,} strands, {e['dna_bases_total']:,} nt"
                 + (f", {e['bases_per_original_byte']:.3f} nt/original byte ({e['net_bits_per_base']:.3f} net bits/nt)"
                    if e["bases_per_original_byte"] else "")]
        if r.get("reads"):
            lines.append(f"  reads        {r['reads'].get('reads_total', 0):,} total, {r['reads'].get('reads_valid', 0):,} valid")
        _emit(r, as_json, None, lines)
    _run(body)


@app.command("extract")
def extract_cmd(source: Path = typer.Argument(..., help="Container (.vxdna) or DNA reads."),
                output: Path = typer.Option(..., "--output", "-o", help="File to write the selection to."),
                chunk: Optional[int] = typer.Option(None, "--chunk", help="Chunk index to recover (see `info`)."),
                start: Optional[int] = typer.Option(None, "--start", help="First plaintext byte of a range."),
                end: Optional[int] = typer.Option(None, "--end", help="End (exclusive) of the byte range; default end of file."),
                key_file: Optional[Path] = KeyFile, indel: bool = IndelRepair, max_indel: int = MaxIndel,
                force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut) -> None:
    """Random access: recover one chunk or a byte range, decoding only the stripes that hold it."""
    def body() -> None:
        r = api.extract(source, output, chunk=chunk, start=start, end=end, key=_key(key_file),
                        options=_decode_options(indel, max_indel), overwrite=force)
        _emit(r, as_json, report_path, [f"extracted {r['selection']} from {source} -> {output}: {r['status']}",
                                        f"  bytes        {r['bytes']:,}, sha256 {r['output_sha256']}",
                                        f"  decoded      {r['stripes_decoded']} of {r['stripes_total']} stripes"
                                        if r["input_kind"] == "dna-reads" else "  source       container (no DNA decoding needed)"])
    _run(body)


# ---------------------------------------------------------------- pipeline
@app.command("pipeline")
def pipeline_cmd(
    input_file: Path = typer.Argument(..., help="File to push through the full lifecycle."),
    output: Path = typer.Option(..., "--output", "-o", help="Recovered file."),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Directory for intermediate files (default: temporary)."),
    dna_output: Optional[Path] = typer.Option(None, "--dna-output", help="Also save the undamaged strand pool (FASTA) here."),
    key_file: Optional[Path] = KeyFile,
    seed: int = typer.Option(0, "--seed"), substitution_rate: float = typer.Option(0.0, "--substitution-rate"),
    insertion_rate: float = typer.Option(0.0, "--insertion-rate"), deletion_rate: float = typer.Option(0.0, "--deletion-rate"),
    dropout_rate: float = typer.Option(0.0, "--dropout-rate"),
    reverse_complement_rate: float = typer.Option(0.0, "--reverse-complement-rate"),
    data_shards: int = typer.Option(64, "--data-shards"), parity_shards: int = typer.Option(16, "--parity-shards"),
    indel: bool = IndelRepair, max_indel: int = MaxIndel,
    force: bool = Force, as_json: bool = JsonOut, report_path: Optional[Path] = ReportOut,
) -> None:
    """store → encode → simulate → decode → restore → verify, with real files. Fails honestly beyond capacity."""
    def body() -> None:
        key = _key(key_file)
        options = StoreOptions(data_shards=data_shards, parity_shards=parity_shards)
        channel = ChannelConfig(seed=seed, substitution_rate=substitution_rate, insertion_rate=insertion_rate,
                                deletion_rate=deletion_rate, dropout_rate=dropout_rate,
                                reverse_complement_rate=reverse_complement_rate, shuffle=True)
        tmp = None
        work = work_dir
        if work is None:
            tmp = tempfile.TemporaryDirectory(prefix="vnxdna-pipeline-")
            work = Path(tmp.name)
        try:
            r = api.pipeline(input_file, output, work_dir=work, store_options=options, channel=channel, key=key,
                             decode_options=_decode_options(indel, max_indel), overwrite=force)
            if dna_output is not None:
                data = (Path(work) / f"{Path(input_file).name}.fasta").read_bytes()
                from .container.vxdna import atomic_write
                atomic_write(dna_output, data, overwrite=force)
        finally:
            if tmp is not None:
                tmp.cleanup()
        s = r["steps"]
        c = s["simulate"]["channel"]
        _emit(r, as_json, report_path, [
            f"pipeline {input_file} -> {output}: {r['status']}",
            f"  store        {s['store']['original_bytes']:,} B -> {s['store']['stored_bytes']:,} B stored "
            f"({'encrypted' if s['store']['encrypted'] else 'not encrypted'})",
            f"  encode       {s['encode']['strands']:,} strands x {s['encode']['strand_nt']} nt",
            f"  simulate     {c['strands_dropped']:,} strands dropped, {c['substitutions']:,} subs, {c['insertions']:,} ins, "
            f"{c['deletions']:,} dels (seed {seed})",
            *_recovery_lines(s["decode"]),
            f"  verify       {s['verify']['status']}",
            f"  identical    {r['bytes_identical']} (sha256 {r['output_sha256']})",
            f"  container    round trip byte-identical: {r['container_roundtrip_identical']}"])
    _run(body)


# ---------------------------------------------------------------- keygen / version / benchmark
@app.command("keygen")
def keygen_cmd(output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the key to this file (mode 0600). "
                                                     "Default: print to stdout."),
               force: bool = Force) -> None:
    """Generate a random 256-bit key (base64url). Keep it secret; it is never stored in archives."""
    def body() -> None:
        key = crypto.generate_key()
        if output is None:
            typer.echo(key)
            return
        path = Path(output)
        if path.exists() and not force:
            raise OutputError(f"key file already exists: {path}")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(key + "\n")
            os.chmod(path, 0o600)
        except OSError as error:
            raise OutputError(f"cannot write key file {path}: {error.strerror or error}") from None
        typer.echo(f"wrote new key to {path} (mode 0600). Losing it makes encrypted archives unrecoverable.")
    _run(body)


@app.command("version")
def version_cmd(as_json: bool = JsonOut) -> None:
    """Print the VNX-DNA version and supported formats."""
    from .provenance import environment
    info_ = {"vnxdna": __version__, "archive_format": 4, "container_file_version": 1, "strand_frame_format": 4,
             "legacy_read_support": ["v0.1 dataset formats 1-3", "RD-1 JSON archive 0.1"], "environment": environment()}
    if as_json:
        typer.echo(json.dumps(info_, indent=2, sort_keys=True))
    else:
        typer.echo(f"vnx-dna {__version__} (archive format 4, container file v1, strand frame 4; reads legacy V0.1)")


@app.command("benchmark")
def benchmark_cmd(sizes: str = typer.Option("1K,10K,100K,1M", "--sizes", help="Comma-separated input sizes (K/M suffix)."),
                  repeats: int = typer.Option(3, "--repeats", min=1, max=50, help="Timed repetitions per measurement."),
                  output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write JSON results here."),
                  seed: int = typer.Option(1, "--seed"), as_json: bool = JsonOut) -> None:
    """Measure every pipeline stage on synthetic data (real measurements on this machine)."""
    def body() -> None:
        from .bench import format_table, parse_sizes, run_benchmarks
        results = run_benchmarks(parse_sizes(sizes), repeats=repeats, seed=seed)
        if output is not None:
            Path(output).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        typer.echo(json.dumps(results, indent=2, sort_keys=True) if as_json else format_table(results))
    _run(body)


# ---------------------------------------------------------------- legacy
@legacy_app.command("info")
def legacy_info_cmd(source: Path = typer.Argument(..., help="V0.1 dataset directory or RD-1 JSON archive.")) -> None:
    """Identify a legacy archive."""
    def body() -> None:
        from .legacy import v0_1
        kind = v0_1.detect(source)
        if kind is None:
            raise InvalidInputError(f"{source} is not a recognised V0.1 archive")
        typer.echo(f"{source}: {kind} (read-only legacy support; re-archive with `vnx-dna store` after restoring)")
    _run(body)


@legacy_app.command("restore")
def legacy_restore_cmd(source: Path = typer.Argument(..., help="V0.1 dataset directory or RD-1 JSON archive."),
                       output: Path = typer.Option(..., "--output", "-o"),
                       key_file: Optional[Path] = typer.Option(None, "--key-file", "-k", help="V0.1 Fernet key file."),
                       force: bool = Force, as_json: bool = JsonOut) -> None:
    """Decode a V0.1 archive with the explicit compatibility decoder and verify its SHA-256."""
    def body() -> None:
        from .container.vxdna import atomic_write
        from .legacy import v0_1
        data, report = v0_1.decode_legacy(source, _key(key_file))
        atomic_write(output, data, overwrite=force)
        report = {**report, "output": str(output)}
        _emit(report, as_json, None, [f"legacy restore {source} ({report['legacy_format']}) -> {output}: {report['status']}",
                                      f"  sha256       {report['recovered_sha256']} (recomputed; matches legacy manifest)"])
    _run(body)


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
