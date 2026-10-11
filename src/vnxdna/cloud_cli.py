"""``vnx-dna cloud ...``: run jobs on the VNX-DNA Cloud API from the terminal.

Quick start (free 7-day trial keys: vishnu@vsnexustechnologies.com)::

    vnx-dna cloud login                      # paste your API key once
    vnx-dna cloud roundtrip report.pdf       # encode -> simulated sequencing -> decode -> verified file
    vnx-dna cloud feedback --rating 4 --would-pay maybe --use-case "lab archives"

All channel results are SIMULATED: no DNA is synthesised or sequenced.
"""
from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import typer

from .cli_v1 import _run

cloud_app = typer.Typer(help="Run encode / simulated sequencing / decode jobs on the VNX-DNA Cloud API "
                             "(results are SIMULATED). Start with `vnx-dna cloud login`.", no_args_is_help=True)

JsonOut = typer.Option(False, "--json", help="Print the service's JSON response.")
Wait = typer.Option(True, "--wait/--no-wait", help="Wait for the job to finish (default) or return its id at once.")


def _client():
    from .cloud import CloudClient
    return CloudClient()


def _show(data: Any, as_json: bool, lines: list[str]) -> None:
    typer.echo(json.dumps(data, indent=2) if as_json else "\n".join(lines))


def _job_lines(job: dict[str, Any]) -> list[str]:
    lines = [f"{job['id']}  {job['kind']:8} {job['status']}  (SIMULATED)"]
    result = job.get("result") or {}
    if result.get("summary"):
        lines.append(f"  summary  {json.dumps(result['summary'])}")
    if job.get("error") or result.get("error"):
        lines.append(f"  error    {job.get('error') or result.get('error')}")
    for f in job.get("files", []):
        lines.append(f"  file     {f['name']}  (vnx-dna cloud download {job['id']} {f['name']})")
    return lines


def _submit(job: dict[str, Any], wait: bool, as_json: bool) -> dict[str, Any]:
    if wait:
        job = _client().wait(job["id"])
    _show(job, as_json, _job_lines(job))
    return job


@cloud_app.command("login")
def login(api_key: str = typer.Option(..., prompt="API key", hide_input=True, help="Your vnx_sk_... key."),
          url: Optional[str] = typer.Option(None, help="Service address (default: the public API).")) -> None:
    """Save your API key (to ~/.config/vnxdna/cloud.json, readable only by you) and check it works."""
    def body() -> None:
        from .cloud import CloudClient, save_key
        me = CloudClient(key=api_key.strip(), url=url).me()  # check before saving
        path = save_key(api_key, url)
        ends = f", trial ends {me['trial_ends']}" if me.get("trial_ends") else ""
        typer.echo(f"logged in as {me['name']} (plan {me['plan']}{ends}); key saved to {path}")
    _run(body)


@cloud_app.command("me")
def me(as_json: bool = JsonOut) -> None:
    """Your plan, limits, usage and trial end date."""
    def body() -> None:
        d = _client().me()
        lim, use = d["limits"], d["usage"]
        _show(d, as_json, [f"{d['name']}  plan {d['plan']}" + (f"  trial ends {d['trial_ends']}" if d.get("trial_ends")
                                                                 else ""),
                           f"  jobs     {use.get('jobs_last_24h', 0)} of {lim['daily_jobs']} in the last 24 h",
                           f"  input    {use.get('month_input_bytes', 0):,} of {lim['monthly_input_bytes']:,} bytes "
                           "this month",
                           f"  feedback {'sent, thank you' if d.get('feedback_given') else 'not sent yet'}"])
    _run(body)


@cloud_app.command("options")
def options() -> None:
    """Encoding profiles, channel models and decoders the service offers."""
    _run(lambda: typer.echo(json.dumps(_client().options(), indent=2)))


@cloud_app.command("encode")
def encode(files: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Files to store."),
           profile: str = typer.Option("v4-balanced", help="Encoding profile (see `vnx-dna cloud options`)."),
           wait: bool = Wait, as_json: bool = JsonOut) -> None:
    """Encode files into DNA strand designs (strands.fasta)."""
    _run(lambda: _submit(_client().encode(list(files), profile), wait, as_json))


@cloud_app.command("simulate")
def simulate(from_job: Optional[str] = typer.Option(None, "--from-job", help="A succeeded encode job."),
             strands: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Or a strands FASTA."),
             model: str = typer.Option("illumina-like", help="Channel model (see `vnx-dna cloud options`)."),
             coverage: float = typer.Option(10.0, min=1.0, max=50.0, help="Mean reads per strand."),
             seed: int = typer.Option(0, min=0, help="Random seed (same seed, same reads)."),
             wait: bool = Wait, as_json: bool = JsonOut) -> None:
    """Simulate synthesis + sequencing errors on encoded strands (reads.fastq). SIMULATED."""
    _run(lambda: _submit(_client().simulate(model, coverage, seed, from_job=from_job, strands=strands), wait, as_json))


