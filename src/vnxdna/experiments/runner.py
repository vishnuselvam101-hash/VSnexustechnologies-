import csv,json,time,os
from pathlib import Path
from ..archive.archive import encode_file,decode_file,simulate_errors
from ..integrity.hashing import calculate_sha256
from ..ecc.reed_solomon import RSCodec
DEFAULT_ECC = 'reed_solomon' if RSCodec is not None else 'none'
from .reproducibility import environment
SIZES={'E001':[1024,10_240,102_400], 'E008':[1024,10_240,102_400,1_048_576]}
def run(experiment_id:str, results_dir:str='results')->dict:
 experiment_id=experiment_id.upper(); root=Path(results_dir); (root/'raw').mkdir(parents=True,exist_ok=True); rows=[]
 if experiment_id=='E001': cases=[{'size':x,'ecc':DEFAULT_ECC,'rate':0} for x in SIZES['E001']]
 elif experiment_id=='E002': cases=[{'size':1024,'ecc':e,'rate':r} for e in (('none','reed_solomon') if RSCodec is not None else ('none',)) for r in (0,.001,.005,.01,.02,.05)]
 elif experiment_id=='E003': cases=[{'size':1024,'ecc':DEFAULT_ECC,'rate':.001,'indel':x} for x in ('insertion','deletion','mixed')]
 elif experiment_id=='E004': cases=[{'size':1024,'ecc':DEFAULT_ECC,'rate':r,'dropout':True} for r in (0,.01,.02,.05,.1,.2)]
 elif experiment_id=='E005': cases=[{'size':1024,'ecc':x,'rate':.001} for x in (('none','reed_solomon') if RSCodec is not None else ('none',))]
 elif experiment_id=='E006': cases=[{'size':1024,'ecc':'none','rate':0,'chunk_size':x} for x in (128,256,512,1024,2048,4096)]
 elif experiment_id=='E007': cases=[{'size':256,'ecc':'none','rate':0}]
 elif experiment_id=='E008': cases=[{'size':x,'ecc':'none','rate':0} for x in SIZES['E008']]
 else: raise ValueError('Unknown experiment ID.')
 for i,c in enumerate(cases):
  inp=root/f'.{experiment_id}_{i}.bin'; arc=root/f'.{experiment_id}_{i}.vnxdna'; out=root/f'.{experiment_id}_{i}.out'; inp.write_bytes(bytes((j*31+i)%256 for j in range(c['size']))); start=time.perf_counter()
  try:
   encode_file(inp,arc,ecc=c['ecc'],chunk_size=min(c.get('chunk_size',128),239));
   if c.get('dropout'): simulate_errors(arc,arc,dropout_rate=c['rate'],seed=20260908)
   elif c.get('indel'): simulate_errors(arc,arc,**{c['indel']+'_rate':c['rate']},seed=20260908)
   elif c['rate']:simulate_errors(arc,arc,substitution_rate=c['rate'],seed=20260908)
   decode_file(arc,out); ok=calculate_sha256(inp)==calculate_sha256(out); error=None
  except Exception as e: ok=False;error=str(e)
  rows.append({**c,'case':i,'success':ok,'runtime_seconds':time.perf_counter()-start,'error':error,'seed':20260908})
  for p in (inp,arc,out):p.unlink(missing_ok=True)
 result={'experiment_id':experiment_id,'timestamp':time.time(),'environment':environment(),'cases':rows,'summary':{'successful_cases':sum(r['success'] for r in rows),'total_cases':len(rows)}}
 (root/'raw'/f'{experiment_id}.json').write_text(json.dumps(result,indent=2));
 with (root/'processed'/f'{experiment_id}.csv').open('w',newline='') as f: csv.DictWriter(f,fieldnames=rows[0].keys()).writeheader();csv.DictWriter(f,fieldnames=rows[0].keys()).writerows(rows)
 return result
