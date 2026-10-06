"""``vnx.channel-model/2`` (V7 protocol 5.3): /1 compatibility, provenance rules, refusal of effects the simulator cannot
honour, the effects it can (insertion runs, 3-mer context), and the size/depth-limited parser (SIMULATED, software tests)."""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError, VNXUnsupportedVersionError
from vnxdna.simulation import engine, model as cm, registry
from vnxdna.simulation.errormodels import context_index

GOLDEN = json.loads((Path(__file__).with_name("golden_v1_models.json")).read_text())
SHA = "0" * 64
FILES = [{"name": "a.txt", "sha256": "1" * 64}]
MODELS = [f"{n}@{v}" for n, v in registry.available()]


def fitted(**over):
    """A valid fitted /2 document (LABORATORY, PUBLIC-DATA-DERIVED)."""
    from vnxdna.simulation.model2 import dataset_digest
    doc = {
        "schema": cm.SCHEMA_V2, "name": "unit-fit", "version": "1.0.0", "model_id": "unit-fit-F",
        "data_source": "LABORATORY", "evidence_class": "PUBLIC-DATA-DERIVED",
        "provenance": {
            "datasets": [{"id": "D04", "accession": "test:cnr", "url": "https://example.org", "files": FILES,
                          "sha256": dataset_digest(FILES)}],
            "split": {"name": "FIT", "manifest_sha256": SHA},
            "fitting": {"method": "unit", "version": "1", "commit": "a" * 40, "dirty": False, "seed": 1,
                        "timestamp_utc": "2026-10-05T12:00:00Z", "software": {"numpy": "2"}}},
        "stages": {"sequencing": {"substitution": {"rate": 0.01}, "insertion": {"rate": 0.01},
                                  "deletion": {"rate": 0.01}}},
        "parameters": {"sequencing.substitution.rate": {"value": 0.01, "ci95": [0.009, 0.011], "basis": "measured"}},
    }
    doc.update(over)
    return doc


# -- /1 unchanged -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ref", MODELS)
def test_every_shipped_v1_model_loads_unchanged(ref):
    m = registry.load_model(ref)
    assert m.doc["schema"] == cm.SCHEMA_V1 and m.read_as == cm.SCHEMA_V1
    assert m.sha256 == GOLDEN["model_sha256"][ref]        # canonical hash captured from the pre-/2 code (810d35e)
    assert m.describe()["schema"] == cm.SCHEMA_V1
    again, _ = cm.from_doc(json.loads(m.dumps()))
    assert again.doc == m.doc
    assert m.to_v1() == m.doc


@pytest.mark.parametrize("ref", MODELS)
def test_shipped_v1_models_still_give_the_same_reads(ref, tmp_path):
    import hashlib
    rnd = random.Random(11)
    p = tmp_path / "s.fasta"
    p.write_text("".join(f">s{i}\n{''.join(rnd.choice('ACGT') for _ in range(120))}\n" for i in range(300)))
    out = tmp_path / "o.fastq"
    engine.simulate_file(p, out, registry.load_model(ref), 7)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == GOLDEN["reads_sha256"][ref]


def test_v1_reader_refuses_v2_fields():
    m = registry.load_model("mixed-mild")
    for path, val in (("sequencing.context", {"k": 3}), ("sequencing.correlation", {"lag": 1}),
                      ("sequencing.asymmetry", {"orientation": "both"}), ("sequencing.insertion.run_length", {"mean": 2})):
        doc = m.to_json()
        node = doc["stages"]
        parts = path.split(".")
        for q in parts[:-1]:
            node = node[q]
        node[parts[-1]] = val
        with pytest.raises(VNXConfigurationError, match="unknown keys"):
            cm.from_doc(doc)
    doc = m.to_json()
    doc["parameters"] = {}
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(doc)


def test_unknown_schema_majors_are_refused():
    for s in ("vnx.channel-model/3", "vnx.channel-model/22", "vnx.channel-model/-1"):
        with pytest.raises((VNXUnsupportedVersionError, VNXConfigurationError)):
            cm.from_doc(fitted(schema=s))


# -- /2 documents ------------------------------------------------------------------------------------------------------
def test_v2_loads_canonicalises_and_is_deterministic():
    m, _ = cm.from_doc(fitted())
    assert m.doc["schema"] == cm.SCHEMA_V2 and m.read_as == cm.SCHEMA_V2 and m.describe()["schema"] == cm.SCHEMA_V2
    seq = m.stages["sequencing"]
    assert seq["context"] is None and seq["correlation"] is None and seq["asymmetry"] is None
    assert seq["insertion"]["run_length"] == {"distribution": "single", "mean": 1.0}
    again, _ = cm.from_doc(json.loads(m.dumps()))
    assert again.doc == m.doc and again.sha256 == m.sha256
    assert m.parameters()["sequencing"]["substitution"]["rate"] == 0.01


