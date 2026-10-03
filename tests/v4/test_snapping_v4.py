"""Address snapping: reads whose header carries a one-byte error join the right consensus group."""
import numpy as np

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.decoder import snap_addresses


def test_snap_unique_one_byte_neighbour():
    missing = {(0, 0xBEEF, 10, 5), (0, 0xBEEF, 10, 6), (0, 0xBEEF, 300, 5)}
    keys = np.array([[0, 0xBEEF, 10, 5],            # exact: unchanged
                     [0, 0xBEEF, 10, 5 | 0x100],    # symbol high byte corrupted → (10, 5)
                     [0, 0xBEEE, 300, 5],           # tag corrupted → (300, 5)
                     [0, 0xBEEF, 10, 7],            # one byte from both (10,5)? no: from (10,6) and (10,5) → ambiguous
                     [0, 0x1234, 99, 77]])          # far from everything: unchanged
    out, n = snap_addresses(keys, missing)
    assert out[0].tolist() == [0, 0xBEEF, 10, 5]
    assert out[1].tolist() == [0, 0xBEEF, 10, 5]
    assert out[2].tolist() == [0, 0xBEEF, 300, 5]
    assert out[3].tolist() == [0, 0xBEEF, 10, 7]     # ambiguous → not snapped
    assert out[4].tolist() == [0, 0x1234, 99, 77]
    assert n == 2


def test_snapping_improves_heavy_error_recovery(tmp_path):
    src = tmp_path / "in.bin"
    src.write_bytes(np.random.default_rng(3).integers(0, 256, 60_000, dtype=np.uint8).tobytes())
    ar.build_archive([src], tmp_path / "a.vnx")
    en.encode_container(tmp_path / "a.vnx", tmp_path / "s.fasta", en.DNAOptions())
    cfg = ch.ChannelConfig(substitution_rate=0.01, insertion_rate=0.004, deletion_rate=0.004, coverage=5, seed=11)
    ch.simulate_file(tmp_path / "s.fasta", tmp_path / "r.fastq", cfg)
    res = de.decode_reads(tmp_path / "r.fastq", tmp_path / "r.vnx")
    assert res.status == "SUCCESS"
    assert res.report["reads"].get("addresses_snapped", 0) > 0
    assert (tmp_path / "r.vnx").read_bytes() == (tmp_path / "a.vnx").read_bytes()
