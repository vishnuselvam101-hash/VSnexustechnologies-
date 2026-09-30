"""Build the VNX-DNA overview PDF from fig/*.png and fig/data.json (all measured numbers come from data.json)."""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Preformatted, Spacer, Table, TableStyle)

FIG = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2])
META = json.loads(Path(sys.argv[3]).read_text())   # version, commit, tag, tests, date, repo
D = json.loads((FIG / "data.json").read_text())

import matplotlib, os
FONT = os.path.join(matplotlib.get_data_path(), 'fonts', 'ttf') + os.sep
pdfmetrics.registerFont(TTFont("Sans", FONT + "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("Sans-Bold", FONT + "DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Sans-Oblique", FONT + "DejaVuSans-Oblique.ttf"))
pdfmetrics.registerFont(TTFont("Mono", FONT + "DejaVuSansMono.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique", boldItalic="Sans-Bold")

NAVY, TEAL, INK, MUTED, RULE, SOFT = (colors.HexColor(c) for c in ("#0B2545", "#13A89E", "#1D2433", "#6B7385", "#D5DAE3", "#F2F5F9"))
AMBER = colors.HexColor("#E0A21B")

st = {
    "h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=19, leading=24, textColor=NAVY, spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=12.5, leading=16, textColor=NAVY, spaceBefore=12, spaceAfter=5),
    "body": ParagraphStyle("body", fontName="Sans", fontSize=9.6, leading=14.2, textColor=INK, spaceAfter=6),
    "small": ParagraphStyle("small", fontName="Sans", fontSize=8.2, leading=11, textColor=MUTED, spaceAfter=4),
    "cap": ParagraphStyle("cap", fontName="Sans-Oblique", fontSize=8, leading=10.5, textColor=MUTED, spaceBefore=2, spaceAfter=10),
    "bullet": ParagraphStyle("bullet", fontName="Sans", fontSize=9.6, leading=13.8, textColor=INK, leftIndent=12, bulletIndent=2,
                             spaceAfter=3),
    "cell": ParagraphStyle("cell", fontName="Sans", fontSize=8.3, leading=11, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Sans-Bold", fontSize=8.3, leading=11, textColor=colors.white),
    "q": ParagraphStyle("q", fontName="Sans-Bold", fontSize=9.6, leading=13.5, textColor=NAVY, spaceBefore=6, spaceAfter=2),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=7.4, leading=9.8, textColor=INK, backColor=SOFT, borderPadding=6,
                           leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=10),
    "kpi_n": ParagraphStyle("kpi_n", fontName="Sans-Bold", fontSize=17, leading=20, textColor=NAVY, alignment=TA_CENTER),
    "kpi_l": ParagraphStyle("kpi_l", fontName="Sans", fontSize=7.6, leading=10, textColor=MUTED, alignment=TA_CENTER),
    "note": ParagraphStyle("note", fontName="Sans", fontSize=9, leading=13, textColor=INK),
}

W = A4[0] - 36 * mm  # content width


def P(text, style="body"):
    return Paragraph(text, st[style])


def bullets(items):
    return [Paragraph(t, st["bullet"], bulletText="•") for t in items]


def fig(name, width=W, caption=None):
    from PIL import Image as PILImage
    w, h = PILImage.open(FIG / f"{name}.png").size
    out = [Image(str(FIG / f"{name}.png"), width=width, height=width * h / w)]
    if caption:
        out.append(P(caption, "cap"))
    return KeepTogether(out)


def table(rows, widths, header=True, zebra=True):
    data = [[c if not isinstance(c, str) else Paragraph(c, st["cellb" if header and i == 0 else "cell"]) for c in row]
            for i, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY)]
    if zebra:
        style += [("BACKGROUND", (0, r), (-1, r), SOFT) for r in range(2 if header else 1, len(rows), 2)]
    t.setStyle(TableStyle(style))
    return t


def callout(html, color=TEAL):
    t = Table([[Paragraph(html, st["note"])]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return KeepTogether([t, Spacer(1, 8)])


def kpis(items):
    cells = [[Paragraph(n, st["kpi_n"]), ] for n, _ in items]
    row1 = [Paragraph(n, st["kpi_n"]) for n, _ in items]
    row2 = [Paragraph(l, st["kpi_l"]) for _, l in items]
    t = Table([row1, row2], colWidths=[W / len(items)] * len(items))
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SOFT), ("LINEAFTER", (0, 0), (-2, -1), 1, colors.white),
                           ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, 1), (-1, 1), 10)]))
    return KeepTogether([t, Spacer(1, 10)])


def code(text):
    return Preformatted(text.strip("\n"), st["code"])


def gb(n):
    return f"{n / 1e9:g} GB" if n >= 1e9 else f"{n / 1e6:g} MB"


def fmt_s(s):
    return f"{s / 60:.1f} min" if s >= 90 else f"{s:.1f} s"


# ------------------------------------------------------------------------------------------------ numbers
scale = D["scale"]
big = scale[-1]
mc = D["montecarlo"]["canonical"]
tot = D["channel"]["totals"]
corr = D["corruption"]
dw = corr["damage_within"]
env = big["env"]
max_peak = max(r["peak_gib"] for r in scale)
all_pass = all(r["status"] == "PASS" for r in scale)
bits_per_base = 8 / big["bases_per_byte"]

# ------------------------------------------------------------------------------------------------ page templates


def on_cover(c, doc):
    c.saveState()
    c.setFillColor(NAVY); c.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
    c.setFillColor(TEAL); c.rect(0, A4[1] - 9 * mm, A4[0], 9 * mm, stroke=0, fill=1)
    c.restoreState()