def test_v2_without_active_effects_converts_to_v1_and_effects_block_it():
    m, _ = cm.from_doc(fitted())
    v1 = m.to_v1()
    assert v1["schema"] == cm.SCHEMA_V1
    cm.from_doc(v1)
    ctx = fitted()
    ctx["stages"]["sequencing"]["context"] = {"k": 3, "deletion": [1.0] * 64}
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        cm.from_doc(ctx)[0].to_v1()
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(ctx)[0].to_v0()
    run = fitted()
    run["stages"]["sequencing"]["insertion"]["run_length"] = {"distribution": "geometric", "mean": 1.5}
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        cm.from_doc(run)[0].to_v1()


def test_with_parameters_on_v2_keeps_schema_and_records_derivation():
    m, _ = cm.from_doc(fitted())
    d = m.with_parameters({"sequencing.substitution.rate": 0.02})
    assert d.doc["schema"] == cm.SCHEMA_V2 and d.stages["sequencing"]["substitution"]["rate"] == 0.02
    assert d.doc["provenance"]["derived"][0]["from"] == m.ref
    assert "sequencing.substitution.rate" in m.doc["parameters"] and "sequencing.substitution.rate" not in d.doc["parameters"]
    c = m.with_parameters({"sequencing.insertion.run_length.mean": 2.0, "sequencing.insertion.run_length.distribution": "geometric"})
    assert c.stages["sequencing"]["insertion"]["run_length"]["mean"] == 2.0


@pytest.mark.parametrize("mut,why", [
    (lambda d: d["provenance"].pop("fitting"), "needs provenance.split and provenance.fitting"),
    (lambda d: d["provenance"].pop("split"), "needs provenance.split and provenance.fitting"),
    (lambda d: d["provenance"].update(datasets=[]), "datasets"),
    (lambda d: d["provenance"]["datasets"][0].pop("sha256"), "dataset"),
    (lambda d: d["provenance"]["datasets"][0]["files"][0].update(sha256="xyz"), "sha256"),
    (lambda d: d["provenance"]["datasets"][0].update(files=[]), "files"),
    (lambda d: d["provenance"]["fitting"].update(commit="abc"), "40-hex"),
    (lambda d: d["provenance"]["fitting"].update(dirty="no"), "dirty"),
    (lambda d: d["provenance"]["fitting"].update(seed=-1), "seed"),
    (lambda d: d["provenance"]["fitting"].update(timestamp_utc="yesterday"), "timestamp"),
    (lambda d: d["provenance"]["fitting"].pop("software"), "missing"),
    (lambda d: d["provenance"]["split"].update(name="DEV"), "split"),
    (lambda d: d["provenance"]["split"].update(manifest_sha256="1"), "split"),
    (lambda d: d.update(data_source="SIMULATED", evidence_class="SIMULATED"), "only valid for data_source LABORATORY"),
    (lambda d: d.update(model_id="bad id!"), "model_id"),
])
def test_fitted_model_provenance_rules(mut, why):
    d = copy.deepcopy(fitted())
    mut(d)
    with pytest.raises(VNXConfigurationError, match=why):
        cm.from_doc(d)


@pytest.mark.parametrize("par", [
    {"sequencing.substitution.rate": {"value": 0.02, "basis": "measured"}},              # value differs from stages
    {"sequencing.substitution.rate": {"value": 0.01, "basis": "guessed"}},               # unknown basis
    {"sequencing.substitution.rate": {"value": 0.01}},                                    # no basis
    {"sequencing.nothing": {"value": 1, "basis": "measured"}},                           # unknown path
    {"sequencing.substitution.rate": {"value": 0.01, "basis": "measured", "ci95": [0.02, 0.01]}},
    {"sequencing.substitution.rate": {"value": 0.01, "basis": "measured", "ci95": [0.01]}},
    {"sequencing.substitution.rate": {"value": 0.01, "basis": "measured", "extra": 1}},
    {"sequencing.substitution.matrix": {"value": None, "basis": "measured", "ci95": [0, 1]}},
    [],
])
def test_parameter_block_rules(par):
    d = fitted()
    d["parameters"] = par
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(d)


