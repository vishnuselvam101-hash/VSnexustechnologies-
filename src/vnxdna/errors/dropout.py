import random
def apply(items:list, rate:float, seed:int)->list:
 r=random.Random(seed); return [x for x in items if r.random()>=rate]
