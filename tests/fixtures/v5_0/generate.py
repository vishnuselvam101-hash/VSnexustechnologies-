"""Generate the SYNTHETIC SOFTWARE TEST fixtures in this directory with a RELEASED VNX-DNA tree.

    PYTHONPATH=<released-tree>/src python generate.py <released-tree> [output-dir]

Generating tree for this directory: V5, commit 6aef3f4 (/root/VSnexustechnologies-v5, v5.0.0).
No DNA was synthesised or sequenced: strands are encoder output and reads come from the SIMULATED channel
(vnxdna.v4.channel) at fixed seeds. Everything here is deterministic; re-running must reproduce SHA256SUMS.
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
EXPECTED_COMMIT = "6aef3f4"
LABEL = "v5.0"
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
FIXED_ID = bytes(range(1, 17))        # test-only archive id / salt so the encrypted archive is reproducible
FIXED_SALT = bytes(range(101, 117))
CHANNEL = dict(substitution_rate=0.004, insertion_rate=0.002, deletion_rate=0.002, coverage=6, coverage_model="fixed",
               reverse_complement_rate=0.1, shuffle_window=64, seed=50001)


def gz(src: Path, dst: Path) -> None:
    with open(src, "rb") as f, open(dst, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0,
                                                                     compresslevel=9) as g:
        shutil.copyfileobj(f, g)
    src.unlink()


def main() -> None:
    assert TREE is not None, "usage: generate.py <released-tree> [output-dir]"
    commit = subprocess.check_output(["git", "-C", str(TREE), "rev-parse", "--short=7", "HEAD"], text=True).strip()
    assert commit == EXPECTED_COMMIT, f"generating tree is {commit}, expected {EXPECTED_COMMIT}"
    sys.path.insert(0, str(TREE / "src"))
    from vnxdna.v4 import archive as ar, channel as ch, datagen, encoder as en

    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "_work"
    shutil.rmtree(work, ignore_errors=True)
    inp = work / "in"
    inp.mkdir(parents=True)
    # deterministic payloads (a few KiB each; sha recorded in manifest.json)
    datagen.generate(inp / "mixed.bin", 6144, "mixed", 5001)
    datagen.generate(inp / "text.txt", 3072, "text", 5002)
    datagen.generate(inp / "random.bin", 2048, "random", 5003)
    (inp / "sub").mkdir()
    datagen.generate(inp / "sub" / "bin.dat", 1500, "binary", 5004)

    cases = {
        "balanced": dict(inputs=["mixed.bin"], profile="v4-balanced", enc=None),
        "archival": dict(inputs=["mixed.bin"], profile="v4-archival", enc=None),
        "encrypted": dict(inputs=["mixed.bin"], profile="v4-balanced", enc=PASSPHRASE),
        "multifile": dict(inputs=["mixed.bin", "text.txt", "random.bin", "sub"], profile="v4-balanced", enc=None),
    }
    manifest = {"label": LABEL, "generated_by_commit": commit, "synthetic": True,
                "statement": "SYNTHETIC SOFTWARE TEST fixtures; no DNA was synthesised or sequenced",
                "channel": CHANNEL, "passphrase_for_encrypted_case": PASSPHRASE, "cases": {}}
    for name, c in cases.items():
        paths = [inp / p for p in c["inputs"]]
        vnx = OUT / f"{name}.vnx"
        opts = ar.ArchiveOptions(passphrase=c["enc"])
        kw = dict(archive_id=FIXED_ID, salt=FIXED_SALT) if c["enc"] else {}
        ar.build_archive(paths, vnx, opts, overwrite=True, **kw)
        fa = OUT / f"{name}.strands.fasta"
        info = en.encode_container(vnx, fa, en.DNAOptions(profile=c["profile"]), overwrite=True)
        seed = CHANNEL["seed"] + len(manifest["cases"])     # one distinct, recorded seed per case
        fq = OUT / f"{name}.reads.fastq"
        stats = ch.simulate_file(fa, fq, ch.ChannelConfig(**{**CHANNEL, "seed": seed}),
                                 fmt="fastq", overwrite=True)
        files = {}
        for p in sorted(x for src in paths for x in ([src] if src.is_file() else src.rglob("*")) if x.is_file()):
            files[p.relative_to(inp).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        manifest["cases"][name] = {
            "profile": c["profile"], "encrypted": bool(c["enc"]), "files": files,
            "container_sha256": hashlib.sha256(vnx.read_bytes()).hexdigest(),
            "strands": info["strands"], "strand_nt": info["strand_nt"],
            "reads": stats["reads"], "channel_seed": seed,
        }
        for f in (fa, fq):
            if f.stat().st_size > 200_000:
                gz(f, f.with_name(f.name + ".gz"))
    shutil.rmtree(work)
    for p in OUT.iterdir():
        if p.is_file():
            p.chmod(0o644)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(OUT.iterdir())
            if p.is_file() and p.name not in ("SHA256SUMS", "generate.py", "README.md")]
    (OUT / "SHA256SUMS").write_text("\n".join(sums) + "\n")


if __name__ == "__main__":
    main()