def test_parameter_vectors_need_matching_ci_shape():
    d = fitted()
    d["stages"]["sequencing"]["position_profile"] = {"basis": "relative", "substitution": [1.0, 2.0, 3.0]}
    d["parameters"] = {"sequencing.position_profile.substitution": {
        "value": [1.0, 2.0, 3.0], "basis": "estimated", "ci95": {"lo": [0.9, 1.9, 2.9], "hi": [1.1, 2.1, 3.1]}}}
    cm.from_doc(d)
    d["parameters"]["sequencing.position_profile.substitution"]["ci95"]["lo"] = [0.9]
    with pytest.raises(VNXConfigurationError, match="shape"):
        cm.from_doc(d)


def test_fit_report_rules():
    d = fitted()
    d["fit_report"] = {"adequacy": "INADEQUATE", "failed_metrics": ["M2"], "metrics": {"M2": {"D": 0.1}}}
    assert cm.from_doc(d)[0].doc["fit_report"]["adequacy"] == "INADEQUATE"
    for bad in ({"adequacy": "GOOD"}, {"adequacy": "ADEQUATE", "failed_metrics": ["M2"]}, {"unknown": 1},
                {"metrics": []}, {"failed_metrics": "M2"}):
        d["fit_report"] = bad
        with pytest.raises(VNXConfigurationError):
            cm.from_doc(d)


# -- effects the simulator cannot honour are refused ------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("correlation", {"lag": 1, "p_event_given_event": 0.3, "p_event_given_no_event": 0.05}),
    ("asymmetry", {"orientation": "backward", "backward_substitution_matrix": None}),
])
def test_unhonourable_effects_are_refused_by_every_simulation_path(field, value, tmp_path):
    d = fitted()
    d["stages"]["sequencing"][field] = value
    m, _ = cm.from_doc(d)                       # the document is valid (it describes a measured effect) ...
    assert m.unsupported_effects() == [field]
    strands = tmp_path / "s.fasta"
    strands.write_text(">a\nACGTACGTAC\n")
    with pytest.raises(VNXConfigurationError, match="cannot honour"):      # ... but is never simulated with it ignored
        engine.simulate_file(strands, tmp_path / "o.fastq", m, 1)
    with pytest.raises(VNXConfigurationError, match="cannot honour"):
        engine.Simulator(m.stages)
    from vnxdna import sdk
    with pytest.raises(VNXConfigurationError, match="cannot honour"):
        sdk.simulate(str(strands), str(tmp_path / "o2.fastq"), model=str(_write(tmp_path, m)), seed=1)
    assert not (tmp_path / "o2.fastq").exists()


def _write(tmp_path, m):
    p = tmp_path / "model.json"
    p.write_text(m.dumps())
    return p


# -- effects the simulator honours --------------------------------------------------------------------------------------
def _strands(path, n, length, seed=3):
    rnd = random.Random(seed)
    seqs = ["".join(rnd.choice("ACGT") for _ in range(length)) for _ in range(n)]
    path.write_text("".join(f">s{i}\n{s}\n" for i, s in enumerate(seqs)))
    return seqs


def _model(seq_stage):
    return cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": {"sequencing": seq_stage}})[0]


def test_insertion_runs_are_honoured(tmp_path):
    p = tmp_path / "s.fasta"
    _strands(p, 600, 100)
    L, n, rate = 100, 600, 0.02
    for mean in (1.0, 3.0):
        m = _model({"insertion": {"rate": rate, "run_length": ({"distribution": "geometric", "mean": mean} if mean > 1 else
                                                              {"distribution": "single", "mean": 1.0})},
                    "coverage": {"model": "fixed", "mean": 1}})
        out = tmp_path / f"o{mean}.fastq"
        res = engine.simulate_file(p, out, m, 5)
        stats = res["stats"] if "stats" in res else res
        lengths = [len(x) for x in out.read_text().split("\n")[1::4] if x]
        extra = sum(lengths) - L * n
        assert extra == stats["insertions"] > 0                      # inserted bases are counted and are the length change
        events = rate * L * n
        sd = (events * (1 + (mean - 1) * 2)) ** 0.5 * mean            # generous: variance of compound geometric sum
        assert abs(extra - events * mean) < 5 * sd, (mean, extra, events * mean)
    # runs of one base give the V4 length change distribution exactly as /1 does
    base = registry.load_model("clean").with_parameters({"sequencing.insertion.rate": rate})
    out1 = tmp_path / "v1.fastq"
    engine.simulate_file(p, out1, base, 5)
    m1 = _model({"insertion": {"rate": rate}, "coverage": {"model": "fixed", "mean": 3}})
    out2 = tmp_path / "v2.fastq"
    engine.simulate_file(p, out2, m1, 5)
    assert out1.read_bytes() == out2.read_bytes()                   # /2 with no active effect = /1, byte for byte


