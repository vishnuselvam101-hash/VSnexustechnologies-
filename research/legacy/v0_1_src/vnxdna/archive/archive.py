import json, uuid
try:
    import zstandard as zstd
    COMPRESSION_ALGORITHM = 'zstandard'
except ImportError:
    # Explicit portable fallback. Archives record this algorithm and never masquerade as Zstandard.
    import zlib
    zstd = None
    COMPRESSION_ALGORITHM = 'zlib'

def _compress(data: bytes, level: int, algorithm: str) -> bytes:
    if algorithm == 'zstandard':
        if zstd is None: raise ValueError('Archive requires zstandard, which is not installed.')
        return zstd.ZstdCompressor(level=level).compress(data)
    if algorithm == 'zlib': return zlib.compress(data, level)
    raise ValueError('Unsupported compression algorithm.')

def _decompress(data: bytes, algorithm: str) -> bytes:
    if algorithm == 'zstandard':
        if zstd is None: raise ValueError('Archive requires zstandard, which is not installed.')
        return zstd.ZstdDecompressor().decompress(data)
    if algorithm == 'zlib': return zlib.decompress(data)
    raise ValueError('Unsupported compression algorithm.')
from datetime import datetime, timezone
from pathlib import Path
from ..config import ARCHIVE_FORMAT,ARCHIVE_VERSION,CODEC_VERSION
from ..codec.codec import encode_bytes,decode_dna
from ..crypto.encryption import encrypt,decrypt
from ..ecc.baseline import NoECC
from ..ecc.reed_solomon import ReedSolomonECC
from ..integrity.hashing import calculate_bytes_sha256
from .chunking import chunk_bytes
from .index import find_file

def _ecc(name,parity): return NoECC() if name=='none' else ReedSolomonECC(parity)
def _atomic_json(path:Path,value:dict):
 tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(value,separators=(',',':'))); tmp.replace(path)
def _read(path:str|Path)->dict:
 try: a=json.loads(Path(path).read_text())
 except (OSError,json.JSONDecodeError) as e: raise ValueError("Archive is unreadable or corrupted.") from e
 m=a.get('manifest',{})
 if m.get('format')!=ARCHIVE_FORMAT or m.get('format_version')!=ARCHIVE_VERSION: raise ValueError("Unsupported archive format version.")
 return a
def create_archive(path:str|Path,chunk_size:int=128,ecc:str='none',parity_symbols:int=16,compression:bool=True,encryption:bool=False,seed:int|None=None)->dict:
 if ecc not in ('none','reed_solomon'):raise ValueError('Unsupported ECC.')
 if chunk_size <= 0: raise ValueError('Chunk size must be positive.')
 if not 0 <= parity_symbols < 255: raise ValueError('Parity symbols must be in 0..254.')
 if ecc=='reed_solomon' and chunk_size>255-parity_symbols:raise ValueError('Chunk size exceeds Reed-Solomon block capacity.')
 archive_id=str(uuid.uuid4()); manifest={'format':ARCHIVE_FORMAT,'format_version':ARCHIVE_VERSION,'archive_id':archive_id,'codec_version':CODEC_VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'chunk_size':chunk_size,'ecc':{'name':ecc,'parity_symbols':parity_symbols if ecc!='none' else 0},'compression':{'enabled':compression,'algorithm':COMPRESSION_ALGORITHM if compression else 'none','level':3 if compression else None},'encryption':{'enabled':encryption,'algorithm':'Fernet' if encryption else 'none'},'experiment_seed':seed,'files':[]}
 a={'manifest':manifest,'files':[]}; _atomic_json(Path(path),a);return a
