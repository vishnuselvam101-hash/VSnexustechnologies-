"""V14 Phase 6 workstream C: the adaptive (strand-check) address scheduler of the streaming engine
(docs/V14_SCHEDULER.md).

- a read is skipped only when the strand check confirms it against a verified frame: on structured pools (zero runs,
  repeated patterns, stripes and column parity, neighbouring strands with identical payloads) and on noisy, reverse-
  complement, dropout, duplicate, substitution and indel channels, every per-read skip is checked against the
  simulator's ground truth (no false skip), pass 1 verifies exactly the addresses of the diagnostic mode (every read
  attempted) with the same majority payloads, and whole decodes return the same status, bytes and outer-code records as
  the diagnostic mode, without a fallback;
- the corrected V13 rule ("corroborated", kept for comparison) loses distinct strands of a zero-run pool, which the
  ladder then hides by a fallback: the defect the strand check removes;
- every read not attempted has exactly one recorded skip reason; the per-read trace covers every read once;
- the diagnostic mode attempts every read; a layout whose inner code is too short for the margin skips nothing;
- the scheduler is deterministic across thread counts; options are validated.

Every read here is SIMULATED (vnxdna.simulation).
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.constraints import iter_fasta, to_codes
from vnxdna.dnaenc.layout import CRC_BYTES, HEADER_BYTES
from vnxdna.dnaenc.scrambler import keystreams
from vnxdna.native import engine as ne
from vnxdna.recovery import engine as re_
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.recovery.spill import Spill
from vnxdna.v4 import archive as ar
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

pytestmark = pytest.mark.skipif(not ne.available(), reason="engine library not built (python -m vnxdna.native.engine build)")

KIB = 1 << 10
INDEL = {"sequencing.substitution.rate": 0.01, "sequencing.insertion.rate": 0.002, "sequencing.deletion.rate": 0.002}
STRIPES = {"stripe_depth": 4, "column_parity": 2}
SKIP_REASONS = ("skip_screen_group", "skip_claim_header", "skip_claim_near", "skip_claim_deferred", "skip_screen_strand",
                "skip_claim_strand", "skip_other")


def _cov(mean, model="fixed"):
    return {"sequencing.coverage": {"model": model, "mean": float(mean), "dispersion": 4.0}}


def _data(path: Path, pattern: str, size: int, seed: int) -> None:
    from vnxdna.v4 import datagen
    if pattern == "zeros":
        path.write_bytes(bytes(size))
    elif pattern == "zero-heavy":
        rng = np.random.default_rng(seed)
        b = np.zeros(size, np.uint8)
        m = rng.random(size) < 0.02
        b[m] = rng.integers(1, 256, int(m.sum()))
        path.write_bytes(b.tobytes())
    else:
        datagen.generate(path, size, pattern, seed)


def _strand_addresses(fasta: Path, lay) -> np.ndarray:
    _, fpos = lay.template()
    ks = keystreams(HEADER_BYTES - 1 + lay.payload_bytes + CRC_BYTES)
    out = []
    for _, seq in iter_fasta(fasta):
        nt = to_codes(seq)[fpos[:4 * HEADER_BYTES]].astype(np.int64).reshape(HEADER_BYTES, 4)
        b = (nt[:, 0] << 6) | (nt[:, 1] << 4) | (nt[:, 2] << 2) | nt[:, 3]
        h = b[1:] ^ ks[b[0], :HEADER_BYTES - 1]
        out.append((int(h[0]) & 15, (int(h[1]) << 8) | int(h[2]), int.from_bytes(bytes(h[3:7].tolist()), "big"),
                    (int(h[7]) << 8) | int(h[8])))
    return np.asarray(out, dtype=np.int64)


def _pool(d: Path, pattern: str, size: int, comp: str, dna: dict, channel: dict, seed: int, shuffled: bool,
          layout=None) -> dict:
    """Archive, strands and reads with per-read ground truth (the address of each read's source strand)."""
    from vnxdna.simulation import engine, registry
    d.mkdir(parents=True, exist_ok=True)
    _data(d / "in.bin", pattern, size, seed)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions(compression=comp))
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions(**dna))
    model = registry.load_model("clean").with_parameters(channel, label="v14 scheduler test")
    truth = engine.simulate_file_with_truth(d / "s.fasta", d / "t.fastq", model, seed)
    lines = (d / "t.fastq").read_bytes().split(b"\n")
    recs = [b"\n".join(lines[4 * i:4 * i + 4]) + b"\n" for i in range(len(lines) // 4)]
    order = np.random.default_rng([seed, 0x51]).permutation(len(recs)) if shuffled else np.arange(len(recs))
    with open(d / "r.fastq", "wb") as f:
        for i in order.tolist():
            f.write(recs[i])
    lay = detect_layout(d / "r.fastq", DecodeOptions(layout=layout))
    addr = _strand_addresses(d / "s.fasta", lay)
    src = truth["source"][order]
    return {"dir": d, "reads": d / "r.fastq", "archive": d / "a.vnx", "lay": lay, "truth": addr[src], "n": len(recs)}


def _pass1(pool: dict, d: Path, threads: int = 1, **opts) -> dict:
    """Engine pass 1 with the per-read trace: stats, trace rows (decision, address) in read order, verified majority
    payload per address."""
    opt = DecodeOptions(**opts)
    lay = pool["lay"]
    d.mkdir(parents=True, exist_ok=True)
    Spill(d, 2, lay.payload_bytes, lay.frame_nt).close()
    tr = d / "trace.txt"
    os.environ["VNX_ENG_TRACE"] = str(tr)
    try:
        st = re_.run_pass1(pool["reads"], d, lay, opt, 2, fmt="fastq", threads=threads, dedup=opt.engine_dedup, batch=4096)
    finally:
        del os.environ["VNX_ENG_TRACE"]
    rows = sorted((int(p[0]), p[1], tuple(int(x) for x in p[2:6])) for p in (x.split() for x in tr.read_text().splitlines()))
    dt = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("payload", "u1", (lay.payload_bytes,))])
    votes: dict = {}
    for b in range(2):
        for r in np.fromfile(d / f"acc{b}.bin", dtype=dt):
            k = (int(r["kind"]), int(r["tag"]), int(r["group"]), int(r["symbol"]))
            votes.setdefault(k, Counter())[bytes(r["payload"])] += 1
    verified = {}
    for k, c in votes.items():
        (top, n1), *rest = c.most_common(2) + [(None, 0)]
        verified[k] = top if n1 > (rest[0][1] if rest else 0) else None
    return {"stats": st, "trace": rows, "verified": verified}


