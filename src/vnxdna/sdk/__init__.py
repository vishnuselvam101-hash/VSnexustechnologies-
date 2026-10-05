"""``vnxdna.sdk``: the stable VNX-DNA Python API (V6_ARCHITECTURE §4, layer 7).

    from vnxdna import sdk
    sdk.archive(["./dataset"], "a.vnx")
    sdk.encode("a.vnx", "strands.fasta", dna=sdk.DNAOptions(profile="v4-balanced"))
    res = sdk.decode("reads.fastq", "recovered.vnx")
    res.status, res.to_json()          # "SUCCESS", the vnx.result/1 envelope

Functions return :class:`Result` objects (``to_json()`` = ``vnx.result/1``); anticipated failures raise
:class:`VNXError` subclasses with a stable ``code`` (spec §10). The option dataclasses are the existing ones, re-exported.
Every channel result is SIMULATED.
"""
from vnxdna.archive.operations import ArchiveOptions
from vnxdna.core.errors import VNXError
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.planner import RecoveryBudget
from vnxdna.sdk.api import (archive, benchmark, benchmark_markdown, channel_convert, channel_model, channel_models,
                            channel_sweep, codec_compare, conformance, decode, encode, experiment_reproduce, experiment_run,
                            extract, generate, inspect, is_container, keygen,
                            list_entries, load_keys, locate, native, parse_size, profiles, simulate, sweep, sweep_table,
                            validate_strands, verify, version)
from vnxdna.sdk.envelope import (ArchiveResult, BenchmarkResult, ConformanceResult, DecodeResult, EncodeResult,
                                 ExtractResult, InspectResult, ListResult, Result, SimulateResult, VerifyResult, error_json,
                                 provenance)

__all__ = ["ArchiveOptions", "DNAOptions", "DecodeOptions", "RecoveryBudget", "VNXError", "Result", "ArchiveResult",
           "EncodeResult", "DecodeResult", "InspectResult", "VerifyResult", "ExtractResult", "ListResult", "SimulateResult",
           "BenchmarkResult", "ConformanceResult", "archive", "encode", "decode", "inspect", "verify", "extract",
           "list_entries", "locate", "simulate", "channel_models", "channel_model", "channel_convert", "channel_sweep",
           "benchmark", "benchmark_markdown", "codec_compare", "sweep", "sweep_table",
           "experiment_run", "experiment_reproduce", "generate", "validate_strands", "profiles", "native", "keygen",
           "load_keys", "parse_size", "is_container", "version", "conformance", "error_json", "provenance"]
