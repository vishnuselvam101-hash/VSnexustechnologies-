from __future__ import annotations
from collections import defaultdict
from ..core.models import Strand, StrandStatus
def classify(strands:list[Strand], expected_total:int)->tuple[dict[int,Strand],dict[int,StrandStatus]]:
    groups:dict[int,list[Strand]]=defaultdict(list)
    for strand in strands: groups[strand.index].append(strand)
    selected={}; status={}
    for index, group in groups.items():
        if not 0<=index<expected_total: status[index]=StrandStatus.UNKNOWN; continue
        selected[index]=group[0]; status[index]=StrandStatus.DUPLICATE if len(group)>1 else StrandStatus.VALID
    for index in range(expected_total): status.setdefault(index,StrandStatus.UNRECOVERABLE)
    return selected,status
