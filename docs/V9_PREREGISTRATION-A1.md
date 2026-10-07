# V9 pre-registration amendment A1 (2026-10-07)

Committed before any V9 benchmark or evaluation decode is run.

**Change.** §7 ("full noisy decodes of the primary cells (20,000 B; 10 EVAL seeds per cell, …)") contradicts §8, which
assigns benchmarks the seeds 94000–94009 and reserves the EVAL seeds 91000–91199 for one evaluation per frozen method.
The §8 seed table governs. The native-kernel benchmark therefore uses seeds **94000–94009**, never EVAL seeds.

**Reason.** A wording slip. Running the benchmark on EVAL seeds would expose EVAL read pools before the consensus V2
evaluation. Nothing has been run on any V9 seed except the DEV smoke decode 90000 (D13-F1, coverage 5, v7-lowcov) used
to check native/reference identity.

**Recording.** Each benchmark row records `decode_seconds` for the V8 decoder (NumPy polish) and the V9 decoder (native
polish) on the same read file, the host load, peak RSS and the commit. The speed ratio is reported as a median with a
bootstrap 95 % interval, as §7 requires.
