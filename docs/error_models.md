# Channel error models

The deterministic simulator reports substitutions, insertions, deletions, dropout, duplicates, and reordering. In VNX-DNA-2, a missing strand or a strand whose base-level changes cause validation/length/SHA-256 failure becomes a **known shard erasure**. Reed–Solomon recovery can restore up to M such erasures per stripe.

Insertion/deletion errors change the two-bit sequence alignment and are not repaired at nucleotide level. They are detected and may be handled only as whole-shard erasures when enough other shards exist. These models are computational abstractions and are not biological validation.