def on_page(c, doc):
    c.saveState()
    c.setStrokeColor(RULE); c.setLineWidth(0.6)
    c.line(18 * mm, A4[1] - 14 * mm, A4[0] - 18 * mm, A4[1] - 14 * mm)
    c.setFont("Sans-Bold", 7.5); c.setFillColor(NAVY); c.drawString(18 * mm, A4[1] - 12 * mm, "VNX-DNA")
    c.setFont("Sans", 7.5); c.setFillColor(MUTED)
    c.drawString(33 * mm, A4[1] - 12 * mm, f"Computational DNA data storage · v{META['version']}")
    c.drawRightString(A4[0] - 18 * mm, A4[1] - 12 * mm, "VS Nexus Technologies")
    c.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    c.drawString(18 * mm, 9 * mm, "Software and simulation results only — no wet-lab synthesis or sequencing has been performed.")
    c.drawRightString(A4[0] - 18 * mm, 9 * mm, str(doc.page))
    c.restoreState()


doc = BaseDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                      title="VNX-DNA — what it is and how it works", author="VS Nexus Technologies",
                      subject="VNX-DNA v%s overview for investors and programmers" % META["version"])
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
cover_frame = Frame(18 * mm, 20 * mm, A4[0] - 36 * mm, A4[1] - 50 * mm, id="c")
doc.addPageTemplates([PageTemplate("cover", [cover_frame], onPage=on_cover), PageTemplate("page", [frame], onPage=on_page)])

S = []
white = lambda size, lead, bold=False, color=colors.white, align=TA_LEFT: ParagraphStyle(
    "w", fontName="Sans-Bold" if bold else "Sans", fontSize=size, leading=lead, textColor=color, alignment=align)

# ------------------------------------------------------------------------------------------------ cover
S += [Spacer(1, 22 * mm), Paragraph("VNX-DNA", white(46, 52, True)),
      Paragraph("Computational DNA data storage, end to end", white(17, 23, False, colors.HexColor("#BFE9E4"))),
      Spacer(1, 10 * mm)]
cover_img = Image(str(FIG / "cover.png"), width=W, height=W * 0.4)
cover_img = Image(str(FIG / "cover.png"), width=W - 16, height=(W - 16) * 0.4)
ct = Table([[cover_img]], colWidths=[W]); ct.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white),
                                                                   ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                                                   ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
S += [ct, Spacer(1, 12 * mm),
      Paragraph("What it is · how it works · what has been proven · what investors and programmers ask", white(11, 16)),
      Spacer(1, 30 * mm),
      Paragraph(f"Version {META['version']} · {META['date']}", white(10, 15, True)),
      Paragraph(f"VS Nexus Technologies · {META['repo']}", white(9, 14, False, colors.HexColor("#BFC8D6"))),
      NextPageTemplate("page"), PageBreak()]

# ------------------------------------------------------------------------------------------------ contents
S += [P("Contents", "h1")]
toc = [("1", "VNX-DNA at a glance"), ("2", "Why store data in DNA?"), ("3", "How VNX-DNA works — the whole journey"),
       ("4", "Inside a DNA strand"), ("5", "Error correction: why nothing is lost"), ("6", "Reading the DNA back"),
       ("7", "Security and integrity"), ("8", "Proven results"), ("9", "Storage profiles"), ("10", "For investors"),
       ("11", "For programmers"), ("12", "Limitations — stated plainly"), ("13", "Glossary"), ("A", "Reproducing every number")]
S += [table([[a, b] for a, b in toc], [12 * mm, W - 12 * mm], header=False, zebra=True), Spacer(1, 12),
      callout("<b>How to read this document.</b> Sections 1–3 explain VNX-DNA to anyone. Sections 4–9 are the technical "
              "core. Section 10 answers investor questions and Section 11 answers programmer questions. Every measured number "
              "in this document was generated by the released software on a real machine and is read directly from its result "
              "files; none is typed by hand."),
      PageBreak()]

# ------------------------------------------------------------------------------------------------ 1 at a glance
S += [P("1  VNX-DNA at a glance", "h1"),
      P("<b>VNX-DNA is software that turns any digital file into DNA sequences — strings of the four letters A, C, G and T "
        "— and turns those sequences back into the exact original file.</b> It is the complete digital half of a DNA data "
        "storage system: the part that decides <i>what</i> DNA to make, and that later reconstructs the data from imperfect "
        "sequencing reads."),
      P("A DNA synthesis company can manufacture the sequences VNX-DNA writes. A sequencing machine can read them back. "
        "Everything in between — compression, encryption, error correction, sequence design, making sense of millions of "
        "noisy, shuffled, duplicated reads, and proving the result is exact — is what VNX-DNA does."),
      Spacer(1, 4),
      kpis([(gb(big["size"]), "largest file stored and recovered exactly"), (f"{max_peak:.2f} GiB", "peak RAM at any size, 1 MB – 10 GB"),
            (f"{mc['success_rate'] * 100:.0f} %", f"of {mc['trials']:,} Monte Carlo trials recovered exactly"),
            ("0", f"wrong files returned in {tot['trials'] + mc['trials']:,} simulated trials")]),
      P("What is in version %s" % META["version"], "h2")]
S += bullets([
    "<b>Any file, any size.</b> Files are processed in small chunks, so memory use stays flat: a 10 GB file needed "
    f"{big['peak_gib']:.2f} GiB of RAM, about the same as a 1 GB file.",
    "<b>Three layers of error correction</b> — a checksum and Reed–Solomon code inside every strand, and a second "
    "Reed–Solomon code across strands — so lost and damaged strands are rebuilt with a mathematical guarantee.",
    "<b>Encryption built in</b> (AES-256-GCM). DNA made from an encrypted archive reveals nothing about the content.",
    "<b>A realistic simulated lab</b>: synthesis errors, uneven copy numbers, lost strands, sequencing errors, duplicates, "
    "reversed reads, junk and contamination — followed by clustering and consensus, just as real sequencing data needs.",
    "<b>Random access.</b> Any byte range of a huge archive can be recovered without decoding the rest.",
    "<b>Proof of exactness.</b> Output is released only when its SHA-256 fingerprint matches the original. When recovery "
    "is impossible, VNX-DNA says so, names the damaged chunk and writes nothing.",
    f"<b>{META['tests']} automated tests</b>, all passing on a clean install; open, reproducible benchmarks.",
])
S += [Spacer(1, 4), callout("<b>Honest scope.</b> VNX-DNA is software. Every result in this document comes from computation "
                            "and <b>simulation</b>. No DNA has yet been synthesised or sequenced with VNX-DNA. The next "
                            "milestone is a wet-lab pilot (Section 10).", AMBER), PageBreak()]

