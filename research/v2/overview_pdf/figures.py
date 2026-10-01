"""Figures for the VNX-DNA overview PDF. Diagrams are drawn; charts are computed from research/results/v2/*.json."""
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch, Circle

RESULTS = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

NAVY, TEAL, INK, MUTED, GRID, PAPER = "#0B2545", "#13A89E", "#1D2433", "#6B7385", "#E3E7EE", "#FFFFFF"
BASE = {"A": "#2E9E5B", "C": "#2F6FDE", "G": "#E0A21B", "T": "#D9483B"}
S1, S2, S3, S4 = "#2F6FDE", "#13A89E", "#E0A21B", "#D9483B"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": "#9AA3B2", "axes.labelcolor": INK,
                     "xtick.color": INK, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
                     "legend.frameon": False, "savefig.dpi": 200, "savefig.bbox": "tight", "savefig.pad_inches": 0.08})


def load(name):
    return json.loads((RESULTS / f"{name}.json").read_text())


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", facecolor=PAPER)
    plt.close(fig)


# ---------------------------------------------------------------- cover: a double helix with base pairs
def cover():
    fig, ax = plt.subplots(figsize=(8.0, 3.2))
    ax.set_axis_off(); ax.set_xlim(0, 4 * math.pi); ax.set_ylim(-1.35, 1.35)
    import numpy as np
    x = np.linspace(0, 4 * math.pi, 600)
    seq = "ACGTTGCAGTCAAGCTTACGGATCCGTA"
    pair = {"A": "T", "T": "A", "C": "G", "G": "C"}
    for i, b in enumerate(seq):
        xi = (i + 0.5) * 4 * math.pi / len(seq)
        y1, y2 = math.sin(xi), -math.sin(xi)
        front = math.cos(xi) > 0
        ax.plot([xi, xi], [y1, 0], color=BASE[b], lw=3.2, alpha=1 if front else 0.45, solid_capstyle="round", zorder=2)
        ax.plot([xi, xi], [0, y2], color=BASE[pair[b]], lw=3.2, alpha=1 if front else 0.45, solid_capstyle="round", zorder=2)
    ax.plot(x, np.sin(x), color=NAVY, lw=5, zorder=3)
    ax.plot(x, -np.sin(x), color=TEAL, lw=5, zorder=3)
    for i, (b, c) in enumerate(BASE.items()):
        ax.scatter([0.55 + i * 0.75], [-1.22], s=90, color=c, zorder=4, clip_on=False)
        ax.text(0.72 + i * 0.75, -1.22, b, va="center", fontsize=10, color=INK)
    save(fig, "cover")


# ---------------------------------------------------------------- pipeline
def box(ax, x, y, w, h, title, sub, color):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08", fc=color, ec="none"))
    ax.text(x + w / 2, y + h * 0.63, title, ha="center", va="center", color="white", fontsize=10.5, fontweight="bold")
    ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", color="white", fontsize=7.1)


def arrow(ax, a, b, color=MUTED):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, color=color, lw=1.6))


def pipeline():
    fig, ax = plt.subplots(figsize=(9, 4.9))
    ax.set_axis_off(); ax.set_xlim(0, 9.2); ax.set_ylim(0.4, 5.45)
    w, h = 2.05, 0.95
    rows = [
        (4.25, NAVY, "WRITE  (digital → DNA)",
         [("Your file", "any size, any type"), ("store", "chunk · zstd · AES-256-GCM"),
          ("encode", "outer + inner Reed–Solomon"), ("DNA strands", "252-nt A/C/G/T, FASTA/VXS")]),
        (2.55, TEAL, "READ  (simulated storage and sequencing)",
         [("sequence", "simulated synthesis + reads"), ("cluster", "group reads by address"),
          ("consensus", "align + vote, N if unsure"), ("decode", "CRC · inner RS · outer RS")]),
        (0.85, "#3B4A6B", "RECOVER  (proof of exactness)",
         [("restore", "decrypt · decompress"), ("verify", "SHA-256 at every layer"),
          ("exact original", "byte-for-byte identical"), ("random access", "any byte range")]),
    ]
    xs = [0.05, 2.35, 4.65, 6.95]
    for y, color, label, items in rows:
        ly = y - 0.28 if y < 1 else y + h + 0.12  # bottom row: label below, clear of the connector
        ax.text(xs[0], ly, label, fontsize=8.5, color=MUTED, fontweight="bold")
        for i, (t, sub) in enumerate(items):
            box(ax, xs[i], y, w, h, t, sub, color)
            if i < 3:
                arrow(ax, (xs[i] + w + 0.03, y + h / 2), (xs[i + 1] - 0.03, y + h / 2))
    arrow(ax, (xs[3] + w / 2, 4.25 - 0.03), (xs[3] + w / 2, 2.55 + h + 0.03))
    # decode → restore: down, left along the gap, down
    mid, x_end = 2.55 - 0.3, xs[0] + w * 0.75
    ax.plot([xs[3] + w / 2, xs[3] + w / 2, x_end], [2.55 - 0.03, mid, mid], color=MUTED, lw=1.6)
    arrow(ax, (x_end, mid), (x_end, 0.85 + h + 0.03))
    save(fig, "pipeline")


