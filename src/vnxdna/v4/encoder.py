"""Façade (V6 Phase 2, M4): ``vnxdna.v4.encoder`` was split into :mod:`vnxdna.pipeline.encode` (DNAOptions, the
encode orchestration and its workers), :mod:`vnxdna.dnaenc.superblock` (Superblock pack/unpack, group sizes) and
:mod:`vnxdna.dnaenc.records` (strand FASTA/FASTQ records). Every name the old module defined or imported is
re-exported. Monkeypatching a name here no longer affects the code that uses it: patch it in its new module."""
import numpy as np  # noqa: F401
import os  # noqa: F401
import struct  # noqa: F401
import time  # noqa: F401
import zlib  # noqa: F401
from collections import deque  # noqa: F401
from concurrent.futures import ProcessPoolExecutor  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path  # noqa: F401
from vnxdna.archive import container as ct  # noqa: F401
from vnxdna.codec.codecs import CODE_IDS, CauchyRSCodec, make_outer  # noqa: F401
from vnxdna.core.errors import VNXConfigurationError, VNXFormatError  # noqa: F401
from vnxdna.core.util import sha256_file  # noqa: F401
from vnxdna.dnaenc.constraints import ConstraintConfig  # noqa: F401
from vnxdna.dnaenc.frame4 import build_strands  # noqa: F401
from vnxdna.dnaenc.layout import KIND_DATA, KIND_SUPER, Layout, PROFILES  # noqa: F401
from vnxdna.dnaenc.strandio import StrandWriter, format_for_output  # noqa: F401
from vnxdna.dnaenc.records import _ASCII, _labels, _serialize  # noqa: F401
from vnxdna.dnaenc.superblock import DIST_IDS, ORDER_IDS, SB_BYTES, SB_MAGIC, SB_VERSION, SB_VERSION_V6, Superblock, group_k  # noqa: F401
from vnxdna.pipeline.encode import DNAOptions, _W, _encode_task, _init, encode_container  # noqa: F401

__facade_of__ = ('vnxdna.dnaenc.records', 'vnxdna.dnaenc.superblock', 'vnxdna.pipeline.encode')
