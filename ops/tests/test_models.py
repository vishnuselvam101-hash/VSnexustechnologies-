from vnxops import models

GiB = 2**30
REG = models.registry()
BENCH = {"qwen2.5-coder:1.5b": {"ram_rss_bytes": int(1.1 * GiB), "coding_benchmark": {"passed": 2}},
         "huihui_ai/qwen3-coder-abliterated:latest": {"ram_rss_bytes": int(17.7 * GiB), "coding_benchmark": {"passed": 5}},
         "agent-cli": {"available": True}}
ALL = list(BENCH)


def sel(task, avail_gib, installed=ALL, remote=True, bench=BENCH):
    return models.select(task, snapshot={"mem_available": int(avail_gib * GiB)}, reg=REG, bench=bench, min_free=6 * GiB,
                         installed_models=installed, loaded_models=[], allow_remote=remote)


def test_tier1_for_classification():
    assert sel("classification", 20)["model"] == "qwen2.5-coder:1.5b"


def test_test_generation_uses_large_local_model_when_ram_allows():
    assert sel("test_generation", 26)["model"] == "huihui_ai/qwen3-coder-abliterated:latest"


def test_large_model_escalates_to_remote_when_ram_short():
    r = sel("test_generation", 20)
    assert r["model"] == "agent-cli"
    assert any("above floor" in x["why"] for x in r["rejected"])


def test_no_remote_and_no_ram_gives_no_model():
    assert sel("test_generation", 10, remote=False)["model"] is None


def test_architecture_needs_strong_model():
    assert sel("architecture", 30)["tier"] == 3


def test_unavailable_remote_is_skipped():
    r = sel("architecture", 30, bench={**BENCH, "agent-cli": {"available": False}})
    assert r["model"] is None or r["model"] != "agent-cli"


def test_weak_local_model_rejected_for_tier2_tasks():
    bench = {**BENCH, "huihui_ai/qwen3-coder-abliterated:latest": {"ram_rss_bytes": GiB, "coding_benchmark": {"passed": 1}}}
    assert sel("test_generation", 30, bench=bench)["model"] == "agent-cli"


def test_local_models_never_get_tools():
    assert sel("classification", 20)["tools_allowed"] is False


def test_extract_code_block():
    assert models._extract_code("x\n```python\ndef f():\n    return 1\n```\ny") == "def f():\n    return 1"


def test_agent_models_rejected_when_no_agent_cli_is_configured(monkeypatch):
    monkeypatch.delenv("VNXDNA_AGENT_CLI", raising=False)
    from vnxops import system
    monkeypatch.setattr(system, "load", lambda: {"agent": {"cli": None}})
    reg = models.registry()
    assert all(not c.get("configured", True) for c in reg["models"].values() if c["backend"] == "agent-cli")
    r = models.select("architecture", snapshot={"mem_available": 30 * GiB}, reg=reg, bench={}, min_free=6 * GiB,
                      installed_models=ALL, loaded_models=[])
    assert r["model"] is None and any("not configured" in x["why"] for x in r["rejected"])