# ---------------------------------------------------------------- strand anatomy
def strand():
    fig, ax = plt.subplots(figsize=(9, 3.3))
    ax.set_axis_off(); ax.set_xlim(0, 63); ax.set_ylim(-3.3, 2.4)
    parts = [(11, "header 11 B", NAVY), (40, "payload: 40 bytes of your (encrypted, compressed) data", TEAL), (4, "CRC", S3), (8, "RS parity", S4)]
    x = 0
    for n, label, color in parts:
        ax.add_patch(Rectangle((x, 0), n, 1, fc=color, ec="white", lw=1.5))
        ax.text(x + n / 2, 0.5, label, ha="center", va="center", color="white", fontsize=8.6, fontweight="bold")
        x += n
    ax.text(0, 1.9, "One strand = 63 bytes → 252 nucleotides (2 bits per base)", fontsize=10.5, fontweight="bold", color=INK)
    ax.text(0, 1.35, "balanced profile · the header travels inside the DNA, so file names or FASTA headers are never needed",
            fontsize=8.3, color=MUTED)
    fields = [(1, "variant"), (1, "format"), (4, "archive tag"), (4, "ECC group no."), (1, "shard")]
    x = 0
    for n, label in fields:
        ax.add_patch(Rectangle((x * 2.4, -1.35), n * 2.4, 0.7, fc="#3B4A6B", ec="white", lw=1.2))
        ax.text(x * 2.4 + n * 1.2, -1.0, {"variant": "v", "format": "fmt"}.get(label, label), ha="center", va="center", color="white", fontsize=6.9)
        x += n
    ax.plot([0, 0], [0, -0.65], color=MUTED, lw=0.8); ax.plot([11, 26.4], [0, -0.65], color=MUTED, lw=0.8)
    ax.text(27.2, -1.0, "which archive · which ECC group · which strand of the group\n(+ which scrambler variant made it pass the DNA rules)",
            va="center", fontsize=7.8, color=MUTED)
    import random
    random.seed(3)
    while True:  # an illustration that obeys the balanced-profile rules: GC 40-60 %, homopolymer runs <= 4
        seq = "".join(random.choice("ACGT") for _ in range(63))
        gc = sum(b in "GC" for b in seq) / len(seq)
        if 0.4 <= gc <= 0.6 and not any(b * 5 in seq for b in "ACGT"):
            break
    for i, b in enumerate(seq):
        ax.text(i + 0.5, -2.35, b, ha="center", va="center", fontsize=6.8, color=BASE[b], fontweight="bold", family="DejaVu Sans Mono")
    ax.text(0, -3.05, "illustrative strand sequence (63 of its 252 nt shown); real strands are screened for GC 40–60 % and runs ≤ 4", fontsize=7.8, color=MUTED)
    save(fig, "strand")


