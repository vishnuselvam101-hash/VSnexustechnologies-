from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
from ..core.errors import MetadataError
from ..core.models import STRAND_MAGIC, Strand
def write_strands(path:Path, strands:Iterable[Strand])->None:
    with path.open('w',encoding='ascii',newline='\n') as handle:
        for strand in strands: handle.write('>'+strand.header()+'\n'+strand.sequence+'\n')
def read_strands(path:Path)->list[Strand]:
    records=[]; header=None; sequence=[]
    def emit():
        if header is None:return
        fields=header.split('|')
        if len(fields)!=6 or fields[0]!=STRAND_MAGIC: raise MetadataError('Invalid VNX-DNA FASTA header.')
        try: records.append(Strand(fields[1],int(fields[2]),int(fields[3]),int(fields[4]),fields[5],''.join(sequence).upper()))
        except ValueError as error: raise MetadataError('Invalid numeric field in FASTA header.') from error
    try:
        for line in path.read_text(encoding='ascii').splitlines():
            if not line: continue
            if line.startswith('>'): emit();header=line[1:];sequence=[]
            elif header is None: raise MetadataError('FASTA sequence appears before a header.')
            else: sequence.append(line.strip())
        emit()
    except UnicodeDecodeError as error: raise MetadataError('Strand file must be ASCII FASTA.') from error
    return records
