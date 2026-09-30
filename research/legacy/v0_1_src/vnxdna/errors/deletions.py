import random
def apply(sequence:str, rate:float, seed:int)->str:
 r=random.Random(seed); return ''.join(b for b in sequence if r.random()>=rate)
