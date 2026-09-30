import random
def apply(sequence:str, rate:float, seed:int)->str:
 r=random.Random(seed); return ''.join(b+(r.choice('ACGT') if r.random()<rate else '') for b in sequence)
