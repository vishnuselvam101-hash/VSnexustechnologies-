from fastapi import FastAPI,HTTPException
from pydantic import BaseModel
from pathlib import Path
from ..archive.archive import encode_file,decode_file,simulate_errors,inspect
from ..codec.constraints import analyze_sequence
from ..integrity.hashing import calculate_sha256
app=FastAPI(title='VNX-DNA R&D-1',version='0.1.0')
class FileOperation(BaseModel): input:str; output:str; key:str|None=None
class SequenceRequest(BaseModel): sequence:str
@app.get('/health')
def health():return {'status':'READY','scope':'local computational R&D'}
@app.post('/encode')
def encode(r:FileOperation):return encode_file(r.input,r.output,key=r.key,encryption=bool(r.key))
@app.post('/decode')
def decode(r:FileOperation):return decode_file(r.input,r.output,r.key)
@app.post('/verify')
def verify(r:FileOperation):return {'integrity':'PASS' if calculate_sha256(r.input)==calculate_sha256(r.output) else 'FAIL'}
@app.post('/validate-sequence')
def validate(r:SequenceRequest):return analyze_sequence(r.sequence)
@app.get('/archive/{archive_id}')
def archive(archive_id:str):raise HTTPException(404,'Archive lookup requires a local archive path; persistent registry is not configured.')
@app.get('/')
def dashboard():return {'application':'VNX-DNA R&D-1','status':'READY','dashboard':'Use /docs for the local API dashboard.'}