# ---------------------------------------------------------------- outer code: one ECC group
def ecc_group():
    import random
    random.seed(11)
    fig, ax = plt.subplots(figsize=(9, 2.9))
    ax.set_axis_off(); ax.set_xlim(-0.5, 40.5); ax.set_ylim(-1.5, 2.9)
    lost = set(random.sample(range(80), 16))
    for i in range(80):
        col, row = i % 40, 1 - i // 40
        color = "#2F6FDE" if i < 64 else S4
        ax.add_patch(Rectangle((col + 0.08, row + 0.08), 0.84, 0.84, fc=color, ec="none", alpha=0.25 if i in lost else 1))
        if i in lost:
            ax.plot([col + 0.2, col + 0.8], [row + 0.2, row + 0.8], color=INK, lw=1.3)
            ax.plot([col + 0.2, col + 0.8], [row + 0.8, row + 0.2], color=INK, lw=1.3)
    ax.text(0, 2.45, "One ECC group: 64 data strands (blue) + 16 parity strands (red)", fontsize=10.5, fontweight="bold", color=INK)
    ax.text(0, -0.55, "Any 16 of the 80 strands may be lost (✕) and the group is still rebuilt exactly — Cauchy Reed–Solomon is MDS.",
            fontsize=8.8, color=INK)
    ax.text(0, -1.2, "Lose 17 in one group and VNX-DNA stops with exit code 5, names the chunk, and writes nothing.",
            fontsize=8.8, color=MUTED)
    save(fig, "ecc_group")


# ---------------------------------------------------------------- layers of integrity
def layers():
    fig, ax = plt.subplots(figsize=(9, 4.1))
    ax.set_axis_off(); ax.set_xlim(0, 10); ax.set_ylim(0, 6.0)
    items = [("1  CRC-32 on every strand", "throws away a damaged read instead of trusting it", S3),
             ("2  Inner Reed–Solomon (8 parity bytes)", "fixes up to 4 wrong bytes in a strand, or 8 at known-bad positions", S4),
             ("3  Outer Cauchy Reed–Solomon (64 + 16)", "rebuilds any 16 missing strands out of every 80", "#2F6FDE"),
             ("4  AES-256-GCM tag + chunk SHA-256", "proves every chunk is authentic, complete and in the right place", TEAL),
             ("5  Whole-file SHA-256 + atomic rename", "the output file appears only if it matches the original exactly", NAVY)]
    for i, (t, sub, c) in enumerate(items):
        y = 4.85 - i * 1.18
        ax.add_patch(FancyBboxPatch((0.1, y), 9.8, 0.98, boxstyle="round,pad=0.02,rounding_size=0.1", fc=c, ec="none"))
        ax.text(0.35, y + 0.64, t, va="center", color="white", fontsize=9.8, fontweight="bold")
        ax.text(0.35, y + 0.28, sub, va="center", color="white", fontsize=8.6)
    save(fig, "layers")


# ---------------------------------------------------------------- measured: scale matrix
def scale_charts():
    d = load("scale-matrix")
    runs = sorted(d["runs"], key=lambda r: r["size"])
    sizes = [r["size"] / 1e9 for r in runs]
    stages = [("store", "store", S1), ("encode_vxs", "encode to DNA", S2), ("recover_dna", "recover from DNA", S3),
              ("restore_container", "restore", S4)]
    # time
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    for key, label, color in stages:
        ax.plot(sizes, [r["stages"][key]["wall_s"] / 60 for r in runs], "-o", color=color, lw=2, ms=5, label=label)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("input size (GB, log scale)"); ax.set_ylabel("wall time (minutes, log scale)")
    ax.set_title("Time per stage grows linearly with file size", loc="left", fontsize=11, fontweight="bold", color=INK)
    ax.legend(ncol=4, loc="upper left", fontsize=8.5)
    save(fig, "scale_time")
    # memory
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    for key, label, color in stages:
        ax.plot(sizes, [r["stages"][key]["peak_rss_tree_bytes"] / 2**30 for r in runs], "-o", color=color, lw=2, ms=5, label=label)
    ax.plot(sizes, [s / 1.073741824 for s in sizes], ":", color=MUTED, lw=1.4, label="file size (for comparison)")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("input size (GB, log scale)"); ax.set_ylabel("peak RAM, all processes (GiB)")
    ax.set_title("Peak memory stays flat while the file grows 10,000×", loc="left", fontsize=11, fontweight="bold", color=INK)
    ax.legend(ncol=3, loc="upper left", fontsize=8.5)
    save(fig, "scale_memory")
    rows = []
    for r in runs:
        st = r["stages"]
        enc = st["encode_vxs"]["report"]["efficiency"]
        total = sum(st[k]["wall_s"] for k in ("store", "encode_vxs", "recover_dna"))
        rows.append({"size": r["size"], "status": r["status"], "strands": enc["strands_total"], "bases": enc["dna_bases_total"],
                     "bases_per_byte": enc["bases_per_original_byte"], "store_s": st["store"]["wall_s"],
                     "encode_s": st["encode_vxs"]["wall_s"], "recover_s": st["recover_dna"]["wall_s"],
                     "peak_gib": max(st[k]["peak_rss_tree_bytes"] for k in st) / 2**30,
                     "disk_gb": max(st[k].get("peak_workdir_bytes", 0) for k in st) / 1e9,
                     "throughput_mb_s": r["size"] / 1e6 / total, "sha": r["sha256_match"] and r["recovered_cmp_identical"],
                     "ra_dna": r["random_access_dna_match"], "ra_offset": r["random_access"]["offset"],
                     "ra_length": r["random_access"]["length"], "env": d["environment"]})
    return rows


