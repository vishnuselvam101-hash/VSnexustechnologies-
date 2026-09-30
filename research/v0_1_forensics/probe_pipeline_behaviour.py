import os, random, json, tempfile, hashlib, traceback
from dataclasses import replace
from pathlib import Path
from vnxdna import DatasetConfig
from vnxdna.core.pipeline import encode_file, decode_dataset
from vnxdna.storage.fasta import read_strands, write_strands
T = Path(tempfile.mkdtemp())
def fresh(name, data, cfg):
    s = T/f'{name}.in'; s.write_bytes(data); d = T/name; encode_file(s, d, cfg); return s, d
def attempt(label, d, **kw):
    out = T/(d.name+'.out')
    try: r = decode_dataset(d, out, **kw); print(f'{label}: DECODED ok={out.read_bytes()==(T/(d.name+".in")).read_bytes()}')
    except Exception as e: print(f'{label}: {type(e).__name__}: {str(e)[:110]}')
data = random.Random(20260929).randbytes(8*512*3)
# 1. pipeline-level non-MDS: K=8 M=4, erase shards 4,5,7,11 in stripe 0 (within documented capacity)
s, d = fresh('rs84', data, DatasetConfig(ecc='reed_solomon', data_shards=8, parity_shards=4, compression='none'))
write_strands(d/'strands.fasta', [r for r in read_strands(d/'strands.fasta') if not (r.stripe_index==0 and r.shard_index in (4,5,7,11))])
attempt('1 non-MDS 4 erasures K8M4', d)
# 2. determinism: identical input + config twice
s1, d1 = fresh('det1', data, DatasetConfig()); s2, d2 = fresh('det2', data, DatasetConfig())
print('2 deterministic strands.fasta:', (d1/'strands.fasta').read_bytes()==(d2/'strands.fasta').read_bytes(),
      '| sequences equal:', [r.sequence for r in read_strands(d1/'strands.fasta')]==[r.sequence for r in read_strands(d2/'strands.fasta')])
# 3. duplicate where FIRST copy is corrupt, second copy valid (V1 and V2)
for name, cfg in [('dupv1', DatasetConfig(strand_payload_bytes=256)), ('dupv2', DatasetConfig(ecc='reed_solomon', data_shards=4, parity_shards=1, strand_payload_bytes=256))]:
    s, d = fresh(name, data, cfg); recs = read_strands(d/'strands.fasta'); good = recs[0]
    bad = replace(good, sequence=('C' if good.sequence[0]!='C' else 'A')+good.sequence[1:])
    others = recs[1:]
    if name=='dupv2':  # also erase another shard in same stripe so the valid duplicate is needed
        others = [r for r in others if not (r.stripe_index==0 and r.shard_index==1)]
    write_strands(d/'strands.fasta', [bad, good] + others); attempt(f'3 {name} corrupt-first-then-valid duplicate', d)
# 4. foreign strand with same index placed first (V1)
s, d = fresh('foreign', data, DatasetConfig(strand_payload_bytes=256)); recs = read_strands(d/'strands.fasta')
write_strands(d/'strands.fasta', [replace(recs[0], dataset_id='00000000-0000-0000-0000-000000000000')] + recs); attempt('4 foreign strand shadows valid one (V1)', d)
# 5. manifest mutations -> exception types
for label, mut in [('drop constraints', lambda m: m['config'].pop('constraints')), ('unknown config key', lambda m: m['config'].__setitem__('evil', 1)),
                   ('format_version=99', lambda m: m.__setitem__('format_version', 99)), ('strand_count str', lambda m: m.__setitem__('strand_count', 'x')),
                   ('config not dict', lambda m: m.__setitem__('config', 5)), ('manifest is list', None)]:
    s, d = fresh('man'+str(abs(hash(label))), data, DatasetConfig())
    mp = d/'manifest.json'; m = json.loads(mp.read_text())
    if mut is None: mp.write_text('[1,2]')
    else: mut(m); mp.write_text(json.dumps(m))
    attempt(f'5 manifest {label}', d)
# 6. header tamper consistent with sequence: change payload_length only
s, d = fresh('hdr', data, DatasetConfig(strand_payload_bytes=256)); recs = read_strands(d/'strands.fasta')
write_strands(d/'strands.fasta', [replace(recs[0], index=10**9)] + recs[1:]); attempt('6 header index out of range', d)
# 7. encrypted manifest leaks plaintext hash / name / size
from vnxdna.crypto.encryption import generate_key
k = generate_key(); s = T/'secret.txt'; s.write_bytes(b'yes'); d = T/'enc'; encode_file(s, d, DatasetConfig(encryption=True), k)
m = json.loads((d/'manifest.json').read_text())
print('7 encrypted manifest exposes: name=%r size=%r sha256==sha256(b"yes"): %s' % (m['original_name'], m['original_size'], m['original_sha256']==hashlib.sha256(b'yes').hexdigest()))
attempt('7b wrong key', d, key=generate_key())
# 8. strand length at defaults
s, d = fresh('len', data, DatasetConfig()); print('8 default strand length (nt):', {len(r.sequence) for r in read_strands(d/'strands.fasta')})
