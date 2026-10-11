"""V11 red team: single-bit flips and truncations of an encrypted VNX4 container must never extract wrong data.
Produces the sweep numbers in pre-fix-measurements.txt. Usage: PYTHONPATH=src python docs/security/v11-redteam/byte_flip_sweep.py
Writes only to a fresh temporary directory. SYNTHETIC SOFTWARE TEST data; the key is test-only (bytes 0..31)."""
import os, tempfile, pathlib, random
from vnxdna.archive import operations as ar
d = pathlib.Path(tempfile.mkdtemp()); (d/"src").mkdir()
data = os.urandom(30000); (d/"src"/"f.bin").write_bytes(data); (d/"src"/"g.txt").write_text("hello"*500)
key = bytes(range(32))
ar.build_archive([d/"src"], d/"a.vnx", ar.ArchiveOptions(chunk_size=4096, key=key))
raw = (d/"a.vnx").read_bytes(); print("size", len(raw))
# wrong key
for k in (bytes(32), key[:-1]+b"\x00"):
    try: ar.extract(d/"a.vnx", d/"wk", key=k); print("WRONG KEY SUCCESS!!")
    except Exception: pass
try: ar.extract(d/"a.vnx", d/"pw", passphrase="nope"); print("PASSPHRASE ON KEY ARCHIVE SUCCESS!!")
except Exception as e: print("pw:", type(e).__name__, getattr(e,"code",None))
bad = 0; ok_same = 0; refused = 0; kinds = {}
offs = sorted(set(list(range(0, len(raw), max(1, len(raw)//1500))) + list(range(len(raw)-300, len(raw)))))
for i in offs:
    b = bytearray(raw); b[i] ^= 1 << random.Random(i).randrange(8)
    p = d/"t.vnx"; p.write_bytes(b)
    out = d/"o"; 
    import shutil; shutil.rmtree(out, ignore_errors=True)
    try:
        r = ar.extract(p, out, key=key)
        same = (out/"src"/"f.bin").read_bytes()==data and (out/"src"/"g.txt").read_text()=="hello"*500
        if same: ok_same += 1; print("identical output after flip at", i, "(bytes from end:", len(raw) - i, ")")
        else: bad += 1; print("WRONG DATA at", i)
    except Exception as e:
        refused += 1; kinds[type(e).__name__] = kinds.get(type(e).__name__,0)+1
        if type(e).__name__ not in ("VNXIntegrityError","VNXFormatError","VNXKeyError","VNXUnsupportedVersionError"): print("odd", i, type(e).__name__, str(e)[:100])
print("flips", len(offs), "refused", refused, "identical", ok_same, "WRONG", bad, kinds)
# truncation at every 97th offset
tr_bad=0
for n in range(0, len(raw), 97):
    p = d/"t.vnx"; p.write_bytes(raw[:n])
    try: ar.extract(p, d/"tr", key=key, overwrite=True); tr_bad+=1
    except Exception as e:
        if type(e).__name__ not in ("VNXIntegrityError","VNXFormatError","VNXKeyError","VNXUnsupportedVersionError"): print("odd trunc", n, type(e).__name__)
print("truncations success:", tr_bad)