# ---------------------------------------------------------------- measured: channel experiments
def rate_chart(rows, axis, title, name, xlabel, pct=True):
    fig, ax = plt.subplots(figsize=(8.6, 3.3))
    for consensus, label, color in ((True, "with clustering + consensus", TEAL), (False, "reads decoded directly", S3)):
        pts = [r for r in rows if r.get("axis") == axis and r["consensus"] == consensus]
        xs = [r["rate"] * (100 if pct else 1) for r in pts]
        ys = [r["summary"]["success_rate"] * 100 for r in pts]
        lo = [r["summary"]["success_ci95"][0] * 100 for r in pts]
        hi = [r["summary"]["success_ci95"][1] * 100 for r in pts]
        ax.fill_between(xs, lo, hi, color=color, alpha=0.15, lw=0)
        ax.plot(xs, ys, "-o", color=color, lw=2, ms=5, label=label)
    ax.set_xscale("log"); ax.set_ylim(-3, 103)
    ax.set_xlabel(xlabel); ax.set_ylabel("exact recoveries (%)")
    ax.set_title(title, loc="left", fontsize=11, fontweight="bold", color=INK)
    ax.legend(loc="lower left", fontsize=8.5)
    save(fig, name)


def channel_charts():
    e = load("errors")["rows"]
    rate_chart(e, "substitution", "Recovery vs. substitution errors (30 trials per point, 95 % CI band)", "subs",
               "substitution rate per base (%, log scale)")
    rate_chart(e, "indel", "Recovery vs. insertion + deletion errors (30 trials per point)", "indels",
               "insertion rate = deletion rate per base (%, log scale)")
    c = load("coverage")["rows"]
    fig, ax = plt.subplots(figsize=(8.6, 3.1))
    pts = [r for r in c if not r["name"].endswith("-direct")]
    xs = [r["channel"]["coverage"] for r in pts]
    ax.bar(range(len(xs)), [r["summary"]["success_rate"] * 100 for r in pts], color=TEAL, width=0.6)
    for i, r in enumerate(pts):
        ax.text(i, r["summary"]["success_rate"] * 100 + 2, f"{r['summary']['success_rate'] * 100:.0f} %", ha="center", fontsize=8.5)
    ax.set_xticks(range(len(xs)), [f"{x:g}×" for x in xs]); ax.set_ylim(0, 112)
    ax.set_xlabel("mean sequencing coverage (reads per strand, Poisson)"); ax.set_ylabel("exact recoveries (%)")
    ax.set_title("How many reads are needed? (40 trials per point)", loc="left", fontsize=11, fontweight="bold", color=INK)
    save(fig, "coverage")
    summary = {"errors": [(r["name"], r["summary"]["success_rate"], r["summary"]["undetected_corruption"], r["summary"]["internal_errors"],
                           r["summary"]["trials"]) for r in e],
               "coverage": [(r["name"], r["summary"]["success_rate"], r["summary"]["trials"]) for r in c]}
    trials = undetected = internal = 0
    for f in ("errors", "coverage", "abundance"):
        for r in load(f)["rows"]:
            trials += r["summary"]["trials"]; undetected += r["summary"]["undetected_corruption"]; internal += r["summary"]["internal_errors"]
    summary["totals"] = {"trials": trials, "undetected": undetected, "internal": internal}
    return summary


