#!/usr/bin/env python3
"""Recompute MANIFEST.json (hashes, counts, length statistics) from the public-data directory. Standard library only.

    python experiments/v7/datasets/build_manifest.py --data-dir /root/vnx-dna-lab/data/public   # write MANIFEST.json
    python experiments/v7/datasets/build_manifest.py --check                                    # recompute, compare, exit 1 on any difference

The data directory is outside the repository (default: $VNX_DATA_DIR or /root/vnx-dna-lab/data/public). Expected layout:
cnr/{Centers.txt,Clusters.txt,LICENSE,README.md}; dt4dds/{ERR..._1.fastq.gz, design_files_Twist_GCfix_0a.fasta};
d03/{clustered_read_segments.tar.gz,oligos.fasta,primers_synthesis.fasta}; each file may have <name>.downloaded_utc
(UTC timestamp of the download, written by the downloader). Metadata that cannot be recomputed (URLs, licences, platform
notes) comes from sources.json. Every dataset here was produced by another group: PUBLIC-DATA-DERIVED, never VNX data.
Nothing here splits the data; the V7 protocol defines the splits.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "MANIFEST.json"
DEFAULT_DIR = os.environ.get("VNX_DATA_DIR", "/root/vnx-dna-lab/data/public")

#: md5 published by the source (ENA fastq_md5, R1 entry; Zenodo file checksum), to cross-check the download
PUBLISHED_MD5 = {
    "dt4dds/ERR12033806_1.fastq.gz": "50711e496dfc4e661b5543b6f2c7f48d",
    "dt4dds/ERR12033810_1.fastq.gz": "1f242501fb98e4c36b05e89fa8b79cee",
    "dt4dds/ERR12033850_1.fastq.gz": "434c817ac7c620ee28c32426a43f39c5",
    "d03/clustered_read_segments.tar.gz": "f770288541b9afcc49675587e61509d4",
    "d03/oligos.fasta": "591dc7072ff5802274f32399d541d4b5",
    "d03/primers_synthesis.fasta": "88b8e4369a0abb6a792a0e121450e6cb",
}
#: read_count published by ENA for the paired run = R1 + R2 reads; R1 alone is half of it
ENA_READ_COUNT = {"ERR12033806": 1928408, "ERR12033810": 2123834, "ERR12033850": 535162}
D02_RUN_INFO = {
    "ERR12033806": {"library": "experimental_Aging_0a_Twist_GCfix", "sample": "Twist_GCfix", "role": "baseline replicate a"},
    "ERR12033810": {"library": "experimental_Aging_0b_Twist_GCfix", "sample": "Twist_GCfix", "role": "baseline replicate b"},
    "ERR12033850": {"library": "phix_Twist_Twist_GCfix_aging", "sample": "PhiX", "role": "PhiX sequencing-only control"},
}


def length_stats(counter: Counter) -> dict:
    n = sum(counter.values())
    if not n:
        return {"n": 0, "min": None, "median": None, "p95": None, "max": None, "mean": None}
    keys = sorted(counter)

    def q(p: float) -> int:
        target, acc = p * (n - 1), 0
        for k in keys:
            acc += counter[k]
            if acc - 1 >= target:
                return k
        return keys[-1]
    return {"n": n, "min": keys[0], "median": q(0.5), "p95": q(0.95), "max": keys[-1],
            "mean": round(sum(k * c for k, c in counter.items()) / n, 4)}


def hash_stream(fh, chunk: int = 1 << 20) -> tuple[str, str, int]:
    s, m, n = hashlib.sha256(), hashlib.md5(), 0  # noqa: S324 (md5 only to compare with the source's published checksum)
    for block in iter(lambda: fh.read(chunk), b""):
        s.update(block)
        m.update(block)
        n += len(block)
    return s.hexdigest(), m.hexdigest(), n


def file_record(root: Path, rel: str) -> dict:
    path = root / rel
    with open(path, "rb") as fh:
        sha, md5, n = hash_stream(fh)
    rec = {"path": rel, "bytes": n, "sha256": sha}
    stamp = path.with_name(path.name.split(".")[0] + ".downloaded_utc")
    if not stamp.exists():
        stamp = path.with_name(path.name + ".downloaded_utc")
    rec["download_utc"] = stamp.read_text().strip() if stamp.exists() else None
    if rel in PUBLISHED_MD5:
        rec["published_md5"] = PUBLISHED_MD5[rel]
        rec["md5_matches_source"] = md5 == PUBLISHED_MD5[rel]
    return rec


def fastq_stats(path: Path) -> dict:
    lens: Counter = Counter()
    quals: Counter = Counter()
    reads = bad = 0
    with gzip.open(path, "rb") as fh:
        while True:
            h = fh.readline()
            if not h:
                break
            seq, plus, q = fh.readline().rstrip(b"\n"), fh.readline(), fh.readline().rstrip(b"\n")
            if not h.startswith(b"@") or not plus.startswith(b"+") or len(seq) != len(q):
                bad += 1
            reads += 1
            lens[len(seq)] += 1
            quals.update(q)
    qv = sorted(c - 33 for c in quals)
    total = sum(quals.values())
    return {"format": "FASTQ (gzip)", "records": reads, "malformed_records": bad, "length": length_stats(lens),
            "quality": {"available": True, "encoding": "Phred+33", "distinct_values": len(qv), "values": qv,
                        "binned": len(qv) <= 8, "min": qv[0], "max": qv[-1],
                        "mean": round(sum((c - 33) * n for c, n in quals.items()) / total, 3),
                        "fraction_ge_30": round(sum(n for c, n in quals.items() if c - 33 >= 30) / total, 4)}}


def fasta_records(fh):
    name, parts = None, []
    for line in fh:
        line = line.rstrip(b"\r\n")
        if line.startswith(b">"):
            if name is not None:
                yield name, b"".join(parts)
            name, parts = line[1:], []
        elif line:
            parts.append(line)
    if name is not None:
        yield name, b"".join(parts)


def fasta_stats(path: Path) -> tuple[dict, set]:
    lens: Counter = Counter()
    seqs: Counter = Counter()
    with open(path, "rb") as fh:
        for _name, seq in fasta_records(fh):
            lens[len(seq)] += 1
            seqs[seq] += 1
    return ({"format": "FASTA", "records": sum(lens.values()), "distinct_sequences": len(seqs), "length": length_stats(lens),
             "quality": {"available": False}}, set(seqs))


def cluster_sizes(lines) -> list[int]:
    """Cluster sizes from an RX/Clusters text: clusters are separated by lines of '=' characters; the first cluster starts
    at the beginning of the file (D03) or after the first separator (CNR, which begins with a separator)."""
    sizes, cur, started = [], 0, False
    for ln in lines:
        s = ln.strip()
        if s.startswith(b"="):
            if started:
                sizes.append(cur)
            cur, started = 0, True
        elif s:
            cur += 1
            started = True
    if started:
        sizes.append(cur)
    return sizes


def size_summary(sizes: list[int]) -> dict:
    c = Counter(sizes)
    st = length_stats(c)
    return {"clusters": len(sizes), "empty_clusters": c.get(0, 0), "reads": sum(sizes),
            "cluster_size": {k: st[k] for k in ("min", "median", "p95", "max", "mean")},
            "clusters_with_ge_10_reads": sum(v for k, v in c.items() if k >= 10)}


def read_lengths_text(lines) -> Counter:
    lens: Counter = Counter()
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith(b"="):
            lens[len(s)] += 1
    return lens


def cnr(root: Path, src: dict) -> dict:
    d = root / "cnr"
    centres = [ln.strip() for ln in (d / "Centers.txt").read_bytes().split(b"\n") if ln.strip()]
    lines = (d / "Clusters.txt").read_bytes().split(b"\n")
    rec = dict(src)
    rec["files"] = [file_record(root, f"cnr/{n}") for n in ("Centers.txt", "Clusters.txt", "LICENSE", "README.md")]
    rec["references"] = {"count": len(centres), "distinct": len(set(centres)),
                         "length": length_stats(Counter(len(c) for c in centres))}
    sizes = cluster_sizes(lines)
    rec["clusters"] = size_summary(sizes)
    rec["clusters"]["matches_reference_count"] = len(sizes) == len(centres)
    rec["reads"] = {"format": "text, one read per line, clusters separated by '=' lines", "records": sum(sizes),
                    "length": length_stats(read_lengths_text(lines)), "quality": {"available": False}}
    return rec


def d02(root: Path, src: dict) -> dict:
    rec = dict(src)
    rec["files"], rec["runs"] = [], {}
    for run in ("ERR12033806", "ERR12033810", "ERR12033850"):
        rel = f"dt4dds/{run}_1.fastq.gz"
        fr = file_record(root, rel)
        st = fastq_stats(root / rel)
        st["ena_read_count_both_mates"] = ENA_READ_COUNT[run]
        st["r1_equals_half_of_ena_count"] = 2 * st["records"] == ENA_READ_COUNT[run]
        rec["files"].append(fr)
        rec["runs"][run] = {**D02_RUN_INFO[run], "mate": "R1", "accession": run, **st}
    rel = "dt4dds/design_files_Twist_GCfix_0a.fasta"
    fr = file_record(root, rel)
    st, _ = fasta_stats(root / rel)
    rec["files"].append(fr)
    rec["design"] = {"file": rel, **st, "committable": False}
    designs = st["records"]
    for run in ("ERR12033806", "ERR12033810"):
        rec["runs"][run]["nominal_reads_per_design"] = round(rec["runs"][run]["records"] / designs, 2)
    return rec


_COMP = bytes.maketrans(b"ACGTN", b"TGCAN")


def canonical(seq: bytes) -> bytes:
    rc = seq.translate(_COMP)[::-1]
    return min(seq, rc)


def d03(root: Path, src: dict) -> dict:
    rec = dict(src)
    rec["files"] = [file_record(root, f"d03/{n}") for n in ("clustered_read_segments.tar.gz", "oligos.fasta", "primers_synthesis.fasta")]
    ostats, oligos = fasta_stats(root / "d03" / "oligos.fasta")
    rec["oligo_pool"] = ostats
    rec["primers"] = [(n.decode(), s.decode()) for n, s in fasta_records(open(root / "d03" / "primers_synthesis.fasta", "rb"))]
    groups, skipped = {}, []
    all_refs: set = set()
    by_file: dict = {}
    with tarfile.open(root / "d03" / "clustered_read_segments.tar.gz", "r:gz") as outer:
        for m in outer:
            if not m.isfile():
                continue
            raw = outer.extractfile(m).read()
            if "passQ-true" not in m.name:
                skipped.append({"inner_archive": m.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                "used": False, "reason": "passQ-false (reads failing the Q>=8 filter)"})
                continue
            gname = m.name.removesuffix(".tar.gz")
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as inner:
                members = {im.name: inner.extractfile(im).read() for im in inner if im.isfile()}
            for name in sorted(members):
                data = members[name]
                base = os.path.basename(name)
                if not base.startswith("TX__"):
                    continue
                rx_name = name.replace("/TX__", "/RX__").replace("TX__", "RX__", 1) if "/TX__" not in name else \
                    name[:name.rindex("/") + 1] + "RX__" + base[4:]
                rx = members[rx_name]
                tx_lines = [ln.strip() for ln in data.split(b"\n") if ln.strip()]
                sizes = cluster_sizes(rx.split(b"\n"))
                direction = "backward" if "backward" in base else "forward"
                refs = set(tx_lines)
                canon = {canonical(t) for t in tx_lines}
                by_file.setdefault(int(gname.split("_")[0].split("-")[1]), set()).update(canon)
                all_refs |= canon
                key = f"{gname}/{direction}"
                groups[key] = {
                    "inner_archive": m.name, "inner_archive_sha256": hashlib.sha256(raw).hexdigest(),
                    "file": int(gname.split("_")[0].split("-")[1]), "accurate_basecaller": "acc-true" in gname,
                    "pass_q": True, "direction": direction,
                    "TX": {"member": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "references": len(tx_lines),
                           "distinct_references": len(refs), "length": length_stats(Counter(len(t) for t in tx_lines))},
                    "RX": {"member": rx_name, "bytes": len(rx), "sha256": hashlib.sha256(rx).hexdigest(),
                           "format": "text, one read segment per line, clusters separated by '=' lines",
                           "length": length_stats(read_lengths_text(rx.split(b"\n"))), "quality": {"available": False}},
                    "clusters": {**size_summary(sizes), "matches_reference_count": len(sizes) == len(tx_lines)},
                }
    rec["groups"] = dict(sorted(groups.items()))
    rec["skipped_inner_archives"] = sorted(skipped, key=lambda r: r["inner_archive"])
    files = sorted(by_file)
    rec["reference_overlap"] = {
        "distinct_canonical_references_over_all_used_groups": len(all_refs),
        "distinct_canonical_references_per_file": {str(f): len(by_file[f]) for f in files},
        "pairwise_shared_between_files": {f"{a}-{b}": len(by_file[a] & by_file[b]) for i, a in enumerate(files) for b in files[i + 1:]},
        "oligo_pool_distinct": len(oligos),
        "note": "canonical = lexicographic minimum of a sequence and its reverse complement (backward groups list the reverse-complemented reference). A file's references are one third of the pool; groups of one file share its references (fast/accurate basecaller, forward/backward), so a reference-disjoint split must group by canonical reference, not by group"}
    return rec


def build(root: Path) -> dict:
    src = json.loads((HERE / "sources.json").read_text())["datasets"]
    return {
        "schema": "vnx.dataset-manifest/1",
        "evidence_class": "PUBLIC-DATA-DERIVED",
        "statement": "Every dataset was produced by another group; VNX-DNA has synthesised, stored and sequenced nothing. Data live outside the repository. No train/dev/held-out split is defined here.",
        "recompute": "python experiments/v7/datasets/build_manifest.py --check",
        "datasets": {"cnr": cnr(root, src["cnr"]), "dt4dds-twist": d02(root, src["dt4dds-twist"]),
                     "d03-nanopore": d03(root, src["d03-nanopore"])},
    }


def strip_volatile(doc: dict) -> dict:
    return json.loads(json.dumps(doc))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DIR)
    ap.add_argument("--check", action="store_true", help="recompute and compare with the committed MANIFEST.json")
    a = ap.parse_args(argv)
    doc = build(Path(a.data_dir))
    text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if a.check:
        old = MANIFEST.read_text()
        if old == text:
            print("MANIFEST.json reproduced exactly")
            return 0
        print("MANIFEST.json differs from the recomputed one", file=sys.stderr)
        return 1
    MANIFEST.write_text(text)
    print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
