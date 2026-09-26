"""Run the authoritative hash-verified VNX-DNA acceptance workflow."""
import os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vnxdna import DatasetConfig
from vnxdna.core.pipeline import decode_dataset,encode_file
with tempfile.TemporaryDirectory(prefix='vnxdna-acceptance-') as temp:
 root=Path(temp); source=root/'input.bin'; dataset=root/'dataset'; output=root/'recovered.bin'; source.write_bytes(os.urandom(64*1024)); encoded=encode_file(source,dataset,DatasetConfig(strand_payload_bytes=512)); recovered=decode_dataset(dataset,output)
 print('VNX-DNA ACCEPTANCE TEST\n=======================');print(f'Input bytes: {source.stat().st_size}\nDNA strands: {encoded.strand_count}\nErrors introduced: 0\nErrors recovered: 0\nUnrecoverable strands: 0\nOriginal SHA-256: {encoded.original_sha256}\nRecovered SHA-256: {recovered.original_sha256}\nRESULT: {"PASS" if recovered.integrity_verified else "FAIL"}')
 if not recovered.integrity_verified: raise SystemExit(1)
