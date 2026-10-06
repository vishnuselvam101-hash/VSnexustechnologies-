"""V7 split rule (protocol 4.1, Amendment 1) and the held-out access guard (4.2). Synthetic data only; no public data needed."""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPLIT = ROOT / "experiments" / "v7" / "split"
sys.path.insert(0, str(SPLIT))
import guard as G  # noqa: E402
import refsplit as rs  # noqa: E402

MANIFEST = json.loads((SPLIT / "SPLIT_MANIFEST.json").read_text())
#: docs/V7_PROTOCOL.md as committed when the split was computed (810d35e) and recorded in SPLIT_MANIFEST.json
PROTOCOL_AT_SPLIT = "64a8f7a8af349fcbc4e362cdddc2f215ccd04c8e9b41be66536bb197abc6083d"
#: SHA-256 of that version's text before "## 5." (sections 1-4: objective, evidence classes, datasets, split rules)
SPLIT_RULES_SHA256 = "5b766395bd1828e7a471a7281da74fa0c17a6bf048f1bec78fd4b0a5a081f090"
#: SHA-256 of protocol sections 5.4 (validation before use, the gating rule) and 5.5 (amendment 2), each from its heading
#: to the next heading: the fits and DEV verdicts of experiments/v7/fit-* were made under exactly this text
SECTION_SHA256 = {"5.4": "931940952413a15529a8715ee465827514ce9a31ec6e3ea146e71015d1f36cdf",
                  "5.5": "f9a9d0960cb12b4800303ba9ad11c57b65f912075f76e996ab9d67ef61b2a153"}


def _before_section_5(text: bytes) -> bytes:
    return text[:text.index(b"\n## 5. ")]


def _section(text: bytes, number: str) -> bytes:
    import re
    a = text.index(b"\n### " + number.encode() + b" ") + 1
    m = re.search(rb"\n(### |## )", text[a:])
    assert m is not None
    return text[a:a + m.start() + 1]


def _rc(s: bytes) -> bytes:
    return s.translate(bytes.maketrans(b"ACGT", b"TGCA"))[::-1]


# -- the rule -------------------------------------------------------------------------------------------------------
def test_canonical_is_strand_and_case_independent():
    s = b"ACGGTTAC"
    assert rs.canonical(s) == rs.canonical(_rc(s)) == rs.canonical(s.lower()) == min(s, _rc(s))


def test_bucket_matches_the_definition_and_maps_to_splits():
    s = b"ACGGTTACCA"
    c = min(s, _rc(s))
    assert rs.bucket(s) == int(hashlib.sha256(c).hexdigest(), 16) % 10 == rs.bucket(_rc(s))
    assert [rs.bucket_split(b) for b in range(10)] == ["FIT"] * 6 + ["DEV"] * 2 + ["HELDOUT"] * 2
    with pytest.raises(ValueError):
        rs.bucket_split(10)


def test_bucket_shares_are_about_60_20_20():
    rnd = random.Random(1)
    seqs = [bytes(rnd.choice(b"ACGT") for _ in range(60)) for _ in range(20000)]
    share = {s: sum(rs.bucket_split(rs.bucket(q)) == s for q in seqs) / len(seqs) for s in rs.SPLITS}
    assert abs(share["FIT"] - 0.6) < 0.02 and abs(share["DEV"] - 0.2) < 0.02 and abs(share["HELDOUT"] - 0.2) < 0.02


def test_heldout_run_of_d03_is_file_1_and_independent_of_input_order():
    assert rs.heldout_run("D03", ["file-0", "file-1", "file-2"]) == "file-1"
    assert rs.heldout_run("D03", ["file-2", "file-0", "file-1"]) == "file-1"
    assert int(hashlib.sha256(b"VNX-V7-HELDOUT/D03").hexdigest(), 16) % 3 == 1


# -- the committed split --------------------------------------------------------------------------------------------
def test_committed_lists_match_their_hashes_and_partition_the_references():
    g = G.Guard("/nonexistent", script="test")
    for key, d in MANIFEST["datasets"].items():
        sets = {s: g.ids(key, s) for s in rs.SPLITS}
        assert not (sets["FIT"] & sets["DEV"]) and not (sets["FIT"] & sets["HELDOUT"]) and not (sets["DEV"] & sets["HELDOUT"])
        assert sum(map(len, sets.values())) == d["references_total"]
        assert {s: len(v) for s, v in sets.items()} == d["references_per_split"]


