import random
def apply(sequence:str, rate:float, seed:int)->str:
 if not 0<=rate<=1: raise ValueError("Rate must be between 0 and 1.")
 r=random.Random(seed); return ''.join(r.choice([x for x in 'ACGT' if x!=b]) if r.random()<rate else b for b in sequence)
