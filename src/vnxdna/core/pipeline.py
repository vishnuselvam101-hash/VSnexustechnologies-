from __future__ import annotations
import hashlib, shutil, uuid
from dataclasses import asdict
from pathlib import Path
from datetime import datetime, timezone
from ..compression.codecs import compress,decompress
from ..core.errors import IntegrityError, MetadataError, MissingStrandError
from ..core.models import DatasetConfig, OperationResult, Strand
from ..encoding.baseline import encode_bytes,decode_sequence
from ..indexing.index import classify
from ..metadata import manifest as manifest_io
from ..storage.fasta import read_strands,write_strands
from ..validation.dna import analyze,require_valid
from ..crypto.encryption import decrypt,encrypt

def sha256(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def _dataset_paths(directory:Path)->tuple[Path,Path]:return directory/'manifest.json',directory/'strands.fasta'
def encode_file(input_path:str|Path, output_directory:str|Path, config:DatasetConfig=DatasetConfig(), key:str|None=None)->OperationResult:
    source=Path(input_path); destination=Path(output_directory)
    if not source.is_file(): raise FileNotFoundError(f'Input file does not exist: {source}')
    if destination.exists() and any(destination.iterdir()): raise FileExistsError(f'Output directory must be new or empty: {destination}')
    if config.encryption and not key: raise ValueError('Encryption requested but no key was supplied.')
    original=source.read_bytes(); transformed=compress(original,config.compression,config.compression_level)
    if config.encryption: transformed=encrypt(transformed,key or '')
    dataset_id=str(uuid.uuid4()); payloads=[transformed[offset:offset+config.strand_payload_bytes] for offset in range(0,len(transformed),config.strand_payload_bytes)] or [b'']
    total=len(payloads); strands=[]
    for index,payload in enumerate(payloads):
        sequence=encode_bytes(payload); require_valid(sequence,config.constraints)
        strands.append(Strand(dataset_id,index,total,len(payload),sha256(payload),sequence))
    destination.mkdir(parents=True,exist_ok=True); manifest_path,strands_path=_dataset_paths(destination)
    manifest={'format':'VNX-DNA','format_version':1,'dataset_id':dataset_id,'created_at':datetime.now(timezone.utc).isoformat(),'original_name':source.name,'original_size':len(original),'original_sha256':sha256(original),'transformed_size':len(transformed),'strand_count':total,'config':asdict(config),'encoding':'two-bit-ACGT-baseline','strand_file':'strands.fasta'}
    manifest_io.write(manifest_path,manifest); write_strands(strands_path,strands)
    return OperationResult('success',dataset_id,len(original),total,manifest['original_sha256'],str(destination),None,{'compression_ratio':len(transformed)/len(original) if original else 1.0})
def decode_dataset(directory:str|Path, output_path:str|Path, key:str|None=None)->OperationResult:
    root=Path(directory); manifest_path,strands_path=_dataset_paths(root); manifest=manifest_io.read(manifest_path); config=DatasetConfig(**{**manifest['config'],'constraints': __import__('vnxdna.core.models',fromlist=['ConstraintSettings']).ConstraintSettings(**manifest['config']['constraints'])})
    strands=read_strands(strands_path); selected,status=classify(strands,manifest['strand_count']); missing=[i for i in range(manifest['strand_count']) if i not in selected]
    if missing: raise MissingStrandError(f'Missing {len(missing)} required strand(s): {missing[:10]}')
    payloads=[]
    for index in range(manifest['strand_count']):
        strand=selected[index]
        if strand.dataset_id!=manifest['dataset_id'] or strand.total!=manifest['strand_count']: raise MetadataError(f'Strand {index} dataset metadata mismatch.')
        require_valid(strand.sequence,config.constraints); payload=decode_sequence(strand.sequence)
        if len(payload)!=strand.payload_length or sha256(payload)!=strand.checksum: raise IntegrityError(f'Strand {index} payload checksum mismatch.')
        payloads.append(payload)
    transformed=b''.join(payloads)
    if config.encryption:
        if not key: raise ValueError('Dataset is encrypted; a key is required.')
        transformed=decrypt(transformed,key)
    original=decompress(transformed,config.compression)
    recovered=sha256(original)
    if recovered!=manifest['original_sha256']: raise IntegrityError(f'Integrity verification failed: expected {manifest["original_sha256"]}, received {recovered}.')
    Path(output_path).write_bytes(original)
    return OperationResult('success',manifest['dataset_id'],manifest['original_size'],manifest['strand_count'],manifest['original_sha256'],str(output_path),True,{'received_strands':len(strands),'strand_status':{str(k):str(v) for k,v in status.items()}})
def inspect_dataset(directory:str|Path)->dict:
    root=Path(directory); manifest=manifest_io.read(root/'manifest.json'); strands=read_strands(root/'strands.fasta')
    return {**manifest,'received_strands':len(strands),'constraint_reports':[analyze(s.sequence) for s in strands[:10]]}
def copy_dataset(source:str|Path,destination:str|Path)->None:
    if Path(destination).exists(): raise FileExistsError('Simulation output directory already exists.')
    shutil.copytree(source,destination)
