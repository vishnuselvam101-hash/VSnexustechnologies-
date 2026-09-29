from dataclasses import dataclass
from ..integrity.hashing import calculate_bytes_sha256
@dataclass(frozen=True)
class Chunk: archive_id:str; file_id:str; chunk_id:int; payload:bytes; checksum:str; ecc_metadata:dict
def chunk_bytes(data:bytes, archive_id:str, file_id:str, size:int, ecc_metadata:dict|None=None)->list[Chunk]:
    if size<=0: raise ValueError("Chunk size must be positive.")
    return [Chunk(archive_id,file_id,i,data[p:p+size],calculate_bytes_sha256(data[p:p+size]),ecc_metadata or {}) for i,p in enumerate(range(0,len(data),size))] or [Chunk(archive_id,file_id,0,b'',calculate_bytes_sha256(b''),ecc_metadata or {})]
