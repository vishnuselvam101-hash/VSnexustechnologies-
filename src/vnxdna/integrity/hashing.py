import hashlib
from pathlib import Path
def calculate_bytes_sha256(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def calculate_sha256(path:str|Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
def verify_integrity(original:bytes, recovered:bytes)->bool:return calculate_bytes_sha256(original)==calculate_bytes_sha256(recovered)
