# V14 workstream D: streaming volume reliability (`vnx.volume/1`)

Scope: the Python volume in `src/vnxdna/volume/` (`Volume`, `Journal`), the disk-like layer of V13 that stores each
extent as an ordinary VNX archive encoded to a strand file. This document does not cover the Rust `vnxd` block volume
(docs/V14_VOLUME_AND_IO.md); some journal rules below follow its design. The strands are files. No DNA is involved.

The tests are in `tests/v14/test_volume_reliability.py`: 23 fault-injection tests, each followed by a reopen and a
scrub. Three of them kill a real child process with SIGKILL. The `tests/v13/test_volume.py` tests still apply.

## 1. Crash-recovery contract

**Acknowledged state.** The contract counts a commit as acknowledged once `flush()` returns for it. A `write()` that
returns after the write-back buffer flushed itself also counts, as does a `close()` that returns. An acknowledged
commit survives a process crash. It also survives a power loss if the file system honours `fsync` on files and on
directories. Every commit follows this order:

1. The strand file is written to `extents/.e<i>-v<n>.fasta.*.partial`, then flushed and fsynced.
2. It is renamed to `extents/e<i>-v<n>.fasta`.
3. `extents/` is fsynced. A real failure here raises.
4. Only then is the journal record appended and fsynced. It carries the extent, version, length, SHA-256 of the
   bytes, archive ID and tag, and SHA-256 of the strand file.

**Unflushed writes are not durable.** Data in the write-back buffer is lost on a crash. A `write()` covers several
extents; their commits are separate, and they run in ascending extent order. After a crash, each extent therefore
holds either its old content or its new content, never a mix. Within one write, the extents that changed form a
prefix of the touched range. This ordering holds per write only. When the buffer spills, it commits every dirty
extent in ascending order, including extents dirtied by earlier writes, so the ordering does not extend across
writes. A write that raises can be partly applied. The caller still holds the data and can write it again.

**After a crash**, an open restores the last state the journal acknowledged:

- A strand file that no commit record names is an orphan: a flush interrupted before its record. A read-write open
  removes it, together with `.partial` and dot files. A read-only open leaves it, and the scrub lists it.
- A torn last append is ignored and reported (§2). The next append cuts it.
- Recovery is idempotent. Any sequence of read-only and read-write opens and scrubs gives the same state and report.
  Only an append changes the journal.

**Unacknowledged commits can go either way.** A commit whose `flush()` raised, or whose process died, may be visible
after the reopen or may not. The test asserts that each extent is either old or new.

**Integrity.** Every read and every scrub fails closed. A read decodes the strand file, and the decoder verifies the
container. It then checks the bytes against the length and SHA-256 in the journal. A missing, truncated, emptied or
damaged strand file raises a `VNXError` on read. It never returns zeros or wrong bytes. The scrub reports such a file
as `DAMAGED`. A strand file replaced by a SIMULATED read pool of the same extent still verifies, and the scrub lists it
under `changed_files`. The scrub never repairs anything: an extent it reports as damaged has to be written again from
its source.

## 2. Journal: torn tail vs damage

Each line of the journal is a canonical JSON record with its own CRC-32 and sequence number. Every append is fsynced
before the next one starts, so only the last append can be incomplete. Replay reads the verified, in-sequence records
and then classifies the bytes after the last one:

| after the last verified record | meaning | result |
|---|---|---|
| nothing | clean | opens |
| an unterminated prefix of a record line (starts with `{` and does not close its object before its last byte) | torn last append | opens; `tail_state = "torn"`, `bad_tail_bytes` reported; the next append cuts it |
| a zero-filled range, unterminated or ending in the newline (the file system extended the file but did not fill it) | torn last append | same |
| an unterminated fragment made only of record characters (lowercase ASCII, digits, JSON punctuation): the remainder of an older, longer torn line that the last record overwrote when the cut of that tail did not reach the disk. The base code did not sync the cut; this code fsyncs the cut before it writes the new line | torn | same |
| any of the above longer than `MAX_RECORD_BYTES` (1,024; the writer refuses longer records) | not one append | **refused** |
| a complete line that does not verify (CRC, JSON, framing), with no zero bytes | damage (for example a bit flip in the last committed record) | **refused** |
| a valid record out of sequence (duplicated, reordered, replayed) | damage | **refused** |
| a record followed by stray bytes, more than one line, bytes outside the record character set, or damage followed by valid records | damage | **refused** |

A refused journal raises `VNXIntegrityError` with `good_bytes` and the reason. The open changes nothing: no
truncation and no orphan removal. The test flips a bit at every offset of a journal, at two bit positions; no flip
opens. The test also cuts the journal at every offset; every cut either opens as a torn tail or, inside the header,
is refused as "not a journal".

**Limit.** Suppose the last committed record later reads back as zeros (a lost write of the last page, or zeroed
media) while its newline survives. That looks exactly like a torn append and is cut. The Rust `vnxd` journal has the
same limit. A torn tail is at most 1,024 bytes, so at most the last two commit records (~370-450 bytes each) can be
lost this way. The open reports any such loss as `bad_tail_bytes`.

