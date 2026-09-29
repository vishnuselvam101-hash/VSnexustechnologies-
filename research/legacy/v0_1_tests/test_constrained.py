import os
import pytest
from vnxdna.core.models import ConstraintSettings,DatasetConfig
from vnxdna.encoding.constrained import encode_bytes,decode_sequence
from vnxdna.validation.dna import analyze
from vnxdna.core.pipeline import encode_file,decode_dataset
settings=ConstraintSettings(min_gc=40,max_gc=60,max_homopolymer_length=3,max_strand_length=4096)
@pytest.mark.parametrize('data',[b'',b'\0'*20,b'\xff'*20,bytes(range(256)),os.urandom(400)])
def test_constrained_roundtrip_and_constraints(data):
 sequence=encode_bytes(data,settings);assert decode_sequence(sequence,settings)==data;assert analyze(sequence,settings)['valid']
def test_constrained_ecc_roundtrip_and_erasures(tmp_path):
 source=tmp_path/'in';dataset=tmp_path/'d';out=tmp_path/'out';source.write_bytes(os.urandom(1500));config=DatasetConfig(encoding='constrained_v1',ecc='reed_solomon',data_shards=4,parity_shards=2,strand_payload_bytes=128,constraints=settings);encode_file(source,dataset,config)
 from vnxdna.storage.fasta import read_strands,write_strands
 records=read_strands(dataset/'strands.fasta');write_strands(dataset/'strands.fasta',[r for r in records if r.shard_index not in (0,1)]);decode_dataset(dataset,out);assert out.read_bytes()==source.read_bytes()
def test_unachievable_constraints_fail():
 with pytest.raises(Exception):encode_bytes(b'x',ConstraintSettings(min_gc=60,max_gc=80,max_homopolymer_length=3))
