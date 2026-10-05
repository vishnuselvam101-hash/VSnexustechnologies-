"""``vnxdna.providers``: the provider interface and the laboratory packages (spec §9; V6_ARCHITECTURE §3, layer 6).

    from vnxdna.providers import ArchiveRef, ReferenceSimulatorProvider, SequencingRequest
    p = ReferenceSimulatorProvider("lab", models_dir="experiments/v6/channel/models")
    exp = p.prepare("strands.fasta", archive=ArchiveRef.from_encode("a.vnx", sdk.encode("a.vnx", "strands.fasta").body), profile="v4-balanced")
    pool = p.write(exp).pool
    imp = p.retrieve(pool, sequencing=SequencingRequest("illumina-like", seed=7, coverage=10))
    reads = list(p.read(imp))                  # then decode imp.read_files[0]

Status (spec §9.1): interface implemented: yes; provider integration tested: **software only**. The only provider is
:class:`ReferenceSimulatorProvider`, and every package it produces is labelled SIMULATED. There is no synthesis or
sequencing vendor adapter (V11), and no DNA has been synthesised, stored or sequenced by VNX-DNA.
"""
from vnxdna.providers.base import (DEFAULT_MAX_STRAND_NT, INTERFACE, STATUS, ArchiveRef, DNAProvider, DNAReader,
                                   DNAWriter, ExportPackage, ImportPackage, PoolRef, PrimerPair, ProviderCapabilities,
                                   Selection, SequencingRequest, WriteReceipt)
from vnxdna.providers.packages import (EVIDENCE_CLASSES, EXPORT_SCHEMA, IMPORT_SCHEMA, build_export_package,
                                       build_import_package, load_export_package, load_import_package, order_rows,
                                       pool_composition)
from vnxdna.providers.reference import ChannelModel, ReferenceSimulatorProvider

__all__ = ["INTERFACE", "DEFAULT_MAX_STRAND_NT", "STATUS", "ArchiveRef", "DNAProvider", "DNAReader", "DNAWriter",
           "ExportPackage", "ImportPackage", "PoolRef", "PrimerPair", "ProviderCapabilities", "Selection",
           "SequencingRequest", "WriteReceipt", "EVIDENCE_CLASSES", "EXPORT_SCHEMA", "IMPORT_SCHEMA",
           "build_export_package", "build_import_package", "load_export_package", "load_import_package", "order_rows",
           "pool_composition", "ChannelModel", "ReferenceSimulatorProvider"]
