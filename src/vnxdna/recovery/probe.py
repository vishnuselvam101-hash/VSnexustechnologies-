"""Decode stage D1: sample the reads and choose the strand layout (spec §3.10). Formerly
``vnxdna.v4.decoder.detect_layout`` (V6 Phase 2, M4)."""
from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXUnsupportedVersionError
from vnxdna.core.version import FRAME_READ, FRAME_VERSION
from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import Layout, PROFILES
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.native.reads import iter_reads
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.pass1 import _RC
from vnxdna.sync.template import TemplateAligner, frame_erasures_to_bytes, strip_markers_exact


# ============================================================================ probe (spec §3.10)
#: (scrambler domain, nibble this reader decodes or refers to vnx-dna, axis name); spec §3.2 frame-version registry
DOMAINS = (("VNX4 scrambler", "vnx4"), ("VNX-DNA/4 scrambler", "v1-frame4"), ("VNX-DNA/5 scrambler", "v3-frame5"))
SAMPLE_READS = 20000
SYNC_VOTE_READS = 1000
#: a domain "shows" a version when that nibble is in ≥ 30 % of the reads and in ≥ 30 % of the reads with a less common
#: variant byte. Spec §3.10 says 50 %; EXP-PROBE-1 (SIMULATED) measured the own-domain share of noisy pools at
#: 0.5–0.6 (nanopore-like) while foreign domains show ≤ 0.1 consistent share and random reads ≈ 1/8.
SUPPORT_SHARE = 0.3
MIN_REFUSAL_SAMPLE = 64
_KS0 = np.array([[hashlib.shake_128(d.encode() + bytes([v])).digest(1)[0] for v in range(256)] for d, _ in DOMAINS],
                dtype=np.uint8)                   # (3, 256): byte 0 of each domain's keystream for every variant


@dataclass
class Probe:
    """What the probe saw: the sample, the frame-start bytes per orientation, candidates and their scores."""

    n: int
    counts: Counter
    sample: list
    heads: dict = field(default_factory=dict)     # orientation -> (b0 (n,), nibbles (3, n) per domain, valid (n,))
    unique: "Probe | None" = None                 # the same over distinct molecules (copies of one strand counted once)
    candidates: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    sync_vote: bool = False

    def dominant(self, domain: str) -> tuple[int, float, float, int]:
        """(nibble u, share of reads showing u in either orientation, share among the reads whose variant byte b0 is
        not the most common one, size of that set) for the most frequent nibble of ``domain``.

        The second share separates the pool's own domain from a foreign one: the frame's byte 1 is (version << 4 |
        kind) XOR keystream(domain, b0)[0], so in its own domain every read shows the version whatever its variant,
        while in a foreign domain a nibble value only follows one variant. Most strands use variant 0, so a foreign
        domain alone can show one nibble in about half of the reads (measured 0.39-0.57 on the probe test pools)."""
        if not self.heads or not self.n:
            return 0, 0.0, 0.0, 0
        di = [d for d, _ in DOMAINS].index(domain)
        (b0f, nf, vf), (b0r, nr, vr) = self.heads["forward"], self.heads["reverse"]
        best = None
        for u in range(16):
            hf, hr = vf & (nf[di] == u), vr & (nr[di] == u)
            hit = hf | hr
            if best is None or hit.sum() > best[1].sum():
                best = (u, hit, np.where(hf | ~hr, b0f, b0r))
        u, hit, b0 = best
        if not hit.any():
            return u, 0.0, 0.0, 0
        mode = np.bincount(b0[hit], minlength=256).argmax()
        rest = b0 != mode
        n_rest = int(rest.sum())
        return u, float(hit.mean()), float(hit[rest].mean()) if n_rest else 0.0, n_rest

    def supports(self, domain: str, nibble: int | None = None, threshold: float = SUPPORT_SHARE) -> bool:
        """``domain`` shows one nibble (``nibble``, if given) in ≥ ``threshold`` of the reads, consistently across
        variant bytes (≥ ``threshold`` of the reads with a less common variant, when there are at least 16)."""
        u, share, rest_share, n_rest = self.dominant(domain)
        if nibble is not None and u != nibble:
            return False
        return share >= threshold and (rest_share >= threshold if n_rest >= 16 else share >= 0.9)


def sample_reads(reads_path: str | os.PathLike, limit: int = SAMPLE_READS) -> tuple[Counter, list]:
    """D1 step 1: up to ``limit`` reads (codes) and the read-length histogram of the sampled batches."""
    counts: Counter = Counter()
    sample: list[np.ndarray] = []
    seen = 0
    for batch in iter_reads(reads_path, 4096):
        counts.update(batch.lengths.tolist())
        offs = np.concatenate([[0], np.cumsum(batch.lengths)])
        sample.extend(batch.codes[offs[i]:offs[i + 1]] for i in range(batch.count))
        seen += batch.count
        if seen >= limit:
            break
    return counts, sample