@cloud_app.command("decode")
def decode(from_job: Optional[str] = typer.Option(None, "--from-job", help="A succeeded simulate job."),
           reads: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Or a reads FASTQ/FASTA."),
           decoder: str = typer.Option("standard", help="standard, or nanopore (EXPERIMENTAL)."),
           wait: bool = Wait, as_json: bool = JsonOut) -> None:
    """Decode reads back into the original files (recovered.zip)."""
    _run(lambda: _submit(_client().decode(decoder, from_job=from_job, reads=reads), wait, as_json))


@cloud_app.command("jobs")
def jobs(limit: int = typer.Option(20, min=1, max=200), as_json: bool = JsonOut) -> None:
    """Your recent jobs, newest first."""
    def body() -> None:
        data = _client().jobs(limit)
        rows = data.get("jobs", data) if isinstance(data, dict) else data
        _show(data, as_json, [f"{j['id']}  {j['kind']:8} {j['status']:9} {j.get('input_bytes', 0):>12,} B"
                              for j in rows] or ["no jobs yet"])
    _run(body)


@cloud_app.command("job")
def job(job_id: str, as_json: bool = JsonOut) -> None:
    """One job's status, result and files."""
    _run(lambda: _show(j := _client().job(job_id), as_json, _job_lines(j)))


@cloud_app.command("download")
def download(job_id: str, name: str = typer.Argument(..., help="File name shown by `vnx-dna cloud job`."),
             output: Optional[Path] = typer.Option(None, "-o", "--output", help="Default: the file name."),
             force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing file.")) -> None:
    """Download one result file of a job."""
    def body() -> None:
        from .core.taxonomy import OutputError
        out = output or Path(name)
        if out.exists() and not force:
            raise OutputError(f"{out} exists; use --force to overwrite")
        data = _client().download(job_id, name)
        out.write_bytes(data)
        typer.echo(f"saved {out} ({len(data):,} bytes)")
    _run(body)


@cloud_app.command("delete")
def delete(job_id: str) -> None:
    """Delete a job and its files from the service now (they are also deleted automatically later)."""
    _run(lambda: (_client().delete(job_id), typer.echo(f"deleted {job_id}")))


@cloud_app.command("roundtrip")
def roundtrip(files: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Files to send through."),
              profile: str = typer.Option("v4-balanced"), model: str = typer.Option("illumina-like"),
              coverage: float = typer.Option(10.0, min=1.0, max=50.0), seed: int = typer.Option(0, min=0),
              decoder: str = typer.Option("standard"),
              output_dir: Path = typer.Option(Path("recovered"), "-o", "--output-dir", file_okay=False,
                                              help="Where the recovered files go.")) -> None:
    """Encode -> simulated sequencing -> decode, then check every recovered file is byte-identical to yours."""
    def body() -> None:
        from .core.taxonomy import IntegrityError, OutputError
        c = _client()
        enc = c.wait(c.encode(list(files), profile)["id"])
        typer.echo(f"encoded    {enc['id']}")
        sim = c.wait(c.simulate(model, coverage, seed, from_job=enc["id"])["id"])
        typer.echo(f"sequenced  {sim['id']}  ({model}, coverage {coverage:g}, SIMULATED)")
        dec = c.wait(c.decode(decoder, from_job=sim["id"])["id"])
        typer.echo(f"decoded    {dec['id']}")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise OutputError(f"{output_dir} is not empty; choose another --output-dir")
        output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(BytesIO(c.download(dec["id"], "recovered.zip"))) as z:
            names = {Path(n).name: n for n in z.namelist()}
            mismatched = []
            for f in files:
                member = names.get(f.name)
                data = z.read(member) if member else None
                if data is None or data != f.read_bytes():
                    mismatched.append(f.name)
                    continue
                (output_dir / f.name).write_bytes(data)
        if mismatched:
            raise IntegrityError(f"recovered files differ from the originals: {', '.join(mismatched)}")
        typer.echo(f"verified   {len(files)} file(s) byte-identical -> {output_dir}/  (channel SIMULATED)")
    _run(body)


@cloud_app.command("feedback")
def feedback(rating: int = typer.Option(..., min=1, max=5, prompt="Rating 1-5", help="1 = poor, 5 = excellent."),
             would_pay: str = typer.Option(..., prompt="Would you pay for a pilot? (yes/maybe/no)"),
             use_case: str = typer.Option(..., prompt="What would you use it for"),
             comments: str = typer.Option("", prompt="What worked, what was missing (optional)")) -> None:
    """Tell us how the trial went; feedback earns a discount on a pilot."""
    def body() -> None:
        from .core.taxonomy import InvalidInputError
        if would_pay not in ("yes", "maybe", "no"):
            raise InvalidInputError("--would-pay must be yes, maybe or no")
        typer.echo(_client().feedback(rating, would_pay, use_case, comments)["message"])
    _run(body)
