"""Pass 2 (decode stages D9–D14): outer row decoding per bucket, V6 stripe recovery, whole-container
verification, publish, partial and selective output. Formerly in ``vnxdna.v4.decoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections import Counter
from pathlib import Path

import numpy as np

from vnxdna.archive import container as ct, operations as ar
from vnxdna.codec.codecs import make_outer
from vnxdna.core.errors import VNXDecodeError, VNXIntegrityError, VNXKeyError
from vnxdna.core.util import atomic_output, peak_rss_bytes
from vnxdna.core.version import FRAME_VERSION, codec_id
from vnxdna.dnaenc.layout import KIND_DATA, Layout
from vnxdna.recovery.consensus import _consensus_symbols, resolve_duplicates
from vnxdna.recovery.options import DecodeOptions, DecodeResult
from vnxdna.recovery.spill import Spill
from vnxdna.recovery.superblock import _decode_superblock
from vnxdna.dnaenc.superblock import Superblock, group_k


def _index_groups(sb: Superblock, payload_bytes: int) -> set[int]:
    """Groups random access decodes first: the index section plus the group holding the container header."""
    return set(range(sb.index_offset // (sb.K * payload_bytes), sb.group_count)) | {0}


def _stripe_groups(sb: Superblock, groups: set) -> set:
    """Data groups sharing a stripe with ``groups`` (column recovery of a row needs its stripe's rows); V4/V5
    superblocks (version 1) have no stripes."""
    if sb.version == 1:
        return set(groups)
    geo = sb.geometry()
    out: set = set()
    for g in groups:
        if 0 <= g < sb.group_count:
            out.update(geo.stripe_rows(geo.stripe_of(g))[0])
    return out


def _pass2(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter, stage: dict, t0: float, output, overwrite, partial_dir,
           select, select_dir, key, passphrase, tmp_root, planner=None, ev=None, recover_groups=None,
           allow_unencrypted=False) -> DecodeResult:
    if planner is None:
        from vnxdna.recovery.planner import RecoveryPlanner
        planner = RecoveryPlanner()
    t2 = time.perf_counter()
    if select and recover_groups is not None:
        # random access, first stage: round S (superblock reads) must run before the superblock is decoded, exactly as
        # in a full decode; the stage's groups (the index's stripes) are only known once round S has decoded it
        recover_groups(lambda sb_: _stripe_groups(sb_, _index_groups(sb_, lay.payload_bytes)))
    sb, sb_info = _decode_superblock(spill, lay, opt, stats)
    if ev is not None:
        ev.archive_id = sb.archive_id.hex()
        ev.emit("superblock", "superblock", formats={"frame_version": FRAME_VERSION, "superblock_version": sb.version,
                                                     "codec": codec_id(FRAME_VERSION, sb.version, sb.outer_code),
                                                     "geometry": {"K": sb.K, "M": sb.M, "groups": sb.group_count,
                                                                  "stripe_depth": sb.stripe_depth,
                                                                  "column_parity": sb.column_parity,
                                                                  "strand_order": sb.strand_order},
                                                     "layout": lay.to_dict()})
    tag = int.from_bytes(sb.archive_id[:2], "big")
    K, P = sb.K, lay.payload_bytes
    codec = make_outer(sb.outer_code, K, sb.M, sb.lt_seed, sb.lt_distribution)
    # V6 (superblock version 2): column-parity groups G … total − 1 are full rows; rows that fail row-wise are kept
    # (verified symbols only) for the iterative stripe decoder (vnxdna.v6.decode). Version 1: total = G, as in V4.
    total = sb.total_groups
    v6 = None
    if sb.version != 1:
        from vnxdna.recovery.stripes import StripeRecovery
        v6 = StripeRecovery(sb.geometry(), Path(tmp_root) / "parity_rows.bin")

    def row_k(g: int) -> int:
        return K if g >= sb.group_count else group_k(sb.container_size, K, P, g)

    work = Path(tmp_root) / "container.vnx"
    with open(work, "wb") as f:
        f.truncate(sb.container_size)
    failed: dict[int, str] = {}
    decoded = 0
    conflicts = 0
    wanted: set[int] | None = None
    if select:
        wanted = _index_groups(sb, P)
    fd = os.open(work, os.O_RDWR)
    try:
        def run(groups_filter: set[int] | None, done: set[int]) -> None:
            nonlocal decoded, conflicts
            for b in range(spill.B):
                planner.checkpoint("pass 2")
                acc, pend = spill.load(b)
                acc = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag)]
                acc = acc[acc["group"] < total]
                if groups_filter is not None:
                    acc = acc[np.isin(acc["group"], list(groups_filter))]
                symbols, c = resolve_duplicates(acc)
                conflicts += c
                targets_b = [g for g in range(b, total, spill.B)
                             if g not in done and (groups_filter is None or g in groups_filter)]
                missing = set()
                for g in targets_b:
                    n_sym = codec.symbols_for(row_k(g))
                    missing.update((KIND_DATA, tag, g, s_) for s_ in range(n_sym) if (KIND_DATA, tag, g, s_) not in symbols)
                symbols.update(_consensus_symbols(pend, symbols, lay, opt, stats, missing))
                by_group: dict[int, dict[int, np.ndarray]] = {}
                for (kd, tg, g, s), v in symbols.items():
                    if kd == KIND_DATA and tg == tag:
                        by_group.setdefault(g, {})[s] = v
                targets = [g for g in range(b, total, spill.B)
                           if g not in done and (groups_filter is None or g in groups_filter)]
                for g in targets:
                    k = row_k(g)
                    syms = by_group.get(g, {})
                    try:
                        if hasattr(codec, "decode_block"):
                            data = codec.decode_block(syms, k, P, g)
                        else:
                            data = codec.decode(syms, k, P)
                    except VNXDecodeError as error:
                        done.add(g)
                        if v6 is not None:
                            v6.row_failed(g, syms, str(error))
                        else:
                            failed[g] = str(error)
                        continue
                    done.add(g)
                    if v6 is not None:
                        v6.row_decoded(g, data, syms)
                        if g >= sb.group_count:
                            continue
                    raw = data.reshape(-1).tobytes()
                    start = g * K * P
                    raw = raw[: max(0, min(len(raw), sb.container_size - start))]
                    os.pwrite(fd, raw, start)
                    decoded += 1
            if v6 is not None:
                decoded += v6.finish(fd, run, done, failed, admit=planner.admit_stripe,
                                     checkpoint=lambda: planner.checkpoint("outer column pass"))

        done: set[int] = set()
        planner.decide("OUTER", True, "row decode of every group" + (" holding the index and the selected files"
                                                                     if select else "") +
                       ("; V6 stripe/column recovery for rows that fail" if v6 is not None else ""),
                       {"groups": sb.group_count, "total_rows": total, "superblock_version": sb.version})
        def stripes_of(groups: set) -> set:
            return _stripe_groups(sb, groups)

        if select:
            run(wanted, done)          # the first random-access recovery stage (the index's stripes) ran above
        else:
            run(None, done)
        stage["pass2_decode"] = time.perf_counter() - t2
        if ev is not None:
            ev.emit("pass2_end", "outer", groups_total=total, groups_decoded=decoded, groups_failed=len(failed),
                    consensus_attempted=stats.get("consensus_attempted", 0),
                    consensus_recovered=stats.get("consensus_recovered", 0),
                    rows_recovered_by_columns=None if v6 is None else len(v6.recovered),
                    seconds=round(stage["pass2_decode"], 4))
        planner.outcome("OUTER", groups_failed=len(failed), consensus_recovered=stats.get("consensus_recovered", 0),
                        rows_recovered_by_columns=None if v6 is None else len(v6.recovered))
        indel = stats.pop("_indel", None)
        schedule = stats.pop("_schedule", None)
        report = {"superblock": {"archive_id": sb.archive_id.hex(), "container_size": sb.container_size, "groups": sb.group_count,
                                 "outer_code": codec.configuration(), "layout": lay.to_dict(),
                                 "version": sb.version},
                  "reads": dict(stats), **sb_info, "duplicate_conflicts": conflicts}
        if v6 is not None:
            report["outer_v6"] = v6.report()
        indel = dict(indel or {})
        softd = {k[5:]: v for k, v in indel.items() if k.startswith("soft_")}
        indel = {k: v for k, v in indel.items() if not k.startswith("soft_")}
        if opt.indel_recovery == "smart":
            report["indel_recovery"] = {"mode": "smart", "config": dict(opt.indel_config.__dict__), **indel}
        if opt.soft_decoding != "off":
            report["soft_decoding"] = {"mode": opt.soft_decoding, "config": dict(opt.soft_config.__dict__), **softd}
        if opt.indel_recovery == "smart" or opt.soft_decoding != "off":
            report["recovery_schedule"] = schedule if schedule is not None else {"mode": "eager"}
        report["recovery_plan"] = planner.report()
        if select:
            recover = None if recover_groups is None else (lambda need: recover_groups(stripes_of(need)))
            res = _selective(sb, work, fd, run, done, failed, select, select_dir, key, passphrase, overwrite, report, stage, t0,
                             recover, allow_unencrypted)
            if v6 is not None:
                res.report["outer_v6"] = v6.report()
            if recover_groups is not None:
                res.report["recovery_schedule"] = dict(recover_groups.state.get("info", {}), random_access_stages=
                                                       recover_groups.state.get("calls", 0))
            res.report["recovery_plan"] = planner.report()
            return res
    finally:
        os.close(fd)
        if v6 is not None:
            v6.parity_rows.close()
    report["groups_decoded"] = decoded
    report["groups_failed"] = len(failed)
    report["failed_groups"] = sorted(failed)[:100]
    if failed:
        planner.decide("REJECT", True, f"{len(failed)} group(s) unrecovered: the container is not published; only files "
                       "whose every chunk verifies may be extracted", {"groups_failed": len(failed)})
        report["recovery_plan"] = planner.report()
        report["status"] = "PARTIAL"
        report.update(_partial(sb, work, failed, partial_dir, key, passphrase, overwrite, allow_unencrypted))
        report["stage_seconds"] = stage
        report["seconds"] = time.perf_counter() - t0
        report["peak_rss_bytes"] = peak_rss_bytes()
        return DecodeResult("PARTIAL" if report.get("files_recovered") else "FAILURE", report)
    t3 = time.perf_counter()
    h = hashlib.sha256()
    with open(work, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    if ev is not None:
        ev.emit("verify", "integrity", sha256_match=h.digest() == sb.container_sha256)
    if h.digest() != sb.container_sha256:
        planner.decide("REJECT", True, "container SHA-256 differs from the superblock: nothing published")
        report["recovery_plan"] = planner.report()
        report["status"] = "FAILURE"
        raise VNXIntegrityError("reconstructed container does not match the SHA-256 recorded in the superblock; nothing published",
                                details=report, code="CONTAINER_HASH_MISMATCH")
    report["encrypted"] = ct.open_container(work).encrypted     # structural + manifest + Merkle validation
    stage["verify"] = time.perf_counter() - t3
    report["container_sha256"] = h.hexdigest()
    report["status"] = "SUCCESS"
    if output is not None:
        with atomic_output(output, overwrite=overwrite) as tmp:
            shutil.copyfile(work, tmp)
    report["stage_seconds"] = stage
    report["seconds"] = time.perf_counter() - t0
    report["peak_rss_bytes"] = peak_rss_bytes()
    return DecodeResult("SUCCESS", report, str(output) if output else None)


def _partial(sb: Superblock, work: Path, failed: dict, partial_dir, key, passphrase, overwrite,
             allow_unencrypted=False) -> dict:
    """Recover individually verified files from an incomplete container (only if the index section survived)."""
    K, P = sb.K, sb.layout.payload_bytes
    lost_ranges = [[g * K * P, min(sb.container_size, (g + 1) * K * P)] for g in sorted(failed)]
    info: dict = {"lost_container_ranges": lost_ranges[:100], "files_recovered": [], "files_lost": []}
    index_lost = any(e > sb.index_offset for _, e in lost_ranges)
    if index_lost:
        info["partial_note"] = "the archive index (tables/manifest) was not recovered; no file can be verified"
        return info
    if 0 in failed:
        # the 16-byte header is a constant of format 4.0 (magic, version, zero flags); restoring it lets the surviving
        # index be read. Every file is still verified chunk by chunk (SHA-256 + chunk ID + file SHA-256) below.
        import struct
        from vnxdna.core.version import FORMAT_VERSION
        with open(work, "r+b") as f:
            f.write(ct.MAGIC + struct.pack(">HHI", FORMAT_VERSION[0], FORMAT_VERSION[1], 0))
        info["header_restored"] = True
    try:
        c = ct.open_container(work, key=key, passphrase=passphrase, require_key=True, allow_unencrypted=allow_unencrypted)
    except VNXKeyError as error:
        info["partial_note"] = f"no file extracted: {error}"
        return info
    except Exception as error:  # noqa: BLE001 - report, never publish
        info["partial_note"] = f"index section did not validate: {error}"
        return info
    info["encrypted"] = c.encrypted
    for rec in c.files:
        if rec.type != ct.TYPE_FILE:
            continue
        ok = True
        for idx in c.file_chunks(rec).tolist():
            off, n = c.chunk_range(idx)
            if any(s < off + n and off < e for s, e in lost_ranges):
                ok = False
                break
        (info["files_recovered"] if ok else info["files_lost"]).append(rec.path)
    if partial_dir and info["files_recovered"]:
        res = ar.extract(work, partial_dir, key=key, passphrase=passphrase, names=info["files_recovered"], overwrite=overwrite,
                         allow_unencrypted=allow_unencrypted)
        info["partial_extract"] = res
    return info


def _selective(sb, work, fd, run, done, failed, select, select_dir, key, passphrase, overwrite, report, stage, t0,
               recover=None, allow_unencrypted=False) -> DecodeResult:
    """Random access: decode the index groups, then only the groups holding the selected files."""
    K, P = sb.K, sb.layout.payload_bytes
    if failed:
        raise VNXDecodeError("the archive index could not be decoded; selective extraction impossible",
                             details={"failed_groups": sorted(failed)[:20]})
    c = ct.open_container(work, key=key, passphrase=passphrase, require_key=True, allow_unencrypted=allow_unencrypted)
    report["encrypted"] = c.encrypted
    need: set[int] = set()
    for name in select:
        rec = c.file(name)
        for idx in c.file_chunks(rec).tolist():
            off, n = c.chunk_range(idx)
            need.update(range(off // (K * P), (off + n - 1) // (K * P) + 1))
    t = time.perf_counter()
    if recover is not None and need - done:
        recover(need - done)                     # smart/soft recovery for the selected files' stripes only
    run(need - done, done)
    stage["selective_decode"] = time.perf_counter() - t
    if failed:
        raise VNXDecodeError("groups holding the selected files could not be decoded", details={"failed_groups": sorted(failed)[:20]})
    res = ar.extract(work, select_dir or ".", key=key, passphrase=passphrase, names=select, overwrite=overwrite,
                     allow_unencrypted=allow_unencrypted)
    report.update({"status": "SUCCESS", "selected": select, "groups_decoded": len(done), "groups_total": sb.group_count,
                   "fraction_of_groups_decoded": round(len(done) / sb.group_count, 6), "extract": res, "stage_seconds": stage,
                   "seconds": time.perf_counter() - t0, "peak_rss_bytes": peak_rss_bytes()})
    return DecodeResult("SUCCESS", report, str(select_dir))
