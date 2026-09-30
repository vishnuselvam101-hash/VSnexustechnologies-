"""Generate V0.1 compatibility fixtures using the ORIGINAL v0.1-baseline code.

Usage (from repo root):
    git worktree add /tmp/v01 v0.1-baseline
    python tests/fixtures/v0_1/generate_fixtures.py /tmp/v01/src

The script imports `vnxdna` from the given baseline source tree (never the
current tree), writes fixtures next to this file, and records the input
SHA-256 and generator provenance in `fixtures.json`. Inputs are
deterministic (`random.Random(seed)`), but V0.1 embeds uuid4 dataset IDs and
timestamps, so regenerated fixtures differ byte-for-byte from committed ones.
The committed fixtures are the reference.

The Fernet key below is a TEST-ONLY fixture key. It protects nothing.
"""
import hashlib, json, random, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_ONLY_FERNET_KEY = "dm54LWRuYS10ZXN0LW9ubHktZml4dHVyZS1rZXkhISE="  # base64url("vnx-dna-test-only-fixture-key!!!")

def main(baseline_src: str) -> None:
    sys.path.insert(0, baseline_src)
    import vnxdna
    from vnxdna import DatasetConfig, ConstraintSettings
    from vnxdna.core.pipeline import encode_file
    from vnxdna.archive import archive as rd1
    assert Path(vnxdna.__file__).resolve().is_relative_to(Path(baseline_src).resolve()), "must import baseline code"
    commit = subprocess.check_output(["git", "-C", baseline_src, "rev-parse", "HEAD"], text=True).strip()
    constrained = ConstraintSettings(min_gc=40, max_gc=60, max_homopolymer_length=3, max_strand_length=4096)
    cases = {
        "dataset_v1_zlib":            (1, DatasetConfig(strand_payload_bytes=256), None),
        "dataset_v1_empty":           (2, DatasetConfig(strand_payload_bytes=256), None),
        "dataset_v1_zstd_encrypted":  (3, DatasetConfig(strand_payload_bytes=200, compression="zstandard", encryption=True), TEST_ONLY_FERNET_KEY),
        "dataset_v2_rs_4_2":          (4, DatasetConfig(ecc="reed_solomon", data_shards=4, parity_shards=2, strand_payload_bytes=128, compression="none"), None),
        "dataset_v2_rs_8_4":          (5, DatasetConfig(ecc="reed_solomon", data_shards=8, parity_shards=4, strand_payload_bytes=100), None),
        "dataset_v3_constrained":     (6, DatasetConfig(encoding="constrained_v1", strand_payload_bytes=128, constraints=constrained), None),
        "dataset_v3_constrained_rs":  (7, DatasetConfig(encoding="constrained_v1", ecc="reed_solomon", data_shards=4, parity_shards=2, strand_payload_bytes=128, constraints=constrained), None),
    }
    sizes = {1: 3000, 2: 0, 3: 2500, 4: 2000, 5: 3500, 6: 900, 7: 1500}
    records = []
    for name, (seed, config, key) in cases.items():
        data = random.Random(seed).randbytes(sizes[seed])
        target = HERE / name
        shutil.rmtree(target, ignore_errors=True)
        src = HERE / f"{name}.input.bin"; src.write_bytes(data)
        result = encode_file(src, target, config, key)
        records.append({"name": name, "kind": "v0.1-dataset", "input": src.name, "input_sha256": hashlib.sha256(data).hexdigest(),
                        "input_size": len(data), "seed": seed, "strand_count": result.strand_count, "key": "TEST_ONLY_FERNET_KEY" if key else None})
    rd1_cases = {"rd1_archive_plain": (11, dict(ecc="none", chunk_size=128), None),
                 "rd1_archive_rs16": (12, dict(ecc="reed_solomon", parity_symbols=16, chunk_size=128), None),
                 "rd1_archive_encrypted": (13, dict(ecc="none", chunk_size=128, encryption=True), TEST_ONLY_FERNET_KEY)}
    for name, (seed, kwargs, key) in rd1_cases.items():
        data = random.Random(seed).randbytes(1200)
        src = HERE / f"{name}.input.bin"; src.write_bytes(data)
        archive = HERE / f"{name}.vnxdna.json"; archive.unlink(missing_ok=True)
        rd1.encode_file(src, archive, key=key, **kwargs)
        records.append({"name": name, "kind": "rd1-archive", "archive": archive.name, "input": src.name, "input_sha256": hashlib.sha256(data).hexdigest(),
                        "input_size": len(data), "seed": seed, "key": "TEST_ONLY_FERNET_KEY" if key else None})
    (HERE / "fixtures.json").write_text(json.dumps({"generator": "tests/fixtures/v0_1/generate_fixtures.py", "baseline_commit": commit,
        "python": sys.version.split()[0], "test_only_fernet_key": TEST_ONLY_FERNET_KEY, "fixtures": records}, indent=2) + "\n")

if __name__ == "__main__":
    main(sys.argv[1])
