from pydantic import BaseModel, Field
from typing import Any
class FileManifest(BaseModel):
 file_id:str; name:str; original_size:int; sha256:str; transformed_size:int; chunks:int
class ArchiveManifest(BaseModel):
 format:str="VNXDNA"; format_version:str="0.1"; archive_id:str; codec_version:str="VNX-CODEC-0.1"; created_at:str; chunk_size:int; ecc:dict[str,Any]; compression:dict[str,Any]; encryption:dict[str,Any]; experiment_seed:int|None=None; files:list[FileManifest]=Field(default_factory=list)
