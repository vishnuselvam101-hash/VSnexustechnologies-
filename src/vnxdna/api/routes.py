from pathlib import Path
from fastapi import APIRouter, HTTPException
from .schemas import FileOperation, SequenceRequest, ErrorSimulationRequest, ExperimentRequest
from ..archive.archive import encode_file, decode_file, simulate_errors
from ..codec.constraints import analyze_sequence
from ..experiments.runner import run
from ..integrity.hashing import calculate_sha256
router = APIRouter()

def _operation(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except (OSError, ValueError, KeyError) as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
@router.get('/health')
def health(): return {'status':'READY','scope':'local computational R&D'}
@router.post('/encode')
def encode(request: FileOperation): return _operation(encode_file, request.input, request.output, key=request.key, encryption=bool(request.key))
@router.post('/decode')
def decode(request: FileOperation): return _operation(decode_file, request.input, request.output, request.key)
@router.post('/verify')
def verify(request: FileOperation): return _operation(lambda: {'integrity': 'PASS' if calculate_sha256(request.input) == calculate_sha256(request.output) else 'FAIL'})
@router.post('/validate-sequence')
def validate(request: SequenceRequest): return analyze_sequence(request.sequence)
@router.post('/simulate-errors')
def simulate(request: ErrorSimulationRequest): return _operation(simulate_errors, **request.model_dump())
@router.post('/experiments/run')
def run_experiment(request: ExperimentRequest): return _operation(run, request.experiment_id)
@router.get('/experiments')
def experiments(): return {'available': [f'E{i:03d}' for i in range(1,9)]}
@router.get('/metrics')
def metrics(): return {'status': 'Run experiments to generate metrics in results/raw and results/processed.'}