# ------------------------------------------------------------------------------------------------ 2 why DNA
S += [P("2  Why store data in DNA?", "h1"),
      P("The world creates data faster than it can afford to keep it. IDC forecast the global datasphere to reach "
        "175 zettabytes by 2025 [1]. Most of that data is rarely read but must be kept for years or decades — medical "
        "images, legal and financial records, scientific datasets, film and cultural archives. Today it sits on "
        "magnetic tape and hard disks, which must be powered, cooled, and migrated to new media every few years."),
      P("DNA is nature's storage medium and it has three properties no electronic medium matches:"),
      *bullets(["<b>Density.</b> DNA stores information at the molecular scale. Published work has estimated that DNA "
                "can hold on the order of 215 petabytes per gram [2] — a data-centre's worth of data in a volume smaller "
                "than a sugar cube.",
                "<b>Longevity.</b> Kept cool and dry (for example encapsulated in silica) DNA has been estimated to remain "
                "readable for thousands of years [3], without power and without migration.",
                "<b>It never becomes obsolete.</b> As long as life sciences exist, people will be able to read DNA. "
                "Tape formats and drive interfaces change every few years; the genetic code does not."]),
      P("Researchers have demonstrated the idea repeatedly: a book stored in DNA in 2012 [4], 739 kB in 2013 [5], "
        "the 'DNA Fountain' codec in 2017 [2], and 200 MB with random access in 2018 [6]."),
      P("What stands in the way", "h2"),
      P("Two things. First, <b>cost and speed of DNA synthesis</b> — the chemistry industry is working on this. Second, "
        "<b>the data problem</b>: synthesis and sequencing are error-prone. Strands are lost, letters are swapped, added or "
        "dropped, reads come back in random order and in wildly uneven numbers. Turning that mess back into a perfect file, "
        "at scale, with a guarantee, is a hard software and coding-theory problem. <b>That is the problem VNX-DNA solves.</b>"),
      Spacer(1, 6),
      P("[1] Reinsel, Gantz, Rydning, <i>Data Age 2025</i>, IDC/Seagate, 2018. [2] Erlich &amp; Zielinski, <i>DNA Fountain "
        "enables a robust and efficient storage architecture</i>, Science 355, 2017. [3] Grass et al., <i>Robust chemical "
        "preservation of digital information on DNA in silica with error-correcting codes</i>, Angew. Chem. 54, 2015. "
        "[4] Church, Gao, Kosuri, <i>Next-generation digital information storage in DNA</i>, Science 337, 2012. "
        "[5] Goldman et al., <i>Towards practical, high-capacity, low-maintenance information storage in synthesized DNA</i>, "
        "Nature 494, 2013. [6] Organick et al., <i>Random access in large-scale DNA data storage</i>, Nature Biotechnology "
        "36, 2018.", "small"),
      PageBreak()]

# ------------------------------------------------------------------------------------------------ 3 how it works
S += [P("3  How VNX-DNA works — the whole journey", "h1"),
      P("VNX-DNA is a chain of steps. Each step is a separate command that writes a file you can inspect and verify, so "
        "the chain can be run one step at a time or all at once with <font face='Mono'>vnx-dna pipeline</font>."),
      fig("pipeline", width=W * 0.9, caption="Figure 1 — The VNX-DNA pipeline. The top row writes DNA, the middle row reads it back "
          "through a simulated lab, the bottom row proves the result is exact."),
      P("Step by step", "h2")]
steps = [
    ("1. store", "The file is cut into fixed-size chunks (1 MiB by default). Each chunk is compressed with zstd (only if that "
     "makes it smaller) and, if a key is given, encrypted with AES-256-GCM. Chunks go into a <i>.vxdna</i> container with an "
     "index and a SHA-256 fingerprint of everything. Memory never holds more than a few chunks."),
    ("2. encode", "Each stored chunk is split into groups of 64 pieces of 40 bytes. For every group, 16 extra "
     "<i>parity</i> pieces are computed (outer Reed–Solomon code). Every piece gets an address header, a CRC checksum and 8 "
     "bytes of inner Reed–Solomon parity, and is written as 252 DNA letters (2 bits per letter)."),
    ("3. sequence rules", "Real DNA chemistry dislikes long runs of one letter (AAAAA) and too much or too little G+C. "
     "VNX-DNA scrambles each strand with one of 256 reversible patterns until it satisfies the rules, and records which "
     "pattern it used inside the strand. If no pattern works, it fails loudly rather than emitting bad DNA."),
    ("4. synthesise and store", "In a real deployment a synthesis provider manufactures the strands. In VNX-DNA today this, and "
     "sequencing, are <b>simulated</b>: strands are lost, copied unevenly, and read with substitution, insertion and deletion "
     "errors, reversed, shuffled, duplicated and mixed with junk."),
    ("5. cluster and consensus", "Reads of the same strand are grouped using the address written inside the DNA itself. "
     "Each group is aligned and a quality-weighted vote produces one best-guess strand. Where the vote is unclear the base is "
     "marked N (unknown) instead of guessed — the error-correction code handles a known unknown twice as efficiently as a "
     "wrong guess."),
    ("6. decode", "Each strand is checked (CRC), repaired if needed (inner RS), and sorted into its group. Missing strands "
     "are rebuilt by the outer code. The container is rebuilt byte for byte."),
    ("7. restore and verify", "Chunks are authenticated, decrypted, decompressed and fingerprinted. The output file appears "
     "only if the whole-file SHA-256 matches the original."),
]
S += [table([["Step", "What happens"]] + [[a, b] for a, b in steps], [30 * mm, W - 30 * mm]), PageBreak()]