Commit records are checked against the invariants the writer keeps, because a CRC is not a MAC. The extent and version
must be non-negative integers. Versions increase by one from 1. The file name must be exactly `e<i>-v<n>.fasta`
(the only name the writer uses; a `.fastq` name is refused), so a record cannot point outside `extents/`. Symlinks are
outside the threat model: a strand file or an `extents/` directory replaced by a symlink is followed like any other
path. Whoever can write the volume directory controls its content. The length must be within `0..extent_bytes`. The SHA-256 fields must be hex,
and the archive tag must be hex. The extents must be contiguous from 0, and only the last one may be short. A
violation raises `VNXFormatError`; it never becomes a `TypeError` or a read outside the volume.

## 3. Failed flushes

| failure (all injected in the tests) | result |
|---|---|
| OSError while writing a strand file (ENOSPC) or on its fsync (EIO) | `flush()` raises; nothing is committed; no partial file remains; the extent stays dirty and a retry in the same session commits |
| a real failure of the `extents/` directory fsync (EIO, ENOSPC) | `flush()` raises `VNXOutputError`; the file is not journaled, so a retry is allowed. EINVAL, ENOTSUP and similar "unsupported" errors are ignored (`journal.sync_dir`). Before V14, `core.fsio.fsync_dir` swallowed every error and the flush reported success |
| any failure inside a journal append (write, flush, fsync, an interrupt), or an interrupt after the append completed but before the volume recorded the commit | the journal is **poisoned**: the record may or may not be on disk, and after a failed fsync a later fsync may report success for data that never reached the disk. Every later `write()` and `flush()` raises `VNXOutputError` before it changes anything. In particular, no strand file is re-encoded under a name that record may hold. `close()` raises if dirty data remains, and the lock is still released. A reopen replays what is actually on disk |
| an interrupt (KeyboardInterrupt) in the middle of an encode | the context manager drops the dirty state; a read-write reopen removes the partial and half-written files; the last commit stands |

## 4. Bounds and observability

- **Decoded-extent cache**: an LRU bounded in bytes (`cache_bytes`). An entry larger than the whole cache is not
  cached and evicts nothing. `stat()["cache"]` reports entries, bytes, limit, hits, misses and evictions.
- **Write-back buffer**: `write_back_bytes`. The buffer is checked after each extent a write or a zero fill touches,
  so one large write holds at most the limit plus one extent. A write far beyond the end commits its zero gap
  progressively instead of building it in memory. The write still counts as one write-back flush in `io.flushes`.
  `stat()["write_back"]` reports `limit` and `peak_bytes` (the high-water mark).
- `stat()["journal"]` reports `records`, `bad_tail_bytes`, `tail` (clean/torn), `poisoned` and `orphans_removed`.
- `scrub()` adds `journal`. It re-reads `volume.json` and the journal from disk, and checks that the journal still
  verifies and still begins with the records the volume opened with. A writer in another process may have appended
  more records; that is not damage, and the count is reported as `newer_records`. The status is `intact`, `damaged`
  or `diverged`, and the scrub reports `DAMAGED` unless the journal is `intact`. Before V14, a journal damaged on
  disk after the open was reported VERIFIED. Orphans are now strand files that no commit record names. Superseded
  versions are kept and are no longer listed as orphans.

## 5. Concurrency

- **One writer per volume, across processes.** A read-write open takes an exclusive `flock` on `volume.lock`; a
  second read-write open raises `VNXOutputError`. The kernel releases the lock when the holder dies, including by
  SIGKILL (tested).
- **Readers never block.** A reader sees the journal as of its open. A record the writer is still appending looks to
  the reader like a torn tail, and the reader ignores it. The writer never deletes a file that a commit record names.
- **A `Volume` object is not thread-safe.** Serialise calls on one object, or give each thread its own read-only
  `Volume`.

## 6. Findings

Severity reflects the consequence of each defect. Every defect has a failing test first, and each was confirmed
against the base code b69e70f.

