"""Generate the SYNTHETIC SOFTWARE TEST fixtures in this directory from the PRE-REFACTOR tree (V6 Phase 2.0).

    PYTHONPATH=<tree>/src python generate.py <tree> [output-dir]

Generating tree: commit 1309554 (build/v6-sprint, before the Phase 2 refactor; package version 5.0.0). The check below
accepts any HEAD whose ``src/`` is identical to that commit's and whose working tree has no uncommitted change under
``src/``, so the fixtures can be regenerated from a later commit that only added tests and fixtures.
No DNA was synthesised or sequenced: strands are encoder output and reads come from the SIMULATED channel
(vnxdna.v4.channel) at fixed seeds. Everything here is deterministic; re-running must reproduce SHA256SUMS.
These fixtures are never regenerated with a newer tree.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

TREE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
OUT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).resolve().parent
EXPECTED_COMMIT = "1309554"
LABEL = "v6.0-golden"
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
FIXED_ID = bytes(range(1, 17))        # test-only archive id / salt so the encrypted archive is reproducible
FIXED_SALT = bytes(range(101, 117))
CHANNEL = dict(substitution_rate=0.004, insertion_rate=0.002, deletion_rate=0.002, coverage=4, coverage_model="fixed",
               reverse_complement_rate=0.1, shuffle_window=64, seed=60001)
CHANNEL_MODEL = "vnxdna.v4.channel.ChannelConfig (SIMULATED i.i.d. substitutions/indels, fixed coverage, strand flips)"
# payload generator seeds: (relative path, pattern, size, seed); the files are stored under inputs/<case>/
PAYLOADS = {
    "stripes-seq": [("random.bin", "random", 7800, 6001)],
    "adaptive-interleaved": [("random.bin", "random", 4000, 6002), ("text.txt", "text", 2000, 6003)],
    "max-recovery": [("random.bin", "random", 8200, 6004)],
    "encrypted-stripes": [("random.bin", "random", 7800, 6005)],
}


def options(name: str):
    from vnxdna.v4 import encoder as en
    from vnxdna.v6 import profiles
    return {
        "stripes-seq": lambda: en.DNAOptions(profile="v4-balanced", stripe_depth=4, column_parity=2, strand_order="sequential"),
        "adaptive-interleaved": lambda: en.DNAOptions(profile="v4-balanced", outer_plan="adaptive"),
        "max-recovery": lambda: profiles.dna_options("maximum-recovery"),
        "encrypted-stripes": lambda: en.DNAOptions(profile="v4-balanced", stripe_depth=4, column_parity=2,
                                                   strand_order="interleaved"),
    }[name]()


def describe(name: str) -> str:
    return {"stripes-seq": "v4-balanced, D 4, Mc 2, sequential",
            "adaptive-interleaved": "v4-balanced, outer_plan adaptive (interleaved)",
            "max-recovery": "redundancy profile maximum-recovery (v4-archival, D 8, Mc 2, interleaved)",
            "encrypted-stripes": "v4-balanced, D 4, Mc 2, interleaved, AES-256-GCM"}[name]


def gz(src: Path, dst: Path) -> None:
    with open(src, "rb") as f, open(dst, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                                                                     compresslevel=9) as g:
        shutil.copyfileobj(f, g)
    src.unlink()


def main() -> None:
    assert TREE is not None, "usage: generate.py <tree> [output-dir]"
    git = ["git", "-C", str(TREE)]
    assert subprocess.call(git + ["diff", "--quiet", EXPECTED_COMMIT, "HEAD", "--", "src"]) == 0, "src/ differs from " + EXPECTED_COMMIT
    assert subprocess.call(git + ["diff", "--quiet", "HEAD", "--", "src"]) == 0, "uncommitted changes under src/"
    sys.path.insert(0, str(TREE / "src"))
    import vnxdna
    assert Path(vnxdna.__file__).resolve().is_relative_to(TREE / "src"), f"vnxdna imported from {vnxdna.__file__}"
    from vnxdna.v4 import archive as ar, channel as ch, datagen, encoder as en

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"label": LABEL, "generated_by_commit": EXPECTED_COMMIT,
                "package_version": vnxdna.__version__, "synthetic": True,
                "statement": "SYNTHETIC SOFTWARE TEST fixtures; no DNA was synthesised or sequenced",
                "channel_model": CHANNEL_MODEL, "channel": CHANNEL, "passphrase_for_encrypted_case": PASSPHRASE,
                "cases": {}}
    work = OUT / "_work"
    for index, (name, payloads) in enumerate(PAYLOADS.items()):
        shutil.rmtree(work, ignore_errors=True)
        inp = OUT / "inputs" / name
        shutil.rmtree(inp, ignore_errors=True)
        inp.mkdir(parents=True)
        for rel, pattern, size, seed in payloads:
            datagen.generate(inp / rel, size, pattern, seed)
        paths = [inp / rel for rel, *_ in payloads]
        encrypted = name.startswith("encrypted")
        vnx = OUT / f"{name}.vnx"
        kw = dict(archive_id=FIXED_ID, salt=FIXED_SALT) if encrypted else {}
        ar.build_archive(paths, vnx, ar.ArchiveOptions(passphrase=PASSPHRASE if encrypted else None), overwrite=True, **kw)
        fa = OUT / f"{name}.strands.fasta"
        info = en.encode_container(vnx, fa, options(name), overwrite=True)
        seed = CHANNEL["seed"] + index                       # one distinct, recorded seed per case
        fq = OUT / f"{name}.reads.fastq"
        stats = ch.simulate_file(fa, fq, ch.ChannelConfig(**{**CHANNEL, "seed": seed}), fmt="fastq", overwrite=True)
        gz(fq, fq.with_name(fq.name + ".gz"))
        manifest["cases"][name] = {
            "description": describe(name), "profile": options(name).profile, "encrypted": encrypted,
            "files": {rel: hashlib.sha256((inp / rel).read_bytes()).hexdigest() for rel, *_ in payloads},
            "payloads": {rel: {"pattern": pattern, "size": size, "seed": seed_} for rel, pattern, size, seed_ in payloads},
            "container_sha256": hashlib.sha256(vnx.read_bytes()).hexdigest(),
            "strands_sha256": hashlib.sha256(fa.read_bytes()).hexdigest(),
            "strands": info["strands"], "strand_nt": info["strand_nt"], "reads": stats["reads"], "channel_seed": seed,
            "dna_options": {k: v for k, v in vars(options(name)).items() if k in (
                "profile", "stripe_depth", "column_parity", "strand_order", "outer_plan", "redundancy_budget")},
        }
    shutil.rmtree(work, ignore_errors=True)
    for p in OUT.rglob("*"):
        if p.is_file():
            p.chmod(0o644)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT).as_posix()}" for p in sorted(OUT.rglob("*"))
            if p.is_file() and p.name not in ("SHA256SUMS", "generate.py", "README.md")]
    (OUT / "SHA256SUMS").write_text("\n".join(sums) + "\n")


if __name__ == "__main__":
    main()