def test_committed_manifest_describes_amendment_1():
    d3 = MANIFEST["datasets"]["d03-nanopore"]
    assert d3["heldout_run"] == "file-1"
    assert MANIFEST["datasets"]["cnr"]["heldout_run"] is None and MANIFEST["datasets"]["dt4dds-twist"]["heldout_run"] is None
    assert MANIFEST["read_files_opened"] is False
    runs = {s: dict(ln.split("\t") for ln in (SPLIT / d3["lists"][s]["runs"]["path"]).read_text().split("\n") if ln) for s in rs.SPLITS}
    assert all(k.startswith(("file-0", "file-2")) for k in list(runs["FIT"]) + list(runs["DEV"]))
    assert sum(v == "all-references" for v in runs["HELDOUT"].values()) == 4
    assert all(k.startswith("file-1") for k, v in runs["HELDOUT"].items() if v == "all-references")
    assert MANIFEST["protocol"]["sha256"] == PROTOCOL_AT_SPLIT
    # Later amendments (protocol 5.5, amendment 2) may add to section 5 onwards; the text that governs the split (everything
    # before section 5) must stay byte-identical to the protocol the split was computed under.
    assert hashlib.sha256(_before_section_5((ROOT / "docs/V7_PROTOCOL.md").read_bytes())).hexdigest() == SPLIT_RULES_SHA256
    # The dataset manifest the split was computed from is in history; later items (D3) may only add datasets to it.
    rel = "experiments/v7/datasets/MANIFEST.json"
    shas = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H", "--", rel], capture_output=True, text=True, check=True).stdout.split()
    blobs = [subprocess.run(["git", "-C", str(ROOT), "show", f"{c}:{rel}"], capture_output=True, check=True).stdout for c in shas]
    pinned = [b for b in blobs if hashlib.sha256(b).hexdigest() == MANIFEST["dataset_manifest"]["sha256"]]
    assert pinned, "dataset manifest the split was computed from is not in history"
    old, new = json.loads(pinned[0]), json.loads((ROOT / rel).read_bytes())
    assert {k: v for k, v in new.items() if k != "datasets"} == {k: v for k, v in old.items() if k != "datasets"}
    assert all(new["datasets"][k] == v for k, v in old["datasets"].items())


@pytest.mark.parametrize("number", sorted(SECTION_SHA256))
def test_protocol_sections_governing_the_fits_are_unchanged(number):
    text = (ROOT / "docs/V7_PROTOCOL.md").read_bytes()
    assert hashlib.sha256(_section(text, number)).hexdigest() == SECTION_SHA256[number]


def test_recorded_protocol_version_is_in_history_and_governs_the_split():
    """The protocol blob whose SHA-256 the split manifest records exists in the repository history, and its text before
    section 5 hashes to SPLIT_RULES_SHA256 (needs full git history; CI's test job uses a shallow checkout)."""
    log = subprocess.run(["git", "log", "--format=%H", "--", "docs/V7_PROTOCOL.md"], cwd=ROOT, capture_output=True, text=True)
    commits = log.stdout.split() if log.returncode == 0 else []
    blobs = [subprocess.run(["git", "show", f"{c}:docs/V7_PROTOCOL.md"], cwd=ROOT, capture_output=True).stdout for c in commits]
    recorded = [b for b in blobs if hashlib.sha256(b).hexdigest() == PROTOCOL_AT_SPLIT]
    if not recorded:
        shallow = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT, capture_output=True, text=True)
        if shallow.returncode != 0 or shallow.stdout.strip() == "true":
            pytest.skip("protocol history not available (not a git checkout, or a shallow one)")
    assert recorded, "the protocol version recorded by the split manifest is not in the history"
    assert hashlib.sha256(_before_section_5(recorded[0])).hexdigest() == SPLIT_RULES_SHA256


def test_no_sequences_in_committed_split_outputs():
    for p in list((SPLIT / "lists").rglob("*.txt")) + [SPLIT / "SPLIT_MANIFEST.json"]:
        text = p.read_text()
        assert not any(len(w) >= 40 and set(w) <= set("ACGTN") for w in text.replace('"', " ").split()), p


