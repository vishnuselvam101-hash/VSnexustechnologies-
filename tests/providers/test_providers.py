"""``vnxdna.providers`` (V6 Phase 7, spec §9): protocols, ReferenceSimulatorProvider, export/import packages.

Round trip ``prepare → write → retrieve → read → decode`` for v4-balanced and the V6 stripe profile maximum-recovery
(superblock 2, column parity, interleaved order), deterministic across 1 and 4 workers; negative tests for
VENDOR_MAX_LENGTH, ARCHIVE_TAG_COLLISION and PROVIDER_ERROR (exit 10).

SYNTHETIC SOFTWARE TEST data; every channel result is SIMULATED. Nothing here involves physical DNA.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from vnxdna import sdk
from vnxdna.core import schema
from vnxdna.core.errors import (VNXConfigurationError, VNXDecodeError, VNXError, VNXFormatError, VNXIntegrityError, VNXProviderError,
                                VNXUnsupportedVersionError, error_json)
from vnxdna.pipeline.profiles import dna_options
from vnxdna.providers import (STATUS, ArchiveRef, DNAProvider, DNAReader, DNAWriter, PrimerPair, ProviderCapabilities,
                              ReferenceSimulatorProvider, Selection, SequencingRequest, build_import_package,
                              load_export_package, load_import_package, order_rows)
from vnxdna.v4 import datagen

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "experiments" / "v6" / "channel" / "models"
PROFILES = {"v4-balanced": ("balanced", "v4-balanced"), "maximum-recovery": ("maximum-recovery", "v4-archival")}


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _archive(d: Path, seed: int, size: int = 20_000, name: str = "f.bin") -> Path:
    (d / "ds").mkdir(parents=True, exist_ok=True)
    datagen.generate(d / "ds" / name, size, "random", seed)
    sdk.archive([d / "ds"], d / "a.vnx")
    return d / "a.vnx"


def _encode(d: Path, container: Path, redundancy: str, workers: int = 1, out: str = "s.fasta"):
    res = sdk.encode(container, d / out, dna=dna_options(redundancy, workers=workers))
    return d / out, ArchiveRef.from_encode(container, res.body, redundancy_profile=redundancy)


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    d = tmp_path_factory.mktemp("prov")
    return d, _archive(d, 7301, name="ws.bin")


def _provider(d: Path, **kw) -> ReferenceSimulatorProvider:
    return ReferenceSimulatorProvider(d, models_dir=MODELS, **kw)


def _round_trip(d: Path, container: Path, redundancy: str, workers: int, model: str, seed: int, coverage):
    strands, ref = _encode(d, container, PROFILES[redundancy][0], workers, out=f"s-{redundancy}-{workers}.fasta")
    p = _provider(d / f"lab-{redundancy}-{workers}")
    exp = p.prepare(strands, archive=ref, profile=PROFILES[redundancy][1])
    receipt = p.write(exp)
    imp = p.retrieve(receipt.pool, sequencing=SequencingRequest(model, seed=seed, coverage=coverage, workers=workers))
    batches = list(p.read(imp))
    out = d / f"rec-{redundancy}-{workers}.vnx"
    res = sdk.decode(imp.read_files[0], out, options=sdk.DecodeOptions(workers=workers))
    return {"export": exp, "receipt": receipt, "import": imp, "reads": sum(b.count for b in batches), "decode": res,
            "recovered": out, "provider": p}


@pytest.fixture(scope="module", params=sorted(PROFILES))
def trips(request, ws):
    d, container = ws
    model, seed, cov = ("illumina-like", 7302, 5) if request.param == "v4-balanced" else ("dropout-5", 7303, 6)
    before = sha(container)
    runs = {w: _round_trip(d, container, request.param, w, model, seed, cov) for w in (1, 4)}
    assert sha(container) == before                      # no operation changes archive bytes
    return request.param, container, runs


# --------------------------------------------------------------------------------------------------- interface status
def test_status_table_says_software_only():
    rows = {r["item"]: r for r in STATUS}
    for item in ("DNAWriter, DNAReader, DNAProvider protocols", "ReferenceSimulatorProvider",
                 "Export and import package manifests"):
        assert rows[item]["interface_implemented"] == "yes"
        assert rows[item]["provider_integration_tested"].startswith("software only")
    assert rows["Any synthesis or sequencing vendor adapter"]["interface_implemented"] == "no"


def test_reference_provider_satisfies_the_protocols(tmp_path):
    p = _provider(tmp_path)
    assert isinstance(p, DNAProvider) and isinstance(p, DNAWriter) and isinstance(p, DNAReader)
    assert (p.name, p.version, p.interface) == ("reference-simulator", "1.0.0", "vnx.provider/1")
    assert p.capabilities().max_strand_nt == 350 and p.list() == []


def test_only_the_reference_provider_exists():
    import vnxdna.providers as prov
    providers = [n for n in prov.__all__ if n.endswith("Provider") and n not in ("DNAProvider",)]
    assert providers == ["ReferenceSimulatorProvider"]


# --------------------------------------------------------------------------------------------------------- round trip
def test_round_trip_succeeds_and_recovers_the_container(trips):
    name, container, runs = trips
    for w, r in runs.items():
        assert r["decode"].status == "SUCCESS", (name, w, r["decode"].body.get("status"))
        assert sha(r["recovered"]) == sha(container)
        assert r["reads"] == r["import"].manifest["files"][0]["reads"] > 0
    if name == "maximum-recovery":
        m = runs[1]["export"].manifest["archives"][0]
        assert m["superblock_version"] == 2 and m["outer"]["Mc"] == 2 and m["outer"]["order"] == "interleaved"
        assert m["codec"] == "f4-sb2-cauchy-rs" and m["strand_profile"] == "v4-archival"
        assert runs[1]["import"].manifest["simulation"]["strands_lost"] > 0       # the dropout model removed strands
    else:
        assert runs[1]["export"].manifest["archives"][0]["codec"] == "f4-sb1-cauchy-rs"


def test_round_trip_is_independent_of_the_worker_count(trips):
    _, _, runs = trips
    one, four = runs[1], runs[4]
    assert one["export"].package_id == four["export"].package_id
    assert (one["export"].path / "manifest.json").read_bytes() == (four["export"].path / "manifest.json").read_bytes()
    assert one["import"].package_id == four["import"].package_id
    assert (one["import"].path / "manifest.json").read_bytes() == (four["import"].path / "manifest.json").read_bytes()
    assert sha(one["import"].read_files[0]) == sha(four["import"].read_files[0])
    assert sha(one["recovered"]) == sha(four["recovered"])


def test_every_package_says_simulated(trips):
    _, _, runs = trips
    for r in runs.values():
        assert r["export"].evidence_class == "SIMULATED" and r["import"].evidence_class == "SIMULATED"
        assert r["receipt"].receipt["evidence_class"] == "SIMULATED"
        for m in (r["export"].manifest, r["import"].manifest):
            assert "No DNA has been synthesised, stored or sequenced" in m["statement"]
        sim = r["import"].manifest["simulation"]
        assert sim["model_schema"] == "vnx.channel-model/0" and sim["seed"] in (7302, 7303) and len(sim["model_sha256"]) == 64
        assert r["import"].manifest["provider"] == {"name": "reference-simulator", "version": "1.0.0",
                                                     "interface": "vnx.provider/1"}


def test_manifests_match_their_json_schemas(trips):
    pytest.importorskip("jsonschema")
    _, _, runs = trips
    schema.validate(runs[1]["export"].manifest, "vnx.export-package/1")
    schema.validate(runs[1]["import"].manifest, "vnx.import-package/1")


def test_export_package_contents(trips):
    _, container, runs = trips
    exp = runs[1]["export"]
    m = exp.manifest
    names = sorted(p.name for p in exp.path.iterdir())
    assert names == ["SHA256SUMS", "manifest.json", "order.csv", "strands.fasta"]
    assert exp.path.name == f"{m['package_id']}.vnxexp"
    a = m["archives"][0]
    assert a["container_sha256"] == sha(container) and a["container_size"] == container.stat().st_size
    assert not any(p.suffix == ".vnx" for p in exp.path.iterdir())         # the container is never exported
    assert {c["id"]: c["status"] for c in m["checks"]} == {
        "vendor-max-length": "PASS", "vendor-min-length": "PASS", "alphabet": "PASS", "constraints": "PASS",
        "strands-match-archive": "PASS", "pool-tag-unique": "PASS"}
    vml = next(c for c in m["checks"] if c["id"] == "vendor-max-length")
    assert vml["limit"] == 350 and vml["value"] == a["strand_nt"]["max"] <= 350
    rows = order_rows(exp)
    assert len(rows) == a["strand_count"] and all(set(s) <= set("ACGT") for _, s in rows)
    assert m["vendor_constraints"]["max_strand_nt"] == 350
    sums = (exp.path / "SHA256SUMS").read_text().splitlines()
    assert sorted(line.split()[1] for line in sums) == ["manifest.json", "order.csv", "strands.fasta"]


def test_pool_catalogue(trips):
    _, _, runs = trips
    p, pool = runs[1]["provider"], runs[1]["receipt"].pool
    aid = runs[1]["export"].manifest["archives"][0]["archive_id"]
    assert p.list() == [pool] and p.search(archive_id=aid) == [pool] and p.search(archive_id=bytes.fromhex(aid)) == [pool]
    assert p.search(pool_tag=pool.archives[0]["pool_tag"]) == [pool] and p.search(archive_id="0" * 32) == []
    assert p.write(runs[1]["export"]).pool == pool                          # idempotent
    with pytest.raises(VNXProviderError):
        p.retrieve(pool, selection=Selection(primer_id=3), sequencing=SequencingRequest("clean", seed=1))


# ----------------------------------------------------------------------------------------------- vendor length limit
def _crafted(tmp_path: Path, ws, n: int) -> tuple[Path, ArchiveRef]:
    """A one-strand file of ``n`` nt (GC 50 %, no homopolymer) attached to a real archive's reference."""
    _, container = ws
    f = tmp_path / f"strand{n}.fasta"
    f.write_text(">crafted-0\n" + ("ACGT" * 100)[:n] + "\n")
    base = ArchiveRef.from_encode(container, sdk.encode(container, tmp_path / "x.fasta").body)
    return f, dataclasses.replace(base, strands_sha256=sha(f), strand_count=1)


