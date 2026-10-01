"""Generate VNX-DNA 2.0.0 compatibility fixtures (run with the v2.0.0 code)."""
import hashlib, json, os, random, sys
from pathlib import Path
import vnxdna
from vnxdna.v2 import archive as arc
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.profiles import options_for
assert vnxdna.__version__ == "2.0.0", vnxdna.__version__
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
r = random.Random(20260930)
data = r.randbytes(6000) + b"VNX-DNA 2.0 compatibility fixture " * 150 + "Unicode: ДНК データ 🧬\n".encode() * 40
(out / "input.bin").write_bytes(data)
key = hashlib.sha256(b"VNX-DNA 2.0 fixture key; public, protects nothing").digest()
(out / "key.hex").write_text(key.hex() + "\n")
opts = options_for("balanced", chunk_size=4096, data_shards=8, parity_shards=4, payload_bytes=24, inner_parity_bytes=8)
arc.store_file(out / "input.bin", out / "plain.vxdna", options=opts, workers=1, overwrite=True)
encode_file(out / "plain.vxdna", out / "plain.fasta", workers=1, overwrite=True, write_index=False)
arc.store_file(out / "input.bin", out / "encrypted.vxdna", options=opts, key=key, workers=1, overwrite=True)
class Stop(BaseException): pass
def stop(done, n):
    if done == 2: raise Stop()
try:
    arc.store_file(out / "input.bin", out / "resumed.vxdna", options=opts, key=key, workers=1, overwrite=True, checkpoint_interval=1, progress=stop)
except Stop:
    pass
arc.store_file(out / "input.bin", out / "resumed.vxdna", options=opts, key=key, workers=1, resume=True, checkpoint_interval=1)
manifest = {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in sorted(os.listdir(out)) if name != "SHA256SUMS.json"}
(out / "SHA256SUMS.json").write_text(json.dumps({"generator": "vnx-dna 2.0.0 (tag v2.0.0)", "files": manifest}, indent=2, sort_keys=True) + "\n")
print(json.dumps(manifest, indent=2))