def test_tampered_list_is_refused(tmp_path):
    import shutil
    shutil.copytree(SPLIT / "lists", tmp_path / "lists")
    shutil.copy(SPLIT / "SPLIT_MANIFEST.json", tmp_path / "SPLIT_MANIFEST.json")
    p = tmp_path / "lists/cnr/FIT.refs.txt"
    p.write_text(p.read_text() + "9999\n")
    with pytest.raises(G.AccessRefused):
        G.Guard("/x", split_dir=tmp_path, script="t").ids("cnr", "FIT")


# -- the guard ------------------------------------------------------------------------------------------------------
def _repo(tmp_path, with_prereg=True):
    r = tmp_path / "repo"
    (r / "experiments/v7/x").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.org")
    run("config", "user.name", "t")
    (r / "a.txt").write_text("a")
    run("add", "-A")
    run("commit", "-qm", "first")
    first = run("rev-parse", "HEAD").stdout.strip()
    (r / "experiments/v7/x" / ("PREREG-test.md" if with_prereg else "notes.md")).write_text("p")
    run("add", "-A")
    run("commit", "-qm", "prereg")
    return r, first, run("rev-parse", "HEAD").stdout.strip()


def test_prereg_check(tmp_path):
    r, first, second = _repo(tmp_path)
    assert G.check_prereg(second, r) == second
    for bad in (None, "", "abc", "0" * 40, first):    # none, malformed, unknown commit, commit without a PREREG file
        with pytest.raises(G.AccessRefused):
            G.check_prereg(bad, r)
    r2, _f, s2 = _repo(tmp_path / "b", with_prereg=False)
    with pytest.raises(G.AccessRefused):
        G.check_prereg(s2, r2)


def test_prereg_must_be_an_ancestor_of_head(tmp_path):
    r, first, second = _repo(tmp_path)
    run = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True)  # noqa: E731
    run("checkout", "-q", first)
    with pytest.raises(G.AccessRefused):
        G.check_prereg(second, r)


def _fake_cnr(data: Path):
    g0 = G.Guard("/x", script="t")
    n = MANIFEST["datasets"]["cnr"]["references_total"]
    (data / "cnr").mkdir(parents=True)
    (data / "cnr/Centers.txt").write_text("".join("ACGT" * 5 + f"{i:08b}".replace("0", "A").replace("1", "C") + "\n" for i in range(n)))
    (data / "cnr/Clusters.txt").write_text("".join(f"{'=' * 31}\nREAD{i}\nREAD{i}b\n" for i in range(n)))
    return g0


def test_fit_and_dev_yield_only_their_references_heldout_is_refused(tmp_path):
    data = tmp_path / "data"
    _fake_cnr(data)
    log = tmp_path / "log.jsonl"
    r, _first, prereg = _repo(tmp_path)
    g = G.Guard(data, script="t", log_path=log, repo=r)
    for split in ("FIT", "DEV"):
        got = [i for i, _c, reads in g.iter_cnr(split, "test")]
        assert set(got) == g.ids("cnr", split) and len(got) == len(set(got))
    assert not log.exists()                      # FIT / DEV use is not in the held-out log ...
    ledger = [json.loads(x) for x in (tmp_path / G.LEDGER_NAME).read_text().splitlines()]   # ... but in the ledger
    assert [(e["split"], e["granted"], e["script"], e["purpose"], e["dataset"]) for e in ledger] == \
        [("FIT", True, "t", "test", "cnr"), ("DEV", True, "t", "test", "cnr")]
    with pytest.raises(G.AccessRefused):
        next(g.iter_cnr("HELDOUT", "test"))
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["granted"] is False and entry["split"] == "HELDOUT" and entry["script"] == "t" and entry["purpose"] == "test"
    gr = G.Guard(data, script="t", log_path=log, repo=r, prereg_sha=prereg)
    got = [i for i, _c, reads in gr.iter_cnr("HELDOUT", "unit test")]
    assert set(got) == g.ids("cnr", "HELDOUT")
    last = json.loads(log.read_text().splitlines()[-1])
    assert last["granted"] is True and last["prereg_sha"] == prereg and last["commit"] == prereg
    assert len((tmp_path / G.LEDGER_NAME).read_text().splitlines()) == 2      # held-out requests stay out of the ledger


