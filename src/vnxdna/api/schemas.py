from pydantic import BaseModel, Field
class FileOperation(BaseModel):
    input: str; output: str; key: str|None=Field(default=None,repr=False)
class EncodeRequest(FileOperation):
    strand_payload_bytes:int=Field(default=512,ge=1,le=1024)
    compression:str=Field(default='zlib',pattern='^(none|zlib|zstandard)$')
    ecc:str=Field(default='none',pattern='^(none|reed_solomon)$')
    encoding:str=Field(default='baseline_binary_2bit',pattern='^(baseline_binary_2bit|constrained_v1)$')
    data_shards:int=Field(default=8,ge=1,le=64)
    parity_shards:int=Field(default=4,ge=1,le=64)
class SequenceRequest(BaseModel):sequence:str=Field(max_length=1_000_000)
class ErrorSimulationRequest(BaseModel):
    input:str;output:str;substitution_probability:float=Field(default=0,ge=0,le=1);insertion_probability:float=Field(default=0,ge=0,le=1);deletion_probability:float=Field(default=0,ge=0,le=1);dropout_probability:float=Field(default=0,ge=0,le=1);reorder:bool=False;duplicate_probability:float=Field(default=0,ge=0,le=1);seed:int=0
class ExperimentRequest(BaseModel):experiment_id:str=Field(pattern=r'^E00[1-8]$')
