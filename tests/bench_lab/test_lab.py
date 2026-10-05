"""Tests for the benchmark-lab files under benchmarks/competitors/lab (aggregation maths and the licence boundary)."""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[2] / "benchmarks" / "competitors" / "lab"


def _load():
    spec = importlib.util.spec_from_file_location("lab_aggregate", LAB / "aggregate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wilson_known_values():
    agg = _load()
    lo, hi = agg.wilson(0, 3)
    assert lo == 0.0 and abs(hi - 0.5614) < 1e-3          # 0/3: upper bound 0.5614 (Wilson)
    lo, hi = agg.wilson(3, 3)
    assert hi == 1.0 and abs(lo - 0.4386) < 1e-3
    lo, hi = agg.wilson(5, 10)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3


def _trial(pid, exact, false_success=False):
    return {"participant": pid, "error_rate": 0.01, "coverage": 5, "dropout": 0.0, "exact_sha256": exact, "false_success": false_success,
            "harness_decoding_success": exact, "recovery_fraction_positional": 1.0 if exact else 0.0, "loadavg": [1.0, 1.0, 1.0],
            "steps": {"encoding": {"duration": 1.0, "peak_rss_mib": 10.0}, "decoding": {"duration": 2.0, "peak_rss_mib": 20.0}},
            "strand_stats": {"mean_len": 100, "nt_per_byte": 8.0, "bits_per_nt": 1.0, "gc_mean": 0.5, "frac_strands_gc_outside_40_60": 0.0,
                             "homopolymer_max": 3, "frac_strands_homopolymer_ge4": 0.0}}


def test_aggregate_counts_every_trial_and_flags_false_success():
    agg = _load()
    rows = agg.aggregate([_trial("a", True), _trial("a", False), _trial("a", False, True)])
    assert len(rows) == 1
    r = rows[0]
    assert r["trials"] == 3 and r["exact"] == 1 and r["false_success"] == 1
    assert abs(r["recovery_fraction_mean"] - 1 / 3) < 1e-9
    assert "Total false-SUCCESS across all trials: 1" in agg.markdown(rows, "t")


def test_licence_boundary_no_third_party_codec_imports():
    """No file of the lab directory imports or vendors a third-party codec/harness (GPL/AGPL tools run only as processes)."""
    forbidden = re.compile(r"^\s*(import|from)\s+(dt4dds|dt4dds_benchmark|NOREC4DNA|jdbrody|hedges|aeon)\b", re.M)
    for p in LAB.rglob("*"):
        if p.suffix in {".py", ".sh"}:
            assert not forbidden.search(p.read_text()), p
    assert not any(p.name in {"texttodna.cpp", "DNAcode.cpp"} for p in LAB.rglob("*"))


def test_adapter_scripts_roundtrip_text_io(tmp_path):
    """encode.sh writes plain strands; decode.sh takes shuffled raw reads and returns the exact input (clean channel)."""
    data = bytes((i * 31 + 7) % 256 for i in range(3000))
    src = tmp_path / "in.bin"; src.write_bytes(data)
    seq = tmp_path / "seq.txt"; out = tmp_path / "out.bin"
    env = {**os.environ, "VNX_PY": sys.executable}  # the interpreter running the tests, on any host
    subprocess.run([str(LAB / "adapters/vnx/encode.sh"), str(src), str(seq), "s184"], check=True, env=env)
    lines = seq.read_text().split("\n")
    assert lines[-1] == "" and all(re.fullmatch(r"[ACGT]+", l) for l in lines[:-1]) and len({len(l) for l in lines[:-1]}) == 1
    reads = tmp_path / "reads.txt"; reads.write_text("\n".join(lines[:-1] * 2) + "\n")
    subprocess.run([str(LAB / "adapters/vnx/decode.sh"), str(reads), str(out), "s184", "7"], check=True, env=env)
    assert out.read_bytes() == data


def test_dropout_report_counts_every_trial_and_rejects_duplicates(tmp_path):
    """dropout_report.py counts failures and false-SUCCESS, and refuses a trial file with a duplicated trial key."""
    import json
    import sys
    rows = []
    for seed, (exact, fs) in enumerate([(True, False), (False, False), (False, True)], start=1):
        t = _trial("vnx-x", exact, fs); t["seed"] = seed; rows.append(t)
    r = _trial("dna-ref", True); r["seed"] = 1; rows.append(r)
    f = tmp_path / "t.jsonl"; f.write_text("".join(json.dumps(t) + "\n" for t in rows))
    out = tmp_path / "o.json"
    subprocess.run([sys.executable, str(LAB / "dropout_report.py"), str(f), "--json", str(out), "--md", str(tmp_path / "o.md")],
                   check=True, capture_output=True)
    js = json.loads(out.read_text())
    assert js["participants"]["vnx-x"]["trials"] == 3
    assert js["participants"]["vnx-x"]["exact"] == 1 and js["participants"]["vnx-x"]["false_success"] == 1
    dup = tmp_path / "d.jsonl"; dup.write_text(f.read_text() + json.dumps(rows[0]) + "\n")
    p = subprocess.run([sys.executable, str(LAB / "dropout_report.py"), str(dup)], capture_output=True, text=True)
    assert p.returncode != 0 and "duplicate trial" in (p.stderr + p.stdout)


def test_dropout_report_rule3_decision(tmp_path):
    """PREREG rule 3 is applied mechanically: a finalist that matches the baseline everywhere and is not separated above it
    in any cell is REJECTed; one that is separated in a cell and has >= 8/10 at dropout <= 10 % is ACCEPTed."""
    import json
    import sys
    rows = []
    for pid, exact_at_d10 in [("vnx-base", 0), ("vnx-good", 10), ("vnx-same", 0)]:
        for d in (0.0, 0.1):
            for seed in range(1, 11):
                t = _trial(pid, d == 0.0 or seed <= exact_at_d10); t["dropout"] = d; t["seed"] = seed; rows.append(t)
    f = tmp_path / "t.jsonl"; f.write_text("".join(json.dumps(t) + "\n" for t in rows))

    def run(finalists):
        out = tmp_path / "o.json"
        subprocess.run([sys.executable, str(LAB / "dropout_report.py"), str(f), "--json", str(out), "--baseline", "vnx-base",
                        "--finalists", finalists], check=True, capture_output=True)
        return json.loads(out.read_text())["decision_rule3"]
    assert run("vnx-good,vnx-same")["lead"] == "vnx-good" and run("vnx-good,vnx-same")["decision"] == "ACCEPT"
    assert run("vnx-same")["decision"] == "REJECT"