def test_refused_fit_or_dev_requests_are_in_the_ledger(tmp_path):
    ids = [str(i) for i in range(50)]
    data = tmp_path / "data"
    _fake_d03(data, ids)
    ledger = tmp_path / "ledger.jsonl"
    g = G.Guard(data, script="t", log_path=tmp_path / "log", ledger_path=ledger, repo=tmp_path)
    with pytest.raises(G.AccessRefused):
        next(g.iter_d03("file-1_acc-true_passQ-true/forward", "DEV", "x"))
    e = json.loads(ledger.read_text().splitlines()[0])
    assert e["granted"] is False and e["split"] == "DEV" and not (tmp_path / "log").exists()


def test_committed_ledger_records_the_dev_looks_and_the_heldout_log_is_empty():
    ledger = [json.loads(x) for x in (ROOT / "experiments/v7/datasets" / G.LEDGER_NAME).read_text().splitlines()]
    assert ledger and all(e["split"] in ("FIT", "DEV") for e in ledger)
    dev = {(e["source"], e["target"]) for e in ledger if e.get("reconstructed")}
    results = sorted(ROOT.glob("experiments/v7/fit-*/results/*.validation.json")) + \
        sorted(ROOT.glob("experiments/v7/fit-*/a2/results/*.validation.json"))
    assert {s for s, _t in dev} == {str(p.relative_to(ROOT)) for p in results}
    # Held-out touches before a PREREG are disclosed here (protocol 4.2). Only the D3 hashing/address-classification
    # exceptions (docs/V7_PROTOCOL_AMENDMENT_D3.md §2) may appear; the guarded CNR/D03 held-out splits never.
    access = [json.loads(x) for x in (ROOT / "experiments/v7/datasets/ACCESS_LOG.jsonl").read_text().splitlines()]
    assert all(e["dataset"] in ("d13-lopez-nanopore", "cas9-random-access") for e in access)
    assert all(e["script"] and e["purpose"] and e["material"] and e["timestamp_utc"] for e in access)


def test_purpose_is_required_and_wrong_sha_never_grants(tmp_path):
    data = tmp_path / "data"
    _fake_cnr(data)
    r, first, prereg = _repo(tmp_path)
    with pytest.raises(G.AccessRefused):
        next(G.Guard(data, script="t", log_path=tmp_path / "l", repo=r, prereg_sha=prereg).iter_cnr("FIT", ""))
    with pytest.raises(G.AccessRefused):
        next(G.Guard(data, script="t", log_path=tmp_path / "l", repo=r, prereg_sha=first).iter_cnr("HELDOUT", "x"))
    with pytest.raises(ValueError):
        next(G.Guard(data, script="t", log_path=tmp_path / "l", repo=r).iter_cnr("TEST", "x"))


def _fake_d03(data: Path, ids: list[str], files=(0, 1)):
    rnd = random.Random(5)
    seqs = {i: bytes(rnd.choice(b"ACGT") for _ in range(40)) for i in ids}
    (data / "d03").mkdir(parents=True)
    (data / "d03/oligos.fasta").write_text("".join(f">{i}\n{s.decode()}\n" for i, s in seqs.items()))
    for f in files:
        for direction in ("forward", "backward"):
            d = data / "d03/ex" / f"file-{f}_acc-true_passQ-true" / direction
            d.mkdir(parents=True)
            tx = [seqs[i] if direction == "forward" else _rc(seqs[i]) for i in ids]
            (d / f"TX__f{f}.txt").write_bytes(b"\n".join(tx) + b"\n")
            (d / f"RX__f{f}.txt").write_bytes(b"".join(b"=" * 31 + b"\n" + t + b"\n" + t + b"\n" for t in tx))
    return seqs