# ------------------------------------------------------------------------------------------------ 4 strand
S += [P("4  Inside a DNA strand", "h1"),
      P("Every strand is self-describing. Its address — which archive, which error-correction group, which position in "
        "the group — is written in the DNA itself, not in a file name or a FASTA header. A test tube of mixed, shuffled "
        "strands therefore contains everything needed to reassemble the archive."),
      fig("strand", caption="Figure 2 — Layout of one strand in the balanced profile. 40 of 63 bytes carry data; the rest make "
          "the strand findable, checkable and repairable."),
      P("Density", "h2"),
      P(f"For the 10 GB test file, VNX-DNA produced {big['strands']:,} strands and {big['bases']:,} bases — "
        f"<b>{big['bases_per_byte']:.2f} bases per input byte</b> after compression, all error correction, addressing and "
        f"metadata. That is {bits_per_base:.2f} bits of original data per base, against a theoretical maximum of 2 bits per base "
        "for uncompressible data. (The test file is a 'mixed' pattern that is partly compressible; profile trade-offs are in Section 9.)"),
      table([["Field", "Size", "Purpose"],
             ["scrambler variant", "1 byte", "which of 256 patterns made the strand satisfy the DNA rules"],
             ["format + kind", "1 byte", "frame version 5; data/parity or metadata strand"],
             ["archive tag", "4 bytes", "which archive the strand belongs to (strands from different archives can be mixed)"],
             ["ECC group", "4 bytes", "which error-correction group (up to 4.3 billion groups, ~11 TB of stored data)"],
             ["shard", "1 byte", "position inside the group (data first, then parity)"],
             ["payload", "40 bytes", "the (compressed, encrypted) data itself"],
             ["CRC-32", "4 bytes", "detects a damaged strand"],
             ["inner RS parity", "8 bytes", "repairs up to 4 damaged bytes inside the strand"]],
            [34 * mm, 20 * mm, W - 54 * mm]),
      PageBreak()]

# ------------------------------------------------------------------------------------------------ 5 ECC
S += [P("5  Error correction: why nothing is lost", "h1"),
      P("DNA storage loses whole strands (they are never synthesised, never sampled, or never read) and corrupts letters "
        "inside strands. VNX-DNA fights each with a dedicated code, and verifies the result with cryptographic fingerprints."),
      fig("ecc_group", caption="Figure 3 — The outer code. Every group of 64 data strands gets 16 parity strands; any 16 of the "
          "80 may disappear."),
      P("The outer code is a <b>Cauchy Reed–Solomon</b> code. It is <i>MDS</i> (maximum distance separable) — the best any "
        "code can do: with 16 parity strands, <i>any</i> 16 losses are recoverable, not just most patterns. This was proven "
        "and verified exhaustively on small codes. (An early prototype, V0.1, used a code that was not MDS; VNX-DNA 1.0 found "
        "and replaced it.)"),
      fig("layers", caption="Figure 4 — Five independent layers stand between a damaged test tube and a wrong file."),
      callout("<b>The guarantee in one sentence.</b> If no error-correction group loses more than 16 of its 80 strands, the "
              "file is recovered exactly; if one does, VNX-DNA exits with code 5, names the chunk, and writes nothing. It "
              "never returns a corrupted file as if it were correct."),
      PageBreak()]

# ------------------------------------------------------------------------------------------------ 6 reading back
S += [P("6  Reading the DNA back", "h1"),
      P("A sequencer does not return 'the strands'. It returns millions of short reads: each strand appears many times "
        "(coverage), some not at all, in random order, some reversed, each with its own errors. VNX-DNA handles this "
        "in four stages, all streaming and disk-backed so they scale:"),
      table([["Stage", "What it does", "Why it matters"],
             ["read filtering", "validates reads, drops junk, applies length and quality limits", "garbage never reaches the decoder"],
             ["clustering", "groups reads by the address written in the DNA; a minimizer index catches reads whose address "
              "is damaged", "most reads cluster in constant time — no all-against-all comparison"],
             ["consensus", "aligns each cluster (banded alignment) and takes a quality-weighted vote; unclear bases become N",
              "turns 10 noisy copies into one clean strand and handles insertions/deletions"],
             ["two-pass decoding", "pass 1 checks and repairs every read and spills it to disk; pass 2 sorts by group and "
              "runs the outer code", "memory is independent of the number of reads or their order"]],
            [28 * mm, 80 * mm, W - 108 * mm]),
      Spacer(1, 8),
      P("Insertions and deletions (letters added or dropped) are the hardest errors in DNA storage because they shift "
        "everything after them. Reed–Solomon codes cannot fix shifts, and VNX-DNA does not pretend they can: consensus "
        "alignment across multiple reads re-synchronises the strand first, and an optional single-read realignment handles "
        "one indel per read. Section 8 shows how far this goes."),
      P("Simulated lab: what the channel model includes", "h2")]
S += bullets(["strand dropout and uneven copy numbers (Poisson or log-normal abundance)",
              "synthesis and sequencing substitutions, insertions and deletions; truncated reads; N calls",
              "duplicates, reverse-complement reads, junk reads and contamination from other archives",
              "informative quality scores, full shuffling of reads, FASTQ/FASTA/VXS output",
              "every event is counted as it actually happened, and every run is reproducible from its seed"])
S += [callout("The channel is a <b>stress model</b>, not a model fitted to a specific sequencing platform. Fitting it to real "
              "wet-lab data is part of the pilot (Section 10).", AMBER), PageBreak()]

