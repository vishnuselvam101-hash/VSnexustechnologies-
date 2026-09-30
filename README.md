# VNX-DNA

**Computational DNA data storage, end to end, on a CPU.** VNX-DNA turns any file into DNA strand sequences and back:

- compression and authenticated encryption (AES-256-GCM);
- chunking and a provably MDS erasure code across strands (Cauchy Reed–Solomon);
- per-strand CRC-32 and inner Reed–Solomon;
- constraint-screened A/C/G/T encoding;
- a seeded DNA-channel simulator;
- recovery, and independent SHA-256 verification of the original bytes.

> **Scope.** This is software. Every result in this repository comes from computation and simulation. No sequence
> has been synthesised or sequenced, and the constraint checks are heuristics, not wet-lab validation. See
> [docs/DNA_CODEC.md](docs/DNA_CODEC.md).

## Install (Ubuntu, Python ≥ 3.12, CPU only)

```bash
git clone https://github.com/vishnuselvam101-hash/VSnexustechnologies-.git
cd VSnexustechnologies-
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # add '.[dev]' to run the tests
vnx-dna --help
```

## Quick start: file → DNA → damaged DNA → file

```bash
echo "Hello VNX-DNA" > input.txt

# 1. store: compress + chunk + authenticated manifest -> self-describing container
vnx-dna store input.txt --output hello.vxdna
vnx-dna info hello.vxdna

# 2. encode: redundancy + ECC + DNA mapping -> strand pool (FASTA; headers are labels only)
vnx-dna encode hello.vxdna --output hello.fasta

# 3. (optional) simulate a damaging DNA channel: 5 % strand dropout, 0.1 % substitutions
vnx-dna simulate hello.fasta --dropout-rate 0.05 --substitution-rate 0.001 --seed 12345 --output damaged.fasta

# 4. decode: DNA reads -> the container, byte-identical to hello.vxdna
vnx-dna decode damaged.fasta --output decoded.vxdna

# 5. restore: container -> original file (SHA-256 recomputed and compared)
vnx-dna restore decoded.vxdna --output recovered.txt
vnx-dna verify decoded.vxdna

sha256sum input.txt recovered.txt     # identical
cmp hello.vxdna decoded.vxdna         # identical
```

`vnx-dna recover damaged.fasta -o recovered.txt` does steps 4 and 5 at once. `vnx-dna pipeline input.bin -o out.bin
--dropout-rate 0.02 --substitution-rate 0.001 --seed 42` runs the whole lifecycle with real intermediate files.

### Encrypted archives (recommended for real data)

```bash
vnx-dna keygen --output key.txt                          # 256-bit key, file mode 0600; keep it safe
vnx-dna store secret.pdf -o secret.vxdna --key-file key.txt
vnx-dna encode secret.vxdna -o secret.fasta              # no key needed to encode or decode DNA
vnx-dna recover secret.fasta -o secret-restored.pdf --key-file key.txt
```

Without the key, name, size and hashes are sealed and the content is unreadable. A wrong key or any tampering exits
with code 4 and writes nothing. [docs/SECURITY.md](docs/SECURITY.md) lists exactly what stays visible.

### Random access

```bash
vnx-dna extract secret.fasta --chunk 3 -o part3.bin --key-file key.txt           # one 256 KiB chunk
vnx-dna extract secret.vxdna --start 1000000 --end 1200000 -o slice.bin -k key.txt  # a byte range
```

Only the stripes of the selected chunks are decoded ([docs/RANDOM_ACCESS.md](docs/RANDOM_ACCESS.md)).

## What is guaranteed

With the default profile (64 data + 16 parity strands per stripe, 244-nt strands, inner RS with 8 parity bytes):

- **Erasures:** any 16 missing or rejected strands of any stripe are recovered. This is proven (MDS) and tested
  exhaustively on small codes, including the V0.1 counterexample {4,5,7,11} for 8+4.
- **Substitutions:** up to 4 byte errors per strand are corrected by the inner code. A strand with more errors becomes
  an erasure.