def test_d03_heldout_run_is_unreachable_without_prereg_even_for_fit_requests(tmp_path):
    ids = [str(i) for i in range(300)]
    data = tmp_path / "data"
    _fake_d03(data, ids)
    r, _first, prereg = _repo(tmp_path)
    g = G.Guard(data, script="t", log_path=tmp_path / "log", repo=r)
    held = "file-1_acc-true_passQ-true/forward"
    for split in ("FIT", "DEV", "HELDOUT"):
        with pytest.raises(G.AccessRefused):
            next(g.iter_d03(held, split, "x"))
    # a valid SHA does not make a whole held-out run readable as FIT or DEV
    gv = G.Guard(data, script="t", log_path=tmp_path / "log", repo=r, prereg_sha=prereg)
    for split in ("FIT", "DEV"):
        with pytest.raises(G.AccessRefused):
            next(gv.iter_d03(held, split, "x"))
    assert len(list(gv.iter_d03(held, "HELDOUT", "x"))) == 300           # whole run, every reference
    ok = "file-0_acc-true_passQ-true/backward"
    fit = list(g.iter_d03(ok, "FIT", "x"))
    assert {i for i, _s, _r in fit} == set(ids) & g.ids("d03-nanopore", "FIT")
    assert all(len(reads) == 2 for _i, _s, reads in fit)
    with pytest.raises(G.AccessRefused):
        next(g.iter_d03(ok, "HELDOUT", "x"))                              # the held-out buckets of file-0: needs the SHA
    hv = list(gv.iter_d03(ok, "HELDOUT", "x"))
    assert {i for i, _s, _r in hv} == set(ids) & g.ids("d03-nanopore", "HELDOUT")


def test_fastq_assignment_filters_by_split(tmp_path):
    import gzip
    data = tmp_path / "data"
    (data / "dt4dds").mkdir(parents=True)
    g = G.Guard(data, script="t", log_path=tmp_path / "log", repo=tmp_path)
    fit_ids = sorted(g.ids("dt4dds-twist", "FIT"))[:3]
    other = sorted(g.ids("dt4dds-twist", "HELDOUT"))[:3]
    reads = [(i, f"ACGT{n}".replace("0", "A").replace("1", "C").replace("2", "G").replace("3", "T")) for n, i in enumerate(fit_ids + other)]
    with gzip.open(data / "dt4dds/ERR12033806_1.fastq.gz", "wt") as fh:
        for n, (i, s) in enumerate(reads):
            fh.write(f"@r{n} x\n{s}\n+\n{'F' * len(s)}\n")
    table = {s.encode(): i for i, s in reads}
    got = [x for b in g.iter_fastq_batches("ERR12033806", "FIT", lambda ss: [table.get(s) for s in ss], "x", batch=2) for x in b]
    assert [x[0] for x in got] == fit_ids and g.dropped["ERR12033806"] == 3
    with pytest.raises(G.AccessRefused):
        next(g.iter_fastq_control("ERR12033806", "x"))                    # not a control run
    with pytest.raises(G.AccessRefused):
        next(g.iter_fastq_batches("ERR12033806", "HELDOUT", lambda ss: [None] * len(ss), "x"))
    with pytest.raises(G.AccessRefused):
        next(g.iter_fastq_batches("ERR12033850", "FIT", lambda ss: [None] * len(ss), "x"))   # control has no references


def test_only_the_guard_names_read_files_in_fitting_code():
    """Fitting code (experiments/v7/fit, library fitting modules) must obtain reads through the guard."""
    names = ("RX__", "Clusters.txt", "fastq.gz", ".fastq", "iter_cnr_unguarded")
    roots = [ROOT / "experiments/v7/fit", ROOT / "src/vnxdna/simulation"]
    offenders = []
    for root in roots:
        for p in root.rglob("*.py") if root.exists() else []:
            text = p.read_text()
            offenders += [f"{p}: {n}" for n in names if n in text]
    assert not offenders, offenders


def test_fitting_data_directory_honours_vnx_data_dir():
    """experiments/v7/fit reads its data directory from VNX_DATA_DIR, like split.py (default: the lab's public data)."""
    pytest.importorskip("edlib")
    code = "import sys; sys.path.insert(0, sys.argv[1]); import fitlib; print(fitlib.DATA_DIR)"
    import os
    env = dict(os.environ, VNX_DATA_DIR="/elsewhere/public", PYTHONPATH=str(ROOT / "src"))
    r = subprocess.run([sys.executable, "-c", code, str(ROOT / "experiments/v7/fit")], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/elsewhere/public"
