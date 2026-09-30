from __future__ import annotations
from pathlib import Path
from collections.abc import Iterable
from ..core.errors import MetadataError
from ..core.models import STRAND_MAGIC, Strand, EccStrand
Record=Strand|EccStrand
def write_strands(path:Path, strands:Iterable[Record])->None:
    with path.open('w',encoding='ascii',newline='\n') as handle:
        for strand in strands: handle.write('>'+strand.header()+'\n'+strand.sequence+'\n')
def read_strands(path:Path)->list[Record]:
    records=[]; header=None; sequence=[]
    def emit():
        if header is None:return
        fields=header.split('|')
        try:
            if len(fields)==6 and fields[0]==STRAND_MAGIC: records.append(Strand(fields[1],int(fields[2]),int(fields[3]),int(fields[4]),fields[5],''.join(sequence).upper()))
            elif len(fields)==8 and fields[0]=='VNX2': records.append(EccStrand(fields[1],int(fields[2]),int(fields[3]),int(fields[4]),int(fields[5]),int(fields[6]),fields[7],''.join(sequence).upper()))
            else: raise MetadataError('Invalid VNX-DNA FASTA header.')
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
