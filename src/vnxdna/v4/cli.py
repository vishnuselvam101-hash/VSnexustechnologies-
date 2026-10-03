"""``vnx`` — the VNX-DNA V4 command-line interface (thin layer over vnxdna.v4).

    vnx archive ./dataset archive.vnx            files/directories → VNX4 container
    vnx inspect | list | verify | locate | extract archive.vnx ...
    vnx encode archive.vnx strands.fasta         container (or plain files) → DNA strands
    vnx channel simulate strands.fasta reads.fastq --config channel.json
    vnx decode reads.fastq -o recovered.vnx      reads → verified container (or --extract DIR)
    vnx validate strands.fasta                   biological constraint diagnostics (JSON)
    vnx benchmark --profile balanced             machine-readable benchmark suite
    vnx experiment run experiments/EXP-0001/config.json
    vnx experimental ...                         experimental features (clearly separated)

Exit codes follow vnxdna.errors (0 success, 1 verification failed, 3 invalid input, 4 key/authentication,
5 insufficient redundancy, 6 unsupported format, 7 configuration, 8 output, 9 PARTIAL recovery, 70 internal error).
The V3 tool (``vnx-dna``, format 5) is unchanged.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import typer

from .errors import VNXConfigurationError, VNXError
from .version import __version__

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
        typer.echo(json.dumps(error.to_dict(), indent=2, default=str), err=True)
        raise typer.Exit(error.exit_code)
    except (typer.Exit, typer.Abort):
        raise
    except KeyboardInterrupt:
        typer.echo("vnx: interrupted", err=True)
        raise typer.Exit(130)
    except Exception as error:  # noqa: BLE001 - mapped to the stable contract
        from ..errors import VNXDNAError
        if isinstance(error, VNXDNAError):
            typer.echo(json.dumps(error.to_dict(), indent=2, default=str), err=True)
            raise typer.Exit(error.exit_code)
        if STATE["verbose"]:
            raise
        typer.echo(json.dumps({"status": "FAILED", "error": "INTERNAL_ERROR", "exit_code": 70,
                               "message": f"{type(error).__name__}: {error}"}), err=True)
        raise typer.Exit(70)


def _keys(key_file: Optional[Path], passphrase_env: Optional[str]) -> tuple[bytes | None, str | None]:
    from .crypto import load_key_file
    key = load_key_file(key_file) if key_file else None
    pw = None
    if passphrase_env:
        pw = os.environ.get(passphrase_env)
        if not pw:
            raise VNXConfigurationError(f"environment variable {passphrase_env} is empty or not set")
    return key, pw


KEY_OPT = typer.Option(None, "--key-file", "-k", help="32-byte key file (raw, 64 hex or 44 base64 characters).")
PW_OPT = typer.Option(None, "--passphrase-env", help="Name of an environment variable holding a passphrase (scrypt).")
JSON_OPT = typer.Option(True, "--json/--human", help="Machine-readable JSON (default) or a short human summary.")
FORCE_OPT = typer.Option(False, "--force", "-f", help="Overwrite existing outputs.")
CONFIG_OPT = typer.Option(None, "--config", "-c", help="JSON configuration file (sections archive/dna/constraints/channel/decode).")
PERF_OPT = typer.Option(None, "--performance", help="Performance profile: safe, balanced, maximum-throughput.")


@app.callback()
def main_callback(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show tracebacks for internal errors."),
                  progress: bool = typer.Option(False, "--progress", help="Progress, stage, throughput on stderr.")) -> None:
    STATE["verbose"] = verbose
    STATE["progress"] = progress


@app.command()
def version() -> None:
    """Print versions."""
    from .version import FORMAT_VERSION
    from ..v5 import native_alignment as na
    st = na.status()
    typer.echo(json.dumps({"vnx": __version__, "vnx4_format": list(FORMAT_VERSION), "frame_version": 4,
                           "alignment_backend": st["active_backend"], "native_alignment": st["native_available"]}))


@app.command()
def native() -> None:
    """Show whether the native (C) marker aligner is active, which library is loaded and why not if it is not."""
    from ..v5 import native_alignment as na
    _emit(na.status())


@app.command()
def keygen(output: Path = typer.Argument(..., help="New key file (mode 0600; refuses to overwrite).")) -> None:
    """Generate a random 32-byte key file."""
    def go():
        from .crypto import generate_key_file
        try:
            generate_key_file(output)
        except FileExistsError:
            from .errors import VNXOutputError
            raise VNXOutputError(f"{output} exists")
        _emit({"status": "OK", "key_file": str(output)})
    _run(go)


@app.command()
def archive(inputs: List[Path] = typer.Argument(..., help="Files and/or directories, then the output .vnx (last argument)."),
            chunk_size: int = typer.Option(1 << 20, help="Chunk size in bytes (4 KiB … 64 MiB)."),
            compression: str = typer.Option("zstd", help="zstd or none."), level: int = typer.Option(3, help="zstd level 1–22."),
            no_dedup: bool = typer.Option(False, "--no-dedup"), preserve_metadata: bool = typer.Option(False, "--preserve-metadata"),
            workers: int = typer.Option(1, "--workers", "-w"), performance: Optional[str] = PERF_OPT,
            key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT, force: bool = FORCE_OPT,
            config: Optional[Path] = CONFIG_OPT, as_json: bool = JSON_OPT) -> None:
    """Create a VNX4 archive: vnx archive ./dataset archive.vnx"""
    def go():
        from . import archive as ar
        from .config import load_config, performance as perf
        if len(inputs) < 2:
            raise VNXConfigurationError("usage: vnx archive INPUT... OUTPUT.vnx")
        cfg = load_config(config).get("archive", {})
        key, pw = _keys(key_file, passphrase_env)
        w = perf(performance)["workers"] if performance else workers
        opts = ar.ArchiveOptions(**{**cfg, "chunk_size": chunk_size if chunk_size != 1 << 20 else cfg.get("chunk_size", chunk_size),
                                    "compression": compression, "level": level, "dedup": not no_dedup,
                                    "preserve_metadata": preserve_metadata, "workers": w}, key=key, passphrase=pw)
        rep = ar.build_archive(list(inputs[:-1]), inputs[-1], opts, overwrite=force, progress=_progress_cb())
        d = rep.to_dict()
        _emit(d, f"archived {d['files']} entries, {d['content_bytes']:,} B → {d['container_bytes']:,} B ({d['output']})", as_json)
    _run(go)


@app.command()
def inspect(container: Path, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT) -> None:
    """Show the manifest and structure of a VNX4 archive."""
    def go():
        from . import archive as ar
        key, pw = _keys(key_file, passphrase_env)
        _emit(ar.inspect_container(container, key=key, passphrase=pw))
    _run(go)


@app.command("list")
def list_cmd(container: Path, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
             as_json: bool = JSON_OPT) -> None:
    """List archive entries."""
    def go():
        from . import archive as ar
        key, pw = _keys(key_file, passphrase_env)
        rows = ar.list_container(container, key=key, passphrase=pw)
        _emit({"entries": rows}, "\n".join(f"{r['size']:>14,}  {r['path']}" for r in rows), as_json)
    _run(go)


@app.command()
def verify(container: Path, chunk: Optional[int] = typer.Option(None, "--chunk", help="Verify one chunk by Merkle proof."),
           key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT) -> None:
    """Verify an archive (trailer, manifest, Merkle root, every chunk and file hash) or one chunk."""
    def go():
        from . import archive as ar
        key, pw = _keys(key_file, passphrase_env)
        _emit(ar.verify_container(container, key=key, passphrase=pw, chunk=chunk))
    _run(go)


@app.command()
def locate(container: Path, name: str, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
           profile: Optional[str] = typer.Option(None, "--dna-profile", help="Also give strand groups/records for this layout.")) -> None:
    """Locate a file: chunk indices, container byte ranges and (with --dna-profile) strand groups and strand records."""
    def go():
        from . import archive as ar
        key, pw = _keys(key_file, passphrase_env)
        _emit(ar.locate(container, name, key=key, passphrase=pw, profile=profile))
    _run(go)


@app.command()
def extract(container: Path, output_dir: Path, names: Optional[List[str]] = typer.Option(None, "--file", help="Extract only these."),
            key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT, force: bool = FORCE_OPT,
            apply_metadata: bool = typer.Option(False, "--apply-metadata")) -> None:
    """Extract (every file verified before it is renamed into place)."""
    def go():
        from . import archive as ar
        key, pw = _keys(key_file, passphrase_env)
        _emit(ar.extract(container, output_dir, key=key, passphrase=pw, names=names or None, overwrite=force,
                         apply_metadata=apply_metadata))
    _run(go)


def _dna_opts(config, profile, outer_code, k, m, workers, performance, experimental=False):
    from .config import dna_options, load_config, performance as perf
    cfg = load_config(config)
    p = perf(performance or cfg.get("performance"))
    w = workers if workers else p["workers"]
    return dna_options(cfg, profile=profile, outer_code=outer_code, data_symbols=k, parity_symbols=m, workers=w,
                       groups_per_task=p["groups_per_task"], experimental=experimental or None), p


@app.command()
def encode(source: Path = typer.Argument(..., help="A .vnx container, or a file/directory (archived first)."),
           output: Path = typer.Argument(..., help="Strand file (.fasta or .fastq)."),
           profile: Optional[str] = typer.Option(None, help="v4-balanced (default), v4-dense, v4-indel, v4-archival."),
           outer_code: Optional[str] = typer.Option(None, help="cauchy-rs (default)."),
           data_symbols: Optional[int] = typer.Option(None, "-K"), parity_symbols: Optional[int] = typer.Option(None, "-M"),
           workers: int = typer.Option(0, "--workers", "-w", help="0 = from the performance profile."),
           performance: Optional[str] = PERF_OPT, config: Optional[Path] = CONFIG_OPT, force: bool = FORCE_OPT,
           keep_archive: Optional[Path] = typer.Option(None, help="When SOURCE is not a container: also keep the .vnx here."),
           key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT) -> None:
    """Encode a VNX4 container (or files) into DNA strands."""
    def go():
        import tempfile
        from . import archive as ar
        from . import container as ct
        from . import encoder as en
        opts, perf = _dna_opts(config, profile, outer_code, data_symbols, parity_symbols, workers, performance)
        if opts.outer_code != "cauchy-rs":
            raise VNXConfigurationError("non-default outer codes are experimental: use `vnx experimental encode`")
        is_container = source.is_file() and source.read_bytes()[:8] == ct.MAGIC if source.is_file() and source.stat().st_size >= 8 \
            else False
        tmpdir = None
        src = source
        if not is_container:
            key, pw = _keys(key_file, passphrase_env)
            tmpdir = tempfile.mkdtemp(prefix="vnx-encode-")
            src = Path(keep_archive) if keep_archive else Path(tmpdir) / "archive.vnx"
            ar.build_archive([source], src, ar.ArchiveOptions(key=key, passphrase=pw, workers=opts.workers), overwrite=force)
        try:
            rep = en.encode_container(src, output, opts, overwrite=force, progress=_progress_cb())
            if perf.get("verify_after_encode"):
                from . import decoder as de
                res = de.decode_reads(output, None, de.DecodeOptions(layout=opts.resolve()[0], workers=opts.workers))
                rep["verified_by_decoding"] = res.status == "SUCCESS"
                if res.status != "SUCCESS":
                    from .errors import VNXIntegrityError
                    raise VNXIntegrityError("encode verification failed: the strands do not decode to the container")
            _emit(rep)
        finally:
            if tmpdir:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
    _run(go)


def _decode(reads, output, extract_dir, partial_dir, select, profile, workers, performance, config, band, min_quality,
            archive_tag, force, key_file, passphrase_env, report):
    from . import archive as ar
    from . import decoder as de
    from .config import decode_options, load_config, performance as perf
    cfg = load_config(config)
    p = perf(performance or cfg.get("performance"))
    opts = decode_options(cfg, profile=profile, workers=workers or p["workers"], band=band, min_quality=min_quality,
                          batch_reads=p["batch_reads"], archive_tag=int(archive_tag, 16) if archive_tag else None)
    key, pw = _keys(key_file, passphrase_env)
    if output is None and extract_dir is None and not select:
        raise VNXConfigurationError("give --output (container), --extract DIR, or --select FILE --extract DIR")
    if select:
        res = de.decode_reads(reads, None, opts, select=select, select_dir=extract_dir or Path("."), key=key, passphrase=pw,
                              overwrite=force, progress=_progress_cb())
    else:
        import tempfile
        target = output
        tmp = None
        if output is None:
            tmp = tempfile.mkdtemp(prefix="vnx-decode-")
            target = Path(tmp) / "recovered.vnx"
        try:
            res = de.decode_reads(reads, target, opts, overwrite=force, partial_dir=partial_dir, key=key, passphrase=pw,
                                  progress=_progress_cb())
            if res.status == "SUCCESS" and extract_dir is not None:
                res.report["extract"] = ar.extract(target, extract_dir, key=key, passphrase=pw, overwrite=force)
        finally:
            if tmp:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
    if report:
        report.write_text(json.dumps(res.report, indent=2, sort_keys=True, default=str) + "\n")
    _emit({"status": res.status, **res.report})
    return 0 if res.status == "SUCCESS" else EXIT_PARTIAL if res.status == "PARTIAL" else 5


@app.command()
def decode(reads: Path, output: Optional[Path] = typer.Option(None, "--output", "-o", help="Recovered .vnx container."),
           extract_dir: Optional[Path] = typer.Option(None, "--extract", help="Also extract the verified files here."),
           partial_dir: Optional[Path] = typer.Option(None, "--partial-dir", help="On PARTIAL recovery, extract verified files here."),
           select: Optional[List[str]] = typer.Option(None, "--select", help="Random access: decode only these files."),
           profile: Optional[str] = typer.Option(None, help="Layout profile (default: detected from read lengths)."),
           workers: int = typer.Option(0, "--workers", "-w"), performance: Optional[str] = PERF_OPT,
           config: Optional[Path] = CONFIG_OPT, band: Optional[int] = typer.Option(None, help="Max net indel drift per read."),
           min_quality: Optional[int] = typer.Option(None, help="Phred below this → erasure."),
           archive_tag: Optional[str] = typer.Option(None, help="Hex archive tag when a pool holds several archives."),
           force: bool = FORCE_OPT, key_file: Optional[Path] = KEY_OPT, passphrase_env: Optional[str] = PW_OPT,
           report: Optional[Path] = typer.Option(None, help="Write the JSON report here too.")) -> None:
    """Reconstruct a verified VNX4 container from DNA reads (FASTA/FASTQ)."""
    _run(lambda: _decode(reads, output, extract_dir, partial_dir, select, profile, workers, performance, config, band, min_quality,
                         archive_tag, force, key_file, passphrase_env, report))


@app.command()
def validate(sequences: Path, config: Optional[Path] = typer.Option(None, "--constraints", help="Constraint JSON."),
             gc_min: Optional[int] = typer.Option(None), gc_max: Optional[int] = typer.Option(None),
             max_homopolymer: Optional[int] = typer.Option(None), motif: Optional[List[str]] = typer.Option(None, "--forbid"),
             max_reported: int = typer.Option(100)) -> None:
    """Check DNA sequences against configurable biological constraints (JSON diagnostics; exit 3 if any violate)."""
    def go():
        from .constraints import ConstraintConfig, validate_file
        cfg = ConstraintConfig.load(config) if config else ConstraintConfig()
        for name, v in (("gc_min_percent", gc_min), ("gc_max_percent", gc_max), ("max_homopolymer", max_homopolymer)):
            if v is not None:
                setattr(cfg, name, v)
        if motif:
            cfg.forbidden_motifs = list(motif)
        cfg.validate()
        rep = validate_file(sequences, cfg, max_reported=max_reported)
        _emit(rep)
        return 0 if rep["valid"] else 3
    _run(go)


@channel_app.command("simulate")
def channel_simulate(strands: Path, output: Path, config: Optional[Path] = typer.Option(None, "--config", "-c"),
                     seed: Optional[int] = typer.Option(None), coverage: Optional[float] = typer.Option(None),
                     substitution_rate: Optional[float] = typer.Option(None), insertion_rate: Optional[float] = typer.Option(None),
                     deletion_rate: Optional[float] = typer.Option(None), dropout_rate: Optional[float] = typer.Option(None),
                     workers: int = typer.Option(1, "--workers", "-w"), force: bool = FORCE_OPT) -> None:
    """Simulate storage + sequencing (SIMULATION): strands → reads (FASTQ/FASTA)."""
    def go():
        from . import channel as ch
        cfg = ch.ChannelConfig.load(config) if config else ch.ChannelConfig()
        for name, v in (("seed", seed), ("coverage", coverage), ("substitution_rate", substitution_rate),
                        ("insertion_rate", insertion_rate), ("deletion_rate", deletion_rate), ("dropout_rate", dropout_rate)):
            if v is not None:
                setattr(cfg, name, v)
        if coverage is not None and cfg.coverage_model == "fixed" and float(coverage) != int(coverage):
            cfg.coverage_model = "poisson"
        _emit(ch.simulate_file(strands, output, cfg.validate(), workers=workers, overwrite=force, progress=_progress_cb()))
    _run(go)


@app.command()
def benchmark(profile: str = typer.Option("balanced", "--profile", help="safe, balanced or maximum-throughput."),
              size: List[str] = typer.Option(["1MB"], "--size"), output: Optional[Path] = typer.Option(None, "--output", "-o"),
              human: bool = typer.Option(False, "--human")) -> None:
    """Run the benchmark suite (stage throughput + clean/noisy end-to-end); JSON (+ .md next to --output)."""
    def go():
        from . import bench, datagen
        doc = bench.run_suite(profile, tuple(datagen.parse_size(s) for s in size), str(output) if output else None)
        if human:
            typer.echo(bench.markdown_report(doc["results"]))
        else:
            _emit(doc)
    _run(go)


@app.command()
def sweep(config: Path, output: Optional[Path] = typer.Option(None, "--output", "-o")) -> None:
    """Run an error sweep (experiment config with a "sweep" section); prints the recovery curve."""
    def go():
        from . import experiment, sweep as sw
        cfg = experiment.load(config) if json.loads(config.read_text()).get("type") else json.loads(config.read_text())
        res = sw.run_sweep(cfg, progress=_progress_cb())
        if output:
            output.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
        typer.echo(sw.curve_table(res))
    _run(go)


@experiment_app.command("run")
def experiment_run(config: Path) -> None:
    """Run an experiment; writes environment.json, seed.json, input.sha256 and results.json next to the config."""
    def go():
        from . import experiment
        res = experiment.run(config, progress=_progress_cb())
        _emit({"status": "DONE", "directory": str(config.parent), "experiment": res.get("experiment")})
    _run(go)


@experiment_app.command("reproduce")
def experiment_reproduce(directory: Path) -> None:
    """Re-run an experiment directory and compare all deterministic results."""
    def go():
        from . import experiment
        rep = experiment.reproduce(directory)
        _emit(rep)
        return 0 if rep["reproduced"] else 1
    _run(go)


@app.command()
def generate(output: Path, size: str = typer.Option("1MB"), pattern: str = typer.Option("mixed"), seed: int = typer.Option(42)) -> None:
    """Generate deterministic test data (random, text, repetitive, binary, mixed)."""
    def go():
        from . import datagen
        _emit({"output": str(output), "size": datagen.parse_size(size), "pattern": pattern, "seed": seed,
               "sha256": datagen.generate(output, datagen.parse_size(size), pattern, seed)})
    _run(go)


@app.command()
def profiles() -> None:
    """List strand layout profiles and performance profiles."""
    from .config import PERFORMANCE_PROFILES
    from .frame import PROFILES
    out = {"layouts": {n: {**lay.to_dict(), "outer_K": k, "outer_M": m} for n, (lay, k, m) in PROFILES.items()},
           "performance": PERFORMANCE_PROFILES}
    typer.echo(json.dumps(out, indent=2))


# ---------------------------------------------------------------- experimental
@experimental_app.command("encode")
def experimental_encode(source: Path, output: Path, outer_code: str = typer.Option("lt-fountain"),
                        distribution: str = typer.Option("dense", help="dense or robust-soliton"),
                        profile: Optional[str] = typer.Option(None), data_symbols: Optional[int] = typer.Option(None, "-K"),
                        parity_symbols: Optional[int] = typer.Option(None, "-M"), workers: int = typer.Option(1, "--workers", "-w"),
                        force: bool = FORCE_OPT) -> None:
    """EXPERIMENTAL: encode with a non-default outer code (e.g. the GF(2) fountain code)."""
    def go():
        from . import encoder as en
        opts = en.DNAOptions(profile=profile or "v4-balanced", outer_code=outer_code, lt_distribution=distribution,
                             data_symbols=data_symbols, parity_symbols=parity_symbols, workers=workers, experimental=True)
        rep = en.encode_container(source, output, opts, overwrite=force)
        rep["stability"] = "EXPERIMENTAL"
        _emit(rep)
    _run(go)


@experimental_app.command("codec-compare")
def experimental_codec_compare(trials: int = typer.Option(100), output: Optional[Path] = typer.Option(None, "--output", "-o")) -> None:
    """EXPERIMENTAL: compare outer codes at the same redundancy budget under i.i.d. loss."""
    def go():
        from . import bench
        res = bench.codec_compare(trials=trials)
        if output:
            output.write_text(json.dumps(res, indent=2) + "\n")
        typer.echo(bench.markdown_report(res))
    _run(go)


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
