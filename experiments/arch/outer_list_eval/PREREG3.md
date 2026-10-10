# Pre-registration 3: preset F at 87b532f (RANSAC + round fix) vs E — SIMULATED

Written before any run of this evaluation.

- **Change since PREREG2.md.** List-decoding rounds continue when a round finds new candidates but no new symbol
  (87b532f). Found and tested on development seeds 93022, 93027, 93028 (not evidence). Seeds 93030–93049 (evaluation 2)
  are not reused.
- **Held-out seeds.** 93050 … 93069 (20 seeds, never used before).
- **Case, recording, presets, acceptance.** As PREREG2.md (results in `results/eval3.jsonl`).
- **Label.** SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced.
