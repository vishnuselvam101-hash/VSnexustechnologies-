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


# ---------------------------------------------------------------------------------------------------------------------
# V7 item D3: D13 (Lopez et al. 2019) and CAS9 (Imburgia et al. 2025). Both GitHub repositories have no licence file:
# internal use only, no read or reference data and no per-read derived data are committed (sources.json).
# ---------------------------------------------------------------------------------------------------------------------
D13_RUNS = {"nanopore_run13.fastq.gz": "space_shuttle", "nanopore_run15.fastq.gz": "apollo", "nanopore_run16.fastq.gz": "365-dishes",
            "nanopore_run18.fastq.gz": "apollo", "nanopore_run20.fastq.gz": "vitruvian"}
D13_REFS = {"365-dishes": "seqs_365-dishes.txt", "apollo": "seqs_apollo.txt", "space_shuttle": "seqs_space_shuttle.txt",
            "vitruvian": "seqs_Vitruvian.txt"}
CAS9_READS = "cas9/20200715_basecalled_reads"
CAS9_META = "meta/github-cas9-random-access-basecalled_reads.json"
CAS9_ROOT_META = "meta/github-cas9-random-access-root.json"
D13_TREE_META = "meta/github-data-ncomms19-nanopore-tree.json"
D13_LFS_META = "meta/github-data-ncomms19-nanopore-lfs-pointers.txt"
D13_COMMIT_META = "meta/github-data-ncomms19-nanopore-head-commit.json"
CAS9_COMMIT_META = "meta/github-cas9-random-access-head-commit.json"
D13_HELDOUT_RUN = "nanopore_run13.fastq.gz"  # space_shuttle, amendment D3 PR-3.1


def head_commit(root: Path, rel: str) -> dict:
    doc = json.loads((root / rel).read_text())
    return {"sha": doc["sha"], "tree": doc["commit"]["tree"]["sha"], "date": doc["commit"]["committer"]["date"],
            "metadata": file_record(root, rel)}