| # | severity | finding | status | test |
|---|---|---|---|---|
| 1 | HIGH | Arbitrary damage after the last verified record was ignored as a "torn tail". Cases: a bit flip in the last committed record, a flipped newline (two merged records), a duplicated or reordered last record, a complete invalid line followed by a fragment, a zero tail of any length. Acknowledged commits silently disappeared, and a read-write open deleted their strand files as orphans. | fixed (`journal.classify_tail`) | `test_corrupt_last_record_is_damage_not_a_torn_tail`, `test_every_single_byte_flip_in_the_journal_refuses_to_open`, `test_duplicated_reordered_and_garbage_tails_are_refused` |
| 2 | MEDIUM | A failed journal append was not handled. The next append wrote over the partial line without cutting it, so the rest of a longer failed line stayed behind a valid record. A failed fsync was followed by retries that could report success, and by a re-encode of the same strand file name that a possibly-durable record names. | fixed (always cut to the last verified record; poison on failure; flush refuses before encoding) | `test_journal_write_failure_poisons_the_journal`, `test_volume_journal_fsync_failure_is_reported_and_poisons` |
| 3 | MEDIUM | Directory fsync errors (EIO) were swallowed by `core.fsio.fsync_dir`, so a flush reported success when its strand file's directory entry was not known to be durable. | fixed (`journal.sync_dir`, used by the volume and the journal) | `test_directory_fsync_failure_is_not_a_silent_success` |
| 4 | MEDIUM | Commit records were trusted once their CRC verified. A re-CRCed record could name a file outside `extents/` (`../../x`), or carry non-integer or impossible fields. The fields then raised `TypeError`/`ValueError` later, and a huge extent id made reads loop. | fixed (`volume._replay_commits`) | `test_tampered_commit_records_are_refused` |
| 5 | MEDIUM | The write-back bound held only between writes: one 8-extent write with a 2-extent limit held 8 extents dirty, and a write 100 extents past the end built 92 extents of zeros in memory. | fixed (`_bound_dirty`) | `test_write_back_bound_holds_inside_one_large_write` |
| 6 | MEDIUM | The scrub verified extents against the journal held in memory only. A journal damaged on disk after the open (the volume would no longer open) was reported VERIFIED. | fixed (`_scrub_journal`) | `test_scrub_checks_the_persisted_journal` |
| 7 | LOW | An extent larger than the whole cache evicted every cached entry and was then not cached. | fixed | `test_cache_bound_under_range_reads_and_oversized_entries` |
| 8 | LOW | The scrub listed superseded versions (still kept and named by the journal) as orphans. | fixed | every `_assert_healthy` call in the new tests |
| 9 | LOW | `create()` did not sync the parent directory, so an acknowledged create could lose the volume directory's entry on a power loss. | fixed (not testable without power loss; the code path runs in every test) | n/a |
| 11 | MEDIUM | Found in review of c2ae79e. When a torn tail was present, the append cut it without an fsync and then wrote the new line over it. A power loss could keep the new line and lose the cut, leaving the new record followed by the rest of the old torn line, and the c2ae79e classifier refused that shape. | fixed: the cut is fsynced before the new line is written, and the remainder shape opens as torn (base-code volumes can hold it) | `test_cutting_a_torn_tail_is_durable_before_the_new_line`, `test_remnant_of_an_old_torn_tail_behind_a_new_record_still_opens` |
| 12 | LOW | Found in review of c2ae79e. An interrupt after the journal append completed, but before the volume recorded the commit, left the journal usable. A retry then journaled the same version twice, and the volume no longer opened. In addition, `write()` on a poisoned journal still buffered data. | fixed: the journal is poisoned, and `write()` fails fast | `test_interrupt_after_the_journal_append_poisons_and_writes_fail_fast` |
| 13 | INFO | Found in review of c2ae79e. The write-back size was re-summed after every extent, O(dirty extents) each time. | changed: a running counter | `test_write_back_bound_holds_inside_one_large_write` |
| 10 | INFO | The journal synced its directory on every append. Only the create needs it: a size change is covered by the file's fsync. | changed (one fsync fewer per commit) | `test_sigkill_at_every_fsync_of_a_flush` |

Behaviour checked and found correct (regression tests only):

- every truncation of the journal is torn or not a journal;
- zero-filled torn appends;
- interrupted encodes;
- missing, truncated and emptied extents fail closed;
- repeated recovery is idempotent;
- a SIGKILL at each of the 15 fsyncs of a 3-extent flush, in the middle of a journal line, and at an arbitrary moment
  of a write loop always leaves an acknowledged-or-later state that verifies;
- the cross-process write lock.

One existing test changed its expectation. In `tests/v13/test_volume.py::test_torn_journal_tail_is_ignored_and_cut`,
the tail `{…"crc":"deadbeef"}\n{"op":"commit","ext` (a complete invalid record plus a fragment) is now asserted to be
refused, with the journal unchanged: this is finding 1. The rest of that test now runs on a real torn tail (an
unterminated prefix), and all of its other assertions are kept.

## 7. Format and compatibility

The on-disk format is unchanged: `vnx.volume/1` and `vnx.volume-journal/1`, with the same records and file names. The
only new writer-side limit is that a journal line cannot exceed 1,024 bytes; commit records are ~370-450 bytes. The new
checks accept every journal the base code writes. `tests/v14/golden/volume_v13_base.tar.gz` is a volume written by
the base code b69e70f: 4,396 bytes in two extents of 4,096 bytes, with extent 0 rewritten once. It opens, reads,
scrubs `VERIFIED` and takes new commits (`test_volume_written_by_the_base_code_still_opens`). A base-code volume whose
journal has one of the damaged shapes in §2 no longer opens. Before V14 it opened with acknowledged commits missing.

## 8. Reproduce

    PYTHONPATH=src python -m pytest -q tests/v14/test_volume_reliability.py tests/v13/test_volume.py

The tests are deterministic. The SIGKILL tests use fixed kill points: the k-th fsync, or byte n of the journal line.
The arbitrary-moment test uses fixed delays and accepts any state at or after the last acknowledged generation. The
whole file runs in about 30 s on one core.
