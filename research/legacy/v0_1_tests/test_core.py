import os
from pathlib import Path
import pytest
from vnxdna.codec.binary import bytes_to_bits,bits_to_bytes
from vnxdna.codec.dna_mapping import bits_to_dna,dna_to_bits
from vnxdna.codec.constraints import analyze_sequence,ConstraintConfig
from vnxdna.archive.chunking import chunk_bytes
from vnxdna.archive.archive import encode_file,decode_file,inspect,simulate_errors,create_archive,add_file,retrieve_file
from vnxdna.crypto.encryption import generate_key, Fernet
from vnxdna.ecc.reed_solomon import ReedSolomonECC, RSCodec
from vnxdna.errors.substitutions import apply
from vnxdna.integrity.hashing import calculate_bytes_sha256,verify_integrity
@pytest.mark.parametrize('data',[b'',b'\0',bytes(range(256)),os.urandom(211)])
def test_binary_dna_roundtrip(data): assert bits_to_bytes(dna_to_bits(bits_to_dna(bytes_to_bits(data))))==data
def test_invalid_bits_and_dna():
 with pytest.raises(ValueError):bits_to_bytes('1')
 with pytest.raises(ValueError):dna_to_bits('AU')
def test_constraints():
 r=analyze_sequence('AACCGGTT',ConstraintConfig(max_homopolymer=2));assert r['valid'] and r['gc_percent']==50
 assert not analyze_sequence('AAAA',ConstraintConfig(max_homopolymer=2))['valid']
def test_chunking():
 c=chunk_bytes(b'abcdef', 'a','f',2);assert [x.payload for x in c]==[b'ab',b'cd',b'ef']
@pytest.mark.skipif(RSCodec is None, reason='reedsolo unavailable in test environment')
def test_ecc_corrects_small_errors():
 e=ReedSolomonECC(16); encoded=bytearray(e.encode(b'hello'));encoded[0]^=1;assert e.decode(bytes(encoded))==b'hello'
def test_substitutions_reproducible():assert apply('ACGT'*10,.2,3)==apply('ACGT'*10,.2,3)
def test_hashing():assert verify_integrity(b'x',b'x') and calculate_bytes_sha256(b'x')!=calculate_bytes_sha256(b'y')
def test_archive_roundtrip(tmp_path):
 src=tmp_path/'input.bin';arc=tmp_path/'a.vnxdna';out=tmp_path/'out.bin';src.write_bytes(os.urandom(500));encode_file(src,arc,ecc='none');assert decode_file(arc,out)['integrity']=='PASS';assert src.read_bytes()==out.read_bytes();assert inspect(arc)['manifest']['format']=='VNXDNA'
@pytest.mark.skipif(Fernet is None, reason='cryptography unavailable in test environment')
def test_encrypted_roundtrip(tmp_path):
 src=tmp_path/'in';arc=tmp_path/'a';out=tmp_path/'out';src.write_bytes(b'repeated'*100);key=generate_key();encode_file(src,arc,key=key,encryption=True);decode_file(arc,out,key);assert out.read_bytes()==src.read_bytes()
def test_corruption_fails_safely(tmp_path):
 src=tmp_path/'in';arc=tmp_path/'a';out=tmp_path/'out';src.write_bytes(b'a'*50);encode_file(src,arc,ecc='none');simulate_errors(arc,arc,substitution_rate=.1,seed=1)
 with pytest.raises(ValueError):decode_file(arc,out)
def test_multiple_file_index(tmp_path):
 arc=tmp_path/'a';create_archive(arc,ecc='none');one=tmp_path/'one';two=tmp_path/'two';one.write_bytes(b'1');two.write_bytes(b'2');add_file(arc,one,'ONE');add_file(arc,two,'TWO');data,m=retrieve_file(arc,'TWO');assert data==b'2' and m['chunks_selected']==1
def test_missing_chunk_fails_safely(tmp_path):
    src=tmp_path/'in'; arc=tmp_path/'a'; out=tmp_path/'out'; src.write_bytes(b'v'*300)
    encode_file(src,arc,ecc='none')
    simulate_errors(arc,arc,dropout_rate=1.0,seed=4)
    with pytest.raises(ValueError, match='missing'):
        decode_file(arc,out)
def test_registry(tmp_path):
    from vnxdna.storage.database import ArchiveRegistry
    registry=ArchiveRegistry(tmp_path/'metadata.sqlite')
    registry.upsert({'archive_id':'id','created_at':'now','files':[{'original_size':3}]}, tmp_path/'a')
    assert registry.get('id')['logical_bytes']==3
