"""Tests for the benchmark-lab files under benchmarks/competitors/lab (aggregation maths and the licence boundary)."""
import importlib.util
import json
import re
import subprocess
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
    subprocess.run([str(LAB / "adapters/vnx/encode.sh"), str(src), str(seq), "s184"], check=True)
    lines = seq.read_text().split("\n")
    assert lines[-1] == "" and all(re.fullmatch(r"[ACGT]+", l) for l in lines[:-1]) and len({len(l) for l in lines[:-1]}) == 1
    reads = tmp_path / "reads.txt"; reads.write_text("\n".join(lines[:-1] * 2) + "\n")
    subprocess.run([str(LAB / "adapters/vnx/decode.sh"), str(reads), str(out), "s184", "7"], check=True)
    assert out.read_bytes() == data
