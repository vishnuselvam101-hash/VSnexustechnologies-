"""PHASE 5: model registry, measurement and selection.

Selection is deterministic: given a task type, the registry (config/laya/models.yaml), measured model numbers
(reports/model-benchmarks.json), and the governor's live snapshot, it returns one model plus the reasons. It picks
the cheapest model that meets the task's minimum tier/quality and fits in RAM; a local model that does not fit
escalates to a stronger tier rather than silently downgrading.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import paths

OLLAMA = "http://127.0.0.1:11434"
BENCH_FILE = paths.REPORTS / "model-benchmarks.json"


def registry(path: Path | None = None) -> dict[str, Any]:
    """The model registry with "{agent_cli}" in commands replaced by the machine's agent CLI (system.agent_cli()).

    Without a configured agent CLI those models are marked ``configured: False`` and never selected.
    """
    from . import system
    reg = yaml.safe_load((path or paths.LAYA_CONFIG / "models.yaml").read_text())
    cli = system.agent_cli()
    for cfg in reg.get("models", {}).values():
        cmd = cfg.get("command")
        if cmd and "{agent_cli}" in cmd:
            cfg["configured"] = cli is not None
            cfg["command"] = [cli if c == "{agent_cli}" else c for c in cmd] if cli else cmd
    return reg


def _post(endpoint: str, body: dict[str, Any], timeout: float = 600) -> dict[str, Any]:
    req = urllib.request.Request(OLLAMA + endpoint, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get(endpoint: str, timeout: float = 5) -> dict[str, Any]:
    with urllib.request.urlopen(OLLAMA + endpoint, timeout=timeout) as r:
        return json.loads(r.read())


def ollama_ready() -> bool:
    try:
        _get("/api/version", timeout=3)
        return True
    except OSError:
        return False


def installed() -> list[str]:
    try:
        return [m["name"] for m in _get("/api/tags").get("models", [])]
    except OSError:
        return []


def loaded() -> list[dict[str, Any]]:
    try:
        return _get("/api/ps").get("models", [])
    except OSError:
        return []


def unload(name: str) -> bool:
    try:
        _post("/api/generate", {"model": name, "keep_alive": 0}, timeout=60)
        return True
    except OSError:
        return False


def generate(model: str, prompt: str, *, system: str | None = None, num_ctx: int = 4096, max_tokens: int = 512,
             keep_alive: str = "2m", tools: list[dict[str, Any]] | None = None, timeout: float = 600) -> dict[str, Any]:
    """One non-streaming chat turn against a local model (temperature 0, fixed seed: reproducible where Ollama is)."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: dict[str, Any] = {"model": model, "messages": msgs, "stream": False, "keep_alive": keep_alive,
                            "options": {"temperature": 0, "seed": 7, "num_ctx": num_ctx, "num_predict": max_tokens}}
    if tools:
        body["tools"] = tools
    return _post("/api/chat", body, timeout=timeout)


def remote(prompt: str, model_cfg: dict[str, Any], timeout: float = 600) -> dict[str, Any]:
    """Tier 3/4: the external agent CLI in print mode. Returns the parsed JSON result (or an error record)."""
    t0 = time.time()
    try:
        # Prompt on stdin: --allowedTools/--disallowedTools are variadic and would swallow a trailing argument.
        p = subprocess.run(model_cfg["command"], input=prompt, capture_output=True, text=True, timeout=timeout,
                           cwd=tempfile.gettempdir())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": repr(exc), "seconds": round(time.time() - t0, 2)}
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": (p.stderr or p.stdout)[-400:], "seconds": round(time.time() - t0, 2)}
    return {"ok": not out.get("is_error", False), "text": out.get("result", ""), "cost_usd": out.get("total_cost_usd"),
            "seconds": round(time.time() - t0, 2), "raw_keys": sorted(out)}


# --------------------------------------------------------------------------------------------- measurement

