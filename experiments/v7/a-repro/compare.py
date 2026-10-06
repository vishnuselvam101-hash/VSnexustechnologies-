"""Compare two result JSONL files on their deterministic fields (timing, load and memory removed).

    python experiments/v7/a-repro/compare.py COMMITTED.jsonl RERUN.jsonl case,arm
"""
import json,sys,hashlib
VOL={"decode_seconds","case_seconds","peak_rss_bytes","load1"}
def load(p,key):
    rows=[json.loads(l) for l in open(p)]
    return {tuple(r[k] for k in key):{k:v for k,v in r.items() if k not in VOL} for r in rows}, rows
key=sys.argv[3].split(",")
a,ra=load(sys.argv[1],key); b,rb=load(sys.argv[2],key)
def h(d): return hashlib.sha256("\n".join(json.dumps(d[k],sort_keys=True) for k in sorted(d,key=str)).encode()).hexdigest()
print("rows",len(a),len(b),"same keys",a.keys()==b.keys())
diff=[k for k in a if k in b and a[k]!=b[k]]
print("differing rows",len(diff), diff[:5])
for k in diff[:3]: print(" ",{f:(a[k][f],b[k][f]) for f in a[k] if a[k][f]!=b[k].get(f)})
print("deterministic-projection sha256 committed",h(a)); print("deterministic-projection sha256 rerun    ",h(b))
print("raw file sha256", hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16], hashlib.sha256(open(sys.argv[2],'rb').read()).hexdigest()[:16])
print("volatile fields excluded:",sorted(VOL & set(ra[0])))
