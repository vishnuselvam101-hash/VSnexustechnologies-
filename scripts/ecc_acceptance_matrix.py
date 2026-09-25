"""Execute measured Reed-Solomon shard-erasure acceptance cases."""
import json, os, sys, tempfile
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vnxdna import DatasetConfig
from vnxdna.core.pipeline import decode_dataset,encode_file
from vnxdna.simulation.channel import ChannelConfig,simulate
from vnxdna.storage.fasta import read_strands,write_strands

def case(name, mutate):
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);source=root/'input';dataset=root/'dataset';output=root/'output';source.write_bytes(os.urandom(4096));config=DatasetConfig(ecc='reed_solomon',data_shards=4,parity_shards=2,strand_payload_bytes=128);encoded=encode_file(source,dataset,config);mutate(dataset)
        try:
            decoded=decode_dataset(dataset,output);passed=output.read_bytes()==source.read_bytes() and decoded.integrity_verified;details=decoded.details
        except Exception as error:passed=False;details={'error':str(error)}
        return {'case':name,'data_shards':4,'parity_shards':2,'original_sha256':encoded.original_sha256,'recovery_status':'PASS' if passed else 'FAIL','details':details}
def drop(indices):
    return lambda path:write_strands(path/'strands.fasta',[s for s in read_strands(path/'strands.fasta') if s.shard_index not in indices])
def corrupt(index):
    def apply(path):
        records=read_strands(path/'strands.fasta');record=next(s for s in records if s.shard_index==index);records[records.index(record)]=replace(record,sequence='A'+record.sequence[1:]);write_strands(path/'strands.fasta',records)
    return apply
def reorder(path):
    records=read_strands(path/'strands.fasta');write_strands(path/'strands.fasta',list(reversed(records)))
def duplicate(path):
    records=read_strands(path/'strands.fasta');write_strands(path/'strands.fasta',records+[records[0]])
results=[case('ecc_no_error',lambda _:None),case('dropout_1',drop([0])),case('dropout_2_maximum',drop([0,1])),case('dropout_3_beyond_capacity',drop([0,1,2])),case('corrupt_data',corrupt(0)),case('corrupt_parity',corrupt(4)),case('reversed_order',reorder),case('duplicate_data',duplicate),case('dropout_plus_reorder',lambda p:(drop([0])(p),reorder(p))),case('corruption_plus_dropout',lambda p:(corrupt(0)(p),drop([1])(p)))]
print(json.dumps(results,indent=2));
if any(item['recovery_status']=='FAIL' for item in results if item['case']!='dropout_3_beyond_capacity'):raise SystemExit(1)
if next(item for item in results if item['case']=='dropout_3_beyond_capacity')['recovery_status']!='FAIL':raise SystemExit(1)
