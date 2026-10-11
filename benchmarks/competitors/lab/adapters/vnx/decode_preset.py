"""Decode with a named VNX-DNA decoder preset (used by decode.sh when its 5th argument is not "default").

    python decode_preset.py READS_FASTA EXTRACT_DIR CONFIG_JSON PRESET

PRESET names an entry of ``experiments/v9/consensus/candidates.py`` (``E``, ``F``, ...): the decoder runs with
``read_clustering="fallback"`` and that cluster configuration, exactly as the held-out evaluations of
``experiments/arch/outer_list_eval`` did (one worker here). Everything else is the CLI path (``vnxdna.sdk.decode`` with
the profile's configuration file). Exit status: 0 = SUCCESS (verified container, file extracted), 9 = PARTIAL,
5 = FAILURE; on any non-zero status nothing is extracted.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]


def preset(name: str) -> dict:
    spec = importlib.util.spec_from_file_location("vnx_candidates", REPO / "experiments/v9/consensus/candidates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.cluster_config(name)


def main(argv: list[str]) -> int:
    reads, extract, cfg, name = argv
    from vnxdna import sdk
    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.sdk.config import decode_options_for
    opts = decode_options_for(Path(cfg), None, workers=1, read_clustering="fallback")
    opts.cluster_config = ClusterConfig(**preset(name)).validate()
    opts.validate()
    res = sdk.decode(Path(reads), None, options=opts, extract_to=Path(extract))
    import json
    rep = res.body or {}
    cl = rep.get("clustering") or {}
    print(json.dumps({"status": res.status, "preset": name,
                      "clustering": {k: cl.get(k) for k in ("status", "trigger", "fill", "outer_list")}}, default=str))
    return 0 if res.status == "SUCCESS" else 9 if res.status == "PARTIAL" else 5


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
