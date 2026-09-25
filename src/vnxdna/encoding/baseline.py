from ..core.errors import InvalidDNAError
_ENCODE=tuple('ACGT'); _DECODE={base:index for index,base in enumerate(_ENCODE)}
def encode_bytes(data: bytes)->str:
    """Two-bit baseline mapping, four bases per input byte."""
    return ''.join(_ENCODE[(value >> shift) & 3] for value in data for shift in (6,4,2,0))
def decode_sequence(sequence: str)->bytes:
    sequence=sequence.upper()
    if len(sequence)%4: raise InvalidDNAError('DNA sequence length must be divisible by 4 for the baseline encoding.')
    try:return bytes(sum(_DECODE[sequence[i+j]] << (6-2*j) for j in range(4)) for i in range(0,len(sequence),4))
    except KeyError as exc: raise InvalidDNAError(f'Invalid DNA base: {exc.args[0]!r}') from exc
