# Limitations

VNX-DNA-1 is a verified no-ECC baseline. It detects missing, duplicate, reordered, malformed, and corrupt strand observations, but does **not** recover missing strands or correct substitutions/insertions/deletions. Reed–Solomon wrapper code from the prior prototype is not used by the VNX-DNA-1 dataset format.

The current implementation reads a complete source and reconstructed transformed payload into memory; its API is structured around strands but GB-scale streaming has not been benchmarked and is not claimed. The baseline two-bit mapping is intentionally simple and can violate biological constraints under strict settings; it is not a constrained encoder.

Channel simulation is deterministic software only. GPU acceleration, biological channel validation, constrained coding, indel-aware decoding, and erasure coding are future work.
