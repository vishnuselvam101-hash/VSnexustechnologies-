"""VNX-DNA V2 command-line interface (a thin layer over :mod:`vnxdna.v2.api`).

Canonical workflow::

    vnx-dna store input.bin -o archive.vxdna              # compress, encrypt, chunk, index (streaming)
    vnx-dna encode archive.vxdna -o strands.fasta         # ECC groups + strand frames + DNA (FASTA or .vxs)
    vnx-dna sequence strands.fasta --coverage 10 -o reads.fastq   # simulated sequencing channel
    vnx-dna cluster reads.fastq -o clusters.jsonl         # address-indexed clustering
    vnx-dna consensus clusters.jsonl -o consensus.fasta   # multi-read synchronization, N for ambiguity
    vnx-dna decode consensus.fasta -o recovered.vxdna     # DNA → the original container, byte for byte
    vnx-dna restore recovered.vxdna -o recovered.bin      # verified original file
    vnx-dna verify recovered.vxdna --file recovered.bin

V1 (format 4) inputs are accepted by every reading command. The V1 CLI is
available unchanged as ``vnx-dna v1 ...``. Exit codes are documented in
``docs/CLI.md`` and :mod:`vnxdna.errors`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import typer

from . import __version__
import errno
import sys

from .cli_v1 import _run as _run_v1, app as v1_app, legacy_app
from .container import crypto
from .errors import ConfigurationError, OutputError
from .v2.paths import atomic_write_text, check_output_file, refuse_same_file, same_file

# OS errors that mean "the output cannot be written" (exit 8, not an internal error)
_OUTPUT_ERRNOS = {errno.ENOSPC, errno.EFBIG, errno.EDQUOT, errno.EROFS}


def _run(fn, *args, **kwargs) -> None:
    """The V1 error contract, plus: disk full / file too large / quota / read-only file system → OUTPUT_ERROR (8),
    and a closed standard output (``vnx-dna … | head``) ends quietly instead of as an internal error."""
    def guarded() -> None:
        try:
            fn(*args, **kwargs)
        except BrokenPipeError:
            _stdout_closed()
        except OSError as error:
            if error.errno in _OUTPUT_ERRNOS:
                raise OutputError(f"cannot write output: {error.strerror or error}") from None
            raise
    _run_v1(guarded)


_KEY_FILE_MAX = 4096  # a key file holds 44 (base64) or 64 (hex) characters; never read an unbounded file


def _key(key_file: Optional[Path]) -> bytes | None:
    """The key from ``--key-file`` or ``$VNXDNA_KEY``. The file must be a small regular file (``-k /dev/zero`` used to
    read until memory ran out); a key file that group or others can read or write is accepted with a warning."""
    if key_file is None:
        env = os.environ.get("VNXDNA_KEY")
        return crypto.parse_key(env) if env else None
    from .errors import InvalidInputError
    import stat as _stat
    try:
        with open(key_file, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not _stat.S_ISREG(st.st_mode):
                raise InvalidInputError(f"key file {key_file} is not a regular file")
            raw = handle.read(_KEY_FILE_MAX + 1)
    except OSError as error:
        raise InvalidInputError(f"cannot read key file {key_file}: {error.strerror or error}") from None
    if len(raw) > _KEY_FILE_MAX:
        raise ConfigurationError(f"key file {key_file} is too large to be a VNX-DNA key ({_KEY_FILE_MAX} bytes at most)")
    if st.st_mode & 0o077:
        typer.echo(f"vnx-dna: warning: key file {key_file} is accessible by group or others "
                   f"(mode {_stat.S_IMODE(st.st_mode):o}); use chmod 600", err=True)
    try:
        return crypto.parse_key(raw.decode("ascii"))
    except UnicodeDecodeError as error:
        raise InvalidInputError(f"cannot read key file {key_file}: {error}") from None


def _stdout_closed() -> None:
    """The reader of stdout went away: silence further writes and exit 141 (128 + SIGPIPE), like other CLI tools."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass
    raise typer.Exit(141)


def _check_report(report_path: Optional[Path], force: bool, *paths: Optional[Path]) -> None:
    """Validate ``--report`` before any work: not an existing file without --force, never one of the command's inputs
    or outputs (VNX-DNA 2.0 overwrote whatever was there, including the command's own input)."""
    if report_path is None:
        return
    for other in paths:
        if other is not None and (same_file(report_path, other) or Path(other).resolve() == Path(report_path).resolve()):
            raise OutputError(f"--report {report_path} is also an input or output of this command")
    check_output_file(report_path, overwrite=force, what="report")


def _require_file(path: Optional[Path], what: str = "input") -> None:
    """A missing or non-regular input is INVALID_INPUT (exit 3), as for every other command (``store``, ``pipeline``,
    ``simulate-errors`` and ``experiment run`` used to exit 2 with a usage box)."""
    from .errors import InvalidInputError
    if path is not None and not Path(path).is_file():
        raise InvalidInputError(f"{what} {path} does not exist or is not a regular file")


def _guard(report_path: Optional[Path], force: bool, inputs: list, outputs: list) -> None:
    """Up-front path checks for one command: no output (and no report) may be one of its inputs, key files and DNA
    indexes included, even with --force (the V3 release review found ``--report key.txt --force`` replacing the key
    of the archive being written, and ``extract a.vxdna -o a.vxdna --force`` replacing the archive)."""
    inputs = [p for p in inputs if p is not None]
    for out in outputs:
        if out is not None:
            refuse_same_file(out, *inputs)
    _check_report(report_path, force, *inputs, *outputs)


