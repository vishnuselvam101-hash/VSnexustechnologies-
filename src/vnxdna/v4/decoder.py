"""Façade (V6 Phase 2, M4): ``vnxdna.v4.decoder`` was split into :mod:`vnxdna.pipeline.decode` (``decode_reads``, the
D-stage orchestration) and the decode machinery of :mod:`vnxdna.recovery` (``options``, ``probe``, ``pass1``, ``spill``,
``consensus``, ``superblock``, ``schedule``, ``outer``). Every name the old module defined or imported, private ones
included, is re-exported, and mutable module state (the pass-1 worker state ``_P``) is the same object.
Monkeypatching a name here no longer affects the code that uses it: patch it in its new module."""
import hashlib  # noqa: F401
import numpy as np  # noqa: F401
import os  # noqa: F401
import shutil  # noqa: F401
import tempfile  # noqa: F401
import time  # noqa: F401
from collections import Counter, deque  # noqa: F401
from collections.abc import Callable  # noqa: F401
from concurrent.futures import ProcessPoolExecutor  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path  # noqa: F401
from vnxdna.archive import container as ct, operations as ar  # noqa: F401
from vnxdna.codec.codecs import CauchyRSCodec, make_outer  # noqa: F401
from vnxdna.core.errors import VNXAddressError, VNXConfigurationError, VNXDecodeError, VNXFormatError, VNXIntegrityError, VNXKeyError, VNXUnsupportedVersionError  # noqa: F401
from vnxdna.core.util import atomic_output, peak_rss_bytes  # noqa: F401
from vnxdna.dnaenc.frame4 import decode_frames, tentative_address  # noqa: F401
from vnxdna.dnaenc.layout import HEADER_BYTES, KIND_DATA, KIND_SUPER, Layout, PROFILES  # noqa: F401
from vnxdna.dnaenc.mapping import nt_to_bytes  # noqa: F401
from vnxdna.native.reads import iter_reads  # noqa: F401
from vnxdna.sync.template import SyncCosts, TemplateAligner, frame_erasures_to_bytes, strip_markers_exact  # noqa: F401
from vnxdna.v4.encoder import SB_BYTES, Superblock, group_k  # noqa: F401
from vnxdna.pipeline.decode import _decode_reads, decode_reads  # noqa: F401
from vnxdna.recovery.consensus import _addr_bytes, _consensus_symbols, _smart_consensus, _soft_consensus, consensus_hard, consensus_soft, resolve_duplicates, snap_addresses  # noqa: F401
from vnxdna.recovery.options import DecodeOptions, DecodeResult  # noqa: F401
from vnxdna.recovery.outer import _index_groups, _partial, _pass2, _selective, _stripe_groups  # noqa: F401
from vnxdna.recovery.pass1 import _P, _RC, _orientation, _p_init, _process, _soft_evidence, _try  # noqa: F401
from vnxdna.recovery.probe import detect_layout  # noqa: F401
from vnxdna.recovery.schedule import _DEFER_CHUNK, _ORPHAN_SLICE, _deferred_recovery, _group_decodable, _group_state, _pending_keys, _plan_pass1, _targeted  # noqa: F401
from vnxdna.recovery.spill import Spill, _bucket_count  # noqa: F401
from vnxdna.recovery.superblock import _decode_superblock  # noqa: F401

__facade_of__ = ('vnxdna.pipeline.decode', 'vnxdna.recovery.consensus', 'vnxdna.recovery.options', 'vnxdna.recovery.outer', 'vnxdna.recovery.pass1', 'vnxdna.recovery.probe', 'vnxdna.recovery.schedule', 'vnxdna.recovery.spill', 'vnxdna.recovery.superblock')
