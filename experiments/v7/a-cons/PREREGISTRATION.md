# A-CONS pre-registration: full-template consensus polish (EXPERIMENTAL, SIMULATED)

Registered before any decode on seeds 82060-82099. Code under test: `bff39a9` (work/v7-nanodecode).

## Question
Does `ClusterConfig.consensus_template="full"` (full-template polish, marker-anchored shift moves) recover more of the
SIMULATED nanopore-like channel than the reference consensus (`"wildcard"`), with no false success?

## Arms (only this field differs)
- OLD: `consensus_template="wildcard"` (default, reference path)
- NEW: `consensus_template="full"`
All other parameters are the `tests/nanopore` corpus slow-tier case of the same coverage (20,000 B, v4-balanced,
data seed 6202, unfitted nanopore-like channel, `read_clustering="fallback"`, ClusterConfig defaults). No parameter was
tuned for NEW; it reuses `c_sub`, `c_indel`, `rounds`, `vote_share`, `min_votes` defaults. No quality values. No RS
policy change.

## Data
- Development (seen while building NEW): frozen corpus seeds 82040-82045, A-LOSS seeds 82046-82055.
- Held-out: channel seeds 82060-82099 x coverage 3/5/10 (120 cases per arm). Not used before this registration.

## Metrics per case and arm
>=2-read data strands, clustered, consensus candidates, consensus-successful (valid frame), data frames recovered,
false frames, archive outcome (EXACT = container SHA-256 equals the input), FALSE_SUCCESS, decode seconds, peak RSS.

## Decision rule (fixed now)
NEW is called an improvement at a coverage only if, on held-out seeds at that coverage:
1. FALSE_SUCCESS = 0 and false frames = 0 in both arms;
2. mean data frames NEW > OLD, with NEW >= OLD on at least 90 % of seeds (paired);
3. EXACT count NEW >= OLD.
The archive-recovery claim at a coverage is the EXACT count with its Wilson 95 % interval; no claim beyond these seeds.
Runtime and memory are reported, not gated.
