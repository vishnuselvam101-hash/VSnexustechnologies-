# VNX-DNA 2.0.0 compatibility fixtures

Generated once with the released VNX-DNA 2.0.0 code (tag `v2.0.0`) by `research/v3/make_v2_fixtures.py`, and
checked by `tests/v3/test_compat_v3.py`:

| file | content |
|---|---|
| `input.bin` | the 12,340-byte test input (random bytes, repeated text, UTF-8 text) |
| `plain.vxdna` | unencrypted format-5 container (8+4 outer code, 4 KiB chunks, 24-byte payloads, 8 inner parity bytes) |
| `plain.fasta` | its DNA strands (frame format 5) |
| `encrypted.vxdna` | the same input, AES-256-GCM |
| `resumed.vxdna` | an encrypted store interrupted after 2 chunks and resumed (VNX-DNA 2.0 sealed its final records in epoch 0) |
| `key.hex` | the fixture key: SHA-256 of a public string. It protects nothing and must never be used for real data. |
| `SHA256SUMS.json` | SHA-256 of every file as generated |

Software artefacts only; no physical DNA is involved.
