#!/usr/bin/env python3
"""Scan the committed VNX-DNA tree for evidence and hygiene problems.

The scan REPORTS; it never gates. It exits 0 even when it finds hits, it never
modifies a file, it needs no network and nothing outside the standard library.

Scanned scope: the committed files under src/, tests/, docs/, experiments/ plus
README.md and CHANGELOG.md. The file set comes from `git ls-files` (the
committed tree); if git is unavailable the script walks the same paths on disk.
Binary files are skipped.

Categories
  1  attribution      AI-tool / assistant / model names, attribution trailers and
                       the agent scratch path, in committed content and in
                       commit-message trailers.
  2  claims           words that assert DNA was synthesised, stored or sequenced,
                       that promise a lifetime, or that assert a superlative.
  3  unlabelled       a number presented as a measurement with no SIMULATED or
     measurements     MEASURED label near it.
  4  credentials      a private-key block, a long hex literal next to a
                      secret-ish word, or an nvapi-/sk- prefixed key. The value
                      is never printed: only the file, the line and the kind.
  5  scratch paths    a temporary or home directory path in a committed file.

Two things are never printed, so that the output can be committed as-is:
an AI-tool name is replaced by [AI-TOOL-NAME], an attribution trailer by
[TRAILER-TEXT], the agent scratch path by [AI-SCRATCH-PATH] and a scratch-path
root by [TMP] or [HOME]; a credential value is replaced by [REDACTED] or
omitted altogether. `python tools/v15-evidence-scan.py --list-terms` prints the
search table so a reviewer can see exactly what is looked for.

usage: python tools/v15-evidence-scan.py [--root DIR] [--include-report]
                                          [--markdown] [--list-terms] [--quiet]
       exit status is always 0
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# The audit report quotes the hits it lists, so scanning it would report the
# report's own quotations. It is skipped by default and the skip is printed.
REPORT_REL = "docs/V15_EVIDENCE_AUDIT.md"

SCOPE_DIRS = ("src/", "tests/", "docs/", "experiments/")
SCOPE_FILES = ("README.md", "CHANGELOG.md")

# A tool name is never printed: a hit is reported with this placeholder so the
# scan's own output can be committed verbatim without carrying a tool name.
TOOL_MASK = "[AI-TOOL-NAME]"
SCRATCH_MASK = "[AI-SCRATCH-PATH]"
TRAILER_MASK = "[TRAILER-TEXT]"

# The search table is stored base64-encoded. The committed source of a hygiene
# scanner must not itself carry an AI-tool name, an attribution trailer or a
# scratch path; this keeps those strings out of the repository while
# `--list-terms` still lets a reviewer read the exact table.
_TERMS_B64 = (
    "eyJhaV9zY3JhdGNoX3BhdGgiOiIvcm9vdC8uY2xhdWRlIiwic2NyYXRjaF9wcmVmaXhl"
    "cyI6WyIvdG1wLyIsIi9ob21lLyJdLCJ0b29sX25hbWVzIjpbImNsYXVkZSIsImFudGhy"
    "b3BpYyIsImFpZGVyIiwiaGVybWVzIiwiZHNoIiwiZGVlcHNlZWsiLCJxd2VuIiwiY29w"
    "aWxvdCIsImNoYXRncHQiLCJncHQtNCIsImdlbWluaSIsImxsYW1hIiwibWlzdHJhbCJd"
    "LCJ0cmFpbGVyX3BocmFzZXMiOlsiY28tYXV0aG9yZWQtYnkiLCJnZW5lcmF0ZWQgd2l0"
    "aCIsImFzc2lzdGVkIGJ5Iiwid3JpdHRlbiBieSBhbiBhaSJdfQ=="
)


def _terms() -> dict:
    return json.loads(base64.b64decode(_TERMS_B64).decode("utf-8"))


# ---------------------------------------------------------------- category 2
# Synthesis / storage / sequencing claims, lifetime promises, superlatives.
_CLAIM_TERMS = (
    "synthesised", "synthesized", "synthesized-into-DNA", "sequenced",
    "guaranteed", "forever", "permanent storage", "fastest", "best",
    "unrivaled", "unbeatable", "the most", "world-class",
)

# ---------------------------------------------------------------- category 3
# A number that is presented as a measurement: a number followed by a unit.
_MEASURE_UNITS = (
    r"%|x\b|ms\b|ns\b|us\b|µs\b|s\b|sec\b|min\b|hours?\b|bytes?\b|bits?\b|"
    r"bp\b|kbp\b|Mb\b|Gb\b|MB\b|GB\b|TB\b|KiB\b|MiB\b|GiB\b|reads?\b|"
    r"bases?\b|nt\b|ops/s\b|GB/s\b|MB/s\b"
)
_MEASURE_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(" + _MEASURE_UNITS + r")", re.IGNORECASE
)
_LABEL_RE = re.compile(r"\b(simulated|measured)\b", re.IGNORECASE)

# ---------------------------------------------------------------- category 4
_PRIVKEY_RE = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_SECRET_WORD_RE = re.compile(
    r"\b(token|secret|api[_-]?key|password|passwd|credential)\b", re.IGNORECASE
)
_NVAPI_RE = re.compile(r"\bnvapi-[A-Za-z0-9_-]{16,}")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
# How far apart a secret-ish word and a hex literal may be and still count.
_SECRET_NEAR_CHARS = 40

# Any long hex literal or key-shaped token is redacted from a printed excerpt,
# so no excerpt can carry a credential into a committed report.
_REDACT_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b|\bnvapi-[A-Za-z0-9_-]{8,}|\bsk-[A-Za-z0-9_-]{8,}")
_REDACTED = "[REDACTED]"

_EXCERPT_RADIUS = 30
_MAX_EXCERPT = 100

_CATEGORY_TITLES = {
    "1 attribution": "AI-tool names, attribution trailers and the agent scratch path",
    "2 claims": "synthesis / storage / sequencing claims, lifetime promises, superlatives",
    "3 unlabelled measurements": "a number presented as a measurement with no SIMULATED or MEASURED label near it",
    "4 credentials": "credential-looking literals (the value is never printed)",
    "5 scratch paths": "temporary or home directory paths in committed files",
}


def _word_pattern(terms):
    """A regex that matches any of the terms on word boundaries."""
    alt = "|".join(re.escape(t) for t in terms)
    return re.compile(r"(?<![\w-])(?:" + alt + r")(?![\w-])", re.IGNORECASE)


_T = _terms()
_TOOL_RE = _word_pattern(_T["tool_names"])
_PHRASE_RE = _word_pattern(_T["trailer_phrases"])
_SCRATCH_AI_PATH = _T["ai_scratch_path"]
_SCRATCH_PATH_RE = re.compile(
    "(?:" + "|".join(re.escape(p) for p in _T["scratch_prefixes"]) + r")[^\s'\"`),;]*"
)
_CLAIM_RE = _word_pattern(_CLAIM_TERMS)


def _excerpt(line: str, start: int, end: int) -> str:
    """A short, single-line excerpt of `line` centred on [start, end)."""
    left = max(0, start - _EXCERPT_RADIUS)
    right = min(len(line), end + _EXCERPT_RADIUS)
    text = line[left:right]
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _MAX_EXCERPT:
        cut = _MAX_EXCERPT // 2
        text = text[:cut].rstrip() + " ... " + text[-cut:].lstrip()
    if left > 0:
        text = "... " + text
    if right < len(line):
        text = text + " ..."
    return _sanitize(text)


def _scratch_label(prefix: str) -> str:
    """The mask used for a scratch path root, e.g. "[TMP]"."""
    return "[" + prefix.strip("/").upper() + "]"


def _mask_scratch_paths(text: str) -> str:
    """Replace the leading directory of a scratch path with a label.

    The report is itself a committed file, so it must not add scratch-path
    literals to the repository. The distinctive tail of the path is kept, and
    the un-masked path is in the source file at the quoted line.
    """
    for prefix in _T["scratch_prefixes"]:
        text = text.replace(prefix, _scratch_label(prefix) + prefix[-1])
    return text


def _sanitize(text: str) -> str:
    """Strip from `text` anything a committed report may not carry.

    AI-tool names become [AI-TOOL-NAME], attribution trailers become
    [TRAILER-TEXT], the agent scratch path becomes [AI-SCRATCH-PATH], a
    scratch-path root becomes [TMP] or [HOME], and any key-shaped or long hex
    literal becomes [REDACTED]. Offsets are not needed afterwards, so masking
    is applied to the finished excerpt rather than to the source line.
    """
    text = _TOOL_RE.sub(TOOL_MASK, text)
    text = _PHRASE_RE.sub(TRAILER_MASK, text)
    text = text.replace(_SCRATCH_AI_PATH, SCRATCH_MASK)
    text = _mask_scratch_paths(text)
    return _REDACT_RE.sub(_REDACTED, text)


def _list_committed_files(root: Path):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    files = [f for f in out.splitlines() if f]
    return [f for f in files if f.startswith(SCOPE_DIRS) or f in SCOPE_FILES]


def _walk_files(root: Path) -> list:
    found = []
    for name in SCOPE_FILES:
        if (root / name).is_file():
            found.append(name)
    for d in SCOPE_DIRS:
        base = root / d.rstrip("/")
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
            for fn in filenames:
                p = Path(dirpath) / fn
                found.append(str(p.relative_to(root)))
    return sorted(found)


def _read_text(path: Path):
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _secret_kind(line: str):
    """The kind of credential-looking thing on this line, or None."""
    if _PRIVKEY_RE.search(line):
        return "private-key-block"
    if _NVAPI_RE.search(line):
        return "nvapi-prefixed-key"
    if _SK_RE.search(line):
        return "sk-prefixed-key"
    for m in _HEX_RE.finditer(line):
        window = line[max(0, m.start() - _SECRET_NEAR_CHARS):m.end() + _SECRET_NEAR_CHARS]
        if _SECRET_WORD_RE.search(window):
            return "hex-literal-near-secret-word"
    return None


# ------------------------------------------------------------- label scope
def _md_blocks(lines):
    """Map each line to (paragraph start, paragraph end, nearest heading)."""
    res = {}
    cur = []
    heading = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*#{1,6}\s", line):
            heading = line
        if not line.strip():
            if cur:
                for k in cur:
                    res[k] = (cur[0], i, heading)
                cur = []
            continue
        cur.append(i)
    if cur:
        for k in cur:
            res[k] = (cur[0], len(lines) - 1, heading)
    return res


def _json_blocks(lines):
    """Map each line to the span of the enclosing top-level JSON value."""
    res = {}
    start = None
    depth = 0
    for i, line in enumerate(lines):
        if start is None and line.lstrip().startswith(("{", "[")):
            start = i
            depth = 0
        if start is not None:
            depth += (line.count("{") + line.count("[")) - (line.count("}") + line.count("]"))
            if depth <= 0:
                for k in range(start, i + 1):
                    res[k] = (start, i, None)
                start = None
    return res


def _label_scope(rel: str, lines):
    """The context in which a SIMULATED / MEASURED label counts as nearby.

    Markdown: the paragraph plus its nearest heading. JSON: the enclosing
    top-level value. JSON Lines: the record (the line itself). Anything else:
    the line and the two lines above it.
    """
    ext = os.path.splitext(rel)[1]
    if ext in (".md", ".rst"):
        return _md_blocks(lines)
    if ext == ".json":
        return _json_blocks(lines)
    if ext == ".jsonl":
        return {i: (i, i, None) for i in range(len(lines))}
    return {}


def _has_label(rel: str, lines, index: int, blocks=None) -> bool:
    if blocks is None:
        blocks = _label_scope(rel, lines)
    if blocks:
        start, end, heading = blocks.get(index, (index, index, None))
        ctx = "\n".join(lines[start:end + 1])
        if heading:
            ctx = heading + "\n" + ctx
        return bool(_LABEL_RE.search(ctx))
    ctx = "\n".join(lines[max(0, index - 2):index + 1])
    return bool(_LABEL_RE.search(ctx))


def scan(root: Path, include_report: bool = False):
    """Return (categories, stats). categories maps name -> list of hit dicts."""
    cats = {
        "1 attribution": [],
        "2 claims": [],
        "3 unlabelled measurements": [],
        "4 credentials": [],
        "5 scratch paths": [],
    }
    stats = {"considered": 0, "scanned": 0, "skipped_binary": 0, "skipped_report": 0}

    files = _list_committed_files(root)
    if files is None:
        files = _walk_files(root)
    stats["considered"] = len(files)

    for rel in files:
        if rel == REPORT_REL and not include_report:
            stats["skipped_report"] += 1
            continue
        path = root / rel
        if not path.is_file():
            continue
        text = _read_text(path)
        if text is None:
            stats["skipped_binary"] += 1
            continue
        stats["scanned"] += 1
        lines = text.splitlines()
        blocks = _label_scope(rel, lines)

        for index, line in enumerate(lines):
            number = index + 1

            # ---- category 1: attribution in committed content
            for m in _TOOL_RE.finditer(line):
                cats["1 attribution"].append({
                    "file": rel, "line": number, "kind": "ai-tool-name",
                    "excerpt": _excerpt(line, m.start(), m.end()),
                })
            for m in _PHRASE_RE.finditer(line):
                cats["1 attribution"].append({
                    "file": rel, "line": number, "kind": "attribution-trailer",
                    "excerpt": _excerpt(line, m.start(), m.end()),
                })
            for m in re.finditer(re.escape(_SCRATCH_AI_PATH), line):
                cats["1 attribution"].append({
                    "file": rel, "line": number, "kind": "ai-scratch-path",
                    "excerpt": _excerpt(line, m.start(), m.end()),
                })

            # ---- category 2: claims and superlatives
            for m in _CLAIM_RE.finditer(line):
                cats["2 claims"].append({
                    "file": rel, "line": number, "kind": "claim-word",
                    "term": m.group(0).lower(), "excerpt": _excerpt(line, m.start(), m.end()),
                })

            # ---- category 3: a number presented as a measurement
            m3 = _MEASURE_RE.search(line)
            if m3 and not _has_label(rel, lines, index, blocks):
                cats["3 unlabelled measurements"].append({
                    "file": rel, "line": number, "kind": "unlabelled-number",
                    "excerpt": _excerpt(line, m3.start(), m3.end()),
                })

            # ---- category 4: credential-looking literals (never print the value)
            kind = _secret_kind(line)
            if kind:
                cats["4 credentials"].append({
                    "file": rel, "line": number, "kind": kind, "excerpt": "",
                })

            # ---- category 5: stray scratch paths
            for m in _SCRATCH_PATH_RE.finditer(line):
                cats["5 scratch paths"].append({
                    "file": rel, "line": number, "kind": "scratch-path",
                    "excerpt": _excerpt(line, m.start(), m.end()),
                })

    cats["1 attribution"].extend(_scan_commit_messages(root))
    return cats, stats


def _scan_commit_messages(root: Path) -> list:
    """Attribution trailers in the commit messages of the committed history."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--format=%H%x00%B%x00%x1e"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    hits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) < 2:
            continue
        sha, body = parts[0].strip(), parts[1]
        for number, line in enumerate(body.splitlines(), 1):
            for m in _TOOL_RE.finditer(line):
                hits.append({
                    "file": "commit " + sha[:12], "line": number, "kind": "ai-tool-name",
                    "excerpt": _excerpt(line, m.start(), m.end()),
                })
            for m in _PHRASE_RE.finditer(line):
                hits.append({
                    "file": "commit " + sha[:12], "line": number, "kind": "attribution-trailer",
                    "excerpt": _excerpt(line, m.start(), m.end()),
                })
    return hits


