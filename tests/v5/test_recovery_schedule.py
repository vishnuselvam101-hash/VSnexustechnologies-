"""Deferred per-read recovery (V5): smart/soft recovery runs only where the cheap pass left a group undecodable.

Scenarios are built from a real archive's strands with controlled damage, so every test knows exactly which addresses
the cheap V4 paths recover and which reads need smart (or soft) recovery. Each damaged read is checked first: the V4
paths fail on it and the eager per-read recovery accepts the right frame. The deferred schedule must give the same
verified result as the eager (Phase 3/4) schedule while trying fewer reads.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import ConstraintConfig, iter_fasta
from vnxdna.v4.errors import VNXConfigurationError, VNXIntegrityError
from vnxdna.v4.frame import KIND_DATA, build_strands
from vnxdna.v4.sync import SyncCosts
from vnxdna.v5.indel.recovery import IndelRecoveryConfig
from vnxdna.v5.soft.decoder import SoftDecodeConfig

from .indel_support import LAY, inject

_, FPOS = LAY.template()


# ---------------------------------------------------------------------------------------------------------------- fixture
@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("sched")
    src = d / "in.bin"
    sha = datagen.generate(src, 16_000, "random", 4901)
    ar.build_archive([src], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    strands = {}
    for name, seq in iter_fasta(d / "s.fasta"):
        _, _, kind, group, symbol = name.split("|")
        strands[(int(kind), int(group), int(symbol))] = np.frombuffer(seq.encode(), dtype=np.uint8).copy()
    lut = np.full(256, 4, dtype=np.uint8)
    lut[list(b"ACGT")] = [0, 1, 2, 3]
    strands = {k: lut[v] for k, v in strands.items()}
    res = de.decode_reads(_fastq(d / "clean.fastq", [(s, None) for s in strands.values()]), None, de.DecodeOptions())
    sb = res.report["superblock"]
    data = {k: v for k, v in strands.items() if k[0] == KIND_DATA}
    per_group = {}
    for (_, g, s) in data:
        per_group.setdefault(g, []).append(s)
    return {"dir": d, "sha": sha, "strands": strands, "data": data, "groups": sb["groups"], "tag": int(sb["archive_id"][:4], 16),
            "M": sb["outer_code"]["parity_symbols"], "per_group": {g: sorted(v) for g, v in per_group.items()}}


def _fastq(path, reads):
    with open(path, "w") as f:
        for i, (r, q) in enumerate(reads):
            q = np.full(r.size, 35, dtype=np.uint8) if q is None else q
            f.write(f"@r{i}\n{bytes(np.array(list(b'ACGT'), dtype=np.uint8)[r]).decode()}\n+\n{bytes(q + 33).decode()}\n")
    return path


def _try(read, q, smart=False, soft=None):
    de._p_init(LAY, 6, SyncCosts(), 0, True, IndelRecoveryConfig() if smart else None,
               SoftDecodeConfig(mode=soft) if soft else None)
    acc, fields, payload, *_ = de._try([read], [q])
    return bool(acc[0]), tuple(int(x) for x in fields[0])


def _damage(arc, key, seed, header_sub=False, strand=None):
    """A copy of strand `key` (or of `strand`) that the V4 paths cannot decode but eager smart recovery can (checked)."""
    rng = np.random.default_rng(seed)
    strand = arc["strands"][key] if strand is None else strand
    want = (key[0], 0, key[1], key[2])
    for _ in range(400):
        # three deletions in separate payload segments: V4 erases three segments (> 16 bytes), smart localises them
        segs = rng.choice(np.arange(3, 10), size=3, replace=False)
        events = [("del", int(FPOS[24 * s + int(rng.integers(4, 20))])) for s in segs]
        if header_sub:
            p = int(FPOS[31])        # least significant base of the group's low byte (header byte 7 of the frame)
            events.append(("sub", p, (int(strand[p]) + 1) % 4))
        read, q = inject(strand, events)
        ok4, _ = _try(read, q)
        ok5, f5 = _try(read, q, smart=True)
        if not ok4 and ok5 and f5[0] == want[0] and f5[2:] == want[2:]:
            return read, q
    raise AssertionError(f"no V4-failing, smart-recoverable damage found for {key}")


def _soft_only(arc, key, seed):
    """An exact-length copy with 10 low-quality substitutions in 10 bytes: hard paths fail, eager soft GMD recovers."""
    rng = np.random.default_rng(seed)
    strand = arc["strands"][key]
    for _ in range(200):
        nts = rng.choice(np.arange(10, LAY.frame_nt // 4), size=10, replace=False) * 4 + rng.integers(0, 4, 10)
        events = [("sub", int(FPOS[p]), (int(strand[FPOS[p]]) + 1 + int(rng.integers(0, 3))) % 4) for p in nts]
        read, q = inject(strand, events, q_err=5)
        if not _try(read, q, smart=True)[0] and _try(read, q, smart=True, soft="auto")[0]:
            return read, q
    raise AssertionError("no soft-only read found")


def _scenario(arc, name, drop=(), damaged=(), extra=(), copies=2):
    """copies clean reads of every strand except `drop`; plus damaged reads; plus arbitrary extra reads."""
    reads = []
    for key, s in arc["strands"].items():
        if key not in drop:
            reads += [(s, None)] * copies
    reads += list(damaged) + list(extra)
    return _fastq(arc["dir"] / f"{name}.fastq", reads)


def _decode(path, out=None, buckets=None, monkeypatch=None, **kw):
    if buckets is not None:
        class Forced(de.Spill):
            def __init__(self, workdir, _b, *a, **k):
                super().__init__(workdir, buckets, *a, **k)
        monkeypatch.setattr(de, "Spill", Forced)
    res = de.decode_reads(path, out, de.DecodeOptions(**kw), overwrite=True)
    rep = {k: v for k, v in res.report.items() if k not in ("stage_seconds", "seconds", "peak_rss_bytes")}
    if "indel_recovery" in rep:          # a float sum whose order depends on batching (as in test_indel_decoder)
        rep["indel_recovery"] = {k: v for k, v in rep["indel_recovery"].items() if k != "false_accept_bound"}
    if "recovery_schedule" in rep and "rounds" in rep["recovery_schedule"]:
        rep["recovery_schedule"] = {**rep["recovery_schedule"], "rounds": {
            r: {k: v for k, v in d.items() if k != "seconds"} for r, d in rep["recovery_schedule"]["rounds"].items()}}
    return res.status, rep


def _same_result(a, b):
    """Eager and deferred: same status, groups, container hash and verified-read counts."""
    (sa, ra), (sb_, rb) = a, b
    assert sa == sb_
    for k in ("groups_decoded", "groups_failed", "failed_groups", "container_sha256", "superblock"):
        assert ra.get(k) == rb.get(k), k


def _missing_group(arc, n_extra):
    """Group 0 loses M + n_extra symbols: k − n_extra remain, so it is not decodable from the cheap pass."""
    g = 0
    syms = arc["per_group"][g]
    return [(KIND_DATA, g, s) for s in syms[: arc["M"] + n_extra]]


# ------------------------------------------------------------------------------------------------------------------ tests
def test_option_validation_and_default():
    assert de.DecodeOptions().recovery_schedule == "deferred"
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(recovery_schedule="lazy").validate()


def test_all_addresses_recovered_by_cheap_pass_means_zero_smart_work(arc, tmp_path):
    # 1 + 4: damaged (V4-failing, smart-recoverable) duplicates of addresses the clean reads already recover
    dups = [_damage(arc, k, 100 + i) for i, k in enumerate(list(arc["data"])[:12])]
    path = _scenario(arc, "dups", damaged=dups)
    st, rep = _decode(path, tmp_path / "o.vnx", indel_recovery="smart")
    assert st == "SUCCESS"
    sched = rep["recovery_schedule"]
    assert sched["groups_decodable_after_cheap_pass"] == sched["groups"]
    assert sched["needed_addresses"] == 0 and sched["round_b"] is False
    assert sched["reads_tried"] == 0 and sched["reads_skipped"] == len(dups)
    assert rep["indel_recovery"].get("attempted", 0) == 0
    _same_result((st, rep), _decode(path, tmp_path / "e.vnx", indel_recovery="smart", recovery_schedule="eager"))


def test_one_missing_address_is_the_only_one_searched(arc, tmp_path):
    drop = _missing_group(arc, 1)
    target = drop[-1]
    dmg = [_damage(arc, target, 200)]                       # one read: a V4 vote of one read cannot help
    dups = [_damage(arc, k, 300 + i) for i, k in enumerate(list(arc["data"])[-6:])]   # recovered elsewhere: skipped
    path = _scenario(arc, "one", drop=drop, damaged=dmg + dups)
    st, rep = _decode(path, tmp_path / "o.vnx", indel_recovery="smart")
    sched = rep["recovery_schedule"]
    assert st == "SUCCESS"
    assert sched["groups_decodable_after_cheap_pass"] == sched["groups"] - 1
    assert sched["rounds"]["A"]["reads"] == 1 and sched["rounds"]["A"]["unique_addresses"] == 1
    assert sched["rounds"]["A"]["recovered"] == 1 and sched["round_b"] is False
    assert sched["reads_skipped"] == len(dups)
    _same_result((st, rep), _decode(path, tmp_path / "e.vnx", indel_recovery="smart", recovery_schedule="eager"))
    ar.extract(tmp_path / "o.vnx", tmp_path / "x")                                    # 8: full archive, SHA-256
    assert hashlib.sha256((tmp_path / "x" / "in.bin").read_bytes()).hexdigest() == arc["sha"]


def test_cheap_v4_consensus_vote_counts_before_any_smart_work(arc, tmp_path):
    # two damaged copies of the needed address: the V4 consensus vote of pass 2 recovers it, so the group is already
    # decodable after the cheap pass and no read needs smart recovery (the eager schedule smart-decodes both)
    drop = _missing_group(arc, 1)
    target = drop[-1]
    dmg = [_damage(arc, target, 200), _damage(arc, target, 201)]
    path = _scenario(arc, "vote", drop=drop, damaged=dmg)
    st, rep = _decode(path, tmp_path / "o.vnx", indel_recovery="smart")
    sched = rep["recovery_schedule"]
    assert st == "SUCCESS" and sched["v4_vote_counted"] is True
    assert sched["groups_decodable_after_cheap_pass"] == sched["groups"] and sched["reads_tried"] == 0
    assert rep["reads"]["consensus_recovered"] >= 1
    st_e, rep_e = _decode(path, tmp_path / "e.vnx", indel_recovery="smart", recovery_schedule="eager")
    assert st_e == "SUCCESS" and rep_e["reads"]["smart"] == 2 and rep_e["container_sha256"] == rep["container_sha256"]


def test_several_missing_addresses_only_those_are_searched(arc, tmp_path):
    drop = _missing_group(arc, 3)
    targets = drop[-3:]
    dmg = [_damage(arc, t, 400 + i) for i, t in enumerate(targets)]
    path = _scenario(arc, "several", drop=drop, damaged=dmg)
    st, rep = _decode(path, tmp_path / "o.vnx", indel_recovery="smart")
    sched = rep["recovery_schedule"]
    assert st == "SUCCESS"
    assert sched["rounds"]["A"]["reads"] == 3 and sched["rounds"]["A"]["unique_addresses"] == 3
    assert sched["rounds"]["A"]["recovered_addresses"] == 3 and sched["round_b"] is False
    _same_result((st, rep), _decode(path, tmp_path / "e.vnx", indel_recovery="smart", recovery_schedule="eager"))


def test_wrong_and_ambiguous_reads_stay_rejected(arc, tmp_path):
    drop = _missing_group(arc, 1)
    target = drop[-1]
    junk = []
    for seed in (5, 6):                                      # header and markers intact, payload random: targeted
        j = arc["strands"][target].copy()
        j[FPOS[4 * 10:]] = np.random.default_rng(seed).integers(0, 4, LAY.frame_nt - 40)
        junk.append((j, None))
    good = _damage(arc, target, 500)
    path = _scenario(arc, "wrong", drop=drop, damaged=[good], extra=junk)
    st, rep = _decode(path, tmp_path / "o.vnx", indel_recovery="smart")
    sched = rep["recovery_schedule"]
    assert st == "SUCCESS" and rep["container_sha256"]
    assert sched["rounds"]["A"]["reads"] == 3 and sched["rounds"]["A"]["recovered"] == 1   # only the genuine read
    _same_result((st, rep), _decode(path, tmp_path / "e.vnx", indel_recovery="smart", recovery_schedule="eager"))


def test_snapped_address_lands_in_the_bucket_of_its_verified_group(arc, tmp_path, monkeypatch):
    # 6: the only copy of the needed address has a header error in the group byte, so its reading names another
    # group (another bucket of three); the snap targets it, and the verified frame must reach group 0's bucket
    drop = _missing_group(arc, 1)
    target = drop[-1]
    read, q = _damage(arc, target, 600, header_sub=True)
    path = _scenario(arc, "snap", drop=drop, damaged=[(read, q)])
    st, rep = _decode(path, tmp_path / "o.vnx", buckets=3, monkeypatch=monkeypatch, indel_recovery="smart")
    sched = rep["recovery_schedule"]
    assert st == "SUCCESS"
    assert sched["rounds"]["A"]["reads"] == 1 and sched["rounds"]["A"]["recovered"] == 1
    assert sched["rounds"]["A"]["unique_addresses"] == 1
    _same_result((st, rep), _decode(path, tmp_path / "e.vnx", buckets=3, monkeypatch=monkeypatch,
                                    indel_recovery="smart", recovery_schedule="eager"))


def test_spill_routes_verified_frames_by_verified_group(tmp_path):
    sp = de.Spill(tmp_path, 3, LAY.payload_bytes, LAY.frame_nt, LAY.strand_nt + 6)
    sp.close()
    fields = np.array([[0, 7, 4, 1], [0, 7, 5, 2], [0, 7, 6, 3]], dtype=np.int64)
    sp.append_acc(fields, np.zeros((3, LAY.payload_bytes), dtype=np.uint8))
    for b in range(3):
        acc, _ = sp.load(b)
        assert acc["group"].tolist() == [g for g in (4, 5, 6) if g % 3 == b]


def test_partial_archive_keeps_missing_addresses_missing(arc, tmp_path):
    # 7: no reads at all for M + 2 symbols of group 0: nothing may be synthesised; eager and deferred agree
    drop = _missing_group(arc, 2)
    path = _scenario(arc, "partial", drop=drop)
    st, rep = _decode(path, None, indel_recovery="smart")
    assert st in ("PARTIAL", "FAILURE") and rep["groups_failed"] == 1 and rep["failed_groups"] == [0]
    assert rep["recovery_schedule"]["round_b"] is True
    _same_result((st, rep), _decode(path, None, indel_recovery="smart", recovery_schedule="eager"))


def test_soft_decoding_opportunities_are_not_bypassed(arc, tmp_path):
    # 9: the missing address has only a soft-recoverable read (hard paths and smart fail on it)
    drop = _missing_group(arc, 1)
    read, q = _soft_only(arc, drop[-1], 700)
    path = _scenario(arc, "soft", drop=drop, damaged=[(read, q)])
    st_h, _ = _decode(path, None, indel_recovery="smart")
    assert st_h != "SUCCESS"
    st, rep = _decode(path, tmp_path / "o.vnx", indel_recovery="smart", soft_decoding="auto")
    assert st == "SUCCESS" and rep["reads"]["soft"] == 1
    assert rep["recovery_schedule"]["rounds"]["A"]["recovered"] == 1
    _same_result((st, rep), _decode(path, tmp_path / "e.vnx", indel_recovery="smart", soft_decoding="auto",
                                    recovery_schedule="eager"))


def test_repeated_and_parallel_decodes_are_identical(arc, tmp_path):
    # 10: same input, same options → identical reports (timings aside), for 1 and 3 workers
    drop = _missing_group(arc, 2)
    dmg = [_damage(arc, t, 800 + i) for i, t in enumerate(drop[-2:])]
    path = _scenario(arc, "det", drop=drop, damaged=dmg, copies=1)
    runs = [_decode(path, tmp_path / f"o{i}.vnx", indel_recovery="smart", workers=w) for i, w in enumerate((1, 1, 3))]
    for st, rep in runs[1:]:
        assert (st, rep) == runs[0]
    assert runs[0][0] == "SUCCESS"


def test_verified_but_wrong_frame_cannot_produce_false_success(arc, tmp_path):
    # 11: an RS- and CRC-valid strand for the needed address with a wrong payload. Nothing at frame level can tell it
    # from the truth; the container SHA-256 must, in both schedules (fail closed: never a false SUCCESS)
    drop = _missing_group(arc, 1)
    _, g, s = drop[-1]
    fake, _ = build_strands(LAY, ConstraintConfig(), arc["tag"], KIND_DATA, np.array([g]), np.array([s]),
                            np.random.default_rng(9).integers(0, 256, (1, LAY.payload_bytes), dtype=np.uint8))
    decoy = _damage(arc, drop[-1], 900, strand=fake[0])     # V4 fails, smart recovers a verified but wrong frame
    path = _scenario(arc, "decoy", drop=drop, damaged=[decoy])
    for sched in ("deferred", "eager"):
        with pytest.raises(VNXIntegrityError):
            _decode(path, tmp_path / f"{sched}.vnx", indel_recovery="smart", recovery_schedule=sched)
        assert not (tmp_path / f"{sched}.vnx").exists()


@pytest.mark.parametrize("cfg", [dict(insertion_rate=0.004, deletion_rate=0.004, substitution_rate=0.008,
                                      quality_informative=0.5, coverage=2, seed=11),
                                 dict(insertion_rate=0.002, deletion_rate=0.002, substitution_rate=0.03,
                                      quality_informative=0.8, coverage=1, seed=12)])
def test_simulated_channels_eager_and_deferred_agree(arc, tmp_path, monkeypatch, cfg):
    # SIMULATED channels, 3 forced buckets: the deferred schedule never changes the verified outcome
    path = arc["dir"] / f"chan{cfg['seed']}.fastq"
    if not path.exists():
        ch.simulate_file(arc["dir"] / "s.fasta", path, ch.ChannelConfig(coverage_model="fixed", **cfg))
    for opts in (dict(indel_recovery="smart"), dict(indel_recovery="smart", soft_decoding="auto")):
        d = _decode(path, tmp_path / "d.vnx", buckets=3, monkeypatch=monkeypatch, **opts)
        e = _decode(path, tmp_path / "e.vnx", buckets=3, monkeypatch=monkeypatch, recovery_schedule="eager", **opts)
        _same_result(d, e)
        if d[0] == "SUCCESS":
            out = tmp_path / f"x{len(opts)}"
            ar.extract(tmp_path / "d.vnx", out)
            assert hashlib.sha256((out / "in.bin").read_bytes()).hexdigest() == arc["sha"]


def test_v4_mode_is_untouched(arc, tmp_path):
    path = _scenario(arc, "v4", drop=_missing_group(arc, 0)[:1])
    st, rep = _decode(path, tmp_path / "o.vnx")
    assert st == "SUCCESS" and "recovery_schedule" not in rep and "indel_recovery" not in rep


def test_cli_flag(arc, tmp_path):
    import json

    from typer.testing import CliRunner
    from vnxdna.v4.cli import app
    path = _scenario(arc, "cli", drop=_missing_group(arc, 0)[:1])
    for sched in ("eager", "deferred"):
        res = CliRunner().invoke(app, ["decode", str(path), "-o", str(tmp_path / f"{sched}.vnx"), "--indel-recovery", "smart",
                                       "--recovery-schedule", sched, "-w", "1"])
        assert json.loads(res.output)["recovery_schedule"]["mode"] == sched
    bad = CliRunner().invoke(app, ["decode", str(path), "-o", str(tmp_path / "y.vnx"), "--recovery-schedule", "nope"])
    assert bad.exit_code != 0
