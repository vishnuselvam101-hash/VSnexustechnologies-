#!/usr/bin/env python3
"""Download (or verify) the CAS9 basecalled reads for V7 item D3. Standard library only.

    python experiments/v7/datasets/fetch_d3.py --verify-only     # check the files already present, download nothing
    python experiments/v7/datasets/fetch_d3.py                   # download the missing files, then verify all

Source: github.com/uwmisl/cas9-random-access, pinned to commit CAS9_COMMIT, directory 20200715_basecalled_reads (288
plain FASTQ files listed by the GitHub contents API; the listing is saved under meta/). Every file is checked against the
git blob SHA-1 of the listing before it is stored, gzip-compressed (level 6, mtime 0), as <name>.gz with a
<name>.gz.downloaded_utc stamp written at download start. A file whose content does not match is deleted and reported.
The repository has no licence: internal use only, never committed (sources.json). The D13 files (Git LFS) were downloaded
by hand and are verified against the LFS pointer SHA-256 by build_manifest.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_DIR = os.environ.get("VNX_DATA_DIR", "/root/vnx-dna-lab/data/public")
CAS9_COMMIT = "03f029cac35896d6abf0ab339ec89b031830b72c"
LISTING = "meta/github-cas9-random-access-basecalled_reads.json"
READS = "cas9/20200715_basecalled_reads"
RAW = "https://raw.githubusercontent.com/uwmisl/cas9-random-access/{commit}/20200715_basecalled_reads/{name}"


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (git object ID)


def verify(path: Path, sha1: str, size: int) -> bool:
    try:
        with gzip.open(path, "rb") as fh:
            data = fh.read()
    except (OSError, EOFError):
        return False
    return len(data) == size and git_blob_sha1(data) == sha1


def fetch(root: Path, entry: dict, retries: int = 4) -> str:
    out = root / READS / (entry["name"] + ".gz")
    if out.exists() and verify(out, entry["sha"], entry["size"]):
        return "present"
    url = RAW.format(commit=CAS9_COMMIT, name=entry["name"])
    for attempt in range(retries):
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (fixed https URL)
                data = resp.read()
        except OSError:
            time.sleep(2 * (attempt + 1))
            continue
        if len(data) != entry["size"] or git_blob_sha1(data) != entry["sha"]:
            time.sleep(2 * (attempt + 1))
            continue
        tmp = out.with_name(out.name + ".part")
        with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz:
            gz.write(data)
        os.replace(tmp, out)
        out.with_name(out.name + ".downloaded_utc").write_text(stamp + "\n")
        return "downloaded"
    if out.exists():
        out.unlink()
    return "FAILED"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DIR)
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    root = Path(a.data_dir)
    (root / READS).mkdir(parents=True, exist_ok=True)
    listing = sorted(json.loads((root / LISTING).read_text()), key=lambda e: e["name"])
    if a.verify_only:
        bad = [e["name"] for e in listing
               if not verify(root / READS / (e["name"] + ".gz"), e["sha"], e["size"])]
        print(json.dumps({"files": len(listing), "missing_or_bad": len(bad), "first": bad[:5]}))
        return 1 if bad else 0
    with ThreadPoolExecutor(a.threads) as pool:
        status = list(pool.map(lambda e: fetch(root, e), listing))
    counts = {s: status.count(s) for s in sorted(set(status))}
    print(json.dumps({"files": len(listing), **counts}))
    return 1 if "FAILED" in counts else 0


if __name__ == "__main__":
    sys.exit(main())