def _fmt_hit(hit: dict) -> str:
    if hit["kind"] in ("private-key-block", "nvapi-prefixed-key", "sk-prefixed-key",
                       "hex-literal-near-secret-word"):
        return "%s:%d  [%s]" % (hit["file"], hit["line"], hit["kind"])
    return "%s:%d  [%s]  %s" % (hit["file"], hit["line"], hit["kind"], hit["excerpt"])


def _print_list(out, cats, stats, quiet: bool) -> int:
    total = 0
    for name, hits in cats.items():
        total += len(hits)
        out.write("CATEGORY %s - %d hit(s)\n" % (name, len(hits)))
        if not quiet:
            for hit in hits:
                out.write("  " + _fmt_hit(hit) + "\n")
        out.write("\n")
    out.write("TOTAL HITS: %d\n" % total)
    out.write("This scan reports; it does not gate. A hit is not a defect on its own.\n")
    return total


def _print_markdown(out, cats, stats) -> int:
    """The report body: counts and the full list of hits, as markdown."""
    total = 0
    out.write("## Result on this tree\n\n")
    out.write("Files considered: **%d**; scanned as text: **%d**; skipped as binary: **%d**."
              % (stats["considered"], stats["scanned"], stats["skipped_binary"]))
    if stats["skipped_report"]:
        out.write("  The report file itself (`%s`) is skipped by default because it quotes"
                  " the hits it lists; `--include-report` scans it." % REPORT_REL)
    out.write("\n\n")
    out.write("| category | what it looks for | hits |\n")
    out.write("| --- | --- | --- |\n")
    for name, hits in cats.items():
        total += len(hits)
        out.write("| %s | %s | **%d** |\n" % (name, _CATEGORY_TITLES[name], len(hits)))
    out.write("| | **total** | **%d** |\n\n" % total)
    for name, hits in cats.items():
        out.write("### %s - %d hit(s)\n\n" % (name, len(hits)))
        if not hits:
            out.write("No hits.\n\n")
            continue
        by_file = {}
        for hit in hits:
            by_file[hit["file"]] = by_file.get(hit["file"], 0) + 1
        out.write("Hits by file (%d file(s)):\n\n" % len(by_file))
        out.write("| file | hits |\n| --- | --- |\n")
        for fname in sorted(by_file, key=lambda f: (-by_file[f], f)):
            out.write("| `%s` | %d |\n" % (fname, by_file[fname]))
        out.write("\nFull list (`file:line  [kind]  matching text`):\n\n")
        out.write("```text\n")
        for hit in hits:
            out.write(_fmt_hit(hit) + "\n")
        out.write("```\n\n")
    return total


