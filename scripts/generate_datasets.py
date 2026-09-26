import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pathlib import Path
import random,json
from vnxdna.integrity.hashing import calculate_bytes_sha256
out=Path('datasets');out.mkdir(exist_ok=True);r=random.Random(20260908)
data={'random.bin':bytes(r.randrange(256) for _ in range(1024)),'repetitive.txt':b'VNX-DNA\n'*256,'sample.json':json.dumps({'records':[{'id':i,'name':'vnx'} for i in range(100)]}).encode(),'sample.csv':b'id,value\n'+b''.join(f'{i},{i*i}\n'.encode() for i in range(100)),'video_like.bin':bytes((i*17)%256 for i in range(2048))}
meta=[]
for n,b in data.items():(out/n).write_bytes(b);meta.append({'file':n,'size':len(b),'seed':20260908,'sha256':calculate_bytes_sha256(b)})
(out/'manifest.json').write_text(json.dumps(meta,indent=2))
