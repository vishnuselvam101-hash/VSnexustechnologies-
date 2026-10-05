"""Façade (V6 Phase 2, M2): the redundancy-profile data and merge moved to :mod:`vnxdna.codec.profiles`, the
``DNAOptions`` builders to :mod:`vnxdna.pipeline.profiles`. Every name this module had is re-exported."""
from vnxdna.codec.profiles import REDUNDANCY_PROFILES, merge, profile  # noqa: F401
from vnxdna.core.errors import V6ConfigurationError  # noqa: F401
from vnxdna.pipeline.profiles import DNAOptions, PROFILES, describe, dna_options  # noqa: F401

__facade_of__ = ("vnxdna.codec.profiles", "vnxdna.pipeline.profiles")
