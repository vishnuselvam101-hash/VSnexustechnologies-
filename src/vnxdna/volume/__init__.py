"""``vnxdna.volume`` (V13, layer 6): a disk-like, crash-consistent volume on top of the codec.

    from vnxdna.volume import Volume, VolumeOptions
    with Volume.create("vol", VolumeOptions(extent_bytes=1 << 20)) as v:
        v.write(data)                      # dirty extents in the write-back buffer
        v.flush()                          # encoded to strands, journaled
    with Volume.open("vol") as v:
        v.read_at(offset, length)          # decodes only the extents the range touches
        v.scrub()

Every extent is an ordinary VNX archive encoded to strands (:mod:`vnxdna.pipeline.stream`); the strands stay files.
"""
from vnxdna.volume.journal import JOURNAL_FORMAT, Journal
from vnxdna.volume.volume import VOLUME_FORMAT, Volume, VolumeOptions

__all__ = ["VOLUME_FORMAT", "JOURNAL_FORMAT", "Journal", "Volume", "VolumeOptions"]
