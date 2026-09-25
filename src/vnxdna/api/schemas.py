from pydantic import BaseModel, Field
class FileOperation(BaseModel):
    input: str
    output: str
    key: str | None = Field(default=None, repr=False)
class SequenceRequest(BaseModel):
    sequence: str = Field(max_length=1_000_000)
class ErrorSimulationRequest(BaseModel):
    input: str
    output: str
    substitution_rate: float = Field(default=0, ge=0, le=1)
    insertion_rate: float = Field(default=0, ge=0, le=1)
    deletion_rate: float = Field(default=0, ge=0, le=1)
    dropout_rate: float = Field(default=0, ge=0, le=1)
    seed: int = 20260908
class ExperimentRequest(BaseModel):
    experiment_id: str = Field(pattern=r"^E00[1-8]$")
