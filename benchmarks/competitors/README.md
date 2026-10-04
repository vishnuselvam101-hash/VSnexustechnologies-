# Published DNA-storage results and their comparability with VNX-DNA

`records.json` holds published results reported by others. Each value comes with its source and a locator (section,
table or figure). A field that could not be checked against the source is `null`. Records are data only: no test,
source file or document may contain a hard-coded claim that VNX-DNA is better or worse than a listed system.

`classify.py` assigns a comparability label to each pair of (record, metric). The label is computed from the
record's metadata (medium, verification, protocol) and is never edited by hand. The labels are:

- **DIRECTLY COMPARABLE.** Only possible when VNX-DNA has re-run a published in-silico protocol unchanged. Gimpel
  et al. 2026 is the one protocol listed so far. Its scenario IDs must be recorded on both sides.
- **PARTIALLY COMPARABLE.** Both numbers may be shown side by side, with the definitional difference stated. They
  are never ranked.
- **NOT COMPARABLE.** Applies to:
  - physical (wet-lab) recovery or physical quantities, compared against VNX-DNA's SIMULATED results;
  - values that were not verified from the primary full text;
  - metrics the source does not report.

Every VNX-DNA result is SIMULATED. VNX-DNA has not synthesised, stored or sequenced DNA.

```bash
python benchmarks/competitors/classify.py                  # markdown matrix
python benchmarks/competitors/classify.py --json m.json    # with the reason for every label
```

Before using a `secondary` record, verify it against the paper and fill in its DOI from Crossref. Records that were
searched for but not found are listed under `not_found`.
