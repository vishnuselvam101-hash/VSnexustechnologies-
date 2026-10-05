#!/usr/bin/env python3
"""Write provenance.json for a lab run: VNX-DNA commit and backends, machine, and every third-party participant (pinned
clone SHA, licence file hash, patches, interpreter). Reads text/JSON and runs `git`; imports nothing from third-party codecs.
usage (from the VNX checkout): PYTHONPATH=src python benchmarks/competitors/lab/collect_provenance.py TOOLS_DIR RUN_DIR OUT.json [--extra JSON]"""
import argparse, hashlib, json, os, platform, subprocess, sys, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from benchmarks.v5.provenance import provenance as vnx_provenance  # noqa: E402


def run(*cmd, cwd=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.stdout.strip() if r.returncode == 0 else (r.stderr.strip() or None)
    except Exception as e:  # noqa
        return f"error: {e}"


def sha256(p: Path):
    h = hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()


def cpu():
    model = None; mhz = None
    for l in Path("/proc/cpuinfo").read_text().splitlines():
        if l.startswith("model name") and not model: model = l.split(":", 1)[1].strip()
        if l.startswith("cpu MHz") and not mhz: mhz = l.split(":", 1)[1].strip()
    mem = [l for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")][0]
    return {"model": model, "mhz_first_core": mhz, "logical_cpus": os.cpu_count(), "mem_total": mem.split(":", 1)[1].strip(),
            "kernel": platform.release(), "virtualisation": "shared VPS; other jobs may run concurrently (see loadavg per trial)",
            "loadavg_now": os.getloadavg()}


def participant(d: Path):
    info = {"dir": str(d), "head": run("git", "rev-parse", "HEAD", cwd=d), "remote": run("git", "remote", "get-url", "origin", cwd=d),
            "dirty_files": run("git", "status", "--porcelain", "--untracked-files=no", cwd=d)}
    for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "apache_licence_20"):
        if (d / n).exists(): info["licence_file"] = n; info["licence_sha256"] = sha256(d / n); break
    subs = run("git", "submodule", "status", cwd=d)
    if subs: info["submodules"] = subs.splitlines()
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("tools"); ap.add_argument("rundir"); ap.add_argument("out"); ap.add_argument("--extra")
    a = ap.parse_args()
    tools = Path(a.tools)
    from vnxdna.v5 import native_alignment as na
    from vnxdna.v6 import native_reads as nr, native_rs as ns
    import vnxdna
    prov = {"label": "recovery results SIMULATED; timing and memory MEASURED on this machine",
            "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "vnx": vnx_provenance(), "vnx_import_path": vnxdna.__file__, "PYTHONPATH": os.environ.get("PYTHONPATH"),
            "vnx_native_backends": {"alignment_v5": na.status(), "reads_v6": nr.status(), "rs_v6": ns.status()},
            "machine": cpu(), "tools": {}, "tool_versions": {"gcc": run("gcc", "--version").splitlines()[0], "cmake": run("cmake", "--version").splitlines()[0],
                                                                  "python_system": platform.python_version()}}
    for d in sorted(tools.iterdir()):
        if d.is_dir() and (d / ".git").exists(): prov["tools"][d.name] = participant(d)
    for patch in sorted((tools / "lab-driver" / "patches").glob("*")):
        prov.setdefault("patches", {})[patch.name] = sha256(patch)
    drv = tools / "lab-driver"
    prov["lab_driver"] = {"commit": run("git", "rev-parse", "HEAD", cwd=drv), "dirty": bool(run("git", "status", "--porcelain", cwd=drv)),
                          "files_sha256": {p.name: sha256(p) for p in sorted(drv.glob("*.py"))}}
    if (tools / "boost-1.83.0-install").exists(): prov["boost"] = {"version": "1.83.0 (program_options static, headers)", "tarball_sha256": sha256(tools / "boost-src" / "boost.tar.gz")}
    rd = Path(a.rundir)
    if (rd / "driver_meta.json").exists(): prov["driver_meta"] = json.loads((rd / "driver_meta.json").read_text())
    if a.extra: prov["extra"] = json.loads(a.extra)
    Path(a.out).write_text(json.dumps(prov, indent=1))
    print("wrote", a.out)
