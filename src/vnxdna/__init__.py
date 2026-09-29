"""Stable public API for VNX-DNA-1 computational datasets."""
from .core.models import DatasetConfig, Strand, ConstraintSettings
from .core.pipeline import decode_dataset, encode_file, inspect_dataset
from .simulation.channel import ChannelConfig, simulate
from ._version import __version__
class Encoder:
    def encode(self,input_path,output_directory,config=DatasetConfig(),key=None): return encode_file(input_path,output_directory,config,key)
class Decoder:
    def decode(self,directory,output_path,key=None): return decode_dataset(directory,output_path,key)
class ChannelSimulator:
    def simulate(self,source,destination,config): return simulate(source,destination,config)