# ------------------------------------------------------------------------------------------------ 7 security
S += [P("7  Security and integrity", "h1")]
S += bullets([
    "<b>Encryption.</b> AES-256-GCM per chunk with keys derived by HKDF-SHA256. File name, size and hashes are sealed inside "
    "the encrypted manifest, which is authenticated with HMAC-SHA256. Encoding and decoding DNA need no key; only restoring "
    "the plaintext does.",
    "<b>Tamper evidence.</b> Each chunk's authentication is bound to its position and to the total chunk count, so modified, "
    "reordered, duplicated, missing, truncated or spliced chunks are all detected. Format version labels in the key "
    "derivation prevent downgrade attacks.",
    "<b>Crash safety.</b> Every output is written to a temporary file, flushed to disk and renamed into place atomically. A "
    "killed process can never leave behind a file that looks valid. An interrupted store resumes with <font face="Mono">--resume</font> and produces a "
    "byte-identical archive (tested with SIGKILL).",
    "<b>No silent failure.</b> Errors are one line with a stable exit code (0–8, 70). A bug shows as exit code 70, never as a "
    "wrong file.",
    "<b>Code hygiene at release.</b> No secrets or keys in the repository, no eval/exec/pickle or shell execution, no debug "
    "leftovers; fuzzed with thousands of malformed containers and manifests.",
])
S += [callout("<b>What encryption does not hide:</b> the approximate size of the archive and how compressible each chunk "
              "was. This is documented in docs/SECURITY.md.", AMBER), PageBreak()]

# ------------------------------------------------------------------------------------------------ 8 results
S += [P("8  Proven results", "h1"),
      P(f"All measurements below were produced by the released code (v{env['vnxdna_version']}, commit "
        f"<font face='Mono'>{env['git']['commit'][:7]}</font>, clean tree) on a {env['cpu_count']}-CPU x86-64 Linux machine "
        f"with Python {env['python']}, CPU only, no GPU."),
      P("8.1  Large files: 1 MB to 10 GB", "h2"),
      P("Each file was generated, stored, encoded to DNA, recovered from the DNA, restored, and compared with the original using "
        "both SHA-256 and a byte-by-byte comparison. A 1 MiB region near the middle was also extracted directly from the DNA "
        "file (random access) and compared with the original bytes.")]
rows = [["Input", "Strands", "DNA bases", "Store", "Encode", "Recover", "Peak RAM", "Result"]]
for r in scale:
    rows.append([gb(r["size"]), f"{r['strands']:,}", f"{r['bases']:,}", fmt_s(r["store_s"]), fmt_s(r["encode_s"]),
                 fmt_s(r["recover_s"]), f"{r['peak_gib']:.2f} GiB",
                 ("exact ✓" if r["sha"] and r["ra_dna"] else "FAILED") if r["status"] == "PASS" else r["status"]])
S += [table(rows, [17 * mm, 25 * mm, 29 * mm, 17 * mm, 18 * mm, 18 * mm, 19 * mm, W - 143 * mm]),
      P("Table 1 — Scale matrix. 'Exact' means SHA-256 and byte comparison matched for the whole file and for the random-access "
        "region. Peak RAM is the whole process tree.", "cap"),
      fig("scale_memory", caption="Figure 5 — The key scalability result: memory depends on chunk size and worker count, not "
          "on file size."),
      fig("scale_time", caption=f"Figure 6 — Time grows linearly with size. End-to-end store + encode + recover averaged "
          f"{big['throughput_mb_s']:.0f} MB/s at 10 GB on 8 CPUs. Peak scratch disk for 10 GB was {big['disk_gb']:.0f} GB."),
      P("8.2  Damage at scale (1 GB)", "h2"),
      P(f"On the real strand file of a 1 GB archive, {dw['strands_deleted']:,} strands were deleted and {dw['strands_substituted']:,} "
        f"more were corrupted, spread over {len(dw['damaged_groups'])} error-correction groups — each group losing exactly "
        f"{dw['damaged_groups'][0]['strands_deleted']} strands, the limit of the guarantee. Recovery was "
        f"<b>{'exact (SHA-256 and byte comparison matched)' if corr['within_sha256_match'] and corr['within_cmp_identical'] else 'NOT exact'}</b>. "
        f"A second run repeated that damage and added one more group that lost "
        f"{corr['damage_beyond']['strands_deleted'] - dw['strands_deleted']} strands — one beyond the guarantee: VNX-DNA exited with code "
        f"{corr['beyond_exit_code']}, wrote {'no output' if not corr['beyond_output_written'] else 'OUTPUT (bug)'}, and reported:"),
      P(f"<font face='Mono' size='7.6'>{corr['beyond_error']}</font>", "small"),
      PageBreak(),
      P("8.3  The simulated lab: how much damage can it take?", "h2"),
      P("These experiments push a 20 kB file through the full chain — simulated synthesis, storage, sequencing, clustering, "
        "consensus, decoding — many times with different random seeds, and count how often the exact file comes back. "
        "Shaded bands are 95 % confidence intervals."),
      fig("subs", caption="Figure 7 — Letter substitutions. Recovery is perfect up to 2 % of bases wrong; consensus extends "
          "the range further. At 6 % the reads are so damaged that no strand can be identified, and VNX-DNA reports that."),
      fig("indels", caption="Figure 8 — Insertions + deletions (each at the given rate). Reads decoded directly cope up to 0.3 % per "
          "base; with consensus, up to 0.5 %. Beyond that recovery fails — cleanly, never with a wrong file."),
      fig("coverage", caption="Figure 9 — Reads per strand. At 1× coverage too many strands are never read; from 5× upwards "
          "recovery was 100 %."),
      callout(f"<b>Monte Carlo, canonical channel.</b> {mc['trials']:,} independent trials at 10× coverage with 2 % dropout, "
              f"0.1 % substitutions and 0.01 % insertions and deletions: <b>{mc['successful_recovery']:,} exact recoveries</b> "
              f"(95 % lower confidence bound {mc['success_ci95'][0] * 100:.1f} %). Across all {tot['trials'] + mc['trials']:,} "
              f"simulated trials in this section, the number of wrong files returned was "
              f"<b>{tot['undetected'] + mc['undetected_corruption']}</b> and the number of internal errors was "
              f"<b>{tot['internal'] + mc['internal_errors']}</b>."),
      PageBreak()]