# ---------------------------------------------------------------- profiles
def profiles_chart():
    rows = load("profiles")["rows"]
    fig, ax = plt.subplots(figsize=(8.6, 3.1))
    names = [r["profile"] for r in rows]
    ax.bar(names, [r["bases_per_original_byte"] for r in rows], color=[S1, S2, S3, S4], width=0.55)
    for i, r in enumerate(rows):
        g = r["expected_overhead"]
        ax.text(i, r["bases_per_original_byte"] + 0.15, f"{r['bases_per_original_byte']:.2f} nt/byte\nany {g['guaranteed_erasures_per_group']}"
                f" of {g['group_strands']} may be lost", ha="center", fontsize=8)
    ax.set_ylim(0, max(r["bases_per_original_byte"] for r in rows) * 1.45)
    ax.set_ylabel("DNA bases per input byte")
    ax.set_title("Profiles trade DNA cost for resilience (4 MB mixed input)", loc="left", fontsize=11, fontweight="bold", color=INK)
    save(fig, "profiles")
    return [{"profile": r["profile"], "nt_per_byte": r["bases_per_original_byte"], "strand_nt": r["strand_nt"],
             "guarantee": f"{r['expected_overhead']['guaranteed_erasures_per_group']} of {r['expected_overhead']['group_strands']}",
             "inner": r["expected_overhead"]["inner_correctable_byte_errors"], "parity_pct": r["expected_overhead"]["outer_parity_percent"],
             "harsh": r["harsh_channel"]["summary"]["success_rate"]} for r in rows]


# ---------------------------------------------------------------- maturity / roadmap
def maturity():
    fig, ax = plt.subplots(figsize=(9, 2.3))
    ax.set_axis_off(); ax.set_xlim(0, 10); ax.set_ylim(0.9, 3.2)
    steps = [("Software\ncodec", "DONE · v2.0.0", TEAL), ("Simulated\nchannel", "DONE · Monte Carlo", TEAL),
             ("Wet-lab\npilot", "NEXT", S3), ("Fitted channel\nmodels", "from pilot data", "#9AA3B2"),
             ("Partner /\nservice", "archive offering", "#9AA3B2")]
    for i, (t, sub, c) in enumerate(steps):
        x = 0.1 + i * 2
        ax.add_patch(FancyBboxPatch((x, 1.05), 1.75, 1.45, boxstyle="round,pad=0.02,rounding_size=0.12", fc=c, ec="none"))
        ax.text(x + 0.875, 1.95, t, ha="center", va="center", color="white", fontsize=9.3, fontweight="bold", linespacing=1.15)
        ax.text(x + 0.875, 1.3, sub, ha="center", va="center", color="white", fontsize=7.6)
        if i < 4:
            arrow(ax, (x + 1.78, 1.78), (x + 1.97, 1.78))
    ax.text(0.1, 2.85, "Where VNX-DNA is today", fontsize=10.5, fontweight="bold", color=INK)
    save(fig, "maturity")


cover(); pipeline(); strand(); ecc_group(); layers(); maturity()
data = {"scale": scale_charts(), "channel": channel_charts(), "profiles": profiles_chart(),
        "corruption": {k: v for k, v in load("corruption").items() if k in ("status", "size", "beyond_error", "beyond_exit_code",
                       "beyond_output_written", "within_sha256_match", "within_cmp_identical", "damage_within", "damage_beyond")},
        "montecarlo": {k: load("montecarlo")[k]["summary"] | {"channel": load("montecarlo")[k]["channel"], "trials": load("montecarlo")[k]["trials"]}
                       for k in ("canonical", "direct")}}
(OUT / "data.json").write_text(json.dumps(data, indent=1, default=str))
print("figures written to", OUT)
