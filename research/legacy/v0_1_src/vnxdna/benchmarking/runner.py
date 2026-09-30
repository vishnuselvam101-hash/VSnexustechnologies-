from __future__ import annotations
import json,time,tempfile,resource
from pathlib import Path
from ..core.models import DatasetConfig
from ..core.pipeline import decode_dataset,encode_file

def run(input_path:str|Path, output_json:str|Path)->dict:
    source=Path(input_path)
    with tempfile.TemporaryDirectory(prefix='vnxdna-benchmark-') as work:
        dataset=Path(work)/'dataset'; recovered=Path(work)/'recovered.bin'; start=time.perf_counter(); encoded=encode_file(source,dataset,DatasetConfig()); encode_seconds=time.perf_counter()-start; start=time.perf_counter(); decoded=decode_dataset(dataset,recovered); decode_seconds=time.perf_counter()-start
        strand_bases=sum(len(line.strip()) for line in (dataset/'strands.fasta').read_text().splitlines() if not line.startswith('>'))
    size=source.stat().st_size; result={'input_bytes':size,'strand_count':encoded.strand_count,'dna_bases':strand_bases,'dna_bases_per_input_byte':strand_bases/size if size else 0,'encode_seconds':encode_seconds,'decode_seconds':decode_seconds,'encode_mb_per_second':size/1_000_000/encode_seconds if encode_seconds else 0,'decode_mb_per_second':size/1_000_000/decode_seconds if decode_seconds else 0,'max_rss_kb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'integrity_verified':decoded.integrity_verified}
    Path(output_json).write_text(json.dumps(result,indent=2));return result
