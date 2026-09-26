"""Deterministic constrained-v1 codebook encoding.

Each byte is represented by one fixed eight-base word. Codewords begin A and
end T, have exactly 50% GC, and are selected per persisted constraints. Fixed
opposite boundary bases prevent homopolymers from extending between words.
This trades density (1 useful bit/base) for deterministic constraints.
"""
from itertools import product
from functools import lru_cache
from ..core.errors import ConfigurationError, InvalidDNAError
from ..core.models import ConstraintSettings
@lru_cache(maxsize=64)
def _codebook(min_gc:float,max_gc:float,max_run:int|None,motifs:tuple[str,...]):
    if not min_gc<=50<=max_gc: raise ConfigurationError('constrained-v1 has 50% GC and requires bounds containing 50%.')
    run_limit=max_run if max_run is not None else 8; choices=[]
    for middle in product('ACGT',repeat=6):
        word='A'+''.join(middle)+'T'
        if word.count('G')+word.count('C')!=4 or any(m.upper() in word for m in motifs):continue
        run=1;best=1
        for a,b in zip(word,word[1:]):run=run+1 if a==b else 1;best=max(best,run)
        if best<=run_limit:choices.append(word)
    if len(choices)<256:raise ConfigurationError('constraints cannot provide the required 256 constrained-v1 codewords.')
    mapping=tuple(choices[:256]);return mapping,{word:index for index,word in enumerate(mapping)}
def encode_bytes(data:bytes,settings:ConstraintSettings)->str:
    forward,_=_codebook(settings.min_gc,settings.max_gc,settings.max_homopolymer_length,tuple(settings.forbidden_motifs));return ''.join(forward[value] for value in data)
def decode_sequence(sequence:str,settings:ConstraintSettings)->bytes:
    if len(sequence)%8:raise InvalidDNAError('constrained-v1 sequence length must be divisible by 8.')
    _,reverse=_codebook(settings.min_gc,settings.max_gc,settings.max_homopolymer_length,tuple(settings.forbidden_motifs))
    try:return bytes(reverse[sequence[i:i+8]] for i in range(0,len(sequence),8))
    except KeyError as error:raise InvalidDNAError('Sequence contains an unknown constrained-v1 codeword.') from error
