import os
from pathlib import Path
import pytest
from vnxdna import DatasetConfig
from vnxdna.core.errors import ECCRecoveryError
from vnxdna.core.pipeline import decode_dataset,encode_file
from vnxdna.ecc.reed_solomon import ReedSolomonShards
from vnxdna.simulation.channel import ChannelConfig,simulate
from vnxdna.storage.fasta import read_strands,write_strands

def test_reed_solomon_recovers_each_allowed_erasure_pattern():
    codec=ReedSolomonShards(4,2); data=[bytes([i])*10 for i in range(4)];encoded=codec.encode(data)
    for absent in ((0,),(1,4),(4,5)):
        shards=encoded[:]
        for index in absent: shards[index]=None
        assert codec.decode(shards)==data
    with pytest.raises(ECCRecoveryError):codec.decode([None,None,None,*encoded[3:]])
def _dataset(tmp_path):
    source=tmp_path/'input';dataset=tmp_path/'dataset';source.write_bytes(os.urandom(2500));config=DatasetConfig(ecc='reed_solomon',data_shards=4,parity_shards=2,strand_payload_bytes=128);encoded=encode_file(source,dataset,config);return source,dataset,encoded
def test_ecc_dropout_maximum_and_beyond(tmp_path):
    source,dataset,_=_dataset(tmp_path);noisy=tmp_path/'noisy';simulate(dataset,noisy,ChannelConfig(dropout_probability=0,seed=1)); strands=read_strands(noisy/'strands.fasta');write_strands(noisy/'strands.fasta',[s for s in strands if s.shard_index not in (0,1)]);decode_dataset(noisy,tmp_path/'out');assert (tmp_path/'out').read_bytes()==source.read_bytes()
    doomed=tmp_path/'doomed';simulate(dataset,doomed,ChannelConfig());strands=read_strands(doomed/'strands.fasta');write_strands(doomed/'strands.fasta',[s for s in strands if s.shard_index not in (0,1,2)])
    with pytest.raises(ECCRecoveryError):decode_dataset(doomed,tmp_path/'bad')
def test_ecc_corruption_duplicates_reorder_and_dropout(tmp_path):
    source,dataset,_=_dataset(tmp_path);noisy=tmp_path/'noisy';simulate(dataset,noisy,ChannelConfig(reorder=True,duplicate_probability=1,seed=2));strands=read_strands(noisy/'strands.fasta');first=next(s for s in strands if s.shard_index==0);strands.remove(first);write_strands(noisy/'strands.fasta',strands);decode_dataset(noisy,tmp_path/'out');assert (tmp_path/'out').read_bytes()==source.read_bytes()
def test_ecc_base_substitution_becomes_erasure_and_recovers(tmp_path):
    source,dataset,_=_dataset(tmp_path);noisy=tmp_path/'noisy';simulate(dataset,noisy,ChannelConfig(substitution_probability=.0002,seed=3));decode_dataset(noisy,tmp_path/'out');assert (tmp_path/'out').read_bytes()==source.read_bytes()
def test_ecc_checksum_invalid_shard_is_recovered(tmp_path):
    source,dataset,_=_dataset(tmp_path);strands=read_strands(dataset/'strands.fasta');target=next(s for s in strands if s.shard_index==0);from dataclasses import replace
    strands[strands.index(target)]=replace(target,sequence='A'+target.sequence[1:]);write_strands(dataset/'strands.fasta',strands)
    decoded=decode_dataset(dataset,tmp_path/'out');assert decoded.details['recovered_shards']>=1 and (tmp_path/'out').read_bytes()==source.read_bytes()
