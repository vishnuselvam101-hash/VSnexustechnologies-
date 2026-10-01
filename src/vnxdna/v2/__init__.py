"""VNX-DNA V2: streaming, bounded-memory archive format 5.

V2 keeps V1 (archive format 4) readable through the untouched V1 modules and
adds, in this package:

* :mod:`.container` - the streaming ``.vxdna`` container file version 2
  (body first, index and manifest in a footer, atomic finalisation, resume);
* :mod:`.frame` - strand frame format 5 (32-bit stripe address, vectorised CRC);
* :mod:`.encoder` / :mod:`.decoder` - chunk-parallel DNA encoding and a
  two-pass, disk-backed decoder whose memory does not grow with the archive;
* :mod:`.sequencing`, :mod:`.cluster`, :mod:`.consensus` - the simulated
  sequencing channel and the read-processing chain;
* :mod:`.experiment`, :mod:`.scale` - reproducible experiments and large-file
  benchmarks.
"""