def _published(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_351_nt_strand_with_350_limit_is_vendor_max_length(tmp_path, ws):
    f, ref = _crafted(tmp_path, ws, 351)
    p = _provider(tmp_path / "lab", capabilities=ProviderCapabilities(max_strand_nt=350))
    with pytest.raises(VNXConfigurationError) as ei:
        p.prepare(f, archive=ref, profile=ref.strand_profile)
    e = ei.value
    assert e.code == "VENDOR_MAX_LENGTH" and e.exit_code == 7 and e.category == "CONFIGURATION_ERROR"
    assert e.details["limit"] == 350 and e.details["value"] == 351
    assert _published(tmp_path / "lab" / "exports") == []                  # nothing published, no temporary left
    if _has_jsonschema():
        schema.validate(error_json(e), "vnx.error/1")


def test_350_nt_strand_with_350_limit_passes(tmp_path, ws):
    f, ref = _crafted(tmp_path, ws, 350)
    exp = _provider(tmp_path / "lab").prepare(f, archive=ref, profile=ref.strand_profile)
    assert next(c for c in exp.manifest["checks"] if c["id"] == "vendor-max-length")["value"] == 350


def test_vendor_limit_is_configurable(tmp_path, ws):
    d, container = ws
    strands, ref = _encode(tmp_path, container, "balanced")
    with pytest.raises(VNXConfigurationError) as ei:
        _provider(tmp_path / "a", capabilities=ProviderCapabilities(max_strand_nt=312)).prepare(
            strands, archive=ref, profile="v4-balanced")
    assert ei.value.code == "VENDOR_MAX_LENGTH" and ei.value.details["value"] == 313
    exp = _provider(tmp_path / "b", capabilities=ProviderCapabilities(max_strand_nt=313)).prepare(
        strands, archive=ref, profile="v4-balanced")
    assert exp.manifest["vendor_constraints"]["max_strand_nt"] == 313
    with pytest.raises(VNXConfigurationError):
        ProviderCapabilities(max_strand_nt=0).validate()


def test_vendor_gc_and_homopolymer_limits(tmp_path, ws):
    f, ref = _crafted(tmp_path, ws, 300)
    with pytest.raises(VNXError) as ei:
        _provider(tmp_path / "lab", capabilities=ProviderCapabilities(gc_min_percent=55)).prepare(
            f, archive=ref, profile=ref.strand_profile)
    assert ei.value.code == "CONSTRAINT_ERROR" and ei.value.exit_code == 7
    assert _published(tmp_path / "lab" / "exports") == []


def _has_jsonschema() -> bool:
    try:
        import jsonschema  # noqa: F401
        return True
    except ImportError:
        return False


# ------------------------------------------------------------------------------------------------- pool composition
@pytest.fixture(scope="module")
def colliding(tmp_path_factory):
    """Two different archives with the same derived archive ID, hence the same frame tag (same file name and size)."""
    d = tmp_path_factory.mktemp("collide")
    a1, a2 = _archive(d / "one", 7311), _archive(d / "two", 7312)
    s1, r1 = _encode(d / "one", a1, "balanced")
    s2, r2 = _encode(d / "two", a2, "balanced")
    assert r1.pool_tag == r2.pool_tag and r1.container_sha256 != r2.container_sha256
    return d, (s1, r1), (s2, r2)


def test_two_archives_with_one_tag_is_archive_tag_collision(colliding, tmp_path):
    _, one, two = colliding
    p = _provider(tmp_path / "lab")
    with pytest.raises(VNXConfigurationError) as ei:
        p.prepare_pool([one, two])
    e = ei.value
    assert e.code == "ARCHIVE_TAG_COLLISION" and e.exit_code == 7
    assert set(e.details["archives"]) == {one[1].archive_id, two[1].archive_id}
    assert _published(tmp_path / "lab" / "exports") == []


def test_identical_archive_twice_is_included_once(colliding, tmp_path):
    _, one, _ = colliding
    exp = _provider(tmp_path / "lab").prepare_pool([one, one])
    assert len(exp.manifest["archives"]) == 1
    check = next(c for c in exp.manifest["checks"] if c["id"] == "pool-tag-unique")
    assert check["status"] == "PASS" and check["details"]["identical_duplicates_included_once"] == [[0, 1]]


def test_pool_of_two_distinct_archives(colliding, ws, tmp_path):
    _, one, _ = colliding
    d, container = ws
    other = _encode(tmp_path, container, "balanced")
    assert other[1].pool_tag != one[1].pool_tag
    p = _provider(tmp_path / "lab")
    exp = p.prepare_pool([one, other])
    a = exp.manifest["archives"]
    assert [x["first_strand"] for x in a] == [0, a[0]["strand_count"]]
    assert len(order_rows(exp)) == a[0]["strand_count"] + a[1]["strand_count"]
    pool = p.write(exp).pool
    assert p.search(pool_tag=other[1].pool_tag) == [pool] and p.search(pool_tag=one[1].pool_tag) == [pool]


# ---------------------------------------------------------------------------------------------- provider errors (10)
def test_provider_error_is_exit_10(tmp_path, ws):
    d, container = ws
    strands, ref = _encode(tmp_path, container, "balanced")
    p = _provider(tmp_path / "lab")
    pool = p.write(p.prepare(strands, archive=ref, profile="v4-balanced")).pool
    tube = tmp_path / "lab" / "pools" / pool.pool_id / "strands.fasta"
    tube.write_text(tube.read_text().replace("A", "C", 1))                 # the "tube" was damaged
    with pytest.raises(VNXProviderError) as ei:
        p.retrieve(pool, sequencing=SequencingRequest("clean", seed=1))
    e = ei.value
    assert (e.exit_code, e.code, e.category) == (10, "PROVIDER_ERROR", "PROVIDER_ERROR")
    assert not isinstance(e, VNXDecodeError)                                # never INSUFFICIENT_REDUNDANCY
    from vnxdna.core.errors import CODES
    from vnxdna.core.taxonomy import EXIT_CODES
    assert EXIT_CODES[10] == "PROVIDER_ERROR" and CODES["PROVIDER_ERROR"] == ("PROVIDER_ERROR", 10, False)
    doc = error_json(e)
    assert doc["exit_code"] == 10 and doc["code"] == "PROVIDER_ERROR"
    if _has_jsonschema():
        schema.validate(doc, "vnx.error/1")
    with pytest.raises(VNXProviderError):
        p.retrieve(dataclasses.replace(pool, pool_id="f" * 64), sequencing=SequencingRequest("clean", seed=1))
    p.close()
    with pytest.raises(VNXProviderError):
        p.list()


def test_cli_maps_provider_error_to_exit_10():
    import typer

    from vnxdna.commands import cli

    def boom():
        raise VNXProviderError("simulated provider outage")
    with pytest.raises(typer.Exit) as ei:
        cli._run(boom)
    assert ei.value.exit_code == 10


# ------------------------------------------------------------------------------------------------ package integrity
def test_tampered_export_package_is_refused(tmp_path, ws):
    d, container = ws
    strands, ref = _encode(tmp_path, container, "balanced")
    p = _provider(tmp_path / "lab")
    exp = p.prepare(strands, archive=ref, profile="v4-balanced")
    copy = tmp_path / "copy.vnxexp"
    shutil.copytree(exp.path, copy)
    (copy / "order.csv").write_text((copy / "order.csv").read_text() + "x,ACGT\n")
    with pytest.raises(VNXIntegrityError):
        load_export_package(copy)
    m = json.loads((exp.path / "manifest.json").read_text())
    m["archives"][0]["pool_tag"] = 1
    (copy / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(VNXIntegrityError):
        load_export_package(copy)
    m["schema"] = "vnx.export-package/2"
    (copy / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(VNXUnsupportedVersionError) as ei:
        load_export_package(copy)
    assert ei.value.code == "SCHEMA_UNSUPPORTED"


def test_tampered_import_package_is_refused(tmp_path, ws):
    d, container = ws
    strands, ref = _encode(tmp_path, container, "balanced")
    p = _provider(tmp_path / "lab")
    imp = p.retrieve(p.write(p.prepare(strands, archive=ref, profile="v4-balanced")).pool,
                     sequencing=SequencingRequest("clean", seed=3, coverage=2))
    copy = tmp_path / "copy.vnximp"
    shutil.copytree(imp.path, copy)
    r1 = copy / "reads" / "r1.fastq"
    r1.write_bytes(r1.read_bytes()[:-10])
    with pytest.raises(VNXIntegrityError):
        load_import_package(copy)
    with pytest.raises(VNXIntegrityError):
        list(p.read(dataclasses.replace(imp, path=copy)))
    m = json.loads((imp.path / "manifest.json").read_text())
    m["files"][0]["name"] = "reads/../../etc"
    (copy / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(VNXFormatError):
        load_import_package(copy)


def test_unsupported_requests_are_refused(tmp_path, ws):
    d, container = ws
    strands, ref = _encode(tmp_path, container, "balanced")
    p = _provider(tmp_path / "lab")
    with pytest.raises(VNXConfigurationError):
        p.prepare(strands, archive=ref, profile="v4-balanced", primers=PrimerPair("ACGT", "TGCA"))
    with pytest.raises(VNXConfigurationError):
        p.prepare(strands, archive=ref, profile="v4-balanced", pool_tag=ref.pool_tag ^ 1)
    with pytest.raises(VNXConfigurationError):
        p.prepare(strands, archive=ref, profile="v4-dense")
    other = tmp_path / "other.fasta"
    other.write_text(strands.read_text().replace("A", "C", 1))
    with pytest.raises(VNXConfigurationError):
        p.prepare(other, archive=ref, profile="v4-balanced")                 # not the strand file of this archive
    pool = p.write(p.prepare(strands, archive=ref, profile="v4-balanced")).pool
    model = json.loads((MODELS / "clean.json").read_text())
    with pytest.raises(VNXUnsupportedVersionError) as ei:
        p.retrieve(pool, sequencing=SequencingRequest({**model, "schema": "vnx.channel-model/9"}, seed=1))
    assert ei.value.code == "SCHEMA_UNSUPPORTED"
    with pytest.raises(VNXConfigurationError):
        p.retrieve(pool, sequencing=SequencingRequest("no-such-model", seed=1))
    with pytest.raises(VNXConfigurationError):
        p.retrieve(pool, sequencing=SequencingRequest("clean", seed=-1))
    imp = p.retrieve(pool, sequencing=SequencingRequest(model, seed=1, coverage=1))   # a model document works
    assert imp.manifest["simulation"]["model"] == "clean"


# ------------------------------------------------------------------------------------- import packages from a laboratory
def test_import_package_classes(tmp_path):
    fq = tmp_path / "x.fastq"
    fq.write_text("@r1\nACGT\n+\nIIII\n@r2\nACGA\n+\nIIII\n")
    lab = {"name": "test-lab", "version": "0", "interface": "vnx.provider/1"}
    with pytest.raises(VNXFormatError):
        build_import_package([fq], tmp_path / "o", provider=lab, evidence_class="REAL PHYSICAL RESULT",
                             export_package_id=None)
    with pytest.raises(VNXFormatError):
        build_import_package([fq], tmp_path / "o", provider=lab, evidence_class="SIMULATED", export_package_id=None)
    with pytest.raises(VNXFormatError):
        build_import_package([fq], tmp_path / "o", provider=lab, evidence_class="PUBLIC-DATA-DERIVED",
                             export_package_id=None)
    assert list((tmp_path / "o").iterdir()) == []
    ds = {"accession": "ENA PRJEB0000000 (test value)", "doi": "unpublished",
          "files": [{"file_name": "x.fastq", "sha256": sha(fq)}]}
    imp = build_import_package([fq], tmp_path / "o", provider=lab, evidence_class="PUBLIC-DATA-DERIVED",
                               export_package_id=None, dataset=ds)
    assert imp.manifest["files"][0]["reads"] == 2 and imp.manifest["sequencing"]["read_length"] == 4
    assert load_import_package(imp.path).package_id == imp.package_id and fq.exists()
    if _has_jsonschema():
        schema.validate(imp.manifest, "vnx.import-package/1")
    real = build_import_package([fq], tmp_path / "r", provider=lab, evidence_class="REAL PHYSICAL RESULT",
                                export_package_id=None, sequencing={"platform": "test platform", "run_id": "RUN-TEST"})
    assert real.evidence_class == "REAL PHYSICAL RESULT"                   # structure only: this is test data


def test_export_package_cannot_claim_a_physical_result(tmp_path, ws):
    from vnxdna.providers import build_export_package
    d, container = ws
    strands, ref = _encode(tmp_path, container, "balanced")
    with pytest.raises(VNXConfigurationError):
        build_export_package([(strands, ref)], tmp_path / "o", provider={"name": "x", "version": "0",
                             "interface": "vnx.provider/1"}, evidence_class="REAL PHYSICAL RESULT")
