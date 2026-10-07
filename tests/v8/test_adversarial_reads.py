"""V8.15 adversarial read sets (SIMULATED): the decoder must not crash, must handle every set deterministically and must
never return wrong data (FALSE_SUCCESS). Sets: valid reads mixed with garbage, truncated reads, heavily duplicated reads,
extreme homopolymer reads, pathological indel clusters, empty-sequence records."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

pytest.importorskip("edlib")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
import nanofunnel as nf                                   # noqa: E402

ALPHA = "ACGT"


def _reads(path: Path) -> list[str]:
    return [x for x in path.read_text().split("\n")[1::4] if x is not None]


def _write(path: Path, seqs: list[str]) -> None:
    path.write_text("".join(f"@r{i}\n{s}\n+\n{'5' * len(s)}\n" for i, s in enumerate(seqs)))


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    from vnxdna.simulation import engine, registry
    w = tmp_path_factory.mktemp("adv")
    arc = nf.build_archive(w / "archive", 4000, 7, "v4-balanced")
    m = registry.load_model("clean").with_parameters({"sequencing.coverage": {"model": "fixed", "mean": 4}})
    engine.simulate_file_with_truth(arc["strands_path"], w / "clean.fastq", m, 5)
    return w, arc, _reads(w / "clean.fastq")


def _decode(w: Path, arc: dict, name: str, seqs: list[str]) -> str:
    p = w / f"{name}.fastq"
    _write(p, seqs)
    case = {"profile": "v4-balanced", "decoder": {"read_clustering": "fallback", "cluster_config": {"consensus_template": "full"}}}
    dec, _cap = nf.run_decode(p, w / f"{name}.vnx", arc["container_sha256"], nf.decoder_options(case))
    return dec["outcome"]


def _homopolymerise(s: str, rnd: random.Random) -> str:
    i = rnd.randrange(len(s))
    return s[:i] + s[i] * rnd.randint(20, 60) + s[i:]


def _indel_cluster(s: str, rnd: random.Random) -> str:
    i = rnd.randrange(10, len(s) - 30)
    return s[:i] + "".join(rnd.choice(ALPHA) for _ in range(rnd.randint(0, 6))) + s[i + rnd.randint(8, 25):]


ADVERSARIAL = {
    "garbage_mixed": lambda rs, rnd: rs + ["".join(rnd.choice(ALPHA) for _ in range(len(rs[0]))) for _ in range(3 * len(rs))],
    "truncated": lambda rs, rnd: [r[: len(r) // 2] for r in rs],
    "duplicated": lambda rs, rnd: [r for r in rs[: len(rs) // 4] for _ in range(40)],
    "homopolymer_extreme": lambda rs, rnd: [_homopolymerise(r, rnd) for r in rs] + ["A" * 300, "C" * 1, "G" * 1000],
    "pathological_indels": lambda rs, rnd: [_indel_cluster(r, rnd) for r in rs],
    "empty_records": lambda rs, rnd: rs[:50] + [""] * 20 + rs[50:],
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_reads_never_give_wrong_data_and_are_deterministic(base, name):
    w, arc, rs = base
    seqs = ADVERSARIAL[name](rs, random.Random(11))
    first = _decode(w, arc, name, seqs)
    again = _decode(w, arc, name + "-again", seqs)
    assert first in ("EXACT", "EXPLICIT_FAILURE"), first          # never FALSE_SUCCESS, never a crash
    assert first == again                                        # same input, same handling


def test_clean_baseline_decodes(base):
    w, arc, rs = base
    assert _decode(w, arc, "baseline", rs) == "EXACT"
