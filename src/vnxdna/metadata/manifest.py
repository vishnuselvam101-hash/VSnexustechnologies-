from __future__ import annotations
import json
from pathlib import Path
from ..core.errors import MetadataError, UnsupportedFormatError
from ..core.models import FORMAT_MAGIC
REQUIRED={'format','format_version','dataset_id','original_sha256','original_size','transformed_size','strand_count','config'}
def write(path:Path, manifest:dict)->None:
    path.write_text(json.dumps(manifest,sort_keys=True,separators=(',',':')),encoding='utf-8')
def read(path:Path)->dict:
    try: manifest=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError) as error: raise MetadataError('Cannot read dataset manifest.') from error
    if not REQUIRED.issubset(manifest): raise MetadataError('Dataset manifest is missing required fields.')
    if manifest['format']!=FORMAT_MAGIC or manifest['format_version'] not in (1, 2): raise UnsupportedFormatError('Unsupported VNX-DNA dataset format.')
    return manifest
