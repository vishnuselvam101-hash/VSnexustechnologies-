from __future__ import annotations
from ..core.errors import InvalidDNAError
from ..core.models import ConstraintSettings
def analyze(sequence:str, settings:ConstraintSettings=ConstraintSettings())->dict:
    seq=sequence.upper(); length=len(seq); invalid=sorted(set(seq)-set('ACGT')); counts={b:seq.count(b) for b in 'ACGT'}
    run=best=0; previous=''
    for base in seq: run=run+1 if base==previous else 1; best=max(best,run); previous=base
    gc=100*(counts['G']+counts['C'])/length if length else 0.0; violations=[]
    if invalid: violations.append('invalid alphabet: '+''.join(invalid))
    if length>settings.max_strand_length: violations.append('strand exceeds configured maximum length')
    if not settings.min_gc<=gc<=settings.max_gc: violations.append('GC percentage outside configured range')
    if settings.max_homopolymer_length is not None and best>settings.max_homopolymer_length: violations.append('homopolymer exceeds configured maximum')
    violations.extend(f'forbidden motif: {motif}' for motif in settings.forbidden_motifs if motif.upper() in seq)
    return {'length':length,'counts':counts,'gc_percent':gc,'longest_homopolymer':best,'violations':violations,'valid':not violations}
def require_valid(sequence:str, settings:ConstraintSettings)->None:
    report=analyze(sequence,settings)
    if not report['valid']: raise InvalidDNAError('; '.join(report['violations']))
