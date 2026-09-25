from __future__ import annotations
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from ..core.pipeline import copy_dataset
from ..storage.fasta import read_strands,write_strands
from ..core.models import Strand
@dataclass(frozen=True)
class ChannelConfig:
    substitution_probability:float=0.0; insertion_probability:float=0.0; deletion_probability:float=0.0; dropout_probability:float=0.0; reorder:bool=False; duplicate_probability:float=0.0; seed:int=0
    def __post_init__(self):
        for value in (self.substitution_probability,self.insertion_probability,self.deletion_probability,self.dropout_probability,self.duplicate_probability):
            if not 0<=value<=1: raise ValueError('Channel probabilities must be between zero and one.')
def simulate(source:str|Path,destination:str|Path,config:ChannelConfig)->dict:
    copy_dataset(source,destination); rng=random.Random(config.seed); output=[]; counts={'strands_total':0,'strands_dropped':0,'substitutions':0,'insertions':0,'deletions':0,'duplicates':0}
    for strand in read_strands(Path(source)/'strands.fasta'):
        counts['strands_total']+=1
        if rng.random()<config.dropout_probability: counts['strands_dropped']+=1;continue
        bases=[]
        for base in strand.sequence:
            if rng.random()<config.deletion_probability: counts['deletions']+=1;continue
            if rng.random()<config.substitution_probability: base=rng.choice([x for x in 'ACGT' if x!=base]);counts['substitutions']+=1
            bases.append(base)
            if rng.random()<config.insertion_probability: bases.append(rng.choice('ACGT'));counts['insertions']+=1
        changed=Strand(strand.dataset_id,strand.index,strand.total,strand.payload_length,strand.checksum,''.join(bases));output.append(changed)
        if rng.random()<config.duplicate_probability: output.append(changed);counts['duplicates']+=1
    if config.reorder:rng.shuffle(output)
    write_strands(Path(destination)/'strands.fasta',output);return {**counts,'seed':config.seed,'reordered':config.reorder}
