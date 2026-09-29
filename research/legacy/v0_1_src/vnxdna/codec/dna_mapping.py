MAP={"00":"A","01":"C","10":"G","11":"T"}; REVERSE={v:k for k,v in MAP.items()}
def bits_to_dna(bits: str) -> str:
    if len(bits)%2 or any(c not in "01" for c in bits): raise ValueError("Bits must be a binary string with even length.")
    return "".join(MAP[bits[i:i+2]] for i in range(0,len(bits),2))
def dna_to_bits(sequence: str) -> str:
    invalid=set(sequence.upper())-set(REVERSE)
    if invalid: raise ValueError(f"Invalid DNA bases: {''.join(sorted(invalid))}")
    return "".join(REVERSE[b] for b in sequence.upper())
