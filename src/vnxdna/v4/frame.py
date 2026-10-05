"""Façade (V6 Phase 2, M3): ``vnxdna.v4.frame`` was split into :mod:`vnxdna.dnaenc.layout`, ``markers``, ``scrambler``,
``mapping`` and ``frame4``. Every name the old module defined or imported, private ones included, is re-exported, and
mutable module state (the keystream cache ``_KEYSTREAM``) is the same object as in the new module.

Monkeypatching a name *here* no longer affects the code that uses it: patch it in its new module.
"""
import hashlib  # noqa: F401
from dataclasses import asdict, dataclass  # noqa: F401

import numpy as np  # noqa: F401

from vnxdna.codec.codecs import InnerRS  # noqa: F401
from vnxdna.core.crc import crc32_bytes_be, crc32_rows  # noqa: F401
from vnxdna.core.errors import VNXConfigurationError, VNXConstraintError  # noqa: F401
from vnxdna.core.version import FRAME_VERSION  # noqa: F401
from vnxdna.dnaenc.constraints import ConstraintConfig, satisfied_batch, to_codes  # noqa: F401
from vnxdna.dnaenc.frame4 import Parsed, build_strands, decode_frames, plain_rows, tentative_address  # noqa: F401
from vnxdna.dnaenc.layout import CRC_BYTES, HEADER_BYTES, KIND_DATA, KIND_SUPER, PROFILES, Layout  # noqa: F401
from vnxdna.dnaenc.mapping import bytes_to_nt, nt_to_bytes  # noqa: F401
from vnxdna.dnaenc.markers import MARKER_TABLES, insert_markers  # noqa: F401
from vnxdna.dnaenc.scrambler import _KEYSTREAM, VARIANTS, keystreams  # noqa: F401

__facade_of__ = ("vnxdna.dnaenc.layout", "vnxdna.dnaenc.markers", "vnxdna.dnaenc.scrambler", "vnxdna.dnaenc.mapping",
                 "vnxdna.dnaenc.frame4")
