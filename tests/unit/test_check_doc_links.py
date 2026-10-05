"""tools/check_doc_links.py: relative links in README.md and docs/**/*.md must resolve (CI job `docs`)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_doc_links", ROOT / "tools" / "check_doc_links.py")
assert _spec is not None and _spec.loader is not None
cdl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdl)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "A.md").write_text("# A\n")
    (tmp_path / "docs" / "sub" / "B.md").write_text("# B\n")
    (tmp_path / "src").mkdir()
    return tmp_path


def test_valid_relative_links_pass(tmp_path):
    root = _tree(tmp_path)
    (root / "README.md").write_text(
        "[a](docs/A.md) [b](docs/sub/B.md#part) [dir](src/) [root](/docs/A.md) ![img](docs/A.md \"title\")\n"
        "[ref]: docs/sub/B.md\n[angle](<docs/A.md>) [query](docs/A.md?x=1) [esc](docs/%41.md)\n")
    (root / "docs" / "sub" / "C.md").write_text("[up](../A.md) [here](B.md) [self](#c)\n")
    assert cdl.main(["--root", str(root)]) == 0


def test_broken_links_are_reported_with_file_and_line(tmp_path, capsys):
    root = _tree(tmp_path)
    (root / "README.md").write_text("ok [a](docs/A.md)\n\nbad [x](docs/missing.md#frag)\n[r]: nowhere/file.txt\n")
    assert cdl.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "README.md:3: broken link: docs/missing.md#frag" in out
    assert "README.md:4: broken link: nowhere/file.txt" in out
    assert "2 broken relative links" in out


def test_links_in_docs_subdirectories_resolve_from_their_own_directory(tmp_path, capsys):
    root = _tree(tmp_path)
    (root / "docs" / "sub" / "C.md").write_text("[wrong](docs/A.md)\n")    # relative to docs/sub/, so broken
    assert cdl.main(["--root", str(root)]) == 1
    assert "docs/sub/C.md:1: broken link: docs/A.md" in capsys.readouterr().out


def test_external_links_anchors_and_code_are_ignored(tmp_path):
    root = _tree(tmp_path)
    (root / "README.md").write_text(
        "[w](https://example.org/x.md) [m](mailto:a@b.c) [anchor](#top)\n"
        "inline `[c](missing-in-code.md)` span\n"
        "```\n[f](missing-in-fence.md)\n```\n"
        "~~~~python\n[g](missing-in-tilde.md)\n```\nstill code\n~~~~\n")
    assert cdl.main(["--root", str(root)]) == 0


def test_the_repository_docs_have_no_broken_relative_links(capsys):
    assert cdl.main([]) == 0, capsys.readouterr().out