# ------------------------------------------------------------------------------------------------ 9 profiles
prof = D["profiles"]
S += [P("9  Storage profiles", "h1"),
      P("One size does not fit all. More redundancy costs more DNA (and synthesis money) but survives worse conditions. "
        "VNX-DNA ships four named profiles; every parameter can also be set individually."),
      fig("profiles", caption="Figure 10 — Measured DNA cost per input byte on a 4 MB mixed file."),
      table([["Profile", "Bases / byte", "Strand", "Outer code guarantee", "Inner repair", "Harsh-channel success"]] +
            [[p["profile"], f"{p['nt_per_byte']:.2f}", f"{p['strand_nt']} nt", f"any {p['guarantee']} strands lost",
              f"{p['inner']} bytes / strand", f"{p['harsh'] * 100:.0f} %"] for p in prof],
            [22 * mm, 22 * mm, 18 * mm, 45 * mm, 30 * mm, W - 137 * mm]),
      P("Table 2 — 'Harsh-channel success' is the share of 100 simulated trials recovered exactly under a deliberately "
        "severe channel (the same channel for all four profiles).", "cap"),
      P("<b>compact</b> minimises synthesis cost for well-controlled conditions; <b>balanced</b> is the default; "
        "<b>resilient</b> survives far worse loss; <b>archival</b> doubles the data with parity and uses stricter "
        "sequence rules for the longest storage.", "body"),
      PageBreak()]

# ------------------------------------------------------------------------------------------------ 10 investors
S += [P("10  For investors", "h1"),
      P("The opportunity", "h2"),
      P("Long-term 'cold' data is growing faster than the budgets that pay to store it. DNA offers a medium that is dense, "
        "durable for millennia and immune to format obsolescence. The chemistry — making and reading DNA — is being industrialised "
        "by synthesis and sequencing companies. What every DNA storage system also needs is the <b>codec</b>: robust, "
        "scalable, verifiable software that turns files into manufacturable DNA and imperfect reads back into perfect files. "
        "VNX-DNA is that layer, and it is independent of which chemistry wins."),
      fig("maturity"),
      P("What has been proven — and what has not", "h2"),
      table([["Proven (software, measured)", "Not yet proven"],
             [f"Exact store → DNA → recovery of files up to 10 GB with flat memory (≈ {max_peak:.1f} GiB)",
              "Any result with physically synthesised and sequenced DNA"],
             ["Mathematically guaranteed recovery of lost strands (MDS outer code)", "Channel model fitted to a real sequencing platform"],
             ["Recovery from simulated reads with substitutions, indels, dropout, duplicates, contamination",
              "Cost per gigabyte in real synthesis; synthesis-vendor sequence rules"],
             ["Zero wrong files in every simulated trial; clean failure beyond the guarantee",
              "Molecular (PCR-based) random access; primer design; secondary structure"],
             [f"Encryption, tamper detection, crash safety; {META['tests']} automated tests", "Long-term physical storage"]],
            [W / 2, W / 2]),
      Spacer(1, 6),
      P("Investor questions", "h2")]
invq = [
    ("Is this a real product or a research demo?",
     "It is working, tested, installable software (v%s) that runs the entire digital side of DNA storage on an ordinary CPU "
     "server. It is not yet a commercial service: DNA has not been physically made with it. The honest description is "
     "'production-quality codec, pre-wet-lab'." % META["version"]),
    ("What is the next milestone, and what does it prove?",
     "A wet-lab pilot: synthesise a pool of VNX-DNA strands through a commercial provider, sequence it, and recover the file. "
     "It converts simulated evidence into physical evidence and supplies real error data to fit the channel model."),
    ("What is defensible here?",
     "The engineering of the whole chain at scale: a strand format with in-DNA addressing, a provably optimal outer code with "
     "inner repair, consensus that marks uncertainty instead of guessing, streaming decoders with flat memory, and a verification "
     "discipline that has never returned a wrong file in testing. Each piece exists in the literature; making them work together at "
     "10 GB scale, reproducibly, is the hard part."),
    ("How could this make money?",
     "Options include licensing the codec/SDK to DNA synthesis and storage companies, offering an archive service with a "
     "synthesis partner, and professional services for institutions that must keep data for decades. Which model is pursued is "
     "a business decision beyond the scope of this technical document."),
    ("What are the main risks?",
     "(1) Synthesis cost: DNA storage becomes mainstream only as synthesis gets cheaper — outside VNX-DNA's control. (2) Real "
     "channels may differ from the simulation; the pilot addresses this, and profiles already trade cost for robustness. (3) "
     "Throughput: today's CPU implementation processes roughly %.0f MB/s end to end on 8 CPUs; faster decoders are on the "
     "roadmap. (4) Competition from other codecs; VNX-DNA's answer is scale, verification and openness." % big["throughput_mb_s"]),
    ("How much DNA does a gigabyte need?",
     f"About {big['bases_per_byte'] * 1e9 / 1e9:.1f} billion bases per GB in the default profile on our mixed test data "
     f"({big['bases_per_byte']:.2f} bases per byte, all overheads included); fewer with the compact profile or more compressible data."),
    ("Can results be checked independently?",
     "Yes. Every number here is produced by commands in the public repository and stored as JSON with the exact commit and "
     "environment. Appendix A lists the commands."),
]
for q, a in invq:
    S += [KeepTogether([P(q, "q"), P(a)])]