def _false_skips(pool: dict, res: dict) -> tuple[int, int]:
    """(skips whose evidence names another strand than the read's true one, skips without an evidence address)."""
    wrong = unknown = 0
    for i, dec, a in res["trace"]:
        if dec.startswith("S"):
            if a[0] < 0:
                unknown += 1
            elif a != tuple(pool["truth"][i].tolist()):
                wrong += 1
    return wrong, unknown


POOLS = {
    # name: (pattern, bytes, compression, DNA options, channel, shuffled)
    "zeros-stripes": ("zeros", 128 * KIB, "none", STRIPES, {**INDEL, **_cov(4)}, False),
    "zero-heavy-rc": ("zero-heavy", 128 * KIB, "none", {}, {**INDEL, **_cov(4), "sequencing.reverse_complement_rate": 0.5},
                      True),
    "repetitive": ("repetitive", 128 * KIB, "none", {}, {**INDEL, **_cov(4)}, True),
    "binary-substitution": ("binary", 128 * KIB, "none", STRIPES, {"sequencing.substitution.rate": 0.02, **_cov(4)}, True),
    "noisy-headers": ("zeros", 96 * KIB, "none", STRIPES,
                      {"sequencing.substitution.rate": 0.02, "sequencing.insertion.rate": 0.005,
                       "sequencing.deletion.rate": 0.005, **_cov(6)}, True),
    "random-dropout-duplicates": ("random", 128 * KIB, "zstd", {},
                                  {**INDEL, **_cov(4, "negative-binomial"), "storage.strand_loss.rate": 0.03,
                                   "amplification.duplicate_rate": 0.3}, True),
    "random-ordered": ("random", 128 * KIB, "zstd", {}, {**INDEL, **_cov(5)}, False),
    "neighbours-coverage1": ("zeros", 64 * KIB, "none", STRIPES, {"sequencing.substitution.rate": 0.005, **_cov(1)}, False),
    "dense-zero-heavy": ("zero-heavy", 64 * KIB, "none", {"profile": "v4-dense"},
                         {"sequencing.substitution.rate": 0.01, **_cov(4)}, True),
    # 4 inner parity bytes: the strand check needs exact agreement on every frame byte (budget 0, margin 5)
    "high-dropout-profile": ("random", 64 * KIB, "zstd", {"profile": "v6-high-dropout"},
                             {"sequencing.substitution.rate": 0.005, **_cov(3)}, True),
}