CODING_TASKS = [
    ("reverse_complement", "def reverse_complement(seq: str) -> str  # DNA A/C/G/T, uppercase in, uppercase out",
     ["assert reverse_complement('ACGT') == 'ACGT'", "assert reverse_complement('AAAC') == 'GTTT'",
      "assert reverse_complement('') == ''"]),
    ("gc_content", "def gc_content(seq: str) -> float  # fraction of G or C, 0.0 for an empty string",
     ["assert gc_content('GGCC') == 1.0", "assert abs(gc_content('ACGT') - 0.5) < 1e-9", "assert gc_content('') == 0.0"]),
    ("max_homopolymer", "def max_homopolymer(seq: str) -> int  # length of the longest run of one repeated base",
     ["assert max_homopolymer('ACGT') == 1", "assert max_homopolymer('AAACCCCG') == 4", "assert max_homopolymer('') == 0"]),
    ("hamming", "def hamming(a: str, b: str) -> int  # raise ValueError if lengths differ",
     ["assert hamming('ACGT','ACGA') == 1", "assert hamming('','') == 0",
      "try:\n    hamming('A','AC')\n    raise SystemExit(1)\nexcept ValueError:\n    pass"]),
    ("bytes_to_dna", "def bytes_to_dna(data: bytes) -> str  # 2 bits per base, A=00 C=01 G=10 T=11, most significant bits first",
     ["assert bytes_to_dna(b'\\x00') == 'AAAA'", "assert bytes_to_dna(b'\\x1b') == 'ACGT'", "assert bytes_to_dna(b'') == ''"]),
]
TOOL_SPEC = [{"type": "function", "function": {
    "name": "gc_content", "description": "Compute the GC fraction of a DNA sequence",
    "parameters": {"type": "object", "properties": {"sequence": {"type": "string"}}, "required": ["sequence"]}}}]


