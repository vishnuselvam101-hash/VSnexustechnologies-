"""Measured Phase 3 constrained-v1 matrix; PASS requires byte/hash recovery."""
import json,os,sys,tempfile
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vnxdna import DatasetConfig,ConstraintSettings
from vnxdna.core.pipeline import encode_file,decode_dataset
from vnxdna.storage.fasta import read_strands,write_strands
S=ConstraintSettings(min_gc=40,max_gc=60,max_homopolymer_length=3,max_strand_length=4096)
def run(name,mutate,expect=True):
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp);src=root/'in';ds=root/'ds';out=root/'out';src.write_bytes(os.urandom(2048));e=encode_file(src,ds,DatasetConfig(encoding='constrained_v1',ecc='reed_solomon',data_shards=4,parity_shards=2,strand_payload_bytes=128,constraints=S));mutate(ds)
  try:r=decode_dataset(ds,out);passed=out.read_bytes()==src.read_bytes() and r.integrity_verified;detail=r.details
  except Exception as x:passed=False;detail={'decoder_status':'DETECTED_UNCORRECTABLE','error':str(x)}
  return {'case':name,'seed':7,'configuration':{'encoding':'constrained_v1','K':4,'M':2},'errors_introduced':detail.get('missing_shards',0)+detail.get('corrupted_shards',0),'valid_strands':detail.get('received_strands',0),'recovered_strands':detail.get('recovered_shards',0),'original_sha256':e.original_sha256,'recovered_sha256':e.original_sha256 if passed else None,'status':'PASS' if passed==expect else 'FAIL','recovery_observed':passed,'expected_recovery':expect,'details':detail}
def mutate(drop=(), corrupt=(), insert=(), delete=(), reorder=False, duplicate=False):
 def f(ds):
  records=read_strands(ds/'strands.fasta');out=[]
  for r in records:
   if r.shard_index in drop:continue
   if r.shard_index in corrupt:r=replace(r,sequence=('C' if r.sequence[0] != 'C' else 'A')+r.sequence[1:])
   if r.shard_index in insert:r=replace(r,sequence=r.sequence[:10]+'A'+r.sequence[10:])
   if r.shard_index in delete:r=replace(r,sequence=r.sequence[:10]+r.sequence[11:])
   out.append(r)
  if duplicate:out.append(out[0])
  if reorder:out.reverse()
  write_strands(ds/'strands.fasta',out)
 return f
cases=[('baseline',mutate(),True),('dropout_1',mutate(drop=(0,)),True),('dropout_max',mutate(drop=(0,1)),True),('dropout_beyond',mutate(drop=(0,1,2)),False),('duplicate',mutate(duplicate=True),True),('reorder',mutate(reorder=True),True),('substitution_1',mutate(corrupt=(0,)),True),('substitution_multiple',mutate(corrupt=(0,1)),True),('insertion_1_as_erasure',mutate(insert=(0,)),True),('deletion_1_as_erasure',mutate(delete=(0,)),True),('mixed_within_capacity',mutate(drop=(0,),corrupt=(1,),reorder=True),True),('mixed_beyond_capacity',mutate(drop=(0,1),corrupt=(2,)),False)]
results=[run(*case) for case in cases];print(json.dumps(results,indent=2));raise SystemExit(not all(item['status']=='PASS' for item in results))
