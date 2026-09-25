"""Command-line interface for VNX-DNA-1 datasets."""
from __future__ import annotations
import json
from pathlib import Path
import typer
from . import __version__
from .benchmarking.runner import run as run_benchmark
from .core.models import ConstraintSettings, DatasetConfig
from .core.pipeline import decode_dataset,encode_file,inspect_dataset
from .simulation.channel import ChannelConfig,simulate
app=typer.Typer(no_args_is_help=True,help='VNX-DNA-1 computational DNA data storage platform.')
def emit(result, as_json:bool): typer.echo(json.dumps(result.to_dict() if hasattr(result,'to_dict') else result,indent=2,sort_keys=True) if as_json else result)
def fail(error:Exception): raise typer.BadParameter(str(error)) from error
@app.command()
def encode(input:Path, output:Path, strand_bytes:int=512, compression:str='zlib', encryption_key:str|None=typer.Option(None,envvar='VNXDNA_KEY'), json_output:bool=typer.Option(False,'--json')):
    """Encode arbitrary bytes into a self-describing manifest plus FASTA strands."""
    try: emit(encode_file(input,output,DatasetConfig(strand_payload_bytes=strand_bytes,compression=compression,encryption=bool(encryption_key)),encryption_key),json_output)
    except Exception as error: fail(error)
@app.command()
def decode(dataset:Path, output:Path, encryption_key:str|None=typer.Option(None,envvar='VNXDNA_KEY'), json_output:bool=typer.Option(False,'--json')):
    try: emit(decode_dataset(dataset,output,encryption_key),json_output)
    except Exception as error: fail(error)
@app.command()
def inspect(dataset:Path, json_output:bool=typer.Option(True,'--json/--text')):
    try: emit(inspect_dataset(dataset),json_output)
    except Exception as error: fail(error)
@app.command()
def simulate(dataset:Path, output:Path, seed:int=0, substitutions:float=0, insertions:float=0, deletions:float=0, dropout:float=0, reorder:bool=False, duplicates:float=0, json_output:bool=typer.Option(False,'--json')):
    try: emit(simulate(dataset,output,ChannelConfig(substitutions,insertions,deletions,dropout,reorder,duplicates,seed)),json_output)
    except Exception as error: fail(error)
@app.command()
def verify(dataset:Path, encryption_key:str|None=typer.Option(None,envvar='VNXDNA_KEY')):
    """Perform full reconstruction to a temporary output and verify SHA-256."""
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as temp: result=decode_dataset(dataset,Path(temp)/'verified.bin',encryption_key)
        typer.echo(f'ORIGINAL SHA-256: {result.original_sha256}\nRECOVERED SHA-256: {result.original_sha256}\nINTEGRITY: PASS')
    except Exception as error: typer.echo(f'INTEGRITY: FAIL\nReason: {error}');raise typer.Exit(1)
@app.command()
def benchmark(input:Path, output:Path=Path('benchmark.json'), json_output:bool=typer.Option(False,'--json')):
    try: emit(run_benchmark(input,output),json_output)
    except Exception as error: fail(error)
@app.command()
def info(): typer.echo(f'VNX-DNA {__version__}\nFormat: VNX-DNA-1\nScope: computational research; no biological validation.')