def _print_terms(out) -> None:
    t = _terms()
    out.write("Search table used by this scanner:\n")
    out.write("  tool names        : %s\n" % ", ".join(t["tool_names"]))
    out.write("  trailer phrases   : %s\n" % ", ".join(t["trailer_phrases"]))
    out.write("  agent scratch path: %s\n" % t["ai_scratch_path"])
    out.write("  scratch prefixes  : %s\n" % ", ".join(t["scratch_prefixes"]))
    out.write("  claim words       : %s\n" % ", ".join(_CLAIM_TERMS))
    out.write("  label words       : simulated, measured\n")
    out.write("  credential kinds  : private-key block, long hex literal next to a\n"
              "                      secret-ish word, nvapi- prefixed key, sk- prefixed key\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: .)")
    ap.add_argument("--include-report", action="store_true",
                    help="also scan the audit report itself")
    ap.add_argument("--markdown", action="store_true",
                    help="print the report body (counts and the full hit list) as markdown")
    ap.add_argument("--list-terms", action="store_true",
                    help="print the search table and exit")
    ap.add_argument("--quiet", action="store_true", help="print only the totals")
    args = ap.parse_args(argv)

    out = sys.stdout
    if args.list_terms:
        _print_terms(out)
        return 0

    root = Path(args.root).resolve()
    cats, stats = scan(root, include_report=args.include_report)

    if args.markdown:
        _print_markdown(out, cats, stats)
        return 0

    out.write("VNX-DNA V15 evidence and hygiene scan\n")
    out.write("root: %s\n" % root)
    out.write("scope: %s plus %s (committed files)\n"
              % (", ".join(SCOPE_DIRS), ", ".join(SCOPE_FILES)))
    out.write("files considered: %d   scanned as text: %d   skipped (binary): %d\n"
              % (stats["considered"], stats["scanned"], stats["skipped_binary"]))
    if stats["skipped_report"]:
        out.write("skipped: %s (this file quotes the hits it lists; --include-report to scan it)\n"
                  % REPORT_REL)
    out.write("AI-tool names are printed as %s, attribution trailers as %s,\n"
              % (TOOL_MASK, TRAILER_MASK))
    out.write("the agent scratch path as %s, a scratch-path root as [TMP] or [HOME];\n"
              % SCRATCH_MASK)
    out.write("credential values are never printed.\n")
    out.write("\n")

    _print_list(out, cats, stats, args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
