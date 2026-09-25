import json,time
from pathlib import Path
import typer
from .config import VERSION,ARCHIVE_FORMAT,CODEC_VERSION
from .archive.archive import encode_file,decode_file,inspect,simulate_errors,create_archive,add_file,retrieve_file
from .codec.constraints import analyze_sequence
from .integrity.hashing import calculate_sha256
from .experiments.runner import run
app=typer.Typer(help='VNX-DNA R&D-1 computational DNA storage research platform.')
archive_app=typer.Typer(); experiment_app=typer.Typer(); app.add_typer(archive_app,name='archive');app.add_typer(experiment_app,name='experiment')
def emit(value,json_output): typer.echo(json.dumps(value,indent=2) if json_output else value if isinstance(value,str) else '\n'.join(f'{k}: {v}' for k,v in value.items()))
@app.command()
def info(json_output:bool=typer.Option(False,'--json')):emit({'version':VERSION,'codec_version':CODEC_VERSION,'archive_format':ARCHIVE_FORMAT,'scope':'Computational R&D only; not biological validation.'},json_output)
@app.command()
def encode(input:Path,output:Path,chunk_size:int=128,ecc:str='none',compression:bool=True,key:str|None=None): emit(encode_file(input,output,chunk_size=chunk_size,ecc=ecc,compression=compression,encryption=bool(key),key=key),False)
@app.command()
def decode(input:Path,output:Path,key:str|None=None):emit(decode_file(input,output,key),False)
@app.command()
def verify(original:Path,recovered:Path):
 a,b=calculate_sha256(original),calculate_sha256(recovered);typer.echo(f'ORIGINAL SHA-256: {a}\nRECOVERED SHA-256: {b}\nINTEGRITY: {"PASS" if a==b else "FAIL"}');raise typer.Exit(0 if a==b else 1)
@app.command()
def inspect_archive(archive:Path,json_output:bool=typer.Option(False,'--json')):emit(inspect(archive)['manifest'],json_output)
@app.command('inspect')
def inspect_alias(archive:Path,json_output:bool=typer.Option(False,'--json')):inspect_archive(archive,json_output)
@app.command('validate-sequence')
def validate_sequence(sequence:str,json_output:bool=typer.Option(False,'--json')):emit(analyze_sequence(sequence),json_output)
@app.command('simulate-errors')
def simulate(input:Path,output:Path,substitution_rate:float=0,insertion_rate:float=0,deletion_rate:float=0,dropout_rate:float=0,seed:int=20260908):emit(simulate_errors(input,output,substitution_rate,insertion_rate,deletion_rate,dropout_rate,seed),False)
@app.command()
def benchmark():
 r=run('E001');emit({'benchmark':'E001 perfect-channel smoke benchmark','successful_cases':r['summary']['successful_cases'],'total_cases':r['summary']['total_cases']},False)
@experiment_app.command('run')
def experiment_run(experiment_id:str):emit(run(experiment_id),False)
@archive_app.command('create')
def archive_create(archive:Path,chunk_size:int=128,ecc:str='none'):emit(create_archive(archive,chunk_size,ecc),False)
@archive_app.command('add')
def archive_add(archive:Path,input:Path,file_id:str|None=None,key:str|None=None):emit(add_file(archive,input,file_id,key),False)
@archive_app.command('list')
def archive_list(archive:Path):emit({'files':inspect(archive)['manifest']['files']},False)
@archive_app.command('retrieve')
def archive_retrieve(archive:Path,file_id:str,output:Path,key:str|None=None): data,m=retrieve_file(archive,file_id,key);output.write_bytes(data);emit(m,False)
@archive_app.command('verify')
def archive_verify(archive:Path):
 a=inspect(archive);emit({'archive_id':a['manifest']['archive_id'],'files':len(a['files']),'status':'metadata readable'},False)