@pytest.fixture(scope="module")
def pools(tmp_path_factory):
    root = tmp_path_factory.mktemp("v14sched")
    cache: dict = {}

    def get(name: str) -> dict:
        if name not in cache:
            pattern, size, comp, dna, channel, shuffled = POOLS[name]
            cache[name] = _pool(root / name, pattern, size, comp, dna, channel, 71000 + list(POOLS).index(name), shuffled)
        return cache[name]
    return get


@pytest.mark.parametrize("name", list(POOLS))
def test_strand_scheduler_skips_only_confirmed_copies(pools, tmp_path, name):
    pool = pools(name)
    ref = _pass1(pool, tmp_path / "all", engine_dedup=False)
    got = _pass1(pool, tmp_path / "strand", engine_scheduler="strand")
    st = got["stats"]
    assert ref["stats"]["attempted"] == pool["n"] and ref["stats"]["skipped"] == 0
    assert _false_skips(pool, got) == (0, 0)                      # every skip names the read's true strand
    assert set(got["verified"]) == set(ref["verified"])           # no verified address lost
    assert all(got["verified"][k] == ref["verified"][k] for k in ref["verified"])
    assert st["skipped"] == sum(st[k] for k in SKIP_REASONS) and st["skip_other"] == 0
    assert st["sched_rescued"] == 0                               # no read left without a decision
    assert st["skip_screen_group"] == st["skip_claim_header"] == st["skip_claim_near"] == st["skip_claim_deferred"] == 0
    if name != "neighbours-coverage1":
        assert st["skipped"] > 0                                   # copies are skipped
    else:
        assert st["skipped"] == 0                                  # one reading per strand: nothing to skip
    assert len(got["trace"]) == pool["n"] and [r[0] for r in got["trace"]] == list(range(pool["n"]))
    assert sum(r[1].startswith("S") for r in got["trace"]) == st["skipped"]
    assert sum(r[1] == "A" for r in got["trace"]) == st["acc_records"]


@pytest.mark.parametrize("name", ["zeros-stripes", "zero-heavy-rc", "noisy-headers", "random-dropout-duplicates"])
def test_decode_equals_the_diagnostic_mode(pools, tmp_path, name):
    """Same status, bytes, duplicate conflicts and failed groups as the diagnostic mode, without a fallback. The outer
    code's symbol counters are compared up to pass-2 consensus: pass 1 verifies the same symbols (test above), but a
    skipped copy leaves no pending record, so the consensus over the pending reads of MISSING addresses can recover a
    different number of symbols (seen on the noisy pool: one symbol more with the scheduler)."""
    pool = pools(name)
    out, outer = {}, {}
    for mode, opts in (("all", {"engine_dedup": False}), ("strand", {"engine_scheduler": "strand"})):
        res = de.decode_reads(pool["reads"], tmp_path / f"{mode}.vnx", DecodeOptions(engine="stream", **opts),
                              overwrite=True)
        eng = res.report["engine"]
        assert eng["rung"] == "stream" and eng["fallbacks"] == [] and eng["scheduler"] == mode
        out[mode] = (res.status, (tmp_path / f"{mode}.vnx").read_bytes(), res.report["duplicate_conflicts"],
                     res.report["groups_failed"])
        outer[mode] = res.report.get("outer_v6") or {}
    assert out["strand"] == out["all"]
    assert out["all"][0] == "SUCCESS" and out["all"][1] == pool["archive"].read_bytes()
    assert outer["strand"].get("symbols_expected") == outer["all"].get("symbols_expected")
    if name != "noisy-headers":                                   # no pass-2 consensus at stake on these pools
        assert outer["strand"] == outer["all"]