def test_insertion_run_lengths_have_the_requested_mean(tmp_path):
    p = tmp_path / "s.fasta"
    seqs = _strands(p, 4000, 40)
    m = _model({"insertion": {"rate": 0.005, "run_length": {"distribution": "geometric", "mean": 2.5}},
                "coverage": {"model": "fixed", "mean": 1}})
    out = tmp_path / "o.fastq"
    engine.simulate_file(p, out, m, 9)
    reads = [x for x in out.read_text().split("\n")[1::4] if x]
    hist = {}
    for r, s in zip(reads, seqs):
        if len(r) > len(s):
            hist[len(r) - len(s)] = hist.get(len(r) - len(s), 0) + 1       # mostly one event per read at this rate
    total = sum(hist.values())
    mean = sum(k * v for k, v in hist.items()) / total
    assert total > 300 and abs(mean - 2.5) < 0.35, (mean, total)


def test_context_multipliers_are_honoured(tmp_path):
    p = tmp_path / "s.fasta"
    seqs = _strands(p, 500, 120)
    acg = 0 * 16 + 1 * 4 + 2                                          # centred 3-mer A C G
    mult = [0.0] * 64
    mult[acg] = 5.0
    m = _model({"deletion": {"rate": 0.02}, "context": {"k": 3, "deletion": mult},
                "coverage": {"model": "fixed", "mean": 1}})
    out = tmp_path / "o.fastq"
    res = engine.simulate_file(p, out, m, 2)
    codes = np.array([[("ACGT".index(c)) for c in s] for s in seqs], dtype=np.uint8)
    nsites = int((context_index(codes) == acg).sum())
    expected = 0.1 * nsites
    stats = res["stats"] if "stats" in res else res
    assert nsites > 500 and abs(stats["deletions"] - expected) < 5 * (expected ** 0.5), (stats["deletions"], expected)
    # the same model without context deletes at the nominal 2 % of all sites
    plain = _model({"deletion": {"rate": 0.02}, "coverage": {"model": "fixed", "mean": 1}})
    res2 = engine.simulate_file(p, tmp_path / "o2.fastq", plain, 2)
    st2 = res2["stats"] if "stats" in res2 else res2
    assert abs(st2["deletions"] - 0.02 * 500 * 120) < 5 * (0.02 * 500 * 120) ** 0.5


def test_context_index_edges():
    codes = np.array([[0, 1, 2, 3, 255, 255]], dtype=np.uint8)
    idx = context_index(codes)[0]
    assert list(idx[:4]) == [0 * 16 + 0 * 4 + 1, 0 * 16 + 1 * 4 + 2, 1 * 16 + 2 * 4 + 3, 2 * 16 + 3 * 4 + 3]


@pytest.mark.parametrize("ctx", [
    {"k": 2}, {"k": 3, "deletion": [1.0] * 63}, {"k": 3, "deletion": [-1.0] * 64}, {"k": 3, "deletion": ["a"] * 64},
    {"k": 3, "substitution": [1e9] * 64}, {"k": 3, "extra": 1}, [], {"k": 3, "deletion": [float("inf")] * 64},
])
def test_context_validation(ctx):
    d = fitted()
    d["stages"]["sequencing"]["context"] = ctx
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(d)


