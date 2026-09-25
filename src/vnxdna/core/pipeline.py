from __future__ import annotations
import hashlib, shutil, uuid
from dataclasses import asdict
from pathlib import Path
from datetime import datetime, timezone
from ..compression.codecs import compress,decompress
from ..core.errors import ECCRecoveryError, IntegrityError, MetadataError, MissingStrandError
from ..core.models import ConstraintSettings, DatasetConfig, EccStrand, OperationResult, Strand
from ..ecc.reed_solomon import ReedSolomonShards
from ..encoding.baseline import encode_bytes,decode_sequence
from ..indexing.index import classify
from ..metadata import manifest as manifest_io
from ..storage.fasta import read_strands,write_strands
from ..validation.dna import analyze,require_valid
from ..crypto.encryption import decrypt,encrypt
def sha256(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def _dataset_paths(directory:Path)->tuple[Path,Path]:return directory/'manifest.json',directory/'strands.fasta'
def _config(raw:dict)->DatasetConfig:
    raw=dict(raw);raw['constraints']=ConstraintSettings(**raw['constraints']);return DatasetConfig(**raw)
def _transform(source:Path,config:DatasetConfig,key:str|None)->tuple[bytes,bytes]:
    original=source.read_bytes(); transformed=compress(original,config.compression,config.compression_level)
    if config.encryption:
        if not key:raise ValueError('Encryption requested but no key was supplied.')
        transformed=encrypt(transformed,key)
    return original,transformed
def encode_file(input_path:str|Path,output_directory:str|Path,config:DatasetConfig=DatasetConfig(),key:str|None=None)->OperationResult:
    source=Path(input_path);destination=Path(output_directory)
    if not source.is_file():raise FileNotFoundError(f'Input file does not exist: {source}')
    if destination.exists() and any(destination.iterdir()):raise FileExistsError(f'Output directory must be new or empty: {destination}')
    original,transformed=_transform(source,config,key);dataset_id=str(uuid.uuid4())
    if config.ecc=='none':
        payloads=[transformed[offset:offset+config.strand_payload_bytes] for offset in range(0,len(transformed),config.strand_payload_bytes)] or [b'']; strands=[Strand(dataset_id,index,len(payloads),len(payload),sha256(payload),encode_bytes(payload)) for index,payload in enumerate(payloads)];version=1;extra={}
    else:
        codec=ReedSolomonShards(config.data_shards,config.parity_shards);stripe_bytes=config.data_shards*config.strand_payload_bytes;strands=[]
        for stripe_index,offset in enumerate(range(0,len(transformed),stripe_bytes) or [0]):
            block=transformed[offset:offset+stripe_bytes]; padded=block.ljust(stripe_bytes,b'\0');data=[padded[i*config.strand_payload_bytes:(i+1)*config.strand_payload_bytes] for i in range(config.data_shards)]
            for shard_index,payload in enumerate(codec.encode(data)):
                sequence=encode_bytes(payload);require_valid(sequence,config.constraints);strands.append(EccStrand(dataset_id,stripe_index,shard_index,config.data_shards,config.parity_shards,len(payload),sha256(payload),sequence))
        version=2;extra={'stripe_count':len(strands)//codec.total_shards,'ecc':{'algorithm':codec.name,'data_shards':config.data_shards,'parity_shards':config.parity_shards,'total_shards':codec.total_shards,'max_erasures_per_stripe':config.parity_shards}}
    for strand in strands:require_valid(strand.sequence,config.constraints)
    destination.mkdir(parents=True,exist_ok=True);manifest={'format':'VNX-DNA','format_version':version,'dataset_id':dataset_id,'created_at':datetime.now(timezone.utc).isoformat(),'original_name':source.name,'original_size':len(original),'original_sha256':sha256(original),'transformed_size':len(transformed),'strand_count':len(strands),'config':asdict(config),'encoding':'two-bit-ACGT-baseline','strand_file':'strands.fasta',**extra};manifest_io.write(destination/'manifest.json',manifest);write_strands(destination/'strands.fasta',strands)
    return OperationResult('success',dataset_id,len(original),len(strands),manifest['original_sha256'],str(destination),None,{'compression_ratio':len(transformed)/len(original) if original else 1.0,**extra})
def _decode_v1(manifest:dict,strands:list[Strand],config:DatasetConfig)->tuple[bytes,dict]:
    selected,status=classify(strands,manifest['strand_count']);missing=[i for i in range(manifest['strand_count']) if i not in selected]
    if missing:raise MissingStrandError(f'Missing {len(missing)} required strand(s): {missing[:10]}')
    payloads=[]
    for index in range(manifest['strand_count']):
        strand=selected[index]
        if strand.dataset_id!=manifest['dataset_id'] or strand.total!=manifest['strand_count']:raise MetadataError(f'Strand {index} dataset metadata mismatch.')
        require_valid(strand.sequence,config.constraints);payload=decode_sequence(strand.sequence)
        if len(payload)!=strand.payload_length or sha256(payload)!=strand.checksum:raise IntegrityError(f'Strand {index} payload checksum mismatch.')
        payloads.append(payload)
    return b''.join(payloads),{'received_strands':len(strands),'recovered_shards':0,'strand_status':{str(k):str(v) for k,v in status.items()}}
def _decode_v2(manifest:dict,strands:list[EccStrand],config:DatasetConfig)->tuple[bytes,dict]:
    ecc=manifest['ecc'];codec=ReedSolomonShards(ecc['data_shards'],ecc['parity_shards']);groups={i:[] for i in range(manifest['stripe_count'])}
    for strand in strands:
        if strand.dataset_id==manifest['dataset_id'] and strand.stripe_index in groups and strand.data_shards==codec.data_shards and strand.parity_shards==codec.parity_shards:groups[strand.stripe_index].append(strand)
    decoded=[];recovered=0;missing=0;corrupted=0
    for stripe_index,group in groups.items():
        by_index={}
        for strand in group:
            if strand.shard_index not in by_index:by_index[strand.shard_index]=strand
        shards=[]
        for shard_index in range(codec.total_shards):
            strand=by_index.get(shard_index)
            if strand is None:shards.append(None);missing+=1;continue
            try:
                require_valid(strand.sequence,config.constraints);payload=decode_sequence(strand.sequence)
                if len(payload)!=strand.payload_length or sha256(payload)!=strand.checksum:raise IntegrityError('checksum')
                shards.append(payload)
            except (IntegrityError,ValueError):shards.append(None);corrupted+=1
        before=sum(x is None for x in shards)
        try:data=codec.decode(shards)
        except ECCRecoveryError as error:raise ECCRecoveryError(f'Stripe {stripe_index} is unrecoverable: {error}') from error
        recovered+=before;decoded.extend(data)
    return b''.join(decoded)[:manifest['transformed_size']],{'received_strands':len(strands),'missing_shards':missing,'corrupted_shards':corrupted,'recovered_shards':recovered,'recoverability':'recovered' if recovered else 'complete'}
def decode_dataset(directory:str|Path,output_path:str|Path,key:str|None=None)->OperationResult:
    root=Path(directory);manifest=manifest_io.read(root/'manifest.json');config=_config(manifest['config']);records=read_strands(root/'strands.fasta')
    if manifest['format_version']==1:
        if config.ecc!='none':raise MetadataError('VNX-DNA-1 datasets must use ecc=none.')
        transformed,details=_decode_v1(manifest,[item for item in records if isinstance(item,Strand)],config)
    else:
        if config.ecc!='reed_solomon':raise MetadataError('VNX-DNA-2 datasets require reed_solomon ECC metadata.')
        transformed,details=_decode_v2(manifest,[item for item in records if isinstance(item,EccStrand)],config)
    if config.encryption:
        if not key:raise ValueError('Dataset is encrypted; a key is required.')
        transformed=decrypt(transformed,key)
    original=decompress(transformed,config.compression);recovered=sha256(original)
    if recovered!=manifest['original_sha256']:raise IntegrityError(f'Integrity verification failed: expected {manifest["original_sha256"]}, received {recovered}.')
    Path(output_path).write_bytes(original);return OperationResult('success',manifest['dataset_id'],manifest['original_size'],manifest['strand_count'],manifest['original_sha256'],str(output_path),True,details)
def inspect_dataset(directory:str|Path)->dict:
    root=Path(directory);manifest=manifest_io.read(root/'manifest.json');records=read_strands(root/'strands.fasta');return {**manifest,'received_strands':len(records),'constraint_reports':[analyze(s.sequence) for s in records[:10]]}
def copy_dataset(source:str|Path,destination:str|Path)->None:
    if Path(destination).exists():raise FileExistsError('Simulation output directory already exists.')
    shutil.copytree(source,destination)
