#!/usr/bin/env python3
"""Check that relative links in README.md and docs/**/*.md resolve to files or directories in the repository.

Checked: inline links and images ``[text](target)`` / ``![alt](target)`` and reference definitions ``[id]: target``.
Not checked: external URLs (any ``scheme:``), pure in-page anchors (``#section``), and anything inside fenced code
blocks or inline code spans. A fragment or query (``file.md#part``) is stripped before the path is resolved; a target
starting with ``/`` is resolved from the repository root, any other relative to the Markdown file's directory.

usage: python tools/check_doc_links.py [--root DIR] [FILE.md ...]     exit 0 = all links resolve, 1 = broken links
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
# [text](target "title") — the text may contain one level of nested brackets (e.g. an image inside a link)
_INLINE = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>]*>|[^\s()]+(?:\([^\s()]*\)[^\s()]*)*)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
_REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def default_files(root: Path) -> list[Path]:
    files = [root / "README.md"] if (root / "README.md").is_file() else []
    return files + sorted((root / "docs").rglob("*.md"))


def iter_links(text: str):
    """Yield (line_number, target) for every link outside code blocks and code spans."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        line = _INLINE_CODE.sub("", line)
        for m in _INLINE.finditer(line):
            yield number, m.group(1)
        m = _REFDEF.match(line)
        if m:
            yield number, m.group(1)


def broken_links(path: Path, root: Path) -> list[tuple[int, str]]:
    out = []
    for number, target in iter_links(path.read_text(encoding="utf-8")):
        target = target.strip("<>").strip()
        if not target or target.startswith("#") or _SCHEME.match(target):
            continue
        rel = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
        if not rel:
            continue
        resolved = (root / rel.lstrip("/")) if rel.startswith("/") else (path.parent / rel)
        if not resolved.exists():
            out.append((number, target))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent, help="repository root")
    ap.add_argument("files", nargs="*", type=Path, help="Markdown files (default: README.md and docs/**/*.md)")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    files = [f.resolve() for f in args.files] or default_files(root)
    total = 0
    for f in files:
        for number, target in broken_links(f, root):
            total += 1
            try:
                shown = f.relative_to(root)
            except ValueError:
                shown = f
            print(f"{shown}:{number}: broken link: {target}")
    print(f"checked {len(files)} Markdown files: {total} broken relative links")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
