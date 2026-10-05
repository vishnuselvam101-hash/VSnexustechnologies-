# V7 channel models fitted to public data (register)

Status: V7 Phase D, 2026-10-05. Models F (FIT split), validated on DEV under [V7_PROTOCOL.md](V7_PROTOCOL.md) 5.4 and
amendment 2 (5.5). Fitted parameters are **PUBLIC-DATA-DERIVED**; reads simulated from any of these models are
**SIMULATED**. VNX-DNA did not synthesise, store or sequence any DNA. Method, metric values and diagnosis:
[experiments/v7/fit/README.md](../experiments/v7/fit/README.md).

Current models (round 2, `vnx.channel-model/2`, version 2.0.0, fitted at df46fa4). Gating metrics: M2, M3, M8.

| Model ID | Data (licence) | File | Verdict on DEV | Failed gating metrics | SHA-256 (after validation) |
|---|---|---|---|---|---|
| cnr-ont-fit-F-a2 | D04 CNR, ONT, basecaller not stated (MIT) | `experiments/v7/fit-cnr/a2/models/cnr-ont-fit.json` | **INADEQUATE** | M2 | `7242e766…cc9c` |
| cnr-ont-p4tie-fit-F-a2 | D04 CNR, P4-EXP-03 alignment tie-break (MIT) | `experiments/v7/fit-cnr/a2/models/cnr-ont-p4tie-fit.json` | **INADEQUATE** | M2, M3 | `52acf054…3234` |
| ont-guppy-hac-pass-fwd-fit-F-a2 | D03 Zenodo 10943282, guppy HAC, pass, forward (CC BY 4.0) | `experiments/v7/fit-d03/a2/models/ont-guppy-hac-pass-fwd-fit.json` | **INADEQUATE** | M3 | `8f9097d4…48fd` |
| ont-guppy-hac-pass-bwd-fit-F-a2 | D03, HAC, pass, backward | `experiments/v7/fit-d03/a2/models/ont-guppy-hac-pass-bwd-fit.json` | **INADEQUATE** | M3 | `3e72de24…c4b3` |
| ont-guppy-fast-pass-fwd-fit-F-a2 | D03, fast, pass, forward | `experiments/v7/fit-d03/a2/models/ont-guppy-fast-pass-fwd-fit.json` | **INADEQUATE** | M3 | `459eb509…02f4` |
| ont-guppy-fast-pass-bwd-fit-F-a2 | D03, fast, pass, backward | `experiments/v7/fit-d03/a2/models/ont-guppy-fast-pass-bwd-fit.json` | **INADEQUATE** | M3 | `d71e13f6…1d8d` |
| illumina-iseq-twist-fit-F-a2 | D02 DT4DDS Twist Aging_0a/0b + PhiX (INSDC) | `experiments/v7/fit-d02/a2/models/illumina-iseq-twist-fit.json` | ADEQUATE on M2/M3/M8 | none (non-gating M1, M4, M5, M6, M9 fail; M3 margin 0.09 pp) | `bec7f24a…ada9` |

Round-1 models (before amendment 2) stay in `experiments/v7/fit-*/models/`; all seven are INADEQUATE (CNR and D03 on
M2 and M3, D02 on M3).

Rules of use (protocol 5.4): an INADEQUATE model is not used to choose decoder parameters; it may be run only as a
labelled stress condition. No nanopore model is ADEQUATE. Held-out validation (after the PREREG commit) has not been run.