- **Order, orientation and duplicates don't matter.** Every read is validated on its own, duplicates are resolved by
  validated majority, and reverse complements are recognised.
- **No silent corruption.** Output is written only after the recomputed SHA-256 matches. Beyond capacity, the
  decoder exits 5 (`INSUFFICIENT_REDUNDANCY`) and writes nothing.
- **Indels:** Reed–Solomon does not correct insertions or deletions. `--experimental-indel-repair` realigns reads with
  one indel. It is opt-in and experimental; see [docs/CHANNEL_MODEL.md](docs/CHANNEL_MODEL.md).

The measured recovery rates under random damage (dropout, substitutions, indels) are in
[docs/CHANNEL_MODEL.md](docs/CHANNEL_MODEL.md), and speed, memory and density are in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md). Stronger redundancy is a flag away, e.g.
`--data-shards 96 --parity-shards 48`.

## Commands

| command | purpose |
|---|---|
| `store` / `pack` | file → `.vxdna` container |
| `encode` | container → DNA strands (FASTA) |
| `simulate` | seeded channel damage with an event log |
| `decode` | DNA reads → container |
| `restore` / `recover` | container or reads → original file, verified |
| `verify` | independent PASS/FAIL integrity report |
| `info` | how an archive was built and how much DNA it uses |
| `extract` | random access to a chunk or byte range |
| `pipeline` | the whole lifecycle in one command |
| `keygen`, `benchmark`, `version` | utilities |
| `legacy info\|restore` | read V0.1 archives |

Exit codes are stable: 0 ok, 1 verification failed, 2 usage, 3 invalid input, 4 authentication, 5 insufficient
redundancy, 6 unsupported format, 7 configuration, 8 output, 70 internal. See [docs/CLI.md](docs/CLI.md).

## Python API

```python
from vnxdna import api
api.store("input.bin", "a.vxdna")
api.encode("a.vxdna", "pool.fasta")
api.simulate("pool.fasta", "reads.fasta", api.ChannelConfig(seed=1, dropout_rate=0.05))
report = api.recover("reads.fasta", "out.bin")   # dict: status, recovery statistics, recomputed SHA-256
```

## Documentation

| | |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | layers, modules, decisions |
| [FORMAT](docs/FORMAT.md) | `.vxdna`, manifest, strand frame, metadata strands |
| [ECC](docs/ECC.md) | Cauchy MDS proof, inner RS, verification boundary, guarantees |
| [DNA_CODEC](docs/DNA_CODEC.md) | mappings, constraints, what is *not* validated |
| [SECURITY](docs/SECURITY.md) | crypto design, visible metadata, limits |
| [CHANNEL_MODEL](docs/CHANNEL_MODEL.md) | simulator, indel research, measured thresholds |
| [RANDOM_ACCESS](docs/RANDOM_ACCESS.md) | what chunk access does and does not mean |
| [BENCHMARKS](docs/BENCHMARKS.md) | measured speed, memory, density |
| [COMPATIBILITY](docs/COMPATIBILITY.md) | V0.1 support and migration |
| [TESTING](docs/TESTING.md), [RELEASE_PROCESS](docs/RELEASE_PROCESS.md) | test suites, release gates |
| [ROADMAP](docs/ROADMAP.md), [PROJECT_STATE](docs/PROJECT_STATE.md) | limitations, future work, current status |
| [V0.1_BASELINE](docs/V0.1_BASELINE.md) | forensic record of the V0.1 prototype |

## Limitations

- Everything is in memory, so the input is limited to 4 GiB, and peak memory is several times the input size.
- Pure Python/numpy: throughput is on the order of MB/s (measured in BENCHMARKS.md).
- The channel model is simple (i.i.d. errors, bursts, dropout, coverage). It is not fitted to any sequencing platform.
- No primer design and no secondary-structure screening. There is no physical validation.
- Unencrypted archives detect corruption but cannot detect deliberate tampering.

## License

MIT, see [LICENSE](LICENSE). V0.1 research material is preserved under [research/legacy](research/legacy/README.md).