def test_corroborated_rule_loses_distinct_strands_of_a_zero_pool(pools, tmp_path):
    """The defect the strand check removes: the V13 rule skips whole groups of distinct strands that share payload
    windows (identical zero payloads), so pass 1 loses verified addresses; the decode is still correct only because the
    ladder falls back to the V13 path."""
    pool = pools("zeros-stripes")
    ref = _pass1(pool, tmp_path / "all", engine_dedup=False)
    v13 = _pass1(pool, tmp_path / "v13", engine_scheduler="corroborated")
    strand = _pass1(pool, tmp_path / "strand", engine_scheduler="strand")
    assert len(set(ref["verified"]) - set(v13["verified"])) > 0
    assert set(ref["verified"]) == set(strand["verified"])
    assert v13["stats"]["skip_screen_group"] > 0 and v13["stats"]["skip_screen_strand"] == 0
    res = de.decode_reads(pool["reads"], tmp_path / "v13.vnx", DecodeOptions(engine_scheduler="corroborated"),
                          overwrite=True)
    assert res.status == "SUCCESS" and (tmp_path / "v13.vnx").read_bytes() == pool["archive"].read_bytes()
    assert res.report["engine"]["fallbacks"]                       # hidden by the ladder, not by the scheduler


def test_diagnostic_mode_attempts_every_read(pools, tmp_path):
    pool = pools("random-ordered")
    st = _pass1(pool, tmp_path / "all", engine_dedup=False, engine_scheduler="strand")["stats"]
    assert st["attempted"] == st["reads"] == pool["n"] and st["skipped"] == 0
    assert all(st[k] == 0 for k in SKIP_REASONS) and st["strand_checks"] == 0
    assert st["full_attempts"] == st["reads"]


def test_corroborated_skips_carry_reasons(pools, tmp_path):
    pool = pools("random-ordered")
    st = _pass1(pool, tmp_path / "v13", engine_scheduler="corroborated")["stats"]
    assert st["skipped"] > 0 and st["skipped"] == sum(st[k] for k in SKIP_REASONS) and st["skip_other"] == 0
    assert st["skip_screen_strand"] == st["skip_claim_strand"] == st["strand_checks"] == 0


def test_layout_without_the_margin_skips_nothing(tmp_path):
    """An inner code with 2 parity bytes (r + 1 < STRAND_MARGIN_MIN): the strand check never applies, so the strand
    scheduler attempts every read (the conservative behaviour, documented cost)."""
    from vnxdna.dnaenc.layout import Layout
    lay = Layout(46, 2, 0, 0)
    pool = _pool(tmp_path / "r2", "random", 48 * KIB, "zstd", {"layout": lay},
                 {"sequencing.substitution.rate": 0.002, **_cov(3)}, 71900, True, layout=lay)
    got = _pass1(pool, tmp_path / "strand", engine_scheduler="strand", layout=lay)
    assert got["stats"]["skipped"] == 0 and got["stats"]["attempted"] == pool["n"] and got["stats"]["strand_checks"] == 0
    res = de.decode_reads(pool["reads"], tmp_path / "o.vnx", DecodeOptions(layout=lay, engine_scheduler="strand"),
                          overwrite=True)
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == pool["archive"].read_bytes()
    assert res.report["engine"]["rung"] == "stream" and res.report["engine"]["pass1"]["skipped"] == 0


