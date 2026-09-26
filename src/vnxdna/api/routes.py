from fastapi import APIRouter,HTTPException
from .schemas import EncodeRequest,FileOperation,SequenceRequest,ErrorSimulationRequest,ExperimentRequest
from ..core.models import DatasetConfig
from ..core.pipeline import decode_dataset,encode_file,inspect_dataset
from ..simulation.channel import ChannelConfig,simulate
from ..validation.dna import analyze
from ..experiments.runner import run
router=APIRouter()
def operation(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except (OSError,ValueError,KeyError) as error:raise HTTPException(400,str(error)) from error
@router.get('/health')
def health():return {'status':'READY','format_versions':[1,2],'scope':'local computational R&D'}
@router.post('/encode')
def encode(request:EncodeRequest):
    data=request.model_dump();key=data.pop('key');input_path=data.pop('input');output=data.pop('output');config=DatasetConfig(encryption=bool(key),**data);return operation(encode_file,input_path,output,config,key)
@router.post('/decode')
def decode(request:FileOperation):return operation(decode_dataset,request.input,request.output,request.key)
@router.post('/validate-sequence')
def validate(request:SequenceRequest):return analyze(request.sequence)
@router.post('/simulate-errors')
def simulate_errors(request:ErrorSimulationRequest):
    values=request.model_dump();source=values.pop('input');destination=values.pop('output');return operation(simulate,source,destination,ChannelConfig(**values))
@router.get('/dataset/inspect')
def inspect(path:str):return operation(inspect_dataset,path)
@router.post('/experiments/run')
def experiment(request:ExperimentRequest):return operation(run,request.experiment_id)