def frame_heads(sample: list) -> dict:
    """D1 step 2: for each read and both orientations, the 8 nt at the frame start → bytes b0 b1 → per domain the
    version nibble ``(b1 ⊕ SHAKE-128(domain ‖ b0)[0]) >> 4`` (spec §3.10; reads shorter than 8 nt or with N there
    give no nibble)."""
    n = len(sample)
    fwd = np.full((n, 8), 4, dtype=np.uint8)
    rev = np.full((n, 8), 4, dtype=np.uint8)
    for i, r in enumerate(sample):
        if r.size >= 8:
            fwd[i] = r[:8]
            rev[i] = _RC[r[-8:][::-1]]
    out = {}
    for name, m in (("forward", fwd), ("reverse", rev)):
        valid = (m <= 3).all(axis=1)
        b = nt_to_bytes(np.minimum(m, 3)) if n else np.zeros((0, 2), dtype=np.uint8)
        nib = (b[:, 1][None, :] ^ _KS0[:, b[:, 0]]) >> 4 if n else np.zeros((3, 0), dtype=np.uint8)
        out[name] = (b[:, 0].astype(np.int64), nib, valid)
    return out


def probe_reads(reads_path: str | os.PathLike, band: int = 6) -> Probe:
    """D1 steps 1–4: sample, nibble histograms, length candidates (≥ 10 % of the sample within the band) and the frame
    check vote of every candidate (exact-length reads, both orientations; applied even to a single candidate)."""
    counts, sample = sample_reads(reads_path)
    p = Probe(len(sample), counts, sample)
    if not counts:
        return p
    p.heads = frame_heads(sample)
    for name, (lay, _, _) in PROFILES.items():
        near = sum(v for length, v in counts.items() if abs(length - lay.strand_nt) <= band)
        if near >= max(1, p.n // 10) and lay not in [c[1] for c in p.candidates]:
            p.candidates.append((name, lay))
    for name, lay in p.candidates:
        exact = [r for r in sample if r.size == lay.strand_nt][:4000]
        score = 0
        if exact:
            mat = np.stack(exact)
            for m in (mat, _RC[mat[:, ::-1]]):
                fb, _ = strip_markers_exact(lay, m)
                score += int(decode_frames(lay, nt_to_bytes(np.minimum(fb, 3)), errors_only_retry=False).ok.sum())
        p.scores[name] = score
    if p.candidates and not any(p.scores.values()):
        # no exact-length read verifies (heavy indels): the same vote through the sync path of pass 1 (marker-template
        # alignment, indels → erasures) on up to SYNC_VOTE_READS reads near each candidate's length, both orientations
        for name, lay in p.candidates:
            near = [r for r in sample if abs(r.size - lay.strand_nt) <= band][:SYNC_VOTE_READS]
            if not near:
                continue
            al = TemplateAligner(lay, band)
            score = 0
            for reads in (near, [_RC[r[::-1]] for r in near]):
                pr = al.project(reads)
                er = frame_erasures_to_bytes(pr.erased)
                score += int((decode_frames(lay, nt_to_bytes(np.minimum(pr.bases, 3)), er, errors_only_retry=False).ok
                              & pr.ok).sum())
            p.scores[name] = score
            p.sync_vote = True
    return p


def distinct_molecules(sample: list) -> list:
    """The sample with copies of one strand counted once: reads are keyed by their first and last 24 nt, orientation-
    independent. Coverage makes many reads copies of few strands; a version refusal must rest on many strands."""
    seen, out = set(), []
    for r in sample:
        if r.size < 24:
            continue
        a, b = r[:24].tobytes(), _RC[r[-24:][::-1]].tobytes()
        key = (a, b) if a <= b else (b, a)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _unique(p: Probe) -> Probe:
    if p.unique is None:
        u = distinct_molecules(p.sample)
        p.unique = Probe(len(u), p.counts, u, frame_heads(u))
    return p.unique


def refusal(p: Probe):
    """D1 step 6 (a) and (b): the error for a pool of an unsupported or legacy frame version, or None. Decided on
    distinct molecules, of which at least MIN_REFUSAL_SAMPLE are required (spec: n ≥ 64)."""
    if not p.heads:
        return None
    p = _unique(p)
    if p.n < MIN_REFUSAL_SAMPLE:
        return None
    u, share, rest, _ = p.dominant("VNX4 scrambler")
    if u not in FRAME_READ and p.supports("VNX4 scrambler", u):
        return VNXUnsupportedVersionError(
            f"strand frame version {u} is not supported (supported: {', '.join(map(str, FRAME_READ))})",
            stage="D1", code="FRAME_VERSION_UNSUPPORTED", retryable=False,
            details={"frame_version": u, "supported": list(FRAME_READ), "share": round(share, 4),
                     "share_other_variants": round(rest, 4), "sample_reads": p.n},
            hint="upgrade VNX-DNA; this pool was written by a newer encoder")
    for domain, nibble, what in (("VNX-DNA/5 scrambler", 5, "V2/V3 frame format 5"), ("VNX-DNA/4 scrambler", 4, "V1 frame format 4")):
        if p.supports(domain, nibble):
            _, share, rest, _ = p.dominant(domain)
            return VNXUnsupportedVersionError(
                f"these reads carry {what} strands (scrambler domain {domain!r}), a legacy format", stage="D1",
                code="LEGACY_FORMAT", retryable=False,
                details={"domain": domain, "nibble": nibble, "share": round(share, 4), "share_other_variants": round(rest, 4),
                         "sample_reads": p.n},
                hint="decode with `vnx-dna` (the V1-V3 tool)")
    return None


def choose_layout(p: Probe, band: int = 6) -> tuple[Layout, bool]:
    """D1 steps 4–6 on a probe: (layout, verified) or the refusal. ``verified`` is False when no sampled frame verified
    and the layout was chosen only because frame version 4 is evident (equal-length layouts are then indistinguishable)."""
    if not p.counts:
        raise VNXFormatError("no reads found", stage="input")
    best = max(p.candidates, key=lambda c: p.scores[c[0]], default=None)
    if best is not None and p.scores[best[0]] > 0:
        return best[1], True
    err = refusal(p)
    if err is not None:
        raise err
    if p.candidates and p.supports("VNX4 scrambler", FRAME_VERSION, threshold=SUPPORT_SHARE):
        # frame version 4 is evident but no sampled frame verifies (very noisy reads, tiny samples): use the candidate
        # most reads fit; a supported pool is never refused here, and the decoder verifies every frame as always
        def fit(c):
            return sum(v for length, v in p.counts.items() if abs(length - c[1].strand_nt) <= band)
        return max(p.candidates, key=fit)[1], False
    if not p.candidates:
        raise VNXFormatError(f"cannot detect the strand layout (modal read length {p.counts.most_common(1)[0][0]}); pass --profile",
                             stage="layout", code="LAYOUT_UNDETECTED")
    raise VNXFormatError("several layouts match the read lengths and none verifies on a sample; pass --profile" if
                         len(p.candidates) > 1 else "no sampled read verifies as a VNX4 frame; pass --profile",
                         stage="layout", code="LAYOUT_UNDETECTED")


def detect_layout(reads_path: str | os.PathLike, opt: DecodeOptions) -> Layout:
    """D1: the strand layout of a read file (or the one the options name).

    Refusals (spec §3.10 step 6): an unsupported frame version → ``FRAME_VERSION_UNSUPPORTED`` (exit 6, not
    retryable); V1/V3 strands → ``LEGACY_FORMAT`` (exit 6); nothing recognisable → ``LAYOUT_UNDETECTED`` (exit 3).
    Candidates are scored by exact-length frame checks (spec step 4) and, if none verifies, by the same checks through
    the sync path. If still nothing verifies but frame version 4 is evident, the best-fitting candidate is used, so a
    supported pool is never refused for being noisy; the decode then verifies every frame as always."""
    if opt.layout is not None:
        return opt.layout.validate()
    if opt.profile is not None:
        if opt.profile not in PROFILES:
            raise VNXConfigurationError(f"unknown profile {opt.profile!r}")
        return PROFILES[opt.profile][0]
    return choose_layout(probe_reads(reads_path, opt.band), opt.band)[0]


def check_unsupported_after_pass1(reads_path: str | os.PathLike) -> None:
    """D1 step 7: pass 1 accepted no frame; decide step 6 (a)/(b) before any superblock error is raised."""
    counts, sample = sample_reads(reads_path)
    err = refusal(Probe(len(sample), counts, sample, frame_heads(sample)))
    if err is not None:
        raise err


def probe_file(path: str | os.PathLike, *, deep: bool = False) -> dict:
    """``vnx.probe/1`` answer for a read or strand file: can this reader decode it, and with which layout?"""
    from vnxdna.core.errors import VNXError
    out = {"schema": "vnx.probe/1", "object": "reads", "readable": "yes", "reason": None, "frame": None,
           "strand_profile": None, "layout": None, "primers": None, "sample_reads": None, "superblock": None,
           "generated_by": None}
    try:
        lay = detect_layout(path, DecodeOptions())
    except VNXError as error:
        out.update(readable="unknown" if error.code == "LAYOUT_UNDETECTED" else "no", reason=error.to_dict())
        return out
    name = next((n for n, (lay_, _, _) in PROFILES.items() if lay_ == lay), None)
    out.update(frame={"version": 4, "domain": "VNX4 scrambler"}, strand_profile=name, layout=lay.to_dict(),
               generated_by={"answer": "VNX4 frame 4: VNX-DNA >= 4.0", "exact": False})
    return out
