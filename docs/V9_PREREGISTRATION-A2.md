# V9 pre-registration amendment A2 (2026-10-07): the G1 fitting procedure

Committed before the G1 model is fitted. No V9 channel-model fit, pre-check or DEV look has been run. The FIT tables and
caches of V8.1 are reused unchanged (hash-checked). The look budget of §4.3 is unchanged.

**Change 1 (G1 class contents).** §4.1 gives each latent class "its own total error rate, substitution/insertion/deletion
mix and substitution spectrum". In V9, G1 classes carry three multipliers: substitution, insertion and deletion. These
three multipliers fix the class's total error rate and its type mix. The substitution spectrum and the 3-mer context stay
global; they are F1's.

*Reason:* the simulator draws substituted bases from one matrix per model. Per-class spectra would need per-read
matrices in the engine. Every gating metric that involves the spectrum (M1c) compares pooled spectra. A pooled
spectrum is the weight-averaged class spectrum, so per-class spectra cannot change it. The deviation is reported as a
limitation of G1.

**Change 2 (procedure, fixed now).** The fit runs as follows (`experiments/v9/d13/g1.py`):

1. **Sufficient statistics.** Per-read statistics come from the FIT segments of every 10th FIT table row. For each
   segment, take the normalised unit-cost NW alignment (the V8 alignment) and count:
   - substitution events;
   - insertion runs;
   - deletion runs.

   The V8 A1 selection, edit distance ≤ 0.30 L, is applied.
2. **EM.** Fit a mixture of K classes, each with three independent Poisson counts per read, for K ∈ {1, 2, 3, 4}.
   - The initialisation is deterministic: classes start at total-count quantiles.
   - The stopping rule is a relative log-likelihood gain below 1e-10, or 2,000 iterations.
3. **Choice of K.** K minimises BIC = −2 log L + (4K − 1) ln n.
4. **Multipliers.** Each class's per-type rate is divided by the weighted mean rate of that type. The resulting
   multipliers replace F1's gamma multiplier (`read_heterogeneity.distribution = "latent-states"`). All other F1
   parameters are kept.
5. **Calibration.** Run 4 iterations of three-ratio calibration of the base substitution, insertion and deletion rates.
   Each iteration simulates 10 reads on each of 2,000 FIT references, applies the A1 selection and tallies the result.
   The goal is for the simulated per-base rates to equal the FIT per-base rates.
6. **Seeds.** The fit seed is 20261110 and the pre-check seed is 20261112.
7. **Pre-check.** The pre-check is the V8 FIT-only pre-check unchanged: full FIT plus two halves, the 7B gating set
   with the V7 thresholds and A1, and M10-V8.

**Unchanged.**

- G2 is fitted only if G1 fails M3 or M2b on the FIT pre-check.
- If neither G1 nor G2 passes every gating metric in sample, the verdict is
  `DEV_LOOK_BLOCKED — FIT PRE-CHECK INADEQUATE`. In that case no DEV look is spent, and the held-out data stays closed.
