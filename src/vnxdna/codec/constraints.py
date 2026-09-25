from dataclasses import dataclass, asdict
@dataclass(frozen=True)
class ConstraintConfig:
    min_gc_percent: float=0.0; max_gc_percent: float=100.0; max_homopolymer: int|None=None; forbidden_motifs: tuple[str,...]=()
def analyze_sequence(sequence: str, config: ConstraintConfig=ConstraintConfig()) -> dict:
    seq=sequence.upper(); invalid=set(seq)-set("ACGT")
    counts={b:seq.count(b) for b in "ACGT"}; length=len(seq); gc=(counts['G']+counts['C'])/length*100 if length else 0.0
    run=longest=0; previous=None
    for base in seq:
        run=run+1 if base==previous else 1; longest=max(longest,run); previous=base
    violations=[]
    if invalid: violations.append("invalid_bases:"+"".join(sorted(invalid)))
    if not config.min_gc_percent<=gc<=config.max_gc_percent: violations.append("gc_percent_out_of_range")
    if config.max_homopolymer is not None and longest>config.max_homopolymer: violations.append("homopolymer_too_long")
    violations += [f"forbidden_motif:{m}" for m in config.forbidden_motifs if m.upper() in seq]
    return {"length":length, **{f"{b}_percent":(counts[b]/length*100 if length else 0.0) for b in "ACGT"}, "gc_percent":gc, "longest_homopolymer":longest, "violations":violations, "valid":not violations, "config":asdict(config)}
