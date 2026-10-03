# Biological constraint engine

Status: **IMPLEMENTED** (screening rules; not a biochemical model). Code: `src/vnxdna/v4/constraints.py`.
Results: EXP-0014.

## Rules

| rule id | parameter(s) | default | meaning |
|---|---|---|---|
| `GC_CONTENT` | `gc_min_percent`, `gc_max_percent` | 40, 60 | whole-sequence GC fraction within the range |
| `GC_WINDOW` | `gc_window_nt`, `gc_window_min_percent`, `gc_window_max_percent` | 0 (off), 25, 75 | every window of the given length within the range |
| `HOMOPOLYMER` | `max_homopolymer` | 4 | no run of one base longer than this (0 disables) |
| `TANDEM_REPEAT` | `max_tandem_repeat_nt` | 0 (off) | no period-2 or period-3 repeat run longer than this (≥ 6 when enabled) |
| `FORBIDDEN_MOTIF` | `forbidden_motifs`, `check_reverse_complement` | none, true | none of the motifs (or their reverse complements) |
| `LENGTH` | `min_length`, `max_length` | 0, 0 (off) | length within the range |
| `INVALID_BASE` | — | — | only A, C, G, T |

No threshold is presented as universal. Synthesis and sequencing platforms differ, so every value is configuration.
Invalid configurations (e.g. `gc_min_percent > gc_max_percent`, a non-ACGT motif, an unknown key) are rejected with
`VNXConfigurationError` (exit 7).

## Diagnostics

`vnx validate strands.fasta [--constraints c.json] [--gc-min 45 --gc-max 55 --max-homopolymer 3 --forbid GAATTC]`
prints JSON and exits 3 if any sequence violates a rule:

```json
{"valid": false, "sequences": 2, "valid_sequences": 1, "invalid_sequences": 1,
 "violation_counts": {"HOMOPOLYMER": 1}, "gc_percent_range": [40.0, 50.0], "max_homopolymer": 8,
 "constraints": {...}, "failures": [{"name": "b", "valid": false, "length": 10, "gc_percent": 20.0,
                                      "max_homopolymer": 8, "violations": ["GC_CONTENT", "HOMOPOLYMER"], ...}]}
```

The vectorised checker (`violations_batch`) processes equal-length batches. A property test compares the homopolymer
rule with a scalar reference.

## Sequence optimisation (screening)

The encoder never emits a strand that violates the configured rules. For each frame it tries scrambler variants
v = 0, 1, …, 255; each variant XORs the frame with a different SHAKE-128 keystream, changing every base except the
markers. The first variant whose final strand (markers included) satisfies all rules is used. The variant byte travels
in the frame, so decoding needs no side information.

* **Cost in nucleotides:** zero beyond the 1-byte variant field already in the frame.
* **Recoverability:** verified by decoding (the study decodes every screened strand).
* **Failure:** if no variant satisfies the rules, encoding fails with `VNXConstraintError`. Strands are never
  silently emitted unscreened.

Measured in EXP-0014 on 20,000 random v4-balanced frames ([results](../experiments/EXP-0014-constraints/results.json)).
The table is produced from `results.json` in [V4_COMPLETION_REPORT.md](V4_COMPLETION_REPORT.md#constraint-engine).

### Limits of screening

Scrambling explores 256 random-looking variants. It works when a random strand satisfies the rules with
non-negligible probability. Strict rules such as `max_homopolymer ≤ 3` over ~300 nt (where a random strand
qualifies only ~3 % of the time), or narrow GC bands, make some frames unsatisfiable, and encoding then fails
explicitly. *Constrained coding* (e.g. V3's rotation code, which forbids homopolymers by construction at log₂3
bits/nt) would be the alternative. It is **PLANNED** for V4 (it changes the mapping and the strand length) and is
listed in [LIMITATIONS.md](LIMITATIONS.md).

## Not modelled

Secondary structure, melting temperature, primer compatibility and synthesis-cost models are **not implemented**.