# -- the parser: size, depth and malformed input -------------------------------------------------------------------------
def _file(tmp_path, data: bytes, name="m.json"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_file_size_limit(tmp_path):
    big = b'{"schema": "vnx.channel-model/2", "note": "' + b"x" * (cm.MAX_FILE_BYTES + 1) + b'"}'
    with pytest.raises(VNXConfigurationError, match="larger than"):
        cm.read_file(_file(tmp_path, big))


@pytest.mark.parametrize("depth", [cm.MAX_DEPTH + 2, 200, 5000, 200_000])
def test_nesting_depth_limit_never_crashes(tmp_path, depth):
    for open_, close in ((b"[", b"]"), (b'{"a":', b"}")):
        data = open_ * depth + b"1" + close * depth
        with pytest.raises(VNXConfigurationError):
            cm.read_file(_file(tmp_path, data))
    nested: object = 1
    for _ in range(depth if depth < 5000 else 5000):
        nested = [nested]
    with pytest.raises(VNXConfigurationError):
        cm.from_doc({"schema": cm.SCHEMA_V2, "name": "x", "version": "1.0.0", "note": nested})


@pytest.mark.parametrize("raw", [
    b"", b"{", b"[]", b"null", b"1", b'"x"', b"\xff\xfe{}", b"\xef\xbb\xbf{x}", b'{"schema": "vnx.channel-model/2", "a": NaN}',
    b'{"schema": "vnx.channel-model/2", "a": Infinity}', b'{"schema": "vnx.channel-model/2", "a": -Infinity}',
    b'{"schema": "vnx.channel-model/2", "schema": "vnx.channel-model/2"}',                    # duplicate key
    b'{"schema": "vnx.channel-model/2", "stages": {"sequencing": {"substitution": {"rate": 1e999}}}}',
    b'{"schema": "vnx.channel-model/2", "stages": []}', b'{"schema": "vnx.channel-model/2", "stages": {"sequencing": []}}',
    b'{"schema": 2}', b'{"schema": ["vnx.channel-model/2"]}', b'{"schema": "vnx.channel-model/2", "name": 5}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "parameters": {"a": 1}}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "parameters": 5}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "fit_report": []}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "provenance": []}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "provenance": {"fitting": {}}}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "provenance": {"datasets": "x"}}',
    b'{"schema": "vnx.channel-model/2", "name": "../../etc/passwd", "version": "1.0.0"}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "__proto__": {}}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "stages": {"sequencing": {"quality": {"correct": 1e30}}}}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "stages": {"sequencing": {"coverage": {"mean": -1}}}}',
    b'{"schema": "vnx.channel-model/2", "name": "a", "version": "1.0.0", "stages": {"sequencing": {"homopolymer": {"min_run": 1e30}}}}',
])
def test_malformed_and_malicious_manifests_are_refused_with_a_typed_error(tmp_path, raw):
    with pytest.raises((VNXConfigurationError, VNXUnsupportedVersionError)):
        cm.read_file(_file(tmp_path, raw))


def test_oversized_lists_and_strings_are_refused(tmp_path):
    d = fitted()
    d["stages"]["sequencing"]["position_profile"] = {"basis": "relative", "substitution": [1.0] * 100_001}
    with pytest.raises(VNXConfigurationError, match="1..100000"):
        cm.from_doc(d)
    d = fitted()
    d["note"] = "x" * (cm.MAX_STRING + 1)
    with pytest.raises(VNXConfigurationError, match="longer than"):
        cm.from_doc(d)
    d = fitted()
    d["parameters"] = {f"sequencing.k{i}": {"value": 1, "basis": "measured"} for i in range(2000)}
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(d)
    d = fitted()
    d["provenance"]["fitting"]["software"] = {f"p{i}": "1" for i in range(100)}
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(d)


def test_random_mutations_never_raise_untyped_errors():
    """Fuzz: mutate a valid /2 document at random places; the reader accepts it or raises a typed error, never anything else."""
    rnd = random.Random(2026)
    base = fitted()
    base["stages"]["sequencing"]["context"] = {"k": 3, "deletion": [1.0] * 64}
    base["stages"]["sequencing"]["insertion"]["run_length"] = {"distribution": "geometric", "mean": 2.0}
    base["fit_report"] = {"adequacy": "UNVALIDATED", "metrics": {"M1": {"x": 1.0}}}
    junk = [None, True, False, -1, 0, 1, 2**70, 1e308, -1e-308, 0.5, "", "x", "ACGT" * 5, [], {}, [None], {"a": 1}, [[1], [2]], 1e400]

    def paths(o, prefix=()):
        yield prefix
        if isinstance(o, dict):
            for k, v in o.items():
                yield from paths(v, prefix + (k,))
        elif isinstance(o, list) and len(o) < 70:
            for i, v in enumerate(o):
                yield from paths(v, prefix + (i,))

    all_paths = [p for p in paths(base) if p]
    accepted = refused = 0
    for _ in range(1500):
        d = copy.deepcopy(base)
        for _k in range(rnd.randint(1, 3)):
            path = rnd.choice(all_paths)
            node = d
            try:
                for q in path[:-1]:
                    node = node[q]
                if rnd.random() < 0.15:
                    del node[path[-1]]
                else:
                    node[path[-1]] = rnd.choice(junk)
            except (KeyError, IndexError, TypeError):
                continue
        try:
            cm.from_doc(d)
            accepted += 1
        except (VNXConfigurationError, VNXUnsupportedVersionError):
            refused += 1
    assert refused > 300 and accepted > 0