def add_file(path:str|Path,input_path:str|Path,file_id:str|None=None,key:str|None=None)->dict:
 a=_read(path); source=Path(input_path); data=source.read_bytes(); m=a['manifest']; file_id=file_id or str(uuid.uuid4())
 if any(x['manifest']['file_id']==file_id for x in a['files']):raise ValueError('Duplicate file ID.')
 transformed=_compress(data, m['compression']['level'], m['compression']['algorithm']) if m['compression']['enabled'] else data
 if m['encryption']['enabled']:
  if not key:raise ValueError('Encryption key required.')
  transformed=encrypt(transformed,key)
 ecc=_ecc(m['ecc']['name'],m['ecc']['parity_symbols']); chunks=chunk_bytes(transformed,m['archive_id'],file_id,m['chunk_size'])
 records=[]
 for c in chunks:
  encoded=ecc.encode(c.payload); records.append({'chunk_id':c.chunk_id,'payload_length':len(c.payload),'checksum':c.checksum,'ecc_metadata':{'name':m['ecc']['name'],'parity_symbols':m['ecc']['parity_symbols'],'encoded_length':len(encoded)},'dna':encode_bytes(encoded)})
 fm={'file_id':file_id,'name':source.name,'original_size':len(data),'sha256':calculate_bytes_sha256(data),'transformed_size':len(transformed),'chunks':len(records)}
 a['files'].append({'manifest':fm,'chunks':records}); m['files'].append(fm);_atomic_json(Path(path),a);return fm
def retrieve_file(path:str|Path,file_id:str,key:str|None=None)->tuple[bytes,dict]:
 a=_read(path); m=a['manifest']; f=find_file(a,file_id); ecc=_ecc(m['ecc']['name'],m['ecc']['parity_symbols']); recovered=[]; failed=[]
 expected_ids=list(range(f['manifest']['chunks']))
 actual_ids=sorted(c.get('chunk_id') for c in f['chunks'])
 if actual_ids != expected_ids: raise ValueError('Recovery failed: missing, duplicate, or reordered chunks.')
 for c in sorted(f['chunks'], key=lambda item: item['chunk_id']):
  try:
   payload=ecc.decode(decode_dna(c['dna']))
   if len(payload)!=c['payload_length'] or calculate_bytes_sha256(payload)!=c['checksum']:raise ValueError('Chunk checksum failed.')
   recovered.append(payload)
  except Exception as e: failed.append({'chunk_id':c['chunk_id'],'reason':str(e)})
 if failed:raise ValueError('Recovery failed: '+json.dumps({'failed_chunks':failed}))
 data=b''.join(recovered)
 if m['encryption']['enabled']:
  if not key:raise ValueError('Encryption key required.')
  data=decrypt(data,key)
 if m['compression']['enabled']: data=_decompress(data, m['compression']['algorithm'])
 integrity=calculate_bytes_sha256(data)==f['manifest']['sha256']
 if not integrity:raise ValueError('Integrity verification failed.')
 return data,{'integrity':'PASS','chunks_selected':len(f['chunks']),'total_chunks':sum(len(x['chunks']) for x in a['files']),'archive_fraction_inspected':len(f['chunks'])/max(1,sum(len(x['chunks']) for x in a['files']))}
def encode_file(input_path:str|Path,output_path:str|Path,**kwargs)->dict:
 key=kwargs.pop('key',None); create_archive(output_path,**kwargs); return add_file(output_path,input_path,file_id='FILE001',key=key)
def decode_file(input_path:str|Path,output_path:str|Path,key:str|None=None)->dict:
 data,metrics=retrieve_file(input_path,'FILE001',key);Path(output_path).write_bytes(data);return metrics
def inspect(path:str|Path)->dict:return _read(path)
def simulate_errors(input_path:str|Path,output_path:str|Path,substitution_rate:float=0,insertion_rate:float=0,deletion_rate:float=0,dropout_rate:float=0,seed:int=0)->dict:
 from ..errors.mixed import apply as mixed
 from ..errors.dropout import apply as dropout
 a=_read(input_path); changed=0
 for f in a['files']:
  before=len(f['chunks']);f['chunks']=dropout(f['chunks'],dropout_rate,seed+100);changed+=before-len(f['chunks'])
  for c in f['chunks']:c['dna']=mixed(c['dna'],substitution_rate,insertion_rate,deletion_rate,seed+c['chunk_id'])
 _atomic_json(Path(output_path),a);return {'dropped_chunks':changed,'seed':seed}
