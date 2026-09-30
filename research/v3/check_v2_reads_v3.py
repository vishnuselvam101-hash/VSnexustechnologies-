"""Forward compatibility: can VNX-DNA 2.0.0 read what VNX-DNA 3 writes? (software check, no physical DNA)

Usage::

    python research/v3/check_v2_reads_v3.py --v2-cli /path/to/v2.0.0/.venv/bin/vnx-dna --out research/results/v3/v2-reads-v3.json

Writes, with the current (V3) code: an unencrypted archive, an encrypted archive, an encrypted archive whose store
was interrupted and resumed, and the unencrypted archive's strands. Then runs the V2 CLI on each and records the exit
code and whether the restored bytes equal the input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import vnxdna
from vnxdna.v2 import archive as arc
from vnxdna.v2.crypto import generate_key, parse_key
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.profiles import options_for


class Stop(BaseException):
    pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-cli", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="vnxdna-v2-reads-v3-"))
    try:
        text = generate_key()
        (work / "key.txt").write_text(text)
        key = parse_key(text)
        data = os.urandom(40_000)
        (work / "in.bin").write_bytes(data)
        opts = options_for("balanced", chunk_size=4096)

        def stop(done: int, n: int) -> None:
            if done == 3:
                raise Stop()

        try:
            arc.store_file(work / "in.bin", work / "resumed.vxdna", options=opts, key=key, workers=1, checkpoint_interval=1, progress=stop)
        except Stop:
            pass
        arc.store_file(work / "in.bin", work / "resumed.vxdna", options=opts, key=key, workers=1, resume=True, checkpoint_interval=1)
        arc.store_file(work / "in.bin", work / "encrypted.vxdna", options=opts, key=key, workers=1)
        arc.store_file(work / "in.bin", work / "plain.vxdna", options=opts, workers=1)
        encode_file(work / "plain.vxdna", work / "plain.fasta", workers=1)
        v2_version = subprocess.run([a.v2_cli, "version"], capture_output=True, text=True).stdout.strip()
        rows = []
        for name, cmd in (("plain.vxdna", "restore"), ("encrypted.vxdna", "restore"), ("resumed.vxdna", "restore"),
                          ("plain.fasta", "recover")):
            out = work / (name + ".out")
            r = subprocess.run([a.v2_cli, cmd, str(work / name), "-o", str(out), "--key-file", str(work / "key.txt")],
                               capture_output=True, text=True)
            rows.append({"file": name, "written_by": vnxdna.__version__, "read_by": v2_version, "command": cmd, "exit": r.returncode,
                         "identical": out.exists() and hashlib.sha256(out.read_bytes()).digest() == hashlib.sha256(data).digest(),
                         "stderr": r.stderr.strip()[:300]})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result = {"note": "software check; VNX-DNA 3 writes, VNX-DNA 2.0.0 reads", "rows": rows}
    text = json.dumps(result, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