def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def run_untrusted_python(code: str, timeout: float = 10) -> tuple[bool, str]:
    """Execute model-written code: no network (unshare -n), isolated interpreter, temp cwd, governed scope, timeout."""
    from . import governor
    with tempfile.TemporaryDirectory(prefix="untrusted-", dir=str(paths.run_dir())) as tmp:
        f = Path(tmp) / "t.py"
        f.write_text(code)
        cmd = ["unshare", "-n", "python3", "-I", str(f)]
        rec = governor.run(cmd, cls="misc", mem=256 * 2**20, timeout=timeout, wait=True, cwd=tmp,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return rec["exit_code"] == 0, rec["status"]


def _rss_ollama() -> int:
    total = 0
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            txt = status.read_text()
        except OSError:
            continue
        if re.search(r"^Name:\s+(ollama|llama-server)", txt, re.M):
            m = re.search(r"^VmRSS:\s+(\d+) kB", txt, re.M)
            total += int(m.group(1)) * 1024 if m else 0
    return total


def bench_local(name: str, num_ctx: int = 4096) -> dict[str, Any]:
    info = _post("/api/show", {"model": name}, timeout=30)
    ctx = next((v for k, v in info.get("model_info", {}).items() if k.endswith("context_length")), None)
    base_rss = _rss_ollama()
    t0 = time.time()
    first = generate(name, "Reply with the single word: ready", num_ctx=num_ctx, max_tokens=8, keep_alive="5m")
    startup = time.time() - t0
    loaded_rss = _rss_ollama()
    ps = next((m for m in loaded() if m["name"] == name), {})
    speed = generate(name, "Explain in about 120 words how Reed-Solomon codes correct erasures.", num_ctx=num_ctx,
                     max_tokens=200)
    gen_tps = speed["eval_count"] / (speed["eval_duration"] / 1e9) if speed.get("eval_duration") else None
    pp_tps = speed["prompt_eval_count"] / (speed["prompt_eval_duration"] / 1e9) if speed.get("prompt_eval_duration") else None
    passed, details = 0, []
    for fn, sig, tests in CODING_TASKS:
        out = generate(name, f"Write a Python function `{sig}`. Standard library only. Reply with only one python code block.",
                       num_ctx=num_ctx, max_tokens=400)
        code = _extract_code(out["message"]["content"]) + "\n\n" + "\n".join(tests) + "\n"
        ok, status = run_untrusted_python(code)
        passed += ok
        details.append({"task": fn, "pass": ok, "status": status})
    tool = generate(name, "What is the GC content of ACGTGGCC? Use the tool.", tools=TOOL_SPEC, num_ctx=num_ctx, max_tokens=200)
    calls = tool.get("message", {}).get("tool_calls") or []
    tool_ok = bool(calls) and calls[0]["function"]["name"] == "gc_content" and \
        str(calls[0]["function"].get("arguments", {}).get("sequence", "")).upper() == "ACGTGGCC"
    unload(name)
    return {"model": name, "context_length": ctx, "measured_num_ctx": num_ctx, "load_and_first_token_s": round(startup, 2),
            "load_duration_s": round(first.get("load_duration", 0) / 1e9, 2),
            "ram_rss_bytes": max(0, loaded_rss - base_rss), "ollama_reported_size_bytes": ps.get("size"),
            "generation_tokens_per_s": round(gen_tps, 2) if gen_tps else None,
            "prompt_tokens_per_s": round(pp_tps, 2) if pp_tps else None,
            "coding_benchmark": {"passed": passed, "total": len(CODING_TASKS), "tasks": details},
            "tool_call_correct": tool_ok, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def bench_remote(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    fn, sig, tests = CODING_TASKS[2]
    r = remote(f"Write a Python function `{sig}`. Standard library only. Reply with only one python code block.", cfg, timeout=300)
    ok = False
    if r.get("ok"):
        ok, _ = run_untrusted_python(_extract_code(r["text"]) + "\n\n" + "\n".join(tests) + "\n")
    return {"model": name, "available": r.get("ok", False), "latency_s": r.get("seconds"), "cost_usd": r.get("cost_usd"),
            "coding_probe": {"task": fn, "pass": ok}, "error": r.get("error"),
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def bench_all(include_remote: bool = True, only: list[str] | None = None) -> dict[str, Any]:
    from . import governor
    reg = registry()
    results: dict[str, Any] = json.loads(BENCH_FILE.read_text()) if BENCH_FILE.exists() else {}
    pol, st = governor.Policy.load(), governor.State()
    for name, cfg in reg["models"].items():
        if only and name not in only:
            continue
        if cfg["backend"] == "ollama":
            if name not in installed():
                results[name] = {"model": name, "error": "not installed"}
                continue
            est = governor.parse_size(cfg.get("ram_estimate", "2GiB"))
            d = governor.admit("llm", est, pol, st)
            if not d.admitted:
                results[name] = {"model": name, "skipped": d.reason}
                continue
            for other in loaded():  # one local model at a time
                unload(other["name"])
            st.set("llm_loaded_by_laya", [name])
            try:
                results[name] = bench_local(name)
            finally:
                st.set("llm_loaded_by_laya", [])
        elif cfg["backend"] == "agent-cli" and cfg.get("configured", True) and include_remote and cfg["tier"] == 3:
            results[name] = bench_remote(name, cfg)
    paths.ensure(paths.REPORTS)
    BENCH_FILE.write_text(json.dumps(results, indent=2) + "\n")
    return results


# --------------------------------------------------------------------------------------------- selection


def select(task_type: str, *, snapshot: dict[str, Any] | None = None, allow_remote: bool = True,
           reg: dict[str, Any] | None = None, bench: dict[str, Any] | None = None,
           min_free: int | None = None, installed_models: list[str] | None = None,
           loaded_models: list[str] | None = None) -> dict[str, Any]:
    """Deterministic model choice for a task. Returns {model, tier, reasons, rejected}."""
    from . import governor
    reg = reg or registry()
    bench = bench if bench is not None else (json.loads(BENCH_FILE.read_text()) if BENCH_FILE.exists() else {})
    req = reg["task_requirements"].get(task_type, {"min_tier": 3, "min_quality": 3})
    snap = snapshot or governor.snapshot().as_dict()
    floor = min_free if min_free is not None else governor.Policy.load().min_free
    avail_models = installed_models
    reasons, rejected = [f"task {task_type}: min tier {req['min_tier']}, min quality {req['min_quality']}"], []
    cands = sorted(reg["models"].items(), key=lambda kv: (kv[1]["tier"], kv[1].get("quality", 0)))
    for name, cfg in cands:
        if cfg["tier"] < req["min_tier"] or cfg.get("quality", 0) < req["min_quality"]:
            continue
        if cfg["backend"] == "ollama":
            if avail_models is None:
                avail_models = installed()
            if name not in avail_models:
                rejected.append({"model": name, "why": "not installed / Ollama down"})
                continue
            b = bench.get(name, {})
            if b.get("coding_benchmark") and cfg["tier"] >= 2 and b["coding_benchmark"]["passed"] < 3:
                rejected.append({"model": name, "why": f"coding benchmark {b['coding_benchmark']['passed']}/5 < 3"})
                continue
            need = b.get("ram_rss_bytes") or governor.parse_size(cfg.get("ram_estimate", "2GiB"))
            already = name in (loaded_models if loaded_models is not None else [m["name"] for m in loaded()])
            if not already and snap["mem_available"] - need < floor:
                rejected.append({"model": name, "why": f"needs {need >> 20} MiB; only {(snap['mem_available'] - floor) >> 20} MiB above floor"})
                continue
        elif cfg["backend"] == "agent-cli":
            if not cfg.get("configured", True):
                rejected.append({"model": name, "why": "agent CLI not configured (system.yaml agent.cli)"})
                continue
            if not allow_remote:
                rejected.append({"model": name, "why": "remote not allowed for this task"})
                continue
            b = bench.get(name)
            if b is not None and not b.get("available", True):
                rejected.append({"model": name, "why": "remote unavailable at last probe"})
                continue
        reasons.append(f"chose {name}: cheapest model meeting requirements that fits current resources")
        return {"model": name, "tier": cfg["tier"], "backend": cfg["backend"], "trust": cfg.get("trust", "standard"),
                "tools_allowed": cfg.get("tools_allowed", cfg["backend"] != "ollama"), "reasons": reasons, "rejected": rejected}
    return {"model": None, "tier": None, "reasons": reasons + ["no model satisfies the requirements now"], "rejected": rejected}


def render_report(results: dict[str, Any]) -> str:
    rows = ["| Model | Load+1st token | RAM (RSS) | Gen tok/s | Prompt tok/s | Context | Coding | Tool call |",
            "|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        if "coding_benchmark" in r:
            rows.append(f"| {name} | {r['load_and_first_token_s']} s | {r['ram_rss_bytes'] / 2**30:.2f} GiB | "
                        f"{r['generation_tokens_per_s']} | {r['prompt_tokens_per_s']} | {r['context_length']} "
                        f"(measured at {r['measured_num_ctx']}) | {r['coding_benchmark']['passed']}/{r['coding_benchmark']['total']} | "
                        f"{'yes' if r['tool_call_correct'] else 'no'} |")
        elif "available" in r:
            rows.append(f"| {name} (remote) | {r['latency_s']} s round trip | – | – | – | – | probe {'pass' if r['coding_probe']['pass'] else 'fail'} | via harness |")
        else:
            rows.append(f"| {name} | {r.get('error') or r.get('skipped')} | | | | | | |")
    return textwrap.dedent("\n".join(rows)) + "\n"