def gzip_intact(path: str) -> bool:
    """Decompress the whole file without parsing it (CRC and length check of gzip)."""
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(1 << 24):
                pass
    except (OSError, EOFError):
        return False
    return True


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 (git object ID, compared with GitHub's)


def fastq_profile(path: str) -> dict:
    """Counts of one gzip FASTQ file (worker function): records, malformed records, read lengths, quality values, header
    fields (run IDs, sample IDs, start-time range, read-ID length). Standard library only; quality bytes are counted in
    blocks by Counter (C implementation)."""
    lens: Counter = Counter()
    quals: Counter = Counter()
    runids: Counter = Counter()
    samples: Counter = Counter()
    idlens: Counter = Counter()
    tmin = tmax = None
    reads = bad = 0
    buf = bytearray()
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
            buf += q
            if len(buf) > 1 << 24:
                quals.update(bytes(buf))
                buf.clear()
            f = h[1:].split()
            idlens[len(f[0]) if f else 0] += 1
            for tok in f[1:]:
                k, _, v = tok.partition(b"=")
                if k == b"runid":
                    runids[v.decode()] += 1
                elif k == b"sampleid":
                    samples[v.decode()] += 1
                elif k == b"start_time":
                    t = v.decode()
                    tmin = t if tmin is None or t < tmin else tmin
                    tmax = t if tmax is None or t > tmax else tmax
    quals.update(bytes(buf))
    return {"reads": reads, "bad": bad, "lens": dict(lens), "quals": dict(quals), "runids": dict(runids),
            "samples": dict(samples), "idlens": dict(idlens), "tmin": tmin, "tmax": tmax}


def merge_profiles(profiles: list[dict]) -> dict:
    out: dict = {"reads": 0, "bad": 0, "tmin": None, "tmax": None}
    for name in ("lens", "quals", "runids", "samples", "idlens"):
        out[name] = Counter()
    for p in profiles:
        out["reads"] += p["reads"]
        out["bad"] += p["bad"]
        for name in ("lens", "quals", "runids", "samples", "idlens"):
            out[name].update(p[name])
        if p["tmin"] is not None:
            out["tmin"] = p["tmin"] if out["tmin"] is None else min(out["tmin"], p["tmin"])
            out["tmax"] = p["tmax"] if out["tmax"] is None else max(out["tmax"], p["tmax"])
    return out


def profile_record(p: dict) -> dict:
    quals = p["quals"]
    total = sum(quals.values())
    qv = sorted(c - 33 for c in quals)
    bases = sum(k * v for k, v in p["lens"].items())
    return {"format": "FASTQ (gzip)", "records": p["reads"], "malformed_records": p["bad"], "bases": bases,
            "length": length_stats(Counter(p["lens"])),
            "quality": {"available": True, "encoding": "Phred+33", "distinct_values": len(qv), "binned": len(qv) <= 8,
                        "min": qv[0] if qv else None, "max": qv[-1] if qv else None,
                        "mean": round(sum((c - 33) * n for c, n in quals.items()) / total, 3) if total else None,
                        "fraction_ge_10": round(sum(n for c, n in quals.items() if c - 33 >= 10) / total, 4) if total else None,
                        "fraction_ge_20": round(sum(n for c, n in quals.items() if c - 33 >= 20) / total, 4) if total else None},
            "header": {"run_ids": dict(sorted(p["runids"].items())), "sample_ids": dict(sorted(p["samples"].items())),
                       "start_time_min": p["tmin"], "start_time_max": p["tmax"],
                       "read_id_lengths": {str(k): v for k, v in sorted(p["idlens"].items())}}}


def _pool_map(fn, items: list, workers: int) -> list:
    if workers <= 1 or len(items) <= 1:
        return [fn(i) for i in items]
    import multiprocessing as mp
    with mp.get_context("fork").Pool(min(workers, len(items))) as pool:
        return pool.map(fn, items, chunksize=1)


def parse_lopez_refs(path: Path) -> list[bytes]:
    out = []
    for ln in path.read_bytes().splitlines():
        s = ln.strip()
        if s:
            if not (s.startswith(b"5'-") and s.endswith(b"-3'")):
                raise SystemExit(f"{path}: unexpected reference line format")
            out.append(s[3:-3])
    return out


def d13(root: Path, src: dict, workers: int) -> dict:
    rec = dict(src)
    tree = {t["path"]: t for t in json.loads((root / D13_TREE_META).read_text())["tree"]}
    lfs = {}
    for block in (root / D13_LFS_META).read_text().split("version ")[1:]:
        kv = dict(ln.split(" ", 1) for ln in block.strip().splitlines()[1:])
        lfs[int(kv["size"])] = kv["oid"].removeprefix("sha256:")
    rec["metadata_files"] = [file_record(root, D13_TREE_META), file_record(root, D13_LFS_META)]
    files = []
    for name in ["read_me_data-ncomms19-nanopore.txt"] + sorted(D13_REFS.values()) + sorted(D13_RUNS):
        fr = file_record(root, f"d13/{name}")
        data_path = root / "d13" / name
        if name in D13_RUNS:
            fr["published_lfs_sha256"] = lfs.get(fr["bytes"])
            fr["sha256_matches_lfs_pointer"] = fr["sha256"] == fr["published_lfs_sha256"]
        else:
            fr["published_git_blob_sha1"] = tree[name]["sha"]
            fr["git_blob_sha1_matches_source"] = git_blob_sha1(data_path.read_bytes()) == tree[name]["sha"]
        files.append(fr)
    rec["files"] = files
    refs = {}
    canon_by_file = {}
    for fname, rel in sorted(D13_REFS.items()):
        seqs = parse_lopez_refs(root / "d13" / rel)
        canon_by_file[fname] = {canonical(s) for s in seqs}
        refs[fname] = {"file": f"d13/{rel}", "references": len(seqs), "distinct": len(set(seqs)),
                       "distinct_canonical": len(canon_by_file[fname]), "length": length_stats(Counter(len(s) for s in seqs)),
                       "distinct_first_20nt": len({s[:20] for s in seqs}), "distinct_last_20nt": len({s[-20:] for s in seqs}),
                       "committable": False}
    names = sorted(canon_by_file)
    rec["references"] = refs
    rec["reference_overlap_between_files"] = {f"{a}|{b}": len(canon_by_file[a] & canon_by_file[b])
                                              for i, a in enumerate(names) for b in names[i + 1:]}
    rec["source_commit"] = head_commit(root, D13_COMMIT_META)
    runs = sorted(r for r in D13_RUNS if r != D13_HELDOUT_RUN)
    profiles = _pool_map(fastq_profile, [str(root / "d13" / r) for r in runs], workers)
    rec["runs"] = {r.removesuffix(".fastq.gz"): {"file": D13_RUNS[r], "fastq": f"d13/{r}", "split": "FIT/DEV (by reference)",
                                                 **profile_record(p)} for r, p in zip(runs, profiles)}
    rec["runs"][D13_HELDOUT_RUN.removesuffix(".fastq.gz")] = {
        "file": D13_RUNS[D13_HELDOUT_RUN], "fastq": f"d13/{D13_HELDOUT_RUN}", "split": "HELD-OUT (whole run)",
        "gzip_intact": gzip_intact(str(root / "d13" / D13_HELDOUT_RUN)),
        "note": "held out (docs/V7_PROTOCOL_AMENDMENT_D3.md PR-3.1): hashed and gzip-checked only, no read-level statistic"}
    return rec


def cas9(root: Path, src: dict, workers: int) -> dict:
    rec = dict(src)
    listing = {e["name"]: e for e in json.loads((root / CAS9_META).read_text())}
    rootmeta = {e["path"]: e for e in json.loads((root / CAS9_ROOT_META).read_text())}
    rec["metadata_files"] = [file_record(root, CAS9_META), file_record(root, CAS9_ROOT_META)]
    small = []
    for name in ("README.md", "20200715_triple_file_access.py", "splint_all.fasta"):
        fr = file_record(root, f"cas9/{name}")
        fr["published_git_blob_sha1"] = rootmeta[name]["sha"]
        fr["git_blob_sha1_matches_source"] = git_blob_sha1((root / "cas9" / name).read_bytes()) == rootmeta[name]["sha"]
        small.append(fr)
    rec["files"] = small
    gz = sorted(p.name for p in (root / CAS9_READS).glob("*.fastq.gz"))
    reads = []
    for name in gz:
        fr = file_record(root, f"{CAS9_READS}/{name}")
        with gzip.open(root / CAS9_READS / name, "rb") as fh:
            content = fh.read()
        src_name = name.removesuffix(".gz")
        fr["stored_as"] = "gzip of the published file (level 6, mtime 0)"
        fr["content_bytes"] = len(content)
        fr["content_sha256"] = hashlib.sha256(content).hexdigest()
        fr["published_git_blob_sha1"] = listing[src_name]["sha"]
        fr["git_blob_sha1_matches_source"] = git_blob_sha1(content) == listing[src_name]["sha"]
        reads.append(fr)
    rec["read_files"] = reads
    rec["read_files_summary"] = {"published_files": len(listing), "downloaded_files": len(gz),
                                 "all_git_blob_sha1_match": all(r["git_blob_sha1_matches_source"] for r in reads),
                                 "published_bytes": sum(e["size"] for e in listing.values()),
                                 "content_bytes": sum(r["content_bytes"] for r in reads)}
    addresses = []
    name = None
    for ln in (root / "cas9" / "splint_all.fasta").read_text().splitlines():
        if ln.startswith(">"):
            name = ln[1:].strip()
        elif ln.strip():
            addresses.append((name, ln.strip()))
    rec["addresses"] = {"file": "cas9/splint_all.fasta", "count": len(addresses),
                        "length": length_stats(Counter(len(s) for _, s in addresses)),
                        "common_prefix": os.path.commonprefix([s for _, s in addresses])}
    rec["source_commit"] = head_commit(root, CAS9_COMMIT_META)
    rec["read_statistics"] = ("not in the manifest: the held-out address g13 is a subset of every chunk, so read-level "
                              "statistics are computed on FIT/DEV reads by experiments/v7/nanodata (amendment D3 PR-2)")
    return rec


def build(root: Path, workers: int = 1) -> dict:
    src = json.loads((HERE / "sources.json").read_text())["datasets"]
    return {
        "schema": "vnx.dataset-manifest/1",
        "evidence_class": "PUBLIC-DATA-DERIVED",
        "statement": "Every dataset was produced by another group; VNX-DNA has synthesised, stored and sequenced nothing. Data live outside the repository. No train/dev/held-out split is defined here.",
        "recompute": "python experiments/v7/datasets/build_manifest.py --check",
        "datasets": {"cnr": cnr(root, src["cnr"]), "dt4dds-twist": d02(root, src["dt4dds-twist"]),
                     "d03-nanopore": d03(root, src["d03-nanopore"]),
                     "d13-lopez-nanopore": d13(root, src["d13-lopez-nanopore"], workers),
                     "cas9-random-access": cas9(root, src["cas9-random-access"], workers)},
    }


def strip_volatile(doc: dict) -> dict:
    return json.loads(json.dumps(doc))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DIR)
    ap.add_argument("--check", action="store_true", help="recompute and compare with the committed MANIFEST.json")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1),
                    help="processes for the FASTQ scans (the output does not depend on it)")
    a = ap.parse_args(argv)
    doc = build(Path(a.data_dir), a.workers)
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