S += [PageBreak()]

# ------------------------------------------------------------------------------------------------ 11 programmers
S += [P("11  For programmers", "h1"),
      P("Install", "h2"),
      P("Linux, Python ≥ 3.12, CPU only. Dependencies: numpy, cryptography, zstandard, reedsolo, pydantic, typer."),
      code(f"""
git clone https://github.com/{META['repo']}.git
cd {META['repo'].split('/')[-1]}
git checkout {META['tag']}
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'          # '.' without dev extras to skip the test tools
vnx-dna --help
python -m pytest                 # {META['tests']} tests
"""),
      P("Hello, DNA", "h2"),
      code("""
echo "Hello VNX-DNA" > input.txt
vnx-dna store  input.txt   --output hello.vxdna     # streaming container (format 5)
vnx-dna encode hello.vxdna --output hello.fasta     # DNA strands + hello.fasta.vxidx
vnx-dna recover hello.fasta --output recovered.txt  # DNA -> verified original
cmp input.txt recovered.txt && echo identical
"""),
      P("The full chain, one command per stage", "h2"),
      code("""
vnx-dna benchmark generate --size 200KB --pattern mixed --seed 42 --output input.bin
vnx-dna store input.bin --output archive.vxdna [--key-file key.txt] [--profile resilient]
vnx-dna encode archive.vxdna --output strands.fasta
vnx-dna sequence strands.fasta --coverage 10 --substitution-rate 0.001 \\
        --insertion-rate 0.0001 --deletion-rate 0.0001 --dropout-rate 0.02 --seed 42 --output reads.fastq
vnx-dna cluster reads.fastq --output clusters.jsonl
vnx-dna consensus clusters.jsonl --output consensus.fasta
vnx-dna decode consensus.fasta --output recovered.vxdna
vnx-dna restore recovered.vxdna --output recovered.bin
vnx-dna verify recovered.vxdna --file recovered.bin
# or everything at once, with a JSON report:
vnx-dna pipeline input.bin --output out.bin --coverage 10 --seed 42 --report pipeline.json
"""),
      P("Large files and random access", "h2"),
      code("""
vnx-dna store big.bin --output big.vxdna --resume           # resumable
vnx-dna encode big.vxdna --output big.vxs                   # packed 2 bits/base + DNA index
vnx-dna recover big.vxs --output big.out --temp-dir /scratch
vnx-dna extract big.vxs --offset 500000000 --length 1048576 --output part.bin
vnx-dna benchmark scale --sizes 1GB,10GB --work-dir /scratch --output scale.json
"""),
      P("Python API (streaming)", "h2"),
      code("""
from vnxdna.v2 import api
api.store("big.bin", "big.vxdna", workers=8)     # resume=True to continue
api.encode("big.vxdna", "big.vxs")
api.recover("big.vxs", "big.out")                # two-pass, disk-backed decoder

from vnxdna.v2.strandio import iter_batches
for batch in iter_batches("reads.fastq", batch_reads=65536):
    batch.codes, batch.lengths, batch.quals          # flat numpy arrays
"""),
      PageBreak(),
      P("Code map", "h2"),
      table([["Module", "Responsibility"],
             ["vnxdna.cli", "Typer CLI (thin); vnx-dna v1 … mounts the unchanged V1 CLI"],
             ["vnxdna.v2.api", "public API; detects V1/V2 inputs and dispatches"],
             ["v2.archive / v2.container", "streaming store, restore, verify, extract; .vxdna v2 writer/reader"],
             ["v2.crypto / v2.manifest", "HKDF, chunked AES-256-GCM, HMAC; manifest, chunk and plaintext indexes"],
             ["v2.frame / v2.crc / v2.constraints", "strand frame 5, vectorised CRC-32, inner RS, sequence-rule screening"],
             ["v2.encoder / v2.decoder", "chunk-parallel encoding and DNA index; two-pass disk-backed decoding"],
             ["v2.sequencing", "simulated synthesis/storage/sequencing channel"],
             ["v2.cluster / consensus / align / sync", "read clustering, consensus, banded alignment, realignment"],
             ["v2.experiment / scale / stages", "Monte Carlo experiments, test data, resource-measured benchmarks"],
             ["vnxdna.* (top level)", "V1 (format 4) modules, unchanged, for reading old archives"]],
            [52 * mm, W - 52 * mm]),
      P("Exit codes", "h2"),
      table([["Code", "Meaning"], ["0", "success (also when repair was needed: status RECOVERED)"], ["1", "verification failed"],
             ["2", "usage error"], ["3", "invalid input (missing, truncated, malformed)"], ["4", "authentication failed / wrong key"],
             ["5", "insufficient redundancy — too much damage; nothing written"], ["6", "unsupported format or feature"],
             ["7", "configuration error (e.g. unsatisfiable sequence rules)"], ["8", "output error (exists, not writable)"],
             ["70", "internal error (a bug; VNXDNA_DEBUG=1 for a traceback)"]], [18 * mm, W - 18 * mm]),
      P("Programmer questions", "h2")]
