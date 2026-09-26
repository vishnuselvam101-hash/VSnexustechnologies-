from __future__ import annotations
import zlib
from ..core.errors import ConfigurationError
try: import zstandard as zstd
except ImportError: zstd=None
def compress(data:bytes, algorithm:str, level:int=6)->bytes:
    if algorithm=='none': return data
    if algorithm=='zlib': return zlib.compress(data,level)
    if algorithm=='zstandard' and zstd is not None:return zstd.ZstdCompressor(level=level).compress(data)
    raise ConfigurationError(f'Compression codec unavailable: {algorithm}')
def decompress(data:bytes, algorithm:str)->bytes:
    if algorithm=='none':return data
    if algorithm=='zlib':return zlib.decompress(data)
    if algorithm=='zstandard' and zstd is not None:return zstd.ZstdDecompressor().decompress(data)
    raise ConfigurationError(f'Compression codec unavailable: {algorithm}')
