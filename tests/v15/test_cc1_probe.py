"""V15 CC-1: the layout probe keeps its sample as one flat array, computes the frame-start bytes only when the layout
choice needs them and builds the exact-length matrix once per strand length. Every probe decision must be the 14.0.0
one: the sample, the length histogram, the candidates, their scores, the sync-vote flag, the chosen layout (or the
refusal) and the version/legacy refusal evidence. The 14.0.0 probe is reproduced here verbatim as the reference.
SIMULATED reads."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.native.reads import iter_reads
from vnxdna.recovery import probe as P
from vnxdna.recovery.pass1 import _RC
from vnxdna.sync.template import TemplateAligner, frame_erasures_to_bytes, strip_markers_exact
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en


NEGATIVE = Path(__file__).resolve().parents[1] / "conformance" / "negative"


def _old_sample_reads(reads_path, limit=P.SAMPLE_READS):
    counts: Counter = Counter()
    sample: list = []
    seen = 0
    for batch in iter_reads(reads_path, 4096):
        counts.update(batch.lengths.tolist())
        offs = np.concatenate([[0], np.cumsum(batch.lengths)])
        sample.extend(batch.codes[offs[i]:offs[i + 1]] for i in range(batch.count))
        seen += batch.count
        if seen >= limit:
            break
    return counts, sample


def _old_probe_reads(reads_path, band=6):
    counts, sample = _old_sample_reads(reads_path)
    p = P.Probe(len(sample), counts, sample)
    if not counts:
        return p
    p.heads = P.frame_heads(sample)
    for name, (lay, _, _) in PROFILES.items():
        nt = lay.strand_nt
        near = sum(v for length, v in counts.items() if abs(length - nt) <= band)
        if near >= max(1, p.n // 10) and lay not in [c[1] for c in p.candidates]:
            p.candidates.append((name, lay))
    for name, lay in p.candidates:
        nt = lay.strand_nt
        exact = [r for r in sample if r.size == nt][:4000]
        score = 0
        if exact:
            mat = np.stack(exact)
            for m in (mat, _RC[mat[:, ::-1]]):
                fb, _ = strip_markers_exact(lay, m)
                score += int(decode_frames(lay, nt_to_bytes(np.minimum(fb, 3)), errors_only_retry=False).ok.sum())
        p.scores[name] = score
    if p.candidates and not any(p.scores.values()):
        for name, lay in p.candidates:
            nt = lay.strand_nt
            close = [r for r in sample if abs(r.size - nt) <= band][:P.SYNC_VOTE_READS]
            if not close:
                continue
            al = TemplateAligner(lay, band)
            votes = 0
            for oriented in (close, [_RC[r[::-1]] for r in close]):
                pr = al.project(oriented)
                er = frame_erasures_to_bytes(pr.erased)
                votes += int((decode_frames(lay, nt_to_bytes(np.minimum(pr.bases, 3)), er, errors_only_retry=False).ok
                              & pr.ok).sum())
            p.scores[name] = votes
            p.sync_vote = True
    return p


def _err(error) -> tuple:
    return (type(error).__name__, getattr(error, "code", None), str(error), repr(getattr(error, "details", None)))


def _outcome(fn):
    try:
        r = fn()
        return ("ok", _err(r) if isinstance(r, Exception) else r)   # refusal() returns its error
    except Exception as error:  # noqa: BLE001 - a refusal is an outcome
        return ("error", *_err(error))


@pytest.fixture(scope="module")
def pools(tmp_path_factory):
    d = tmp_path_factory.mktemp("cc1probe")
    datagen.generate(d / "in.bin", 40_000, "random", 15401)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    out = {}
    for profile in ("v4-balanced", "v4-dense", "v6-high-dropout"):
        en.encode_container(d / "a.vnx", d / f"s-{profile}.fasta", en.DNAOptions(profile=profile))
    cases = {"clean": ("v4-balanced", {"coverage": 3}),
             "indel-rc": ("v4-balanced", {"coverage": 4, "substitution_rate": 0.01, "insertion_rate": 0.003,
                                          "deletion_rate": 0.003, "reverse_complement_rate": 0.5}),
             "heavy": ("v4-balanced", {"coverage": 3, "substitution_rate": 0.04, "insertion_rate": 0.02,
                                       "deletion_rate": 0.02}),
             "dense": ("v4-dense", {"coverage": 3, "substitution_rate": 0.01}),
             "hd": ("v6-high-dropout", {"coverage": 3})}
    for name, (profile, cfg) in cases.items():
        ch.simulate_file(d / f"s-{profile}.fasta", d / f"{name}.fastq", ch.ChannelConfig(seed=15409, **cfg))
        out[name] = d / f"{name}.fastq"
    ch.simulate_file(d / "s-v4-balanced.fasta", d / "rc-heavy.fastq",
                     ch.ChannelConfig(seed=15413, coverage=3, substitution_rate=0.01, reverse_complement_rate=0.9))
    out["rc-heavy"] = d / "rc-heavy.fastq"
    # damaged headers: the first 48 bases of every read replaced by random bases
    rng = np.random.default_rng(15417)
    lines = (d / "clean.fastq").read_text().splitlines()
    for i in range(1, len(lines), 4):
        lines[i] = "".join("ACGT"[int(x)] for x in rng.integers(0, 4, 48)) + lines[i][48:]
    (d / "damaged-header.fastq").write_text("\n".join(lines) + "\n")
    out["damaged-header"] = d / "damaged-header.fastq"
    # the conformance refusal vectors (unsupported frame version 7, V3 frame-5 strands), and pools of 63/64/65 reads
    # of the version-7 vector around MIN_REFUSAL_SAMPLE (refusal needs >= 64 distinct molecules)
    for vec in ("nibble7", "v3-frame5"):
        out[vec] = NEGATIVE / f"probe.{vec}" / "reads.fasta"
    recs = (NEGATIVE / "probe.nibble7" / "reads.fasta").read_text().split(">")[1:]
    for k in (P.MIN_REFUSAL_SAMPLE - 1, P.MIN_REFUSAL_SAMPLE, P.MIN_REFUSAL_SAMPLE + 1):
        (d / f"nibble7-{k}.fasta").write_text("".join(">" + r for r in recs[:k]))
        out[f"nibble7-{k}"] = d / f"nibble7-{k}.fasta"
    rng = np.random.default_rng(15411)
    with open(d / "junk.fastq", "w") as f:                      # no layout: random reads of random lengths
        for i in range(3000):
            n = int(rng.integers(20, 400))
            f.write(f"@j{i}\n{''.join('ACGT'[int(x)] for x in rng.integers(0, 4, n))}\n+\n{'I' * n}\n")
    out["junk"] = d / "junk.fastq"
    (d / "empty.fastq").write_text("")
    out["empty"] = d / "empty.fastq"
    return out


@pytest.mark.parametrize("name", ["clean", "indel-rc", "heavy", "dense", "hd", "junk", "empty", "rc-heavy",
                                  "damaged-header", "nibble7", "v3-frame5", "nibble7-63", "nibble7-64", "nibble7-65"])
def test_probe_decisions_equal_the_v14_probe(pools, name):
    path = pools[name]
    o, n = _outcome(lambda: _old_probe_reads(path)), _outcome(lambda: P.probe_reads(path))
    if o[0] == "error" or n[0] == "error":
        assert n == o                                           # the same refusal of an unreadable input
        return
    old, new = o[1], n[1]
    assert new.n == old.n and new.counts == old.counts
    assert len(new.sample) == len(old.sample) and all(np.array_equal(a, b) for a, b in zip(new.sample, old.sample))
    assert [c[0] for c in new.candidates] == [c[0] for c in old.candidates]
    assert new.scores == old.scores and new.sync_vote == old.sync_vote
    assert _outcome(lambda: P.choose_layout(new)) == _outcome(lambda: P.choose_layout(old))
    assert _outcome(lambda: P.refusal(new)) == _outcome(lambda: P.refusal(old))
    for domain, _ in P.DOMAINS:
        assert new.dominant(domain) == old.dominant(domain)
    if old.heads:
        for k in ("forward", "reverse"):
            assert all(np.array_equal(a, b) for a, b in zip(new.heads[k], old.heads[k]))
    assert P.sample_reads(path)[0] == _old_sample_reads(path)[0]
    assert all(np.array_equal(a, b) for a, b in zip(P.sample_reads(path)[1], _old_sample_reads(path)[1]))
