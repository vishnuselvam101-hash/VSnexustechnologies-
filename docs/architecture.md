# Architecture
Pipeline: input bytes → optional Zstandard compression → optional Fernet authenticated encryption → logical chunking → per-chunk ECC → 2-bit DNA mapping. Decoding reverses this order and verifies chunk checksums and final SHA-256. Reed–Solomon protects byte substitutions only; indels generally disrupt alignment and are expected to fail safely.
