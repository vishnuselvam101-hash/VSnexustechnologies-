# VNX-DNA

VNX-DNA-1 is a **computational DNA data-storage research system** for byte-for-byte archival round trips. It accepts arbitrary binary files, applies optional compression and authenticated encryption, splits the transformed byte stream into self-describing DNA strands, stores them as FASTA plus a deterministic JSON manifest, and reconstructs the original only when per-strand and final SHA-256 checks pass.

> **Scientific scope:** VNX-DNA models DNA symbols and synthetic error channels in software. It is **not** a wet-lab system and makes no claim about synthesis/sequencing compatibility, physical density, longevity, cost, or commercial readiness.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
vnx-dna encode input.bin dataset/
vnx-dna inspect dataset/ --json
vnx-dna decode dataset/ recovered.bin
vnx-dna verify dataset/
python scripts/acceptance_test.py
```

The output dataset contains `manifest.json` and `strands.fasta`; see [the format specification](docs/FORMAT.md). The FASTA headers are self-describing and payloads use a lossless two-bit A/C/G/T baseline encoding.

## Operations

* `encode INPUT DATASET` creates a new dataset directory.
* `decode DATASET OUTPUT` reconstructs output only after strand and final digest checks.
* `simulate DATASET NOISY` deterministically models substitutions, insertions, deletions, dropout, duplicates, and reordering.
* `inspect DATASET`, `verify DATASET`, and `benchmark INPUT` provide inspection, full integrity verification, and measured local benchmark output.

Set `VNXDNA_KEY` only when operating on encrypted datasets. Keys are never put in manifests or logs.

## Deployment

`docker compose up --build` provides a loopback-only FastAPI process. It is a trusted-local-user service, **not** a public or multi-user deployment boundary. See [security](docs/SECURITY.md).

## Documentation

* [Architecture](docs/architecture.md), [format](docs/FORMAT.md), [CLI](docs/CLI.md), and [configuration](docs/CONFIGURATION.md)
* [Error model](docs/error_models.md), [ECC scope](docs/ECC.md), and [benchmarks](docs/BENCHMARKS.md)
* [Research scope](docs/RESEARCH.md), [development](docs/DEVELOPMENT.md), and [limitations](docs/limitations.md)

## Reed–Solomon recovery (VNX-DNA-2)

Use `vnx-dna encode input.bin dataset/ --ecc reed_solomon --data-shards 8 --parity-shards 4`. VNX-DNA-2 creates a new, explicitly versioned strand format. Per stripe, it recovers up to four **known missing or checksum-invalid shards** from 8 data + 4 parity shards. It does not correct nucleotide insertions/deletions and does not reinterpret VNX-DNA-1 datasets. See [ECC](docs/ECC.md).

## Constrained encoding research

`--encoding constrained_v1` selects a deterministic lossless 256-codeword encoder. Each input byte maps to eight bases with exactly 50% GC, fixed opposite word boundaries, and a configured homopolymer limit. It is a computational constraint mechanism, not biological validation. One complete nucleotide-damaged shard can be recovered only as a checksum-detected Reed–Solomon erasure when parity capacity remains; indel alignment itself is not corrected.
