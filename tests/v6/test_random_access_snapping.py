"""Random access snaps pending reads exactly as a full decode does (job #56 follow-up, V6 Phase 2).

A pending read's header may be wrong in one byte; it is snapped to the unique *missing* address within one byte. Random
access used to snap against the addresses of the groups it needed only. With that smaller set, a read of another group
(e.g. a column-parity row, needed later by the stripe decoder) could be snapped onto a needed address: it then cost a
consensus vote there (pass 2) or an extra smart-recovery attempt (round A). Found when the 6.0.0.dev0 manifest changed
the container bytes of the job #56 fixtures: select failed where the full decode succeeded (2 of 40 cells) and selecting
one small file examined more reads than selecting every file.

Both places now snap against the full decode's address set and only then restrict to the needed groups.
"""
from __future__ import annotations

import numpy as np

from vnxdna.dnaenc.layout import KIND_DATA
from vnxdna.recovery.consensus import snap_addresses
from vnxdna.recovery.schedule import _targeted
from vnxdna.recovery.spill import Spill

TAG = 0x1234


def _pend(tmp_path, keys):
    sp = Spill(tmp_path, 1, 4, 8)
    rec = np.zeros(len(keys), dtype=sp.pend_dtype)
    for i, (k, t, g, s) in enumerate(keys):
        rec[i]["kind"], rec[i]["tag"], rec[i]["group"], rec[i]["symbol"] = k, t, g, s
        rec[i]["alt"] = (k, t, g, s)
    sp.close()
    return rec


def test_a_read_of_another_group_is_not_snapped_onto_a_needed_address(tmp_path):
    needed = {(KIND_DATA, TAG, 1, 5)}                    # random access needs group 1, symbol 5
    other = (KIND_DATA, TAG, 3, 5)                       # missing too (a column-parity row), not needed now
    read = (KIND_DATA, TAG, 7, 5)                        # one header byte from both (group 1 vs 3 vs 7): ambiguous
    pend = _pend(tmp_path, [read])
    assert _targeted(pend, needed).tolist() == [True]                       # snapped against `needed` only: wrongly
    assert _targeted(pend, needed, needed | {other}).tolist() == [False]    # as a full decode: ambiguous, not snapped
    keys = np.array([read], dtype=np.int64)
    assert snap_addresses(keys, needed)[1] == 1 and snap_addresses(keys, needed | {other})[1] == 0


def test_unambiguous_reads_are_still_snapped_and_exact_ones_kept(tmp_path):
    needed = {(KIND_DATA, TAG, 1, 5)}
    every = needed | {(KIND_DATA, TAG, 3, 9)}
    pend = _pend(tmp_path, [(KIND_DATA, TAG, 1, 5), (KIND_DATA, TAG, 1, 4), (KIND_DATA, TAG, 3, 9)])
    assert _targeted(pend, needed, every).tolist() == [True, True, False]
