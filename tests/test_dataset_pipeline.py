import os
from pathlib import Path
import pytest
from vnxdna import DatasetConfig
from vnxdna.core.errors import IntegrityError, MissingStrandError
from vnxdna.core.pipeline import decode_dataset,encode_file
from vnxdna.simulation.channel import ChannelConfig,simulate
from vnxdna.storage.fasta import read_strands,write_strands
@pytest.mark.parametrize('data',[b'',b'x',os.urandom(513),('ΔNA'.encode()*150)])
def test_dataset_roundtrip(tmp_path,data):
    source=tmp_path/'input'; target=tmp_path/'dna'; output=tmp_path/'output';source.write_bytes(data)
    result=encode_file(source,target,DatasetConfig(strand_payload_bytes=128)); decoded=decode_dataset(target,output)
    assert output.read_bytes()==data and decoded.integrity_verified and result.strand_count>=1
def test_reordered_and_duplicate_observations_recover(tmp_path):
    source=tmp_path/'input'; target=tmp_path/'dna'; noisy=tmp_path/'noisy'; output=tmp_path/'out';source.write_bytes(os.urandom(500))
    encode_file(source,target,DatasetConfig(strand_payload_bytes=100)); report=simulate(target,noisy,ChannelConfig(reorder=True,duplicate_probability=1,seed=8));decode_dataset(noisy,output)
    assert output.read_bytes()==source.read_bytes() and report['duplicates']>0
def test_dropout_is_explicit(tmp_path):
    source=tmp_path/'input'; target=tmp_path/'dna'; noisy=tmp_path/'noisy';source.write_bytes(os.urandom(500));encode_file(source,target,DatasetConfig(strand_payload_bytes=100));simulate(target,noisy,ChannelConfig(dropout_probability=1,seed=8))
    with pytest.raises(MissingStrandError):decode_dataset(noisy,tmp_path/'out')
def test_tampered_strand_is_rejected(tmp_path):
    source=tmp_path/'input'; target=tmp_path/'dna';source.write_bytes(b'abc'*100);encode_file(source,target,DatasetConfig(strand_payload_bytes=100));strands=read_strands(target/'strands.fasta');strands[0]=strands[0].__class__(**{**strands[0].__dict__,'sequence':'A'+strands[0].sequence[1:]});write_strands(target/'strands.fasta',strands)
    with pytest.raises(IntegrityError):decode_dataset(target,tmp_path/'out')
def test_acceptance_report(tmp_path,capfd):
    """Authoritative zero-error acceptance: PASS only after hash-verified reconstruction."""
    source=tmp_path/'input.bin'; dataset=tmp_path/'dataset'; recovered=tmp_path/'recovered.bin';source.write_bytes(os.urandom(4096)); encoded=encode_file(source,dataset,DatasetConfig(strand_payload_bytes=256)); decoded=decode_dataset(dataset,recovered)
    print(f'VNX-DNA ACCEPTANCE TEST\nInput bytes: 4096\nDNA strands: {encoded.strand_count}\nErrors introduced: 0\nOriginal SHA-256: {encoded.original_sha256}\nRecovered SHA-256: {decoded.original_sha256}\nRESULT: {"PASS" if decoded.integrity_verified else "FAIL"}')
    assert decoded.integrity_verified and source.read_bytes()==recovered.read_bytes()
