# V7 channel models fitted to public data (register)

Status: V7 Phase D, 2026-10-05. Models F (FIT split), validated on DEV under [V7_PROTOCOL.md](V7_PROTOCOL.md) 5.4 and
amendment 2 (5.5). Fitted parameters are **PUBLIC-DATA-DERIVED**; reads simulated from any of these models are
**SIMULATED**. VNX-DNA did not synthesise, store or sequence any DNA. Method, metric values and diagnosis:
[experiments/v7/fit/README.md](../experiments/v7/fit/README.md).

Current models (round 2, `vnx.channel-model/2`, version 2.0.0, fitted at df46fa4). Gating metrics: M2, M3, M8.

| Model ID | Data (licence) | File | Verdict on DEV | Failed gating metrics | canonical SHA-256 (vnx channel show) |
|---|---|---|---|---|---|
| cnr-ont-fit-F-a2 | D04 CNR, ONT, basecaller not stated (MIT) | `experiments/v7/fit-cnr/a2/models/cnr-ont-fit.json` | **INADEQUATE** | M2 (M3 passes, but largely automatically: real and simulated reads go through the same matched length window [-4, +5]; without the window, `M3_unselected`, it fails by 11.1 pp at <= 3) | `7242e766…cc9c` |
| cnr-ont-p4tie-fit-F-a2 | D04 CNR, P4-EXP-03 alignment tie-break (MIT) | `experiments/v7/fit-cnr/a2/models/cnr-ont-p4tie-fit.json` | **INADEQUATE** | M2, M3 | `52acf054…3234` |
| ont-guppy-hac-pass-fwd-fit-F-a2 | D03 Zenodo 10943282, guppy HAC, pass, forward (CC BY 4.0) | `experiments/v7/fit-d03/a2/models/ont-guppy-hac-pass-fwd-fit.json` | **INADEQUATE** | M3 | `f5ffdb28…6c93` (1) |
| ont-guppy-hac-pass-bwd-fit-F-a2 | D03, HAC, pass, backward | `experiments/v7/fit-d03/a2/models/ont-guppy-hac-pass-bwd-fit.json` | **INADEQUATE** | M3 | `3e72de24…c4b3` |
| ont-guppy-fast-pass-fwd-fit-F-a2 | D03, fast, pass, forward | `experiments/v7/fit-d03/a2/models/ont-guppy-fast-pass-fwd-fit.json` | **INADEQUATE** | M3 | `1def4436…b5b0` (1) |
| ont-guppy-fast-pass-bwd-fit-F-a2 | D03, fast, pass, backward | `experiments/v7/fit-d03/a2/models/ont-guppy-fast-pass-bwd-fit.json` | **INADEQUATE** | M3 | `d71e13f6…1d8d` |
| illumina-iseq-twist-fit-F-a2 | D02 DT4DDS Twist Aging_0a/0b + PhiX (INSDC) | `experiments/v7/fit-d02/a2/models/illumina-iseq-twist-fit.json` | ADEQUATE (marginal: M3 passes by 0.09 pp, about 1 Monte Carlo SE; DEV already looked at twice) (2) | none (non-gating M1, M4, M5, M6, M9 fail) | `bec7f24a…ada9` |

(1) Relabelled 2026-10-06 without a refit: with homopolymer min_run 2 the substitution rate is confounded with the 3-mer
context and the homopolymer substitution multiplier, so its basis is `estimated` (was `measured`), and the effective
per-base substitution rate observed on FIT is recorded in `fit_report.measured_statistics` (HAC forward: rate 0.0489,
effective 0.0165; fast forward: 0.0696 vs 0.0363). The stages, and so the simulated reads, are unchanged; the canonical
SHA-256 changed (as validated: HAC `8f9097d4…48fd`, fast `459eb509…02f4`; the results files keep those). Record:
`experiments/v7/fit-d03/RELABEL-2026-10-06.json` (round-1 models too).

(2) **D02 must not be used for quality-dependent decoder decisions while M9 fails** (the Q11 bin: real error Phred 12.8
vs simulated 3.0; the binned iSeq qualities do not fit the Gaussian quality model). Its ADEQUATE verdict covers the
gating metrics only, on a split by reference (protocol 4.3), at the second look at DEV.

Standard errors of the gating metrics (round 2; from the committed validation results only, no new look at DEV;
`experiments/v7/fit/gating_se.py` -> `experiments/v7/fit/results/gating-se.json`). M3: binomial SE of the difference
simulated minus real (real n = DEV reads tallied, simulated n = 5 x 6 reads per simulated reference); reads of one
reference share their rates, so these are lower bounds. M2: the two-sample KS 5 % critical value under equal
distributions. M8: from the committed Wilson intervals (largest SE over k). The SE of M2's TV distance and p90/p99
comparison needs the read-level histograms, which are not committed; computing it would re-read DEV, so it is not given.

| Model | M3 diff pp (<=0, <=3, <=6) +- SE | M2 KS D (limit 0.03; null 5 % value) | M8 largest SE of the difference (pp) |
|---|---|---|---|
| cnr-ont-fit-F-a2 | -0.0451 +- 0.228, -0.5795 +- 0.198, +0.0000 +- 0.000 | 0.0381 (0.0082) | 0.083 |
| cnr-ont-p4tie-fit-F-a2 | +1.4358 +- 0.232, +0.2944 +- 0.196, +0.0000 +- 0.000 | 0.0370 (0.0082) | 0.083 |
| ont-guppy-hac-pass-fwd-fit-F-a2 | -2.8502 +- 0.178, +0.1412 +- 0.141, +1.0049 +- 0.070 | 0.0138 (0.0054) | 0.059 |
| ont-guppy-hac-pass-bwd-fit-F-a2 | -2.9836 +- 0.177, -0.5885 +- 0.146, +0.5338 +- 0.074 | 0.0203 (0.0054) | 0.062 |
| ont-guppy-fast-pass-fwd-fit-F-a2 | -0.3034 +- 0.107, -1.2262 +- 0.190, +0.2444 +- 0.149 | 0.0155 (0.0052) | 0.087 |
| ont-guppy-fast-pass-bwd-fit-F-a2 | -0.4072 +- 0.107, -2.1510 +- 0.196, -1.4314 +- 0.156 | 0.0243 (0.0053) | 0.089 |
| illumina-iseq-twist-fit-F-a2 | -0.9080 +- 0.093, -0.0833 +- 0.026, +0.1874 +- 0.011 | 0.0212 (0.0056) | 0.029 |

Round-1 models (before amendment 2) stay in `experiments/v7/fit-*/models/`; all seven are INADEQUATE (CNR and D03 on
M2 and M3, D02 on M3).

Rules of use (protocol 5.4): an INADEQUATE model is not used to choose decoder parameters; it may be run only as a
labelled stress condition. No nanopore model is ADEQUATE. Held-out validation (after the PREREG commit) has not been run.
