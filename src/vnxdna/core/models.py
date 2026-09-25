from __future__ import annotations
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
FORMAT_MAGIC='VNX-DNA'; FORMAT_VERSION=1; STRAND_MAGIC='VNX1'
class StrandStatus(StrEnum): VALID='VALID'; CORRUPTED='CORRUPTED'; RECOVERED='RECOVERED'; UNRECOVERABLE='UNRECOVERABLE'; DUPLICATE='DUPLICATE'; UNKNOWN='UNKNOWN'
@dataclass(frozen=True)
class ConstraintSettings:
    min_gc: float=0.0; max_gc: float=100.0; max_homopolymer_length: int|None=None; forbidden_motifs: tuple[str,...]=(); max_strand_length: int=4096
@dataclass(frozen=True)
class DatasetConfig:
    strand_payload_bytes: int=512; compression: str='zlib'; compression_level: int=6; encryption: bool=False; ecc: str='none'; constraints: ConstraintSettings=field(default_factory=ConstraintSettings)
    def __post_init__(self):
        if not 1 <= self.strand_payload_bytes <= 1_000_000: raise ValueError('strand_payload_bytes must be between 1 and 1,000,000')
        if self.compression not in ('none','zlib','zstandard'): raise ValueError('unsupported compression codec')
        if self.ecc != 'none': raise ValueError('only ecc=none is currently supported by the VNX-DNA-1 strand format')
@dataclass(frozen=True)
class Strand:
    dataset_id: str; index: int; total: int; payload_length: int; checksum: str; sequence: str
    def header(self)->str:return f'{STRAND_MAGIC}|{self.dataset_id}|{self.index}|{self.total}|{self.payload_length}|{self.checksum}'
@dataclass
class OperationResult:
    status:str; dataset_id:str; input_size:int; strand_count:int; original_sha256:str; output_path:str; integrity_verified:bool|None=None; details:dict[str,Any]=field(default_factory=dict)
    def to_dict(self)->dict[str,Any]:return asdict(self)