def _emit(report: dict[str, Any], as_json: bool, report_path: Optional[Path], summary: list[str]) -> None:
    """Print the summary (or JSON) and write the JSON report atomically (checked beforehand by :func:`_check_report`)."""
    if report_path is not None:
        try:
            atomic_write_text(report_path, json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        except OSError as error:
            raise OutputError(f"cannot write report {report_path}: {error.strerror or error}") from None
    try:
        if as_json:
            typer.echo(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            for line in summary:
                typer.echo(line)
    except BrokenPipeError:
        _stdout_closed()

HELP = """VNX-DNA 3: streaming computational DNA data storage (software simulation; not wet-lab validated).

\b
Canonical workflow:
  vnx-dna store input.bin -o archive.vxdna
  vnx-dna encode archive.vxdna -o strands.fasta
  vnx-dna sequence strands.fasta --coverage 10 -o reads.fastq --substitution-rate 0.001 --seed 42
  vnx-dna cluster reads.fastq -o clusters.jsonl
  vnx-dna consensus clusters.jsonl -o consensus.fasta
  vnx-dna decode consensus.fasta -o recovered.vxdna
  vnx-dna restore recovered.vxdna -o recovered.bin
  vnx-dna verify recovered.vxdna --file recovered.bin
\b
Large files:   vnx-dna encode archive.vxdna -o strands.vxs   (2-bit packed strands)
One command:   vnx-dna pipeline input.bin -o recovered.bin --coverage 10 --substitution-rate 0.001 --seed 42
Random access: vnx-dna extract archive.vxdna --offset 500000000 --length 1048576 -o section.bin
Encryption:    vnx-dna keygen -o key.txt ; then add --key-file key.txt
Error sweeps:  vnx-dna simulate-errors input.bin -o sweep/ --sweep substitution=0,0.005 --sweep burst-deletion=0.2 --burst-repair 16
Coverage 1:    vnx-dna recover reads.fastq -o out.bin --burst-repair 16 --experimental-indel-repair --max-indel 2
V1 archives:   read by every command; V1 tools: vnx-dna v1 --help
\b
Exit codes: 0 ok, 1 verification failed, 2 usage, 3 invalid input, 4 authentication,
5 insufficient redundancy, 6 unsupported format, 7 configuration, 8 output, 70 internal, 130 interrupted,
141 standard output closed early (e.g. piped into head).
"""

app = typer.Typer(help=HELP, no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False,
                  context_settings={"help_option_names": ["-h", "--help"]})
app.add_typer(legacy_app, name="legacy")
app.add_typer(v1_app, name="v1")  # the V1 (1.0.0) CLI, unchanged, with its own help text
experiment_app = typer.Typer(help="Reproducible experiments and Monte Carlo trials.", no_args_is_help=True)
benchmark_app = typer.Typer(help="Test-data generation and measured benchmarks.", no_args_is_help=True)
app.add_typer(experiment_app, name="experiment")
app.add_typer(benchmark_app, name="benchmark")

KeyFile = typer.Option(None, "--key-file", "-k", help="File with a 32-byte key (from `vnx-dna keygen`); default: $VNXDNA_KEY.",
                       dir_okay=False)
JsonOut = typer.Option(False, "--json", help="Print the full machine-readable JSON report.")
ReportOut = typer.Option(None, "--report", help="Also write the JSON report to this file.", dir_okay=False)
Force = typer.Option(False, "--force", "-f", help="Overwrite existing output files.")
Workers = typer.Option(0, "--workers", "-j", min=0, max=64, help="Worker processes/threads (0 = CPU count - 1, at most 8).")
TempDir = typer.Option(None, "--temp-dir", help="Directory for temporary files (default: system temp).", file_okay=False)
IndelRepair = typer.Option(False, "--experimental-indel-repair", help="Try single-read realignment of reads 1..--max-indel bases off.")
MaxIndel = typer.Option(1, "--max-indel", min=1, max=3, help="Largest length difference tried by single-read indel repair.")
QualityErasure = typer.Option(0, "--quality-erasure-below", min=0, max=60,
                              help="Treat FASTQ bases with Phred below this as erasures (0 = off).")
BurstRepair = typer.Option(0, "--burst-repair", min=0, max=64, help="Resynchronise reads that lost or gained one contiguous "
                           "run of up to this many nt (single-read burst repair; 0 = off).")
ArchiveTag = typer.Option(None, "--archive-tag", help="Decode only this archive (8 hex digits, see `info`) from a pool "
                                                      "that holds strands of several archives.")


def _decode_options(indel: bool = False, max_indel: int = 1, quality: int = 0, archive_tag: Optional[str] = None,
                    burst: int = 0):
    from .v2.decoder import DecodeOptionsV2
    return DecodeOptionsV2(indel_repair=indel, max_indel=max_indel, quality_erasure_below=quality,
                           archive_tag=archive_tag.lower() if archive_tag else None, burst_repair=burst)


def _size(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    from .v2.scale import parse_size
    return parse_size(text)


def _mb(n: Optional[float]) -> str:
    return "n/a" if n is None else f"{n:,.1f} MB/s"


def _recovery_lines(r: dict[str, Any]) -> list[str]:
    reads = r.get("reads", {})
    rec = r.get("recovery", {})
    lines = []
    if reads:
        lines.append(f"  reads        {reads.get('reads_total', 0):,} total, {reads.get('reads_valid', 0):,} valid "
                     f"({reads.get('reads_inner_corrected', 0):,} inner-corrected, {reads.get('reads_reverse_complement', 0):,} rev-comp, "
                     f"{reads.get('reads_indel_repaired', 0):,} indel-repaired), {reads.get('reads_rejected', 0):,} rejected, "
                     f"{reads.get('reads_length_mismatch', 0):,} wrong length")
    if rec:
        lines.append(f"  outer code   {rec.get('stripes_decoded', 0):,} ECC group(s), {rec.get('shards_erased', 0):,} strands erased, "
                     f"{rec.get('stripes_outer_recovered', 0):,} group(s) repaired, worst group lost {rec.get('max_erasures_in_a_group', 0)}"
                     f" [{rec.get('pass2_mode', '')}]")
    return lines


# ======================================================================= store / encode
@app.command("store")
def store_cmd(
    source: Path = typer.Argument(..., help="File to archive (a regular file; read in bounded chunks)."),
    output: Path = typer.Option(..., "--output", "-o", help="Container to create (.vxdna, format 5).", dir_okay=False),
    key_file: Optional[Path] = KeyFile,
    encrypt: Optional[bool] = typer.Option(None, "--encrypt/--no-encrypt", help="AES-256-GCM (default: on when a key is supplied)."),
    profile: str = typer.Option("balanced", "--profile", help="compact | balanced | resilient | archival (see docs/ECC.md)."),
    chunk_size: Optional[str] = typer.Option(None, "--chunk-size", help="Plaintext bytes per chunk, e.g. 1MiB (profile default)."),
    compression: Optional[str] = typer.Option(None, "--compression", help="zstd | zlib | none (per chunk, kept only if smaller)."),
    level: Optional[int] = typer.Option(None, "--level", help="Compression level."),
    data_shards: Optional[int] = typer.Option(None, "--data-shards", help="Outer code K (data strands per ECC group)."),
    parity_shards: Optional[int] = typer.Option(None, "--parity-shards", help="Outer code M (any M strands per group may be lost)."),
    mapping: Optional[str] = typer.Option(None, "--mapping", help="2bit | rotation3 | codebook8."),
    payload_bytes: Optional[int] = typer.Option(None, "--payload-bytes", help="Payload bytes per strand."),
    inner_parity: Optional[int] = typer.Option(None, "--inner-parity", help="Inner RS parity bytes per strand (even)."),
    gc_min: Optional[int] = typer.Option(None, "--gc-min", help="Minimum GC content (%)."),
    gc_max: Optional[int] = typer.Option(None, "--gc-max", help="Maximum GC content (%)."),
    max_homopolymer: Optional[int] = typer.Option(None, "--max-homopolymer", help="Longest homopolymer (0 = off)."),
    gc_window: Optional[int] = typer.Option(None, "--gc-window", help="Also enforce GC bounds per window of this many nt."),
    max_tandem: Optional[int] = typer.Option(None, "--max-tandem-repeat", help="Longest period-2/3 repeat in nt (0 = off, else >= 6)."),
    forbid: Optional[list[str]] = typer.Option(None, "--forbid-motif", help="Forbidden motif (repeatable; reverse complement too)."),
    max_strand_nt: Optional[int] = typer.Option(None, "--max-strand-nt", help="Fail if strands would be longer than this."),
    no_name: bool = typer.Option(False, "--no-name", help="Do not record the input file name."),
    timestamp: Optional[str] = typer.Option(None, "--timestamp", help="Record this creation time (omitted by default)."),
    resume: bool = typer.Option(False, "--resume", help="Continue an interrupted store from its validated checkpoint."),
    checkpoint_interval: int = typer.Option(64, "--checkpoint-interval", min=1, help="Chunks between checkpoints."),
    workers: int = Workers, force: bool = Force, as_json: bool = JsonOut, report: Optional[Path] = ReportOut,
) -> None:
    """Archive a file into a streaming format-5 container (bounded memory, resumable, atomic)."""
    def body() -> None:
        _require_file(source)
        from .v2 import api
        from .v2.profiles import options_for
        check_output_file(output, overwrite=force or resume)
        _guard(report, force, [source, key_file], [output])
        key = _key(key_file)
        if encrypt is True and key is None:
            raise ConfigurationError("--encrypt needs a key (--key-file or VNXDNA_KEY); create one with `vnx-dna keygen`")
        use_key = key if (encrypt is not False and key is not None) else None
        chosen_level = level
        if compression is not None and level is None:
            # the algorithm's default level must be chosen before validation (VNX-DNA 2.0 validated
            # `--compression none` against the profile's zstd level and always failed)
            chosen_level = {"none": 0, "zlib": 6, "zstd": 3}.get(compression)
        options = options_for(profile, chunk_size=_size(chunk_size), compression=compression, compression_level=chosen_level,
                              data_shards=data_shards, parity_shards=parity_shards, mapping=mapping, payload_bytes=payload_bytes,
                              inner_parity_bytes=inner_parity, gc_min_percent=gc_min, gc_max_percent=gc_max,
                              max_homopolymer=max_homopolymer, gc_window_nt=gc_window, max_tandem_repeat_nt=max_tandem,
                              forbidden_motifs=tuple(forbid) if forbid else None, store_name=False if no_name else None,
                              timestamp=timestamp)
        geometry = options.validate()
        if max_strand_nt is not None and geometry.strand_nt > max_strand_nt:
            raise ConfigurationError(f"strands would be {geometry.strand_nt} nt, above --max-strand-nt {max_strand_nt}")
        r = api.store(source, output, options=options, key=use_key, overwrite=force, resume=resume, workers=workers,
                      checkpoint_interval=checkpoint_interval)
        _emit(r, as_json, report, [
            f"stored {source} -> {output} ({r['profile']}, {'encrypted' if r['encrypted'] else 'not encrypted'})",
            f"  input        {r['original_bytes']:,} B  sha256 {r['original_sha256']}",
            f"  stored       {r['stored_bytes']:,} B in {r['chunks']:,} chunk(s) of {r['chunk_size']:,} B "
            f"({r['chunks_compressed']:,} compressed), {r['ecc_stripes']:,} ECC group(s)",
            f"  container    {r['container_bytes']:,} B, {r['elapsed_s']:.2f} s ({_mb(r['throughput_mb_s'])})"
            + (f", resumed at chunk {r['resumed_from_chunk']}" if r["resumed_from_chunk"] else "")])
    _run(body)


@app.command("encode")
def encode_cmd(container: Path = typer.Argument(..., help="Container from `vnx-dna store` (V2; V1 containers also accepted)."),
               output: Path = typer.Option(..., "--output", "-o", help="Strand file: .fasta or .vxs (2-bit packed)."),
               fmt: Optional[str] = typer.Option(None, "--format", help="fasta | vxs (default: from the file extension)."),
               no_index: bool = typer.Option(False, "--no-index", help="Do not write the DNA index (<output>.vxidx)."),
               workers: int = Workers, force: bool = Force, as_json: bool = JsonOut, report: Optional[Path] = ReportOut) -> None:
    """Encode a container into DNA strands (ECC groups, strand frames, constraint screening), streaming."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [container], [output, None if no_index else Path(str(output) + ".vxidx")])
        r = api.encode(container, output, fmt=fmt, workers=workers, overwrite=force, write_index=not no_index)
        if r.get("format_version") == 4:
            _emit(r, as_json, report, [f"encoded V1 container {container} -> {output}: {r['strands']:,} strands"])
            return
        e = r["efficiency"]
        _emit(r, as_json, report, [
            f"encoded {container} -> {output} ({r['output_format']})",
            f"  strands      {r['strands']:,} x {e['strand_nt']} nt = {r['dna_bases']:,} nt ({e['metadata_strands']:,} metadata strands)",
            f"  outer code   {e['outer_code']}: any {e['guaranteed_erasures_per_group']} strands of each of {e['ecc_groups']:,} groups may be lost",
            f"  density      {e['bases_per_original_byte']:.3f} nt per original byte "
            f"({e['net_bits_per_base']:.3f} net bits/nt, all overheads)" if e["bases_per_original_byte"] else
            f"  density      {e['bases_per_stored_byte']:.3f} nt per stored byte" if e["bases_per_stored_byte"] else
            "  density      n/a (empty input: metadata strands only)",
            f"  output       {r['output_bytes']:,} B in {r['elapsed_s']:.2f} s ({_mb(r['throughput_mb_s'])} of stored data)"
            + (f"; DNA index {r['dna_index']}" if r.get("dna_index") else "")])
    _run(body)


# ======================================================================= channel
def _channel(seed, coverage, coverage_model, abundance_sigma, dropout, syn_sub, syn_ins, syn_del, sub, ins, dele, dup, trunc, n_rate,
             invalid, contamination, rc, shuffle, quality_model, informativeness, burst_rate=0.0, burst_length=4.0,
             burst_kind="substitution"):
    from .v2.sequencing import SequencingConfig
    return SequencingConfig(seed=seed, coverage=coverage, coverage_model=coverage_model, abundance_sigma=abundance_sigma,
                            dropout_rate=dropout, synthesis_substitution_rate=syn_sub, synthesis_insertion_rate=syn_ins,
                            synthesis_deletion_rate=syn_del, substitution_rate=sub, insertion_rate=ins, deletion_rate=dele,
                            duplication_rate=dup, truncation_rate=trunc, n_rate=n_rate, invalid_read_rate=invalid,
                            contamination_rate=contamination, reverse_complement_rate=rc, shuffle=shuffle,
                            quality_model=quality_model, quality_informativeness=informativeness, burst_rate=burst_rate,
                            burst_length_mean=burst_length, burst_kind=burst_kind)


BurstRate = typer.Option(0.0, "--burst-rate", help="Probability a read carries one burst (contiguous run of errors).")
BurstLength = typer.Option(4.0, "--burst-length", help="Mean burst length in nt (geometric, 1..64).")
BurstKind = typer.Option("substitution", "--burst-kind", help="substitution | deletion | insertion | mixed.")


def _channel_command(name: str, default_coverage: float, default_model: str, default_quality: str, doc: str):
    def command(
        strands: Path = typer.Argument(..., help="Strand pool (FASTA from `encode`, or .vxs)."),
        output: Path = typer.Option(..., "--output", "-o", help="Reads to write: .fastq, .fasta or .vxs."),
        seed: int = typer.Option(0, "--seed", help="PRNG seed (PCG64); same input + config + seed = same output."),
        coverage: float = typer.Option(default_coverage, "--coverage", help="Mean reads per strand (1x, 5x, 10x, 50x ...)."),
        coverage_model: str = typer.Option(default_model, "--coverage-model", help="fixed | poisson | lognormal (uneven abundance)."),
        abundance_sigma: float = typer.Option(0.0, "--abundance-sigma", help="Log-normal sigma of strand abundance (lognormal model)."),
        dropout: float = typer.Option(0.0, "--dropout-rate", help="Probability a strand species is lost."),
        syn_sub: float = typer.Option(0.0, "--synthesis-substitution-rate", help="Per-base synthesis substitution probability."),
        syn_ins: float = typer.Option(0.0, "--synthesis-insertion-rate", help="Per-base synthesis insertion probability."),
        syn_del: float = typer.Option(0.0, "--synthesis-deletion-rate", help="Per-base synthesis deletion probability."),
        sub: float = typer.Option(0.0, "--substitution-rate", help="Per-base sequencing substitution probability."),
        ins: float = typer.Option(0.0, "--insertion-rate", help="Per-base sequencing insertion probability."),
        dele: float = typer.Option(0.0, "--deletion-rate", help="Per-base sequencing deletion probability."),
        dup: float = typer.Option(0.0, "--duplication-rate", help="Probability a read is duplicated (PCR/optical duplicate)."),
        trunc: float = typer.Option(0.0, "--truncation-rate", help="Probability a read is truncated (50-99 % kept)."),
        n_rate: float = typer.Option(0.0, "--n-rate", help="Probability a base is called N."),
        invalid: float = typer.Option(0.0, "--invalid-read-rate", help="Junk reads added, as a fraction of reads."),
        contamination: float = typer.Option(0.0, "--contamination-rate", help="Foreign random reads added, as a fraction of reads."),
        rc: float = typer.Option(0.0, "--reverse-complement-rate", help="Probability a read is reverse-complemented."),
        shuffle: bool = typer.Option(True, "--shuffle/--no-shuffle", help="Uniformly permute the reads (out of core)."),
        quality_model: str = typer.Option(default_quality, "--quality-model", help="informative | flat."),
        informativeness: float = typer.Option(0.8, "--quality-informativeness", help="Share of sequencing-error bases given low quality."),
        burst_rate: float = BurstRate, burst_length: float = BurstLength, burst_kind: str = BurstKind,
        fmt: Optional[str] = typer.Option(None, "--format", help="fastq | fasta | vxs (default: from the extension)."),
        temp_dir: Optional[Path] = TempDir, force: bool = Force, as_json: bool = JsonOut, report: Optional[Path] = ReportOut,
    ) -> None:
        def body() -> None:
            from .v2 import api
            _guard(report, force, [strands], [output])
            config = _channel(seed, coverage, coverage_model, abundance_sigma, dropout, syn_sub, syn_ins, syn_del, sub, ins, dele, dup,
                              trunc, n_rate, invalid, contamination, rc, shuffle, quality_model, informativeness,
                              burst_rate, burst_length, burst_kind)
            fn = api.sequence if name == "sequence" else api.simulate
            r = fn(strands, output, config, fmt=fmt, overwrite=force, temp_dir=temp_dir)
            err = r["errors"]
            _emit(r, as_json, report, [
                f"{name} {strands} -> {output} ({r['output_format']}; SOFTWARE SIMULATION)",
                f"  strands      {r['strands']:,} ({r['strands_dropped']:,} dropped, {r['strands_with_zero_reads']:,} with no read)",
                f"  reads        {r['reads']:,} (mean {r['reads_per_strand_mean']:.2f} per strand; {r['duplicate_reads']:,} duplicates, "
                f"{r['reads_truncated']:,} truncated, {r['invalid_reads_added']:,} junk, {r['contamination_reads_added']:,} foreign)",
                f"  errors       seq sub {err['sequencing_substitutions']:,} ins {err['sequencing_insertions']:,} del "
                f"{err['sequencing_deletions']:,}; synth sub {err['synthesis_substitutions']:,} ins {err['synthesis_insertions']:,} "
                f"del {err['synthesis_deletions']:,}; N {err['n_calls']:,}"
                + (f"; bursts sub {err['bursts_substitution']:,} del {err['bursts_deletion']:,} ins {err['bursts_insertion']:,}"
                   if config.burst_rate else ""),
                f"  output       {r['output_bytes']:,} B sha256 {r['output_sha256'][:16]}... in {r['elapsed_s']:.2f} s"])
        _run(body)
    command.__doc__ = doc
    return command


app.command("sequence")(_channel_command("sequence", 10.0, "poisson", "informative",
                                         "Simulate sequencing: coverage, errors, duplicates, quality scores (FASTQ). Software simulation."))
app.command("simulate")(_channel_command("simulate", 1.0, "fixed", "flat",
                                         "Simulate the storage channel (synthesis errors, dropout, abundance); default 1 read per strand."))


@app.command("reads")
def reads_cmd(source: Path = typer.Argument(..., help="Reads (FASTQ/FASTA/plain/VXS)."),
              output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the reads that pass the filters here."),
              min_length: int = typer.Option(0, "--min-length", help="Drop reads shorter than this."),
              max_length: int = typer.Option(0, "--max-length", help="Drop reads longer than this (0 = no limit)."),
              min_mean_quality: float = typer.Option(0.0, "--min-mean-quality", help="Drop reads with lower mean Phred quality."),
              keep_invalid: bool = typer.Option(False, "--keep-invalid", help="Keep reads with symbols other than ACGTN."),
              force: bool = Force, as_json: bool = JsonOut, report: Optional[Path] = ReportOut) -> None:
    """Validate and filter raw reads; print read statistics."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [source], [output])
        r = api.reads_filter(source, output, min_length=min_length, max_length=max_length, min_mean_quality=min_mean_quality,
                             drop_invalid=not keep_invalid, overwrite=force)
        _emit(r, as_json, report, [
            f"reads {source} ({r['input_format']}): {r['reads_in']:,} in, {r['reads_kept']:,} kept, {r['reads_removed']:,} removed",
            f"  lengths      {r['length_min']}..{r['length_max']}, most common "
            + (", ".join(f"{x['length']} nt ({x['reads']:,} reads)" for x in r['length_most_common'][:3]) or "n/a"),
            f"  quality      mean {'n/a (no quality scores)' if r['mean_quality'] is None else round(r['mean_quality'], 2)}; "
            f"invalid-symbol reads {r['reads_invalid_symbols']:,}; N calls {r['n_calls']:,}"])
    _run(body)


@app.command("cluster")
def cluster_cmd(reads: Path = typer.Argument(..., help="Reads (FASTQ/FASTA/plain/VXS)."),
                output: Path = typer.Option(..., "--output", "-o", help="Cluster file (JSON Lines)."),
                workers: int = Workers, temp_dir: Optional[Path] = TempDir, force: bool = Force, as_json: bool = JsonOut,
                report: Optional[Path] = ReportOut) -> None:
    """Group reads by strand (address indexing; minimizer index for unaddressed reads)."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [reads], [output])
        r = api.cluster(reads, output, workers=workers, overwrite=force, temp_dir=temp_dir)
        s = r["stats"]
        _emit(r, as_json, report, [
            f"clustered {reads} -> {output}: {r['clusters']:,} clusters ({s.get('clusters_orphan', 0):,} without address)",
            f"  reads        {s.get('reads_total', 0):,}: {s.get('reads_verified', 0):,} verified address, "
            f"{s.get('reads_tentative', 0):,} tentative, {s.get('reads_orphan', 0):,} orphan",
            f"  time         {r['elapsed_s']:.2f} s"])
    _run(body)


@app.command("consensus")
def consensus_cmd(clusters: Path = typer.Argument(..., help="Cluster file from `vnx-dna cluster`."),
                  output: Path = typer.Option(..., "--output", "-o", help="Consensus strands (FASTA)."),
                  band: int = typer.Option(12, "--band", min=1, max=64, help="Alignment band (largest net indel handled)."),
                  max_edit_fraction: float = typer.Option(0.15, "--max-edit-fraction", help="Exclude reads farther than this from the draft."),
                  min_winner_share: float = typer.Option(0.6, "--min-winner-share", help="Below this share of the top-2 weight: N."),
                  force: bool = Force, as_json: bool = JsonOut, report: Optional[Path] = ReportOut) -> None:
    """One consensus sequence per cluster; ambiguous positions are written as N (erasures for the inner code)."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [clusters], [output])
        r = api.consensus(clusters, output, overwrite=force, band=band, max_edit_fraction=max_edit_fraction,
                          min_winner_share=min_winner_share)
        s = r["stats"]
        _emit(r, as_json, report, [
            f"consensus {clusters} -> {output}: {r['consensus_sequences']:,} sequences",
            f"  checks       {s.get('consensus_crc_valid', 0):,} CRC-valid, {s.get('consensus_valid_after_inner_rs', 0):,} valid after "
            f"inner RS, {s.get('fallback_to_verified_read', 0):,} fell back to a verified read, "
            f"{s.get('consensus_unverified_written', 0):,} unverified",
            f"  alignment    {s.get('reads_aligned', 0):,} reads aligned, {s.get('reads_excluded_unalignable', 0):,} excluded; "
            f"{s.get('ambiguous_positions', 0):,} ambiguous positions (N)",
            f"  time         {r['elapsed_s']:.2f} s"])
    _run(body)


# ======================================================================= decode / restore / recover
@app.command("decode")
def decode_cmd(reads: Path = typer.Argument(..., help="Reads or strands (FASTA/FASTQ/plain/VXS; V1 reads accepted)."),
               output: Path = typer.Option(..., "--output", "-o", help="Container to write (.vxdna)."),
               indel: bool = IndelRepair, max_indel: int = MaxIndel, quality: int = QualityErasure,
               archive_tag: Optional[str] = ArchiveTag, burst: int = BurstRepair, workers: int = Workers, temp_dir: Optional[Path] = TempDir, force: bool = Force, as_json: bool = JsonOut,
               report: Optional[Path] = ReportOut) -> None:
    """Rebuild the original container from DNA (byte-identical; no key needed)."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [reads], [output])
        r = api.decode(reads, output, options=_decode_options(indel, max_indel, quality, archive_tag, burst), workers=workers, overwrite=force,
                       temp_dir=temp_dir)
        _emit(r, as_json, report, [f"decoded {reads} -> {output}: {r['status']}"] + _recovery_lines(r)
              + ([f"  container    {r['container_bytes']:,} B (trailer sha256 {r['container_sha256_trailer'][:16]}...)"]
                 if "container_bytes" in r else []))
    _run(body)


def _restore_like(op: str):
    def command(source: Path = typer.Argument(..., help="Container (V1/V2) or DNA reads/strands." if op == "restore" else "DNA reads/strands."),
                output: Path = typer.Option(..., "--output", "-o", help="File to write (published only after full verification)."),
                key_file: Optional[Path] = KeyFile, indel: bool = IndelRepair, max_indel: int = MaxIndel, quality: int = QualityErasure,
                archive_tag: Optional[str] = ArchiveTag, burst: int = BurstRepair, workers: int = Workers, temp_dir: Optional[Path] = TempDir, force: bool = Force, as_json: bool = JsonOut,
                report: Optional[Path] = ReportOut) -> None:
        def body() -> None:
            from .v2 import api
            _guard(report, force, [source, key_file], [output])
            fn = api.restore if op == "restore" else api.recover
            r = fn(source, output, key=_key(key_file), options=_decode_options(indel, max_indel, quality, archive_tag, burst), workers=workers,
                   overwrite=force, temp_dir=temp_dir)
            _emit(r, as_json, report, [f"{op} {source} -> {output}: {r.get('status', 'SUCCESS')}"] + _recovery_lines(r) + [
                f"  size         {r['size']:,} B", f"  sha256       {r['recovered_sha256']} (recomputed; matches the archive)"])
        _run(body)
    command.__doc__ = ("Restore the original file from a container or from DNA reads, fully verified." if op == "restore"
                       else "Recover the original file directly from DNA reads (decode + restore in one step).")
    return command


app.command("restore")(_restore_like("restore"))
app.command("recover")(_restore_like("recover"))


@app.command("verify")
def verify_cmd(source: Path = typer.Argument(..., help="Container (V1/V2) or DNA reads/strands."),
               key_file: Optional[Path] = KeyFile,
               against: Optional[Path] = typer.Option(None, "--file", help="Also compare this recovered file with the archive."),
               indel: bool = IndelRepair, max_indel: int = MaxIndel, quality: int = QualityErasure,
               archive_tag: Optional[str] = ArchiveTag, burst: int = BurstRepair, workers: int = Workers, temp_dir: Optional[Path] = TempDir, as_json: bool = JsonOut,
               report: Optional[Path] = ReportOut,
               force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing --report file.")) -> None:
    """Independently verify an archive (structure, authentication, ECC, every SHA-256). Writes nothing."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [source, against, key_file], [])
        r = api.verify(source, key=_key(key_file), against=against, options=_decode_options(indel, max_indel, quality, archive_tag, burst),
                       workers=workers, temp_dir=temp_dir)
        lines = [f"verify {source}: {r['status']}"] + [f"  [{c['result']}] {c['check']}" + (f": {c['detail']}" if c.get("detail") else "")
                                                        for c in r.get("checks", [])]
        if r.get("message") and r["status"] != "PASS":
            lines.append(f"  error: {r['message']}")
        _emit(r, as_json, report, lines)
        if r.get("exit_code", 0):
            raise typer.Exit(r["exit_code"])
    _run(body)


@app.command("info")
def info_cmd(source: Path = typer.Argument(..., help="Container, DNA reads/strands, or legacy archive."),
             key_file: Optional[Path] = KeyFile, workers: int = Workers, temp_dir: Optional[Path] = TempDir,
             as_json: bool = JsonOut) -> None:
    """Describe an archive from its authenticated metadata."""
    def body() -> None:
        from .v2 import api
        r = api.info(source, key=_key(key_file), workers=workers, temp_dir=temp_dir)
        if as_json or r.get("format_version") != 5:
            typer.echo(json.dumps(r, indent=2, sort_keys=True, default=str))
            return
        ec, dna, content = r["erasure_code"], r["dna"], r["content"]
        typer.echo("\n".join([
            f"{source}: {r['format']} ({r['input_kind']}), {r['encoder']}, profile {r['profile']}",
            f"  archive id   {r['archive_id']} ({r['manifest_authentication']})",
            "  content      " + (f"{content['name']!r}, {content['size']:,} B, sha256 {content['sha256']}" if isinstance(content, dict) else content),
            f"  storage      {r['chunks']:,} chunks of {r['chunk_size']:,} B, {r['stored_bytes']:,} stored bytes, {r['compression']}, "
            f"encryption {r['encryption']}",
            f"  ECC          {ec['data_shards']}+{ec['parity_shards']} {ec['algorithm']} x {ec['ecc_groups']:,} groups: {ec['guarantee']}",
            f"  strands      {dna['strands_total']:,} x {r['strand']['strand_nt']} nt = {dna['dna_bases_total']:,} nt"
            + (f" ({dna['bases_per_original_byte']:.3f} nt per original byte)" if dna["bases_per_original_byte"] else "")]))
    _run(body)


@app.command("extract")
def extract_cmd(source: Path = typer.Argument(..., help="Container (V1/V2) or DNA strands/reads."),
                output: Path = typer.Option(..., "--output", "-o", help="File to write the selection to."),
                offset: Optional[int] = typer.Option(None, "--offset", help="First byte of the range."),
                length: Optional[int] = typer.Option(None, "--length", help="Number of bytes (default: to the end)."),
                chunk: Optional[int] = typer.Option(None, "--chunk", help="Recover one whole chunk instead of a byte range."),
                start: Optional[int] = typer.Option(None, "--start", help="V1-style range start (same as --offset)."),
                end: Optional[int] = typer.Option(None, "--end", help="V1-style range end (exclusive)."),
                dna_index: Optional[Path] = typer.Option(None, "--dna-index", help="DNA index (default: <strands>.vxidx if present)."),
                key_file: Optional[Path] = KeyFile, workers: int = Workers, temp_dir: Optional[Path] = TempDir, force: bool = Force,
                as_json: bool = JsonOut, report: Optional[Path] = ReportOut) -> None:
    """Random access: recover a byte range (or chunk), reading and decoding only what it needs."""
    def body() -> None:
        from .v2 import api
        _guard(report, force, [source, dna_index, Path(str(source) + ".vxidx"), key_file], [output])
        # conflicting selections are refused instead of silently ignored (V3 release review: `--chunk 0 --length 5`
        # wrote the whole chunk, and `--end` was dropped when `--length` was given)
        from .errors import InvalidInputError
        if offset is not None and start is not None:
            raise InvalidInputError("--offset and --start are the same option; give one of them")
        if length is not None and end is not None:
            raise InvalidInputError("give --length or --end, not both")
        if chunk is not None and any(v is not None for v in (offset, start, length, end)):
            raise InvalidInputError("--chunk selects a whole chunk; it cannot be combined with --offset/--start/--length/--end")
        off = offset if offset is not None else start
        ln = length if length is not None else ((end - off) if (end is not None and off is not None) else None)
        r = api.extract(source, output, offset=off, length=ln, chunk=chunk, key=_key(key_file), dna_index=dna_index,
                        workers=workers, overwrite=force, temp_dir=temp_dir)
        if r.get("format_version") == 4:
            _emit(r, as_json, report, [f"extracted (V1) {r.get('bytes', 0):,} B -> {output}"])
            return
        extra = (f", read {r['container_bytes_read']:,} of {r['object_bytes']:,} B of the container body" if "container_bytes_read" in r
                 else f", {r.get('strands_processed', 0):,} strands scanned ({r.get('dna_index')})")
        _emit(r, as_json, report, [
            f"extracted {r['bytes']:,} B at offset {r['range']['offset']:,} -> {output}",
            f"  chunks       {r['chunks_processed']} of {r['chunks_total']:,}{extra}",
            f"  sha256       {r['output_sha256']}  ({r['elapsed_s']:.2f} s)"])
    _run(body)


@app.command("migrate")
def migrate_cmd(source: Path = typer.Argument(..., help="V1 container or V1 DNA reads."),
                output: Path = typer.Option(..., "--output", "-o", help="V2 container to create."),
                key_file: Optional[Path] = KeyFile,
                new_key_file: Optional[Path] = typer.Option(None, "--new-key-file", help="Encrypt the V2 archive with this key instead."),
                profile: str = typer.Option("balanced", "--profile", help="V2 storage profile."),
                chunk_size: Optional[str] = typer.Option(None, "--chunk-size", help="V2 chunk size (profile default)."),
                workers: int = Workers, temp_dir: Optional[Path] = TempDir, force: bool = Force, as_json: bool = JsonOut,
                report: Optional[Path] = ReportOut) -> None:
    """Convert a V1 archive to V2, verifying the data before and after (corrupted archives are refused)."""
    def body() -> None:
        from .v2 import api
        from .v2.profiles import options_for
        _guard(report, force, [source, key_file, new_key_file], [output])
        new_key = _key(new_key_file) if new_key_file is not None else None
        r = api.migrate(source, output, options=options_for(profile, chunk_size=_size(chunk_size)), key=_key(key_file), new_key=new_key,
                        overwrite=force, workers=workers, temp_dir=temp_dir)
        _emit(r, as_json, report, [f"migrated {source} (V1) -> {output} (V2, {r['v2']['profile']}): SUCCESS",
                                   f"  sha256       {r['v1']['sha256']} (V1 verified) == {r['v2']['sha256']} (V2 verified)"])
    _run(body)


# ======================================================================= pipeline
@app.command("pipeline")
def pipeline_cmd(
    source: Path = typer.Argument(..., help="File to push through the whole lifecycle."),
    output: Path = typer.Option(..., "--output", "-o", help="Recovered file."),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Directory for intermediate files (default: temporary)."),
    key_file: Optional[Path] = KeyFile,
    profile: str = typer.Option("balanced", "--profile"), chunk_size: Optional[str] = typer.Option(None, "--chunk-size"),
    strand_format: Optional[str] = typer.Option(None, "--strand-format", help="fasta | vxs (default: vxs above 256 MB without indels)."),
    seed: int = typer.Option(0, "--seed"),
    coverage: Optional[float] = typer.Option(None, "--coverage", help="Enable the sequencing channel with this mean coverage."),
    coverage_model: str = typer.Option("poisson", "--coverage-model"), abundance_sigma: float = typer.Option(0.0, "--abundance-sigma"),
    dropout: float = typer.Option(0.0, "--dropout-rate"), sub: float = typer.Option(0.0, "--substitution-rate"),
    ins: float = typer.Option(0.0, "--insertion-rate"), dele: float = typer.Option(0.0, "--deletion-rate"),
    dup: float = typer.Option(0.0, "--duplication-rate"), rc: float = typer.Option(0.0, "--reverse-complement-rate"),
    syn_sub: float = typer.Option(0.0, "--synthesis-substitution-rate"),
    use_consensus: Optional[bool] = typer.Option(None, "--consensus/--no-consensus", help="Cluster + consensus (default: when coverage > 1)."),
    resume: bool = typer.Option(False, "--resume", help="Reuse completed stages in --work-dir; resume an interrupted store."),
    cleanup: str = typer.Option("keep", "--cleanup", help="keep | outputs | all: intermediates to delete from --work-dir at the end "
                                "(a temporary work dir is always removed)."),
    workers: int = Workers, temp_dir: Optional[Path] = TempDir, force: bool = Force, as_json: bool = JsonOut,
    report: Optional[Path] = ReportOut,
) -> None:
    """store → encode → (sequence → cluster → consensus) → decode → restore → verify, with real files."""
    def body() -> None:
        _require_file(source)
        from .v2 import api
        from .v2.profiles import options_for
        if cleanup not in ("keep", "outputs", "all"):
            raise ConfigurationError("--cleanup must be keep, outputs or all")
        _guard(report, force, [source, key_file], [output])
        any_channel = coverage is not None or any((dropout, sub, ins, dele, dup, rc, syn_sub))
        channel = _channel(seed, coverage if coverage is not None else 1.0, coverage_model if coverage is not None else "fixed",
                           abundance_sigma, dropout, syn_sub, 0.0, 0.0, sub, ins, dele, dup, 0.0, 0.0, 0.0, 0.0, rc, True,
                           "informative", 0.8) if any_channel else None
        r = api.pipeline(source, output, work_dir=work_dir, options=options_for(profile, chunk_size=_size(chunk_size)), key=_key(key_file),
                         channel=channel, strand_format=strand_format, workers=workers, use_consensus=use_consensus, resume=resume,
                         cleanup=cleanup, overwrite=force, temp_dir=temp_dir, report_path=report)
        steps = r["steps"]
        lines = [f"pipeline {source} -> {output}: {r['status']} ({r['elapsed_s']:.1f} s; work dir {r['work_dir']})"]
        for name, seconds in r["timings_s"].items():
            lines.append(f"  {name:<12} {seconds:8.2f} s")
        if "sequence" in steps:
            s = steps["sequence"]
            lines.append(f"  channel      {s['reads']:,} reads from {s['strands']:,} strands (SOFTWARE SIMULATION)")
        lines += _recovery_lines(steps["decode"])
        lines += [f"  identical    {r['bytes_identical']} (sha256 {r['output_sha256']})",
                  f"  container    round trip byte-identical: {r['container_roundtrip_identical']}"]
        _emit(r, as_json, None, lines)
    _run(body)


# ======================================================================= experiments / benchmarks
@experiment_app.command("run")
def experiment_run_cmd(
    source: Path = typer.Option(..., "--input", "-i", help="Input file."),
    output: Path = typer.Option(..., "--output", "-o", help="Experiment directory (configuration.json, results.json, ...)."),
    trials: int = typer.Option(1, "--trials", min=1, max=100_000, help="Monte Carlo trials (seeds seed, seed+1, ...)."),
    seed: int = typer.Option(0, "--seed"), profile: str = typer.Option("balanced", "--profile"),
    chunk_size: Optional[str] = typer.Option(None, "--chunk-size"), key_file: Optional[Path] = KeyFile,
    coverage: float = typer.Option(10.0, "--coverage"), coverage_model: str = typer.Option("poisson", "--coverage-model"),
    abundance_sigma: float = typer.Option(0.0, "--abundance-sigma"), dropout: float = typer.Option(0.0, "--dropout-rate"),
    sub: float = typer.Option(0.0, "--substitution-rate"), ins: float = typer.Option(0.0, "--insertion-rate"),
    dele: float = typer.Option(0.0, "--deletion-rate"), dup: float = typer.Option(0.0, "--duplication-rate"),
    rc: float = typer.Option(0.0, "--reverse-complement-rate"), syn_sub: float = typer.Option(0.0, "--synthesis-substitution-rate"),
    syn_ins: float = typer.Option(0.0, "--synthesis-insertion-rate"), syn_del: float = typer.Option(0.0, "--synthesis-deletion-rate"),
    trunc: float = typer.Option(0.0, "--truncation-rate"), n_rate: float = typer.Option(0.0, "--n-rate"),
    burst_rate: float = BurstRate, burst_length: float = BurstLength, burst_kind: str = BurstKind,
    use_consensus: Optional[bool] = typer.Option(None, "--consensus/--no-consensus", help="Default: consensus when coverage > 1."),
    workers: int = Workers, force: bool = Force, as_json: bool = JsonOut,
) -> None:
    """Run a reproducible channel experiment (optionally Monte Carlo); results are generated, never typed by hand."""
    def body() -> None:
        _require_file(source)
        from .v2.experiment import run_experiment
        from .v2.profiles import options_for
        channel = _channel(seed, coverage, coverage_model, abundance_sigma, dropout, syn_sub, syn_ins, syn_del, sub, ins, dele, dup, trunc,
                           n_rate, 0.0, 0.0, rc, True, "informative", 0.8, burst_rate, burst_length, burst_kind)
        r = run_experiment(source, output, channel=channel, trials=trials, options=options_for(profile, chunk_size=_size(chunk_size)),
                           key=_key(key_file), use_consensus=use_consensus, workers=workers, overwrite=force)
        s = r["summary"]
        _emit(r, as_json, None, [
            f"experiment {output}: {s['trials']} trial(s), SOFTWARE SIMULATION",
            f"  recovered    {s['successful_recovery']} exact ({s['success_rate']:.4f}; 95% Wilson CI "
            f"[{s['success_ci95'][0]:.4f}, {s['success_ci95'][1]:.4f}])",
            f"  failed       {s['failed_recovery']}: detected {s['detected_failures']}, undetected corruption "
            f"{s['undetected_corruption']}, internal errors {s['internal_errors']}",
            f"  files        {', '.join(r['files'])}"])
    _run(body)


@app.command("simulate-errors")
def simulate_errors_cmd(
    source: Path = typer.Argument(..., help="Input file to store, encode and push through the simulated channel."),
    output: Path = typer.Option(..., "--output", "-o", help="Sweep directory (sweep.json, sweep.csv, sweep.md)."),
    sweep: list[str] = typer.Option(..., "--sweep", help="TYPE=RATE[,RATE...] (repeatable) or mixed=TYPE:RATE+TYPE:RATE. Types: "
                                    "substitution insertion deletion dropout duplication n truncation reverse-complement "
                                    "burst-substitution burst-deletion burst-insertion burst-mixed."),
    trials: int = typer.Option(10, "--trials", min=1, max=100_000, help="Trials per point (seeds seed, seed+1, ...)."),
    seed: int = typer.Option(0, "--seed"), profile: str = typer.Option("balanced", "--profile"),
    chunk_size: Optional[str] = typer.Option(None, "--chunk-size"), key_file: Optional[Path] = KeyFile,
    coverage: float = typer.Option(1.0, "--coverage", help="Base channel coverage (default 1: every strand read once)."),
    coverage_model: str = typer.Option("fixed", "--coverage-model"),
    burst_length: float = BurstLength,
    shuffle: bool = typer.Option(True, "--shuffle/--no-shuffle", help="Reorder reads uniformly (default on)."),
    use_consensus: Optional[bool] = typer.Option(None, "--consensus/--no-consensus", help="Default: consensus when coverage > 1."),
    indel: bool = typer.Option(False, "--indel-repair", help="Enable single-read indel repair in the decoder."),
    max_indel: int = MaxIndel, burst: int = BurstRepair, workers: int = Workers, force: bool = Force, as_json: bool = JsonOut,
    report: Optional[Path] = ReportOut,
) -> None:
    """Error-channel sweep: recovery statistics per error type and rate (software simulation, seeded)."""
    def body() -> None:
        _require_file(source)
        from .v2.profiles import options_for
        from .v2.sequencing import SequencingConfig
        from .v3.sweep import run_sweep
        _guard(report, force, [source], [])
        base = SequencingConfig(seed=seed, coverage=coverage, coverage_model=coverage_model, burst_length_mean=burst_length,
                                shuffle=shuffle)
        r = run_sweep(source, output, sweep=sweep, base=base, trials=trials, options=options_for(profile, chunk_size=_size(chunk_size)),
                      key=_key(key_file), use_consensus=use_consensus, indel_repair=indel, max_indel=max_indel, burst_repair=burst,
                      workers=workers,
                      overwrite=force)
        lines = [f"simulate-errors {source} -> {output}: {len(r['points'])} point(s) x {r['trials_per_point']} trial(s), "
                 "SOFTWARE SIMULATION"]
        for p in r["points"]:
            lines.append(f"  {p['point']:<32} exact {p['exact']}/{p['trials']} [{p['ci95_low']:.3f}, {p['ci95_high']:.3f}]  "
                         f"detected {p['failed_detected']}  undetected {p['undetected_corruption']}  internal {p['internal_errors']}")
        _emit(r, as_json, report, lines)
        if r["undetected_corruption_total"] or r["internal_errors_total"]:
            raise typer.Exit(70)  # a verification bug: never report such a sweep as a success
    _run(body)


@benchmark_app.command("generate")
def benchmark_generate_cmd(size: str = typer.Option(..., "--size", help="Exact size, e.g. 100MB, 1GB, 10GB (decimal) or 64MiB."),
                           pattern: str = typer.Option("mixed", "--pattern", help="random | compressible | mixed | structured"),
                           seed: int = typer.Option(42, "--seed"),
                           output: Path = typer.Option(..., "--output", "-o", help="File to write (never commit it to Git)."),
                           force: bool = Force, as_json: bool = JsonOut) -> None:
    """Generate a reproducible test file and report its exact size and SHA-256."""
    def body() -> None:
        from .v2.scale import generate_file, parse_size
        r = generate_file(output, parse_size(size), pattern, seed, overwrite=force)
        _emit(r, as_json, None, [f"generated {output}: {r['size']:,} B, pattern {pattern}, seed {seed}",
                                 f"  sha256       {r['sha256']}  ({r['elapsed_s']:.1f} s)"])
    _run(body)


@benchmark_app.command("scale")
def benchmark_scale_cmd(sizes: str = typer.Option("1MB,10MB,100MB", "--sizes", help="Comma-separated sizes (1MB .. 10GB)."),
                        pattern: str = typer.Option("mixed", "--pattern"), seed: int = typer.Option(42, "--seed"),
                        work_dir: Path = typer.Option(..., "--work-dir", help="Scratch directory (needs ~5x the largest size free)."),
                        output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write JSON results here (updated per stage)."),
                        profile: str = typer.Option("balanced", "--profile"), key_file: Optional[Path] = KeyFile,
                        workers: int = Workers, keep: bool = typer.Option(False, "--keep", help="Keep generated files."),
                        as_json: bool = JsonOut) -> None:
    """Large-file scalability benchmark with the real CLI: time, CPU, peak RAM, disk, exact recovery."""
    def body() -> None:
        from .v2.scale import parse_size, scale_benchmark
        results = scale_benchmark([parse_size(s) for s in sizes.split(",") if s.strip()], work_dir, pattern=pattern, seed=seed,
                                  profile=profile, workers=workers, key_file=str(key_file) if key_file else None, keep=keep,
                                  output=output, log=lambda m: typer.echo(m, err=True))
        if as_json:
            typer.echo(json.dumps(results, indent=2, sort_keys=True, default=str))
            return
        for run in results["runs"]:
            typer.echo(f"{run['size']:>14,} B  {run['status']}")
            for name, st in run["stages"].items():
                typer.echo(f"    {name:<18} {st['wall_s']:9.2f} s  cpu {st['cpu_utilisation'] or 0:5.2f}x  "
                           f"peak RSS {st['peak_rss_tree_bytes'] / 2**20:8.1f} MiB  disk {st['peak_workdir_bytes'] / 2**30:6.2f} GiB")
    _run(body)


@benchmark_app.command("corruption")
def benchmark_corruption_cmd(size: str = typer.Option("1GB", "--size"), pattern: str = typer.Option("mixed", "--pattern"),
                             seed: int = typer.Option(42, "--seed"), profile: str = typer.Option("balanced", "--profile"),
                             groups: int = typer.Option(50, "--groups", help="ECC groups damaged inside the guarantee."),
                             substitutions: int = typer.Option(5, "--substitutions", help="Extra substituted strands per damaged group."),
                             work_dir: Path = typer.Option(..., "--work-dir"), output: Optional[Path] = typer.Option(None, "--output", "-o"),
                             workers: int = Workers, keep: bool = typer.Option(False, "--keep"), as_json: bool = JsonOut) -> None:
    """Large-file damage acceptance: inside the ECC guarantee → exact file; beyond it → clear failure, no output."""
    def body() -> None:
        from .v2.scale import corruption_acceptance, parse_size
        r = corruption_acceptance(parse_size(size), work_dir, pattern=pattern, seed=seed, profile=profile, groups_within=groups,
                                  substitutions_per_group=substitutions, workers=workers, keep=keep, log=lambda m: typer.echo(m, err=True))
        if output is not None:
            atomic_write_text(output, json.dumps(r, indent=2, sort_keys=True, default=str) + "\n")
        _emit(r, as_json, None, [
            f"corruption acceptance ({r['size']:,} B): {r['status']}",
            f"  within       {len(r.get('damage_within', {}).get('damaged_groups', []))} groups lost M strands "
            f"(+{r.get('damage_within', {}).get('strands_substituted', 0)} substituted strands): exit {r.get('within_exit_code')}, "
            f"cmp identical {r.get('within_cmp_identical')}, sha256 match {r.get('within_sha256_match')}",
            f"  beyond       one group lost M+1: exit {r.get('beyond_exit_code')}, output written {r.get('beyond_output_written')}, "
            f"verify reported chunks {r.get('verify_reported_damaged_chunks')} (expected {r.get('verify_expected_damaged_chunks')})"])
    _run(body)


@benchmark_app.command("stages")
def benchmark_stages_cmd(sizes: str = typer.Option("100KB,1MB,10MB", "--sizes"), seed: int = typer.Option(1, "--seed"),
                         output: Optional[Path] = typer.Option(None, "--output", "-o"), workers: int = Workers,
                         as_json: bool = JsonOut) -> None:
    """Per-stage benchmark (storage, encode, decode, sequencing, clustering, consensus, ECC, end to end)."""
    def body() -> None:
        from .v2.stages import format_stages, parse_sizes, run_stages
        results = run_stages(parse_sizes(sizes), seed=seed, workers=workers)
        if output is not None:
            atomic_write_text(output, json.dumps(results, indent=2, sort_keys=True, default=str) + "\n")
        typer.echo(json.dumps(results, indent=2, sort_keys=True, default=str) if as_json else format_stages(results))
    _run(body)


@benchmark_app.command("v1")
def benchmark_v1_cmd(sizes: str = typer.Option("1K,10K,100K,1M", "--sizes"), repeats: int = typer.Option(3, "--repeats", min=1, max=50),
                     output: Optional[Path] = typer.Option(None, "--output", "-o"), seed: int = typer.Option(1, "--seed"),
                     as_json: bool = JsonOut) -> None:
    """The V1 (format 4) stage benchmark, unchanged."""
    def body() -> None:
        from .bench import format_table, parse_sizes, run_benchmarks
        results = run_benchmarks(parse_sizes(sizes), repeats=repeats, seed=seed)
        if output is not None:
            atomic_write_text(output, json.dumps(results, indent=2, sort_keys=True) + "\n")
        typer.echo(json.dumps(results, indent=2, sort_keys=True) if as_json else format_table(results))
    _run(body)


# ======================================================================= keygen / version
@app.command("keygen")
def keygen_cmd(output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the key here (mode 0600); default stdout."),
               force: bool = Force) -> None:
    """Generate a random 256-bit key (base64url). Keep it secret; it is never stored in archives."""
    def body() -> None:
        key = crypto.generate_key()
        if output is None:
            typer.echo(key)
            return
        from .v2.container import publish
        from .v2.paths import private_temp
        path = Path(output)
        if os.path.lexists(path) and not force:
            raise OutputError(f"key file already exists: {path}")
        check_output_file(path, overwrite=True, what="key file")
        # written to a private (0600) temporary file and then published: never through a symlink, and a failed write
        # never truncates an existing key (VNX-DNA 2.0/3.0 opened the name itself with O_TRUNC)
        tmp = None
        try:
            fd, tmp = private_temp(path, ".tmp")
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(key + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            publish(tmp, path, overwrite=force)
        except OSError as error:
            raise OutputError(f"cannot write key file {path}: {error.strerror or error}") from None
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        typer.echo(f"wrote new key to {path} (mode 0600). Losing it makes encrypted archives unrecoverable.")
    _run(body)


@app.command("version")
def version_cmd(as_json: bool = JsonOut) -> None:
    """Print the VNX-DNA version and supported formats."""
    from .provenance import environment
    info_ = {"vnxdna": __version__, "writes": {"archive_format": 5, "container_file_version": 2, "strand_frame_format": 5,
                                               "strand_files": ["FASTA", "VXS 1"], "read_files": ["FASTQ", "FASTA", "plain", "VXS 1"]},
             "reads": {"archive_formats": [5, 4], "container_file_versions": [2, 1], "strand_frame_formats": [5, 4],
                       "legacy": ["v0.1 dataset formats 1-3", "RD-1 JSON archive 0.1"]},
             "optional_features_written": ["final-seal-epoch-v3 (resumed encrypted stores only)"],
             "environment": environment()}
    if as_json:
        typer.echo(json.dumps(info_, indent=2, sort_keys=True))
    else:
        typer.echo(f"vnx-dna {__version__} (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1)")


_MAIN_PID = os.getpid()


def _terminate(signum, frame) -> None:
    """SIGTERM/SIGHUP (``kill``, ``timeout``, ``docker stop``, a closed terminal) end the command like Ctrl-C: worker
    processes are stopped first (so pools shut down at once), then KeyboardInterrupt runs the normal cleanup, which
    removes partial outputs and temporary directories, and the command exits 130. VNX-DNA 2.0/3.0 had no handler:
    a terminated command left partial files, temporary directories and orphaned workers behind."""
    if os.getpid() != _MAIN_PID:
        # a worker forked while the signal was pending inherits it and would run this handler before its initializer
        # resets it (a KeyboardInterrupt in the initializer broke the pool: exit 70); the parent handles the interrupt
        return
    import multiprocessing
    for child in multiprocessing.active_children():
        child.terminate()
    raise KeyboardInterrupt


def main() -> None:  # pragma: no cover - console entry point
    import signal
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _terminate)
    try:
        try:
            app()
        except KeyboardInterrupt:  # an interrupt outside a command body (start-up, argument parsing)
            typer.echo("vnx-dna: interrupted", err=True)
            raise SystemExit(130)
    except SystemExit as done:
        if done.code == 130:
            # interrupted: the command's cleanup has run; leave without joining worker-pool threads that an interrupt
            # may have left blocked (see vnxdna.v2.workers), which could otherwise hang the exit
            for stream in (sys.stdout, sys.stderr):
                try:
                    stream.flush()
                except (OSError, ValueError):
                    pass
            os._exit(130)
        raise


if __name__ == "__main__":  # pragma: no cover
    main()