@pytest.mark.parametrize("name", ["zero-heavy-rc", "random-ordered"])
def test_strand_scheduler_is_deterministic_across_threads(pools, tmp_path, name):
    pool = pools(name)
    runs = [_pass1(pool, tmp_path / f"t{t}", threads=t, engine_scheduler="strand") for t in (1, 2, 5)]
    for t in ("t2", "t5"):
        for f in ("acc0.bin", "acc1.bin", "pend0.bin", "pend1.bin"):
            assert (tmp_path / t / f).read_bytes() == (tmp_path / "t1" / f).read_bytes()
    keys = ("attempted", "skipped", "cheap_attempts", "full_attempts", "strand_checks", *SKIP_REASONS)
    assert all({k: r["stats"][k] for k in keys} == {k: runs[0]["stats"][k] for k in keys} for r in runs)
    assert all(r["trace"] == runs[0]["trace"] for r in runs)


def test_scheduler_option_validation_and_report(pools, tmp_path):
    with pytest.raises(VNXConfigurationError):
        DecodeOptions(engine_scheduler="fast").validate()
    with pytest.raises(ValueError):
        ne.run_pass1(pools("random-ordered")["reads"], tmp_path, re_.params_for(pools("random-ordered")["lay"],
                                                                                DecodeOptions()),
                     1, fmt="fastq", threads=1, scheduler="fast")
    assert DecodeOptions().engine_scheduler == "auto"          # the V13 rule with the strand retry rung (lead decision)
    pool = pools("random-ordered")
    res = de.decode_reads(pool["reads"], tmp_path / "o.vnx", DecodeOptions(engine_dedup=False), overwrite=True)
    assert res.report["engine"]["scheduler"] == "all" and res.report["engine"]["pass1"]["skipped"] == 0


def test_cli_engine_scheduler_flag(pools, tmp_path):
    import json
    from typer.testing import CliRunner
    from vnxdna.commands.cli import app
    pool = pools("random-ordered")
    rep = tmp_path / "rep.json"
    r = CliRunner().invoke(app, ["decode", str(pool["reads"]), "-o", str(tmp_path / "o.vnx"), "--engine", "stream",
                                 "--engine-scheduler", "corroborated", "--report", str(rep)])
    assert r.exit_code == 0, r.output
    eng = json.loads(rep.read_text())["result"]["engine"]
    assert eng["scheduler"] == "corroborated" and eng["pass1"]["skip_claim_header"] > 0
    assert (tmp_path / "o.vnx").read_bytes() == pool["archive"].read_bytes()


def test_a_reading_with_a_parity_error_does_not_poison_the_frame_store(tmp_path):
    """The first reading of every strand carries one substitution in an inner-parity base. Its frame passes the clean
    path (no erasure, CRC verified: the CRC does not cover the parity), so it is accepted; the frame stored as the
    strand's reference must still be the codeword (re-checked by the inner code before it is stored). On the
    4-parity-byte layout (exact agreement, budget 0) the clean copies that follow are then confirmed and skipped."""
    from vnxdna.dnaenc.constraints import iter_fasta
    pool = _pool(tmp_path / "hd", "random", 32 * KIB, "zstd", {"profile": "v6-high-dropout"},
                 {"sequencing.substitution.rate": 0.0, **_cov(1)}, 71950, False)
    strands = [seq for _, seq in iter_fasta(pool["dir"] / "s.fasta")]
    flip = {"A": "C", "C": "G", "G": "T", "T": "A"}
    with open(tmp_path / "r.fasta", "w") as f:
        for i, seq in enumerate(strands):
            bad = seq[:-3] + flip[seq[-3]] + seq[-2:]           # a base of the last inner-parity byte
            f.write(f">p{i}\n{bad}\n>c{i}a\n{seq}\n>c{i}b\n{seq}\n")
    lay = pool["lay"]
    d = tmp_path / "spill"
    d.mkdir()
    Spill(d, 2, lay.payload_bytes, lay.frame_nt).close()
    st = re_.run_pass1(tmp_path / "r.fasta", d, lay, DecodeOptions(engine_scheduler="strand"), 2, fmt="fasta", threads=1,
                       batch=4096)
    n = len(strands)
    assert st["store_parity_fixed"] == n                         # every first reading had its parity re-derived
    assert st["skipped"] == 2 * n and st["attempted"] == n       # the clean copies match the stored codeword exactly
    res = de.decode_reads(tmp_path / "r.fasta", tmp_path / "o.vnx", DecodeOptions(), overwrite=True)
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == pool["archive"].read_bytes()