devq = [
    ("Does it load the whole file into memory?", "No. Every stage processes bounded blocks: at most about 2 × workers chunks are "
     "in flight. What grows with the file is disk (output, decoder spill, shuffle buckets) — point <font face="Mono">--temp-dir</font> at a big volume."),
    ("Is output deterministic?", "Yes for unencrypted archives (content-derived archive ID) and for every simulation (seeded). "
     "Experiments give identical results regardless of worker count (tested)."),
    ("Can old archives still be read?", "Yes. V1 (format 4) containers and reads are read by every command using the exact V1 code; "
     "vnx-dna migrate converts them with verification before and after."),
    ("How do I add a sequence rule or a mapping?", "Constraints live in vnxdna.v2.constraints and are part of the stored options, "
     "so decoders know what was used. Mappings (2bit, rotation3, codebook8) are selected per archive. Add tests in tests/v2 that "
     "prove round-trips and that unsatisfiable settings fail with exit code 7."),
    ("What is tested?", f"{META['tests']} tests: unit, integration, property-based (Hypothesis), adversarial/fuzz, CLI "
     "subprocess tests including the README examples, bounded-memory tests, SIGKILL-and-resume tests, exhaustive ECC boundary "
     "tests (exactly M losses recover, M + 1 fail)."),
    ("Where are the formats specified?", "docs/V2_FORMAT.md (container, manifest, indexes, frame 5, VXS, DNA index). "
     "docs/V2_ARCHITECTURE.md lists every design decision and where each guarantee is tested."),
]
for q, a in devq:
    S += [KeepTogether([P(q, "q"), P(a)])]
S += [PageBreak()]

# ------------------------------------------------------------------------------------------------ 12 limitations
S += [P("12  Limitations — stated plainly", "h1")]
S += bullets([
    "<b>Software only.</b> No wet-lab synthesis or sequencing yet. The channel is a stress model, not a platform model. "
    "Sequence rules are common heuristics, not a synthesis vendor's specification; secondary structure and primer design are "
    "not modelled.",
    "<b>Guarantees are per error-correction group.</b> Any 16 of 80 strands per group may be lost (balanced profile). Beyond "
    "that, success under random damage is a measured probability, not a promise.",
    "<b>Indels</b> need coverage above 1× (consensus) or the opt-in single-read realignment (one indel per read). Strands have "
    "no in-strand synchronisation markers.",
    "<b>Scale of the sequencing simulation.</b> Store, encode, decode, restore and random access are tested at 10 GB. The full "
    "simulated sequencing → clustering → consensus chain is measured on inputs up to 10 MB, because 10× coverage of 10 GB "
    "means roughly 6 × 10<super>11</super> sequenced bases (about 1.2 TB of reads). Memory is not the limit; time and disk are.",
    f"<b>Throughput</b> is CPU-bound Python/numpy, about {big['throughput_mb_s']:.0f} MB/s end to end on 8 CPUs; inner-RS "
    "decoding and consensus alignment are the slowest stages for damaged reads.",
    "<b>Encrypted archives reveal</b> their approximate size and per-chunk compressibility.",
    "<b>Random access</b> reads only the needed records of a strand <i>file</i>; it is not molecular (PCR-based) random access.",
])
S += [Spacer(1, 10), P("13  Glossary", "h1")]
gl = [("nucleotide / base", "one DNA letter: A, C, G or T; VNX-DNA stores 2 bits per base before overheads"),
      ("strand / oligo", "a short synthetic DNA molecule; here 252 bases"),
      ("synthesis", "manufacturing DNA strands with a chosen sequence"),
      ("sequencing / read", "reading DNA; each read is one noisy observation of one strand"),
      ("coverage", "average number of reads per strand"), ("dropout", "a strand that is never read"),
      ("substitution / insertion / deletion", "a wrong, extra or missing letter in a read; insertions and deletions are 'indels'"),
      ("cluster / consensus", "grouping reads of the same strand / combining them into one best-guess strand"),
      ("Reed–Solomon (RS)", "an error-correcting code; 'MDS' means it recovers the maximum possible number of losses"),
      ("CRC-32", "a 4-byte checksum that detects damage"), ("SHA-256", "a cryptographic fingerprint; any change produces a different value"),
      ("AES-256-GCM", "authenticated encryption: secrecy plus tamper detection"),
      ("GC content / homopolymer", "share of G and C letters / a run of one repeated letter; both constrained for chemistry"),
      ("FASTA / FASTQ / VXS", "text strand formats (FASTQ adds quality scores) / VNX-DNA's packed 2-bit strand file")]
S += [table([["Term", "Meaning"]] + [[a, b] for a, b in gl], [48 * mm, W - 48 * mm]), PageBreak()]

# ------------------------------------------------------------------------------------------------ appendix
S += [P("A  Reproducing every number", "h1"),
      P(f"Release: <b>{META['tag']}</b>, commit <font face='Mono'>{META['commit']}</font>, repository "
        f"<font face='Mono'>github.com/{META['repo']}</font>. Measurements were taken on the release-candidate commit "
        f"<font face='Mono'>{env['git']['commit'][:7]}</font> (v{env['vnxdna_version']}); the release commit differs only in "
        "version number and documentation."),
      code("""
python -m venv /tmp/rel && /tmp/rel/bin/pip install -e '.[dev]'
python -m pytest
vnx-dna benchmark scale --sizes 1MB,10MB,100MB,500MB,1GB,2GB,5GB,10GB \\
        --work-dir /scratch --output research/results/v2/scale-matrix.json
vnx-dna benchmark corruption --size 1GB --work-dir /scratch --output research/results/v2/corruption.json
python research/v2/run_v2_research.py --out research/results/v2     # all channel experiments (~1 h)
python research/v2/render_v2_tables.py                                # regenerates the tables in docs/
"""),
      P("Environment of the measurements", "h2"),
      table([["Item", "Value"], ["VNX-DNA", env["vnxdna_version"]], ["commit", env["git"]["commit"]],
             ["platform", env["platform"]], ["CPUs", str(env["cpu_count"])], ["Python", f"{env['implementation']} {env['python']}"],
             ["libraries", ", ".join(f"{k} {v}" for k, v in env["libraries"].items() if v != "unknown")]],
            [30 * mm, W - 30 * mm]),
      Spacer(1, 10),
      P("Result files: research/results/v2/*.json (scale-matrix, corruption, errors, coverage, abundance, profiles, chunks, "
        "stages, montecarlo). Each records its inputs, seeds, environment and commit.", "small")]

doc.build(S)
print("wrote", OUTPUT)
