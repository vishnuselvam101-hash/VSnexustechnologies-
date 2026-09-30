"""Systematic Reed-Solomon erasure coding over GF(256).

This is an independently implemented Vandermonde-matrix erasure codec, not a
wrapper around byte-oriented `reedsolo`. It corrects up to M *known erasures*
per stripe (missing/checksum-invalid shards); it does not correct indels.
"""
from __future__ import annotations
from ..core.errors import ECCRecoveryError
_EXP=[0]*512; _LOG=[0]*256
x=1
for i in range(255):
    _EXP[i]=x; _LOG[x]=i; x<<=1
    if x&0x100:x^=0x11D
for i in range(255,512):_EXP[i]=_EXP[i-255]
def _mul(a:int,b:int)->int:return 0 if not a or not b else _EXP[_LOG[a]+_LOG[b]]
def _pow(a:int,n:int)->int:return 1 if n==0 else _EXP[(_LOG[a]*n)%255]
def _invert(matrix:list[list[int]])->list[list[int]]:
    n=len(matrix); work=[row[:] + [int(i==j) for j in range(n)] for i,row in enumerate(matrix)]
    for col in range(n):
        pivot=next((row for row in range(col,n) if work[row][col]),None)
        if pivot is None: raise ECCRecoveryError('Available shards are not linearly independent.')
        work[col],work[pivot]=work[pivot],work[col]; inverse=_EXP[255-_LOG[work[col][col]]]
        work[col]=[_mul(value,inverse) for value in work[col]]
        for row in range(n):
            if row!=col and work[row][col]:
                factor=work[row][col]; work[row]=[value ^ _mul(factor,base) for value,base in zip(work[row],work[col])]
    return [row[n:] for row in work]
def _linear(rows:list[list[int]], shards:list[bytes])->list[bytes]:
    length=len(shards[0]); result=[]
    for coefficients in rows:
        out=bytearray(length)
        for coefficient,shard in zip(coefficients,shards):
            if coefficient:
                for i,value in enumerate(shard):out[i]^=_mul(coefficient,value)
        result.append(bytes(out))
    return result
class ReedSolomonShards:
    name='reed_solomon_vandermonde_gf256'
    def __init__(self,data_shards:int,parity_shards:int):
        if not 1<=data_shards<=64 or not 1<=parity_shards<=64 or data_shards+parity_shards>255: raise ValueError('Require 1..64 data/parity shards and at most 255 total shards.')
        self.data_shards=data_shards;self.parity_shards=parity_shards
        self.generator=[[int(row==col) for col in range(data_shards)] for row in range(data_shards)] + [[_pow(parity+1,column) for column in range(data_shards)] for parity in range(parity_shards)]
    @property
    def total_shards(self)->int:return self.data_shards+self.parity_shards
    def encode(self,data_shards:list[bytes])->list[bytes]:
        if len(data_shards)!=self.data_shards: raise ValueError('Unexpected data shard count.')
        if len({len(item) for item in data_shards})!=1: raise ValueError('Data shards must have equal lengths.')
        return data_shards+_linear(self.generator[self.data_shards:],data_shards)
    def recover(self,shards:list[bytes|None])->list[bytes]:
        if len(shards)!=self.total_shards: raise ValueError('Unexpected total shard count.')
        available=[index for index,item in enumerate(shards) if item is not None]
        if len(available)<self.data_shards: raise ECCRecoveryError(f'Insufficient shards: need {self.data_shards}, received {len(available)}.')
        selected=available[:self.data_shards]; selected_data=[shards[index] for index in selected]
        if len({len(item) for item in selected_data})!=1: raise ECCRecoveryError('Shard lengths are inconsistent.')
        inverse=_invert([self.generator[index] for index in selected]); data=_linear(inverse,selected_data) # type: ignore[arg-type]
        return self.encode(data)
    def decode(self,shards:list[bytes|None])->list[bytes]:return self.recover(shards)[:self.data_shards]
    def verify(self,shards:list[bytes|None])->bool:
        try:
            recovered=self.recover(shards)
            return all(shard is None or shard==recovered[index] for index,shard in enumerate(shards))
        except ECCRecoveryError:return False
# Compatibility adapter for the pre-VNX-DNA-2 byte-codeword API.  New datasets
# use ReedSolomonShards above; this wrapper remains for legacy archive imports.
try:
    from reedsolo import RSCodec, ReedSolomonError
except ImportError:
    RSCodec = None
    class ReedSolomonError(Exception): pass
class ReedSolomonECC:
    name='reed_solomon'
    def __init__(self,parity_symbols:int=16):
        if RSCodec is None: raise RuntimeError('Legacy Reed-Solomon codewords require the reedsolo package.')
        self.parity_symbols=parity_symbols;self.codec=RSCodec(parity_symbols)
    def encode(self,data:bytes)->bytes:return bytes(self.codec.encode(data))
    def decode(self,data:bytes)->bytes:
        try:return bytes(self.codec.decode(data)[0])
        except ReedSolomonError as error:raise ValueError('Reed-Solomon decoding failed.') from error
