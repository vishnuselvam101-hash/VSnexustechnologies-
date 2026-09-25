try:
 from reedsolo import RSCodec, ReedSolomonError
except ImportError:
 RSCodec = None
 class ReedSolomonError(Exception): pass
class ReedSolomonECC:
    name="reed_solomon"
    def __init__(self, parity_symbols:int=16):
        if not 1<=parity_symbols<255: raise ValueError("Parity symbols must be 1..254.")
        if RSCodec is None: raise RuntimeError('Reed-Solomon support requires the reedsolo package.')
        self.parity_symbols=parity_symbols; self.codec=RSCodec(parity_symbols)
    def encode(self,data:bytes)->bytes:
        if len(data)>255-self.parity_symbols: raise ValueError("Chunk exceeds Reed-Solomon block capacity.")
        return bytes(self.codec.encode(data))
    def decode(self,data:bytes)->bytes:
        try: return bytes(self.codec.decode(data)[0])
        except ReedSolomonError as e: raise ValueError("Reed-Solomon decoding failed.") from e
