#!/usr/bin/env python3
"""V7 data split (docs/V7_PROTOCOL.md section 4.1, Amendment 1). Standard library only.

    python experiments/v7/split/split.py            # write lists/ and SPLIT_MANIFEST.json
    python experiments/v7/split/split.py --check    # recompute, compare with the committed files, exit 1 on any difference

Inputs: experiments/v7/datasets/MANIFEST.json and the *reference / design* files only (CNR Centers.txt, D03 TX files and
oligos.fasta, DT4DDS design FASTA), each verified against the SHA-256 in the manifest. This script never opens a read file:
no RX file, no Clusters.txt, no FASTQ. Output: per dataset and split the reference-ID list and the run/group-ID list, with
the SHA-256 of every list, and SPLIT_MANIFEST.json. No sequence is written (the DT4DDS design FASTA has no licence);
reference IDs are indices (CNR: 0-based line of Centers.txt; D02: design FASTA ID; D03: oligos.fasta ID).
Evidence class: PUBLIC-DATA-DERIVED (a partition of other groups' reference designs).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refsplit as rs  # noqa: E402

REPO = HERE.parents[2]
DATASETS_DIR = HERE.parent / "datasets"
MANIFEST = DATASETS_DIR / "MANIFEST.json"
PROTOCOL = REPO / "docs" / "V7_PROTOCOL.md"
OUT_MANIFEST = HERE / "SPLIT_MANIFEST.json"
LISTS = HERE / "lists"
DEFAULT_DIR = os.environ.get("VNX_DATA_DIR", "/root/vnx-dna-lab/data/public")
#: protocol dataset IDs (used in the held-out run hash) and the short names of the list directories
IDS = {"cnr": ("D04", "cnr"), "d03-nanopore": ("D03", "d03"), "dt4dds-twist": ("D02", "d02")}
MIN_REFS = 2000


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fasta_records(path: Path):
    name, parts = None, []
    with open(path, "rb") as fh:
        for line in fh:
            line = line.rstrip(b"\r\n")
            if line.startswith(b">"):
                if name is not None:
                    yield name.decode(), b"".join(parts)
                name, parts = line[1:], []
            elif line:
                parts.append(line)
    if name is not None:
        yield name.decode(), b"".join(parts)


def verify(path: Path, expected: str, what: str) -> None:
    got = sha256_file(path)
    if got != expected:
        raise SystemExit(f"{what}: SHA-256 {got} differs from the manifest {expected}; refusing to split")


def manifest_file_sha(ds: dict, rel: str) -> str:
    for f in ds["files"]:
        if f["path"] == rel:
            return f["sha256"]
    raise SystemExit(f"{rel} not in the manifest")


def assign(seqs_by_id: list[tuple[str, bytes]]) -> dict[str, str]:
    """reference ID -> split by bucket only (no held-out run)."""
    return {i: rs.bucket_split(rs.bucket(s)) for i, s in seqs_by_id}


def split_cnr(root: Path, ds: dict) -> dict:
    rel = "cnr/Centers.txt"
    verify(root / rel, manifest_file_sha(ds, rel), rel)
    centres = [ln.strip() for ln in (root / rel).read_bytes().split(b"\n") if ln.strip()]
    ref_split = assign([(str(i), c) for i, c in enumerate(centres)])
    runs = ["cnr"]
    return {"inputs": [{"path": rel, "sha256": manifest_file_sha(ds, rel), "verified": True}],
            "runs": runs, "ref_split": ref_split, "order": [str(i) for i in range(len(centres))],
            "run_lists": {s: [("cnr", "refs-in-split")] for s in (rs.FIT, rs.DEV, rs.HELDOUT)},
            "heldout_run": None, "disjoint_runs": None, "run_note": "single run: split by reference only"}


def split_d02(root: Path, ds: dict) -> dict:
    rel = ds["design"]["file"]
    verify(root / rel, manifest_file_sha(ds, rel), rel)
    recs = list(fasta_records(root / rel))
    ref_split = assign(recs)
    runs = ["ERR12033806", "ERR12033810"]
    rl = {s: [(r, "refs-in-split") for r in runs] for s in (rs.FIT, rs.DEV, rs.HELDOUT)}
    # the PhiX run has no designed strands (reference = PhiX174 genome): it is a sequencing-only control for the FIT stage split
    rl[rs.FIT].append(("ERR12033850", "control-no-references"))
    return {"inputs": [{"path": rel, "sha256": manifest_file_sha(ds, rel), "verified": True, "committable": False}],
            "runs": runs + ["ERR12033850"], "ref_split": ref_split, "order": [n for n, _ in recs], "run_lists": rl,
            "heldout_run": None, "disjoint_runs": False,
            "run_note": "runs ERR12033806 / ERR12033810 share one 12,000-design pool (Amendment 1): split by reference only"}


def split_d03(root: Path, ds: dict) -> dict:
    ol_rel = "d03/oligos.fasta"
    verify(root / ol_rel, manifest_file_sha(ds, ol_rel), ol_rel)
    oligo_id = {rs.canonical(s): n for n, s in fasta_records(root / ol_rel)}
    inputs = [{"path": ol_rel, "sha256": manifest_file_sha(ds, ol_rel), "verified": True}]
    file_refs: dict[int, dict[str, bytes]] = {}
    groups = {}
    for gkey, g in sorted(ds["groups"].items()):
        # TX (reference) file only, from the extracted archive d03/ex/<inner archive>/<direction>/; never the RX read file
        member = g["TX"]["member"].lstrip("./")
        path = root / "d03" / "ex" / g["inner_archive"].removesuffix(".tar.gz") / member
        verify(path, g["TX"]["sha256"], f"d03 TX {gkey}")
        inputs.append({"path": str(path.relative_to(root)), "sha256": g["TX"]["sha256"], "verified": True})
        tx = [ln.strip() for ln in path.read_bytes().split(b"\n") if ln.strip()]
        ids = []
        for t in tx:
            c = rs.canonical(t)
            if c not in oligo_id:
                raise SystemExit(f"{gkey}: a TX reference is not in oligos.fasta")
            ids.append(oligo_id[c])
            file_refs.setdefault(g["file"], {})[oligo_id[c]] = t
        groups[gkey] = {"file": g["file"], "refs": len(tx)}
    files = sorted(file_refs)
    names = [f"file-{f}" for f in files]
    for i, a in enumerate(files):
        for b in files[i + 1:]:
            if set(file_refs[a]) & set(file_refs[b]):
                raise SystemExit(f"files {a} and {b} share references: Amendment 1 premise violated")
    held = rs.heldout_run("D03", names)
    held_files = {int(held.split("-")[1])}
    ref_split: dict[str, str] = {}
    order: list[str] = []
    for f in files:
        for i in sorted(file_refs[f], key=int):
            order.append(i)
            ref_split[i] = rs.HELDOUT if f in held_files else rs.bucket_split(rs.bucket(file_refs[f][i]))
    rl: dict[str, list] = {rs.FIT: [], rs.DEV: [], rs.HELDOUT: []}
    for gkey, g in groups.items():
        if g["file"] in held_files:
            rl[rs.HELDOUT].append((gkey, "all-references"))
        else:
            rl[rs.FIT].append((gkey, "refs-in-split"))
            rl[rs.DEV].append((gkey, "refs-in-split"))
            rl[rs.HELDOUT].append((gkey, "refs-in-split"))
    return {"inputs": inputs, "runs": names, "ref_split": ref_split, "order": order, "run_lists": rl,
            "heldout_run": held, "disjoint_runs": True,
            "run_note": f"files have pairwise disjoint reference sets; held-out run = {held} (all its groups)",
            "groups": groups}


def write_lists(name: str, split: str, kind: str, out: Path, lines: list[str]) -> dict:
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{split}.{kind}.txt"
    p.write_text("".join(f"{i}\n" for i in lines))
    return {"path": f"lists/{name}/{split}.{kind}.txt", "count": len(lines), "sha256": rs.list_sha256(lines)}


def build(root: Path, out: Path) -> dict:
    man = json.loads(MANIFEST.read_text())
    doc = {
        "schema": "vnx.split-manifest/1", "evidence_class": "PUBLIC-DATA-DERIVED",
        "statement": "Partition of other groups' reference designs by the V7 protocol section 4.1 (Amendment 1). No read file was opened to compute it; no sequence is stored here.",
        "protocol": {"path": "docs/V7_PROTOCOL.md", "sha256": sha256_file(PROTOCOL)},
        "dataset_manifest": {"path": "experiments/v7/datasets/MANIFEST.json", "sha256": sha256_file(MANIFEST)},
        "script": {"path": "experiments/v7/split/split.py", "sha256": sha256_file(Path(__file__)),
                   "refsplit_sha256": sha256_file(HERE / "refsplit.py")},
        "rule": {"bucket": "int(SHA-256(canonical sequence), 16) mod 10; canonical = min(seq, reverse complement), upper case",
                 "FIT": "buckets 0-5 of non-held-out runs", "DEV": "buckets 6-7 of non-held-out runs",
                 "HELDOUT": "buckets 8-9 of every run plus the held-out run (disjoint-reference datasets only)",
                 "heldout_run": "runs sorted by name, index int(SHA-256('VNX-V7-HELDOUT/' + dataset_id), 16) mod n_runs"},
        "read_files_opened": False, "datasets": {},
    }
    builders = {"cnr": split_cnr, "dt4dds-twist": split_d02, "d03-nanopore": split_d03}
    for key, fn in builders.items():
        did, short = IDS[key]
        r = fn(root, man["datasets"][key])
        counts = {s: sum(1 for v in r["ref_split"].values() if v == s) for s in rs.SPLITS}
        lists = {}
        for s in rs.SPLITS:
            ids = [i for i in r["order"] if r["ref_split"][i] == s]
            lists[s] = {"refs": write_lists(short, s, "refs", out, ids),
                        "runs": write_lists(short, s, "runs", out, [f"{g}\t{scope}" for g, scope in r["run_lists"][s]])}
        weak = {s: counts[s] < MIN_REFS for s in rs.SPLITS}
        entry = {
            "dataset_id": did, "manifest_key": key, "runs": r["runs"], "disjoint_runs": r["disjoint_runs"],
            "heldout_run": r["heldout_run"], "run_note": r["run_note"], "inputs": r["inputs"],
            "references_total": len(r["order"]), "references_per_split": counts,
            "split_strength": "held-out run plus reference buckets" if r["heldout_run"] else "reference buckets only (weaker kind, protocol 4.3)",
            "splits_below_2000_references": [s for s in rs.SPLITS if weak[s]], "lists": lists,
        }
        if "groups" in r:
            entry["groups"] = r["groups"]
        doc["datasets"][key] = entry
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DEFAULT_DIR)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    out = LISTS
    if a.check:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            doc = build(Path(a.data_dir), tmp)
            ok = True
            for p in sorted(tmp.rglob("*.txt")):
                old = LISTS / p.relative_to(tmp)
                if not old.exists() or old.read_bytes() != p.read_bytes():
                    print("differs:", old, file=sys.stderr)
                    ok = False
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        if not OUT_MANIFEST.exists() or OUT_MANIFEST.read_text() != text:
            print("SPLIT_MANIFEST.json differs", file=sys.stderr)
            ok = False
        print("split reproduced exactly" if ok else "split differs")
        return 0 if ok else 1
    doc = build(Path(a.data_dir), out)
    OUT_MANIFEST.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    for k, d in doc["datasets"].items():
        print(k, d["references_per_split"], "held-out run:", d["heldout_run"], "weak:", d["splits_below_2000_references"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
