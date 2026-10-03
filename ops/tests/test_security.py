import pytest

from vnxops import security


def test_scanner_finds_known_secret_shapes():
    text = "\n".join(["-----BEGIN OPENSSH PRIVATE KEY-----", "AKIA" + "A" * 16, "ghp_" + "a" * 36,
                      "sk-ant-" + "b" * 30, 'password = "hunter2hunter2hunter2"'])
    rules = {f["rule"] for f in security.scan_text(text)}
    assert {"private-key", "aws-access-key", "github-token", "llm-provider-key", "generic-assignment"} <= rules


def test_scanner_never_returns_the_value():
    f = security.scan_text("token = 'abcdefghijklmnopqrstuvwxyz'")[0]
    assert set(f) == {"rule", "source", "line"}


def test_patch_scan_only_checks_added_lines():
    patch = "+++ b/a.py\n-token = 'abcdefghijklmnopqrstuvwxyz'\n+x = 1\n"
    assert security.scan_patch(patch) == []
    assert security.scan_patch("+++ b/a.py\n+token = 'abcdefghijklmnopqrstuvwxyz'\n")[0]["source"] == "a.py"


def test_clean_text_has_no_findings():
    assert security.scan_text("def gc_content(seq):\n    return 0.5\n") == []


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "a/../../x"])
def test_safe_join_blocks_traversal(tmp_path, bad):
    with pytest.raises(ValueError):
        security.safe_join(tmp_path, bad)


def test_safe_join_blocks_symlink_escape(tmp_path):
    (tmp_path / "link").symlink_to("/etc")
    with pytest.raises(ValueError):
        security.safe_join(tmp_path, "link/passwd")


def test_safe_join_allows_inside(tmp_path):
    assert security.safe_join(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()
