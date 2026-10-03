"""Render docs/V4_RESULTS.md from experiments/*/results.json (no number in that file is typed by hand).

    python research/v4/render_v4_results.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments"
OUT = ROOT / "docs" / "V4_RESULTS.md"


def load(name: str):
    matches = sorted(EXP.glob(name + "*"))
    for m in matches:
        p = m / "results.json"
        if p.exists():
            return json.loads(p.read_text()), m.name
    return None, name


def fmt(v):
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def params(p: dict) -> str:
    return ", ".join(f"{k.replace('_rate', '')}={fmt(v)}" for k, v in p.items() if k not in ("coverage_model",))


def curve(res: dict, title: str) -> list[str]:
    out = [f"**{title}** — {res.get('nt_per_input_byte')} nt per input byte, {res.get('strands')} strands of "
           f"{res.get('strand_nt')} nt, {res.get('trials_per_point')} trials per point", "",
           "| channel | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | median decode s |",
           "|---|---|---|---|---|---|---|---|"]
    for p in res["points"]:
        o = p["outcomes"]
        out.append(f"| {params(p['parameters'])} | {o['SUCCESS']} | {o['PARTIAL']} | {o['DECODER_FAILURE']} | {o['INTEGRITY_FAILURE']} | "
                   f"{o['ADDRESS_FAILURE'] + o['ERROR']} | {p['success_rate']} | {p['decode_seconds_median']} |")
    return out + [""]


def section(lines: list[str], exp: str, heading: str, body) -> None:
    res, name = load(exp)
    lines.append(f"## {heading}")
    lines.append("")
    if res is None:
        lines += [f"_{name}: no results recorded._", ""]
        return
    meta = res.get("experiment", {})
    lines.append(f"Source: [`experiments/{name}/results.json`](../experiments/{name}/results.json) · commit `{meta.get('git_commit')}` · "
                 f"{meta.get('timestamp_utc')}")
    lines.append("")
    body(res, lines)


def sweep_body(title):
    def b(res, lines):
        if "variants" in res:
            for v in res["variants"]:
                lines.extend(curve(v, v["variant"]))
        else:
            lines.extend(curve(res, title))
    return b


def codec_body(res, lines):
    lines += [f"Same data ({res['total_data_symbols']} symbols × {res['symbol_bytes']} B), same i.i.d. loss, same seeds, "
              f"{res['budget']:.0%} parity/data for every scheme, {res['trials']} trials per point.", "",
              "| scheme | " + " | ".join(f"loss {p}" for p in res["loss_rates"]) + " | encode MB/s |",
              "|---|" + "---|" * (len(res["loss_rates"]) + 1)]
    for name, s in res["schemes"].items():
        lines.append(f"| {name} | " + " | ".join(fmt(s['success_rate'][str(p)]) for p in res["loss_rates"]) + f" | {s['encode_mb_s']} |")
    lines.append("")


def constraints_body(res, lines):
    lines += [f"{res['strands']} random {res['profile']} frames ({res['strand_nt']} nt). Before = {res.get('before_definition', 'variant 0')}.",
              "", "| rule set | status | violating before | by rule (before) | violating after | mean variant | max variant | strands/s | decodes |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in res["results"]:
        lines.append(f"| {r['name']} | {r['status']} | {r.get('violating_before')} ({fmt(r.get('violating_before_fraction', ''))}) | "
                     f"{r.get('violations_before_by_rule')} | {r.get('violating_after', '—')} | {r.get('variants_mean', '—')} | "
                     f"{r.get('variants_max', '—')} | {r.get('strands_per_second', '—')} | {r.get('recoverable_after_screening', '—')} |")
    lines += ["", res.get("note", ""), ""]


def stages_body(res, lines):
    lines += ["| stage | value |", "|---|---|"]
    for k, v in res.items():
        if k not in ("experiment", "case"):
            lines.append(f"| {k} | {fmt(v)} |")
    lines.append("")


def runs_table(runs, lines, key="workers"):
    lines += [f"| {key} | input MB | status | archive s | encode s | channel s | decode s | encode MB/s | decode MB/s | recoverable MB/s | "
              "reads/s | peak RSS MB (process / largest worker) |", "|---" * 12 + "|"]
    for r in runs:
        if r.get("status") == "ERROR":
            lines.append(f"| {r.get(key, '?')} | ERROR: {r.get('message', '')[:80]} |" + " |" * 10)
            continue
        lines.append(f"| {r.get(key)} | {r['input_size'] / 1e6:.1f} | {r['status']} | {r['archive_seconds']} | {r['encode_seconds']} | "
                     f"{r['channel_seconds']} | {r['decode_seconds']} | {r['encode_mb_s']} | {r['decode_mb_s']} | "
                     f"{r['recoverable_mb_s']} | {r.get('reads_per_second')} | {r.get('peak_rss_self_mb')} / {r.get('peak_rss_workers_mb')} |")
    lines.append("")


def scaling_body(res, lines):
    runs = res["runs"]
    runs_table(runs, lines)
    base = next((r for r in runs if r.get("workers") == 1 and r.get("status") != "ERROR"), None)
    if base:
        lines += ["| workers | encode speed-up | decode speed-up | decode scaling efficiency |", "|---|---|---|---|"]
        for r in runs:
            if r.get("status") == "ERROR":
                continue
            se = base["encode_seconds"] / r["encode_seconds"]
            sd = base["decode_seconds"] / r["decode_seconds"]
            lines.append(f"| {r['workers']} | {se:.2f}× | {sd:.2f}× | {sd / r['workers']:.0%} |")
        lines.append("")


def memory_body(res, lines):
    runs_table(res["runs"], lines, key="input_size")


def v3v4_body(res, lines):
    lines += [f"Input {res['input']}. V3 balanced: {res['v3']['nt_per_input_byte']} nt/byte; V4 {res['v4']['profile']}: "
              f"{res['v4']['nt_per_input_byte']} nt/byte. {res['fairness']}.", "",
              "| channel | trials | V3 default | V3 best (coverage 1: indel + burst repair) | V4 | V3 median s | V4 median s |",
              "|---|---|---|---|---|---|---|"]
    for p in res["points"]:
        lines.append(f"| {params(p['parameters'])} | {p['trials']} | {p.get('v3_default_success_rate')} | "
                     f"{p.get('v3_best_success_rate', 'n/a (coverage > 1 uses cluster + consensus)')} | {p.get('v4_success_rate')} | "
                     f"{p.get('v3_default_seconds_median')} | {p.get('v4_seconds_median')} |")
    lines.append("")


def main() -> None:
    lines = ["# VNX-DNA V4 measured results", "",
             "_Generated by `research/v4/render_v4_results.py` from `experiments/*/results.json`. Do not edit by hand._", "",
             "Every result below is **SIMULATED**: software-generated strands through the configurable V4 channel "
             "([CHANNEL_MODEL.md](CHANNEL_MODEL.md)); nothing was synthesised or sequenced. Unless stated, inputs are 256 KiB of "
             "seeded random bytes, the profile is v4-balanced (296 nt, 2-nt markers every 32 nt, RS r = 16, Cauchy RS 64+16), "
             "coverage 'fixed' means exactly that many reads per surviving strand.", ""]
    section(lines, "EXP-0001", "Substitutions (EXP-0001)", sweep_body("substitution sweep"))
    section(lines, "EXP-0002", "Insertions (EXP-0002)", sweep_body("insertion sweep"))
    section(lines, "EXP-0003", "Deletions (EXP-0003)", sweep_body("deletion sweep"))
    section(lines, "EXP-0004", "Strand dropout (EXP-0004)", sweep_body("dropout sweep"))
    section(lines, "EXP-0005", "Coverage and consensus under heavy errors (EXP-0005)", sweep_body("coverage sweep (64 KiB input)"))
    section(lines, "EXP-0006", "Mixed channel (EXP-0006)", sweep_body("mixed channel"))
    section(lines, "EXP-0007", "Outer codes at equal redundancy, erasure-only (EXP-0007)", codec_body)
    section(lines, "EXP-0016", "Outer codes in the full DNA pipeline under dropout (EXP-0016)", sweep_body("outer codes"))
    section(lines, "EXP-0008", "Indels at coverage 1: markers vs no markers (EXP-0008)", sweep_body("indel profiles"))
    section(lines, "EXP-0009", "Marker design study (EXP-0009)", sweep_body("marker design"))
    section(lines, "EXP-0013", "Channel models: homopolymer indels, GC bias, uneven coverage (EXP-0013)", sweep_body("channel models"))
    section(lines, "EXP-0010", "V3 vs V4 under the identical channel (EXP-0010)", v3v4_body)
    section(lines, "EXP-0014", "Constraint screening (EXP-0014)", constraints_body)
    section(lines, "EXP-0015", "Stage throughput, single core (EXP-0015)", stages_body)
    section(lines, "EXP-0011", "Worker scaling (EXP-0011)", scaling_body)
    section(lines, "EXP-0012", "Memory and large inputs (EXP-0012)", memory_body)
    for extra in sorted(EXP.glob("EXP-00[1-9][7-9]*")) + sorted(EXP.glob("EXP-002*")):
        section(lines, extra.name, f"{extra.name}", sweep_body(extra.name))
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
