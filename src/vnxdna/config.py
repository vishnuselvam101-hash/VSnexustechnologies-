from dataclasses import dataclass
VERSION="0.1.0"; CODEC_VERSION="VNX-CODEC-0.1"; ARCHIVE_FORMAT="VNXDNA"; ARCHIVE_VERSION="0.1"
@dataclass(frozen=True)
class Settings:
    chunk_size: int = 128
    ecc: str = "reed_solomon"
    parity_symbols: int = 16
    compression: bool = True
    compression_level: int = 3
    encryption: bool = False
