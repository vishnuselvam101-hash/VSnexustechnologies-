# Benchmarks

`vnx-dna benchmark INPUT --output benchmark.json` measures a local encode/decode execution. For ECC datasets, measure the selected K/M configuration separately; parity overhead is `M / K` relative to one stripe's data-shard capacity, not storage efficiency. Report output strand count, DNA bases, encode/decode time, observed RSS, and final integrity result. No result is a physical DNA-storage performance claim.