def test_cli_engine_scheduler_rejects_unknown_values(pools, tmp_path):
    from typer.testing import CliRunner
    from vnxdna.commands.cli import app
    pool = pools("random-ordered")
    r = CliRunner().invoke(app, ["decode", str(pool["reads"]), "-o", str(tmp_path / "o.vnx"), "--engine-scheduler", "fast"])
    assert r.exit_code != 0 and not (tmp_path / "o.vnx").exists()


# ---------------------------------------------------------------------------------------- the scheduler retry rung
def _decode_events(pool, out, threads=1, **opts):
    got: list = []
    res = de.decode_reads(pool["reads"], out, DecodeOptions(workers=threads, **opts), overwrite=True, observer=got.append)
    return res, [e for e in got if e["event"] == "engine_fallback"]


def test_default_retries_a_failed_v13_rule_run_with_the_strand_scheduler(pools, tmp_path):
    """Lead decision (docs/V14_SCHEDULER.md §9.4): the default "auto" runs the V13 rule; on the zero-run pool it loses
    distinct strands and the stream run fails, so the engine is re-run once with "strand", which decodes on the stream
    rung: no fallback to the V13 path. The failed first attempt is recorded as a fallback activation and an event."""
    pool = pools("zeros-stripes")
    res, ev = _decode_events(pool, tmp_path / "o.vnx")
    eng = res.report["engine"]
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == pool["archive"].read_bytes()
    assert eng["rung"] == "stream" and eng["scheduler"] == "strand"
    assert [f["reason"].split(" (")[0] for f in eng["fallbacks"]] == ["sched_retry: corroborated->strand"]
    assert eng["fallbacks"][0]["pass1"]["skip_screen_group"] > 0     # the first attempt ran the V13 rule
    assert eng["sched_retry"]["from"] == "corroborated" and eng["sched_retry"]["to"] == "strand"
    assert [e["rung"] for e in ev] == ["stream"] and ev[0]["reason"].startswith("sched_retry: corroborated->strand")


@pytest.mark.parametrize("name", ["random-ordered", "random-dropout-duplicates"])
def test_random_pools_never_trigger_the_retry(pools, tmp_path, name):
    pool = pools(name)
    res, ev = _decode_events(pool, tmp_path / "o.vnx")
    eng = res.report["engine"]
    assert res.status == "SUCCESS" and eng["rung"] == "stream" and eng["fallbacks"] == [] and ev == []
    assert eng["scheduler"] == "corroborated" and "sched_retry" not in eng


@pytest.mark.parametrize("opts", [{"engine_scheduler": "corroborated"}, {"engine_scheduler": "strand"},
                                  {"engine_dedup": False}])
def test_an_explicit_scheduler_is_never_retried(pools, tmp_path, opts):
    pool = pools("zeros-stripes")
    res, ev = _decode_events(pool, tmp_path / "o.vnx", **opts)
    eng = res.report["engine"]
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == pool["archive"].read_bytes()
    assert "sched_retry" not in eng and not any("sched_retry" in f["reason"] for f in eng["fallbacks"])
    if opts == {"engine_scheduler": "corroborated"}:
        assert eng["rung"] == "v13" and [e["rung"] for e in ev] == ["v13"]   # the V13 rule's loss: the ladder falls back
    else:
        assert eng["rung"] == "stream" and eng["fallbacks"] == [] and ev == []


def test_the_retry_rung_is_deterministic_across_threads(pools, tmp_path):
    pool = pools("zeros-stripes")
    runs = []
    for t in (1, 3):
        res, _ = _decode_events(pool, tmp_path / f"o{t}.vnx", threads=t)
        eng = res.report["engine"]
        keys = ("attempted", "skipped", "full_attempts", "skip_claim_strand", "skip_screen_strand")
        runs.append(((tmp_path / f"o{t}.vnx").read_bytes(), eng["rung"], eng["scheduler"],
                     [f["reason"] for f in eng["fallbacks"]], {k: eng["pass1"][k] for k in keys},
                     {k: eng["fallbacks"][0]["pass1"][k] for k in keys}))
    assert runs[0] == runs[1]
