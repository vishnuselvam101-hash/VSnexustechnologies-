from .binary import bytes_to_bits,bits_to_bytes
from .dna_mapping import bits_to_dna,dna_to_bits
def encode_bytes(data: bytes)->str:return bits_to_dna(bytes_to_bits(data))
def decode_dna(sequence: str)->bytes:return bits_to_bytes(dna_to_bits(sequence))
