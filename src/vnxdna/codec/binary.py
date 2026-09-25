def bytes_to_bits(data: bytes) -> str:
    return "".join(f"{byte:08b}" for byte in data)
def bits_to_bytes(bits: str) -> bytes:
    if any(c not in "01" for c in bits): raise ValueError("Bits must contain only 0 and 1.")
    if len(bits) % 8: raise ValueError("Bit length must be divisible by 8.")
    return bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
