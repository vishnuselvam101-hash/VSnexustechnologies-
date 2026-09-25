# VNX-DNA R&D-1

VNX-DNA 0.1.0 is an **offline, computational** research platform for evaluating a reproducible digital-data-to-DNA-symbol archive pipeline. It implements an intentionally simple 2-bit mapping (`00 A`, `01 C`, `10 G`, `11 T`), diagnostics, simulated errors, authenticated encryption, Zstandard compression, chunk-level Reed–Solomon coding, a versioned JSON archive, a CLI, and a local FastAPI service.

## What it is—and is not

It is a software simulator and archive-format research prototype. **R&D-1 uses configurable synthetic error models and does not constitute experimental validation of a biological DNA-storage channel.** The baseline mapping is diagnostic only, not constrained/biologically optimized encoding. No claims are made about physical density, longevity, cost, or commercial readiness.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Quick start

```bash
vnxdna encode test.bin test.vnxdna
vnxdna inspect test.vnxdna
vnxdna decode test.vnxdna recovered.bin
vnxdna verify test.bin recovered.bin
vnxdna experiment run E001
vnxdna benchmark
uvicorn vnxdna.api.app:app --host 127.0.0.1 --port 8000
```

The API is intended for local binding only. Visit `/docs` on the local server for its interactive dashboard-like API interface.

## Archive format

`VNXDNA-0.1` is JSON, not pickle. It stores manifest metadata, per-file identities and hashes, plus DNA payload strings. It never stores encryption keys. See [archive format](docs/archive_format.md).

## Reproducibility and experiments

`python scripts/generate_datasets.py` creates seeded synthetic datasets. `python scripts/run_all_experiments.py` runs E001–E008 safely at deliberately modest sizes and writes actual results under `results/`. E001–E008 are software experiments; generated results must be consulted rather than assumed.

## Limitations and roadmap

See [limitations](docs/limitations.md), [error models](docs/error_models.md), and [experimental protocol](docs/experimental_protocol.md). The next justified milestone is validation against a measured, independently characterized DNA channel—not biological claims based solely on this simulator.
