from . import substitutions,insertions,deletions
def apply(sequence:str, substitution_rate:float=0, insertion_rate:float=0, deletion_rate:float=0, seed:int=0)->str:
 return deletions.apply(insertions.apply(substitutions.apply(sequence,substitution_rate,seed),insertion_rate,seed+1),deletion_rate,seed+2)
