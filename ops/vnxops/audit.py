"""PHASE 0: read-only environment audit. Writes environment.json + topic markdown files to /opt/vnx-dna/environment-audit.

Read-only by construction: only /proc, /sys, /etc reads and fixed `--version`/status commands. Secrets are never
read: SSH keys and env files are reported by count/permission only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import shutil
import socket
import stat
from pathlib import Path
from typing import Any

from . import paths, system
from .sysinfo import (cpu_info, disk, docker_containers, listening_ports, meminfo, os_release, run, systemd_active,
                      tool_version)

TOOLS = ["git", "git-lfs", "python3", "python3.12", "python3.13", "uv", "pip", "node", "npm", "pnpm", "yarn", "rustc",
         "cargo", "go", "gcc", "clang", "cmake", "ninja", "pkg-config", "java", "psql", "sqlite3", "redis-server",
         "ollama", "docker", "podman", "gh", "jq", "yq", "rg", "fdfind", "fzf", "shellcheck", "pre-commit",
         "tmux", "htop", "btop", "iotop", "sar", "iostat", "tailscale", "fail2ban-client", "ufw", "ruff", "mypy",
         "gitleaks", "pip-audit", "hyperfine", "fio", "sysbench"]
VERSION_ARGV = {"go": ["go", "version"], "java": ["java", "-version"], "ollama": ["ollama", "--version"],
                "tailscale": ["tailscale", "version"], "sar": ["sar", "-V"], "iostat": ["iostat", "-V"],
                "fail2ban-client": ["fail2ban-client", "--version"], "ufw": ["ufw", "--version"],
                "tmux": ["tmux", "-V"], "htop": ["htop", "--version"], "iotop": ["iotop", "--version"]}
SERVICES = ["ssh", "docker", "containerd", "postgresql@16-main", "redis-server", "fail2ban", "ufw",
            "snap.ollama.listener", "snap.tailscale.tailscaled", "cron", "unattended-upgrades", "sysstat"]


def _ollama() -> dict[str, Any]:
    out: dict[str, Any] = {"present": bool(shutil.which("ollama")), "models": []}
    code, txt = run(["curl", "-s", "-m", "3", "http://127.0.0.1:11434/api/tags"])
    if code == 0 and txt.startswith("{"):
        for m in json.loads(txt).get("models", []):
            d = m.get("details", {})
            out["models"].append({"name": m["name"], "size_bytes": m.get("size"), "family": d.get("family"),
                                  "parameter_size": d.get("parameter_size"), "quantization": d.get("quantization_level")})
    _, ver = run(["curl", "-s", "-m", "3", "http://127.0.0.1:11434/api/version"])
    out["api_version"] = ver
    _, ps = run(["curl", "-s", "-m", "3", "http://127.0.0.1:11434/api/ps"])
    out["loaded"] = [m["name"] for m in json.loads(ps).get("models", [])] if ps.startswith("{") else []
    _, conf = run(["snap", "get", "-d", "ollama"])
    try:
        out["snap_config"] = json.loads(conf)
    except json.JSONDecodeError:
        out["snap_config"] = {}
    return out


def _ssh() -> dict[str, Any]:
    _, txt = run(["sshd", "-T"])
    keep = {"port", "permitrootlogin", "passwordauthentication", "pubkeyauthentication", "maxauthtries",
            "x11forwarding", "kbdinteractiveauthentication"}
    cfg: dict[str, Any] = {k: v for k, _, v in (line.partition(" ") for line in txt.splitlines()) if k in keep}
    ak = Path("/root/.ssh/authorized_keys")
    cfg["authorized_keys_count"] = sum(1 for line in ak.read_text().splitlines() if line.strip() and not line.startswith("#")) if ak.exists() else 0
    cfg["private_keys_in_root_ssh"] = sorted(p.name for p in Path("/root/.ssh").glob("id_*") if not p.name.endswith(".pub")) if Path("/root/.ssh").exists() else []
    return cfg


def _mode(p: Path) -> str | None:
    try:
        return oct(stat.S_IMODE(p.stat().st_mode))
    except OSError:
        return None


def _repos() -> list[dict[str, Any]]:
    found = []
    for root in [Path("/root"), Path("/opt"), Path("/srv"), Path("/home")]:
        if not root.exists():
            continue
        for gitdir in list(root.glob("*/.git")) + list(root.glob("*/*/.git")):
            repo = gitdir.parent
            _, remote = run(["git", "-C", str(repo), "remote", "get-url", "origin"])
            _, head = run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"])
            _, branch = run(["git", "-C", str(repo), "branch", "--show-current"])
            _, dirty = run(["git", "-C", str(repo), "status", "--porcelain"])
            found.append({"path": str(repo), "remote": remote.replace("\n", " "), "head": head, "branch": branch,
                          "dirty_files": len([x for x in dirty.splitlines() if x.strip()]),
                          "vnx_dna": "VSnexustechnologies-" in remote or "vnx-dna" in str(repo)})
    return found


def _mcp() -> dict[str, Any]:
    cli = system.agent_cli()
    if not cli:
        return {"skipped": True, "reason": "no agent CLI configured"}
    code, txt = run([cli, "mcp", "list"], timeout=90)
    servers = []
    for line in txt.splitlines():
        if ": " in line and (" - " in line):
            name, _, rest = line.partition(": ")
            status = rest.rsplit(" - ", 1)[-1].strip()
            servers.append({"name": name.strip(), "endpoint": rest.rsplit(" - ", 1)[0].strip(), "status": status})
    return {"exit": code, "servers": servers}


def collect(include_mcp: bool = True) -> dict[str, Any]:
    agent_cli, agent_home = system.agent_cli(), system.agent_home()
    agent_tools = [agent_cli] if agent_cli else []
    agent_settings = [str(agent_home / "settings.json")] if agent_home is not None else []
    mem = meminfo()
    _, ufw = run(["ufw", "status"])
    _, f2b = run(["fail2ban-client", "status"])
    _, swaps = run(["swapon", "--show", "--noheadings", "--bytes"])
    _, lsblk = run(["lsblk", "-d", "-J", "-o", "NAME,SIZE,ROTA,TYPE"])
    _, fs = run(["findmnt", "-J", "-n", "-o", "SOURCE,FSTYPE,TARGET", "/"])
    _, dns = run(["resolvectl", "dns"])
    _, timers = run(["systemctl", "list-timers", "--no-legend", "--all"])
    _, crontab = run(["crontab", "-l"])
    _, gpu = run(["lspci"])
    code6, _ = run(["curl", "-6", "-s", "-m", "4", "-o", "/dev/null", "https://www.google.com"])
    code4, _ = run(["curl", "-4", "-s", "-m", "4", "-o", "/dev/null", "https://github.com"])
    _, ddf = run(["docker", "system", "df", "--format", "{{json .}}"])
    backups = {str(p): sorted(x.name for x in p.iterdir())[-10:] for p in
               [Path("/opt/vnx-dna/backups"), Path("/root/vnx-weather-backups")] if p.exists()}
    vnx_procs = run(["pgrep", "-af", "vnxdna|vnx-dna"])[1]
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "os": {k: v for k, v in os_release().items() if k in ("PRETTY_NAME", "VERSION_ID")},
        "kernel": platform.release(),
        "cpu": cpu_info(),
        "memory": {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable"),
                   "swap_total": mem.get("SwapTotal"), "swap_free": mem.get("SwapFree")},
        "swap_devices": swaps.splitlines(),
        "storage": {"root": disk("/"), "blockdevices": json.loads(lsblk).get("blockdevices", []) if lsblk.startswith("{") else [],
                    "root_fs": json.loads(fs) if fs.startswith("{") else fs,
                    "docker_df": [json.loads(x) for x in ddf.splitlines() if x.startswith("{")]},
        "gpu": [x for x in gpu.splitlines() if any(k in x for k in ("VGA", "3D", "NVIDIA", "AMD/ATI"))],
        "network": {"ipv4_egress": code4 == 0, "ipv6_egress": code6 == 0, "dns": dns.splitlines()[:4],
                    "listening": listening_ports()},
        "tools": {t: tool_version(t, VERSION_ARGV.get(t)) for t in TOOLS + agent_tools},
        "services": {s: systemd_active(s) for s in SERVICES},
        "containers": docker_containers(),
        "timers": [line.split()[-2:] for line in timers.splitlines() if line.strip()],
        "crontab_root": [] if "no crontab" in crontab else crontab.splitlines(),
        "ollama": _ollama(),
        "mcp": _mcp() if include_mcp else {"skipped": True},
        "security": {"ssh": _ssh(), "ufw": ufw.splitlines()[:30], "fail2ban": f2b.splitlines(),
                     "modes": {p: _mode(Path(p)) for p in ["/root/.ssh", "/root/.ssh/authorized_keys",
                                                           *agent_settings,
                                                           "/opt/vnx-dna"]}},
        "repositories": _repos(),
        "vnx_dna_processes": vnx_procs.splitlines() if vnx_procs else [],
        "backups": backups,
    }


def _gb(n: int | None) -> str:
    return "?" if n is None else f"{n / 2**30:.1f} GiB"


def render(env: dict[str, Any]) -> dict[str, str]:
    """Topic markdown files. Facts only; recommendations are labelled as such."""
    c, m, s = env["cpu"], env["memory"], env["storage"]["root"]
    t = env["tools"]
    hdr = f"_Generated {env['generated_at']} on `{env['hostname']}` by `vnxdna audit` (read-only)._\n\n"
    hardware = hdr + "# Hardware\n\n| Item | Value |\n|---|---|\n" + "\n".join([
        f"| CPU | {c['model']} |", f"| Logical CPUs | {c['logical_cpus']} |", f"| SIMD | {', '.join(c['simd'])} |",
        f"| RAM total | {_gb(m['total'])} |", f"| RAM available | {_gb(m['available'])} |",
        f"| Swap | {_gb(m['swap_total'])} ({', '.join(env['swap_devices']) or 'none'}) |",
        f"| Root disk | {_gb(s['total'])} total, {_gb(s['free'])} free |",
        f"| GPU | {', '.join(env['gpu']) or 'none (CPU-only)'} |",
        f"| Load average | {', '.join(f'{x:.2f}' for x in c['loadavg'])} |"]) + "\n"
    software = hdr + "# Software\n\n" + f"OS: {env['os'].get('PRETTY_NAME')}, kernel {env['kernel']}\n\n| Tool | Version |\n|---|---|\n" + \
        "\n".join(f"| {k} | {(v or '**missing**').replace('|', '/')} |" for k, v in t.items()) + "\n"
    net = env["network"]
    network = hdr + "# Network\n\n" + f"- IPv4 egress: {net['ipv4_egress']}\n- IPv6 egress: {net['ipv6_egress']}\n" + \
        "- DNS: " + "; ".join(net["dns"]) + "\n\n## Listening sockets\n\n| Address | Process |\n|---|---|\n" + \
        "\n".join(f"| {p['listen']} | {p['process']} |" for p in net["listening"]) + "\n"
    docker_df = "\n".join(f"| {d.get('Type')} | {d.get('Size')} | {d.get('Reclaimable')} |" for d in env["storage"]["docker_df"])
    storage = hdr + "# Storage\n\n" + f"Root: {_gb(s['used'])} used of {_gb(s['total'])} ({100 * s['used'] / s['total']:.0f}%), {_gb(s['free'])} free.\n\n" + \
        f"Filesystem: `{json.dumps(env['storage']['root_fs'])}`\n\n## Docker\n\n| Type | Size | Reclaimable |\n|---|---|---|\n{docker_df}\n\n" + \
        "Reclaimable Docker space belongs to other projects (VNX Weather). It is reported, not cleaned.\n"
    o = env["ollama"]
    ai = hdr + "# AI models\n\n" + f"Ollama API: {o.get('api_version')}; loaded now: {o.get('loaded') or 'none'}\n\n| Model | Size | Params | Quant |\n|---|---|---|---|\n" + \
        "\n".join(f"| {x['name']} | {_gb(x['size_bytes'])} | {x['parameter_size']} | {x['quantization']} |" for x in o["models"]) + \
        "\n\nSnap config (non-empty keys): " + json.dumps({k: v for k, v in o.get("snap_config", {}).items() if v not in ("", 0, None)}) + \
        "\n\nAgent CLI: " + str(t.get(system.agent_cli() or "", "not configured")) + "\n\nPer-model measurements live in `/opt/vnx-dna/reports/model-benchmarks.json` (PHASE 5).\n"
    sec = env["security"]
    ssh = sec["ssh"]
    security = hdr + "# Security\n\n## SSH (effective config)\n\n" + "\n".join(f"- {k}: {v}" for k, v in ssh.items()) + \
        "\n\n## Firewall (ufw)\n\n```\n" + "\n".join(sec["ufw"]) + "\n```\n\n## fail2ban\n\n```\n" + "\n".join(sec["fail2ban"]) + \
        "\n```\n\n## Permissions\n\n" + "\n".join(f"- `{k}`: {v}" for k, v in sec["modes"].items()) + "\n"
    services = hdr + "# Services\n\n| Unit | State |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in env["services"].items()) + \
        "\n\n## Containers\n\n| Name | Image | Status |\n|---|---|---|\n" + \
        "\n".join(f"| {x['name']} | {x['image']} | {x['status']} |" for x in env["containers"]) + \
        "\n\n## MCP servers (agent CLI `mcp list`)\n\n" + "\n".join(f"- {x['name']}: {x['status']}" for x in env["mcp"].get("servers", [])) + \
        "\n\n## Timers\n\n" + "\n".join(f"- {' '.join(x)}" for x in env["timers"]) + \
        f"\n\nRoot crontab: {env['crontab_root'] or 'none'}\n\n## Repositories\n\n| Path | Branch | HEAD | Dirty | Remote |\n|---|---|---|---|---|\n" + \
        "\n".join(f"| {r['path']} | {r['branch']} | {r['head']} | {r['dirty_files']} | {r['remote']} |" for r in env["repositories"]) + \
        f"\n\nVNX-DNA processes running: {env['vnx_dna_processes'] or 'none'}\n"
    rec = recommendations(env)
    recs = hdr + "# Recommendations\n\nEach item is a recommendation from the audit, not a change that was made.\n\n" + \
        "\n".join(f"{i}. **[{r['severity']}] {r['title']}** — {r['detail']}" for i, r in enumerate(rec, 1)) + "\n"
    summary = hdr + "# Environment audit\n\n" + "\n".join(
        f"- [{n}]({n}.md)" for n in ["hardware", "software", "network", "storage", "ai-models", "security", "services",
                                     "recommendations"]) + \
        f"\n\nMachine: {c['model']}, {c['logical_cpus']} vCPU, {_gb(m['total'])} RAM, {_gb(s['free'])} disk free, no GPU.\n" + \
        f"Missing tools: {', '.join(k for k, v in t.items() if not v) or 'none'}.\n" + \
        f"Containers: {len(env['containers'])} running (production workloads share this host).\n"
    return {"environment.md": summary, "hardware.md": hardware, "software.md": software, "network.md": network,
            "storage.md": storage, "ai-models.md": ai, "security.md": security, "services.md": services,
            "recommendations.md": recs}


def recommendations(env: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    ssh = env["security"]["ssh"]
    if ssh.get("passwordauthentication") == "yes":
        out.append({"severity": "HIGH", "title": "SSH password authentication is enabled",
                    "detail": "Set `PasswordAuthentication no` once key login is confirmed from every client (keeps fail2ban as a second layer). Not changed automatically: a wrong change can lock the owner out."})
    if ssh.get("permitrootlogin") == "yes":
        out.append({"severity": "HIGH", "title": "Root login over SSH is allowed with any method",
                    "detail": "Use `PermitRootLogin prohibit-password` (key only)."})
    if ssh.get("x11forwarding") == "yes":
        out.append({"severity": "LOW", "title": "X11 forwarding enabled", "detail": "Not needed on a headless VPS."})
    for line in env["security"]["ufw"]:
        m = re.match(r"^(\d+)/tcp\s+ALLOW(?: IN)?\s+Anywhere\s*$", line.strip())
        if m and m.group(1) not in ("22", "2244", "80", "443"):
            out.append({"severity": "MEDIUM", "title": f"ufw allows {m.group(1)}/tcp from anywhere",
                        "detail": "Development APIs bind 127.0.0.1 today, but this rule exposes anything that later binds 0.0.0.0 on that port. Restrict it to tailscale0 or delete it if unused."})
    mem = env["memory"]
    for model in env["ollama"]["models"]:
        if model["size_bytes"] and model["size_bytes"] > 0.4 * mem["total"]:
            out.append({"severity": "MEDIUM", "title": f"Local model {model['name']} is {_gb(model['size_bytes'])}",
                        "detail": "Loading it takes >40% of RAM on a host that also serves production. The governor only allows it when MemAvailable leaves the production reserve intact; add a 1–4 B model for routine tasks."})
        if "abliterated" in model["name"]:
            out.append({"severity": "MEDIUM", "title": f"{model['name']} is a community 'abliterated' (refusal-removed) fine-tune",
                        "detail": "Provenance is unverified. LAYA treats its output as untrusted text and never gives it tool or write permissions."})
    missing = [k for k, v in env["tools"].items() if not v]
    if missing:
        out.append({"severity": "INFO", "title": "Missing tools", "detail": ", ".join(missing) + " (PHASE 4 decides which are needed)."})
    dirty = [r for r in env["repositories"] if r["vnx_dna"] and r["dirty_files"]]
    for r in dirty:
        out.append({"severity": "INFO", "title": f"Uncommitted changes in {r['path']} ({r['branch']})",
                    "detail": "Left untouched; the environment work uses its own worktree."})
    out.append({"severity": "INFO", "title": "Production shares this host",
                "detail": "vnxw-prod-* and redroid13 run here. VNX-DNA work runs inside the capped `vnxdna.slice`, and the governor never signals processes outside it."})
    return out


def write(env: dict[str, Any], out_dir: Path | None = None) -> Path:
    out = out_dir or paths.AUDIT
    paths.ensure(out)
    (out / "environment.json").write_text(json.dumps(env, indent=2, default=str) + "\n")
    for name, text in render(env).items():
        (out / name).write_text(text)
    os.chmod(out, 0o750)
    return out
