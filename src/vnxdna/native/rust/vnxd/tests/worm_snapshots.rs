//! Task D2: AP-1 snapshots and WORM retention - Snapshot methods tests

use std::sync::Arc;
use vnxd::addr::{ExtentId, Generation, GenerationRef, Geometry};
use vnxd::error::ErrorCode;
use vnxd::fault::{Fault, FaultMedia, MediaOp, Rule};
use vnxd::journal::Journal;
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::record::{Record, Retention};
use vnxd::volume::{Volume, VolumeConfig, JOURNAL_FILE};

const EXTENT_SIZE: usize = 2048;

fn data_for_extent(extent: u64, run: u64) -> Vec<u8> {
    (0..EXTENT_SIZE).map(|i| ((extent * 1000 + i as u64 + run * 7) % 256) as u8).collect()
}

/// The directory the volume of this run lives in.
fn volume_dir(run: u64) -> std::path::PathBuf {
    std::env::temp_dir().join(format!("vnxd-worm-d2-{}-{}", std::process::id(), run))
}

/// A volume directory, removed when the test ends: the reopen tests need it to outlive the volume they reopen.
struct Dir(std::path::PathBuf);

impl Dir {
    fn new(run: u64) -> Dir {
        let p = volume_dir(run);
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn create_volume(run: u64) -> (Volume, Arc<MemoryMedia>) {
    // a directory left behind by an earlier run would hold its journal: the volume needs an empty one
    let _ = std::fs::remove_dir_all(&volume_dir(run));
    let media = Arc::new(MemoryMedia::new());
    let geo = Geometry::new(512, 4, 16).unwrap(); // 16 extents
    let vol = Volume::create(
        &volume_dir(run),
        geo,
        [run as u8; 16],
        0,
        media.clone(),
        VolumeConfig::default(),
    ).unwrap();
    (vol, media)
}

/// The transaction id of a journal record, if it has one.
fn record_txid(r: &Record) -> Option<u64> {
    match r {
        Record::Superblock(_) => None,
        Record::MapUpdate(m) => Some(m.txid),
        Record::MapBatch(b) => Some(b.txid),
        Record::GenerationReleased(g) => Some(g.txid),
        Record::Snapshot(s) => Some(s.txid),
        Record::Retention(x) => Some(x.txid),
    }
}

/// Append a retention policy for snapshot `id` to the volume's journal. The volume has no public setter for a
/// policy, so it is written the way the service persists one and the volume is reopened to replay it.
fn inject_retention(dir: &std::path::Path, id: u64, until_ms: i64, worm: bool) {
    let mut opened = Journal::open(&dir.join(JOURNAL_FILE)).unwrap();
    let last = opened.records.iter().filter_map(record_txid).max().unwrap_or(0);
    opened.journal.append(&Record::Retention(Retention { txid: last + 1, id, until_ms, worm })).unwrap();
}

#[test]
fn snapshot_pins_current_mapping() {
    let (vol, _media) = create_volume(1);

    // Write some data
    let data1 = data_for_extent(1, 1);
    let data2 = data_for_extent(2, 1);
    vol.write_extent(ExtentId(1), &data1, 1000).unwrap();
    vol.write_extent(ExtentId(2), &data2, 1000).unwrap();

    // Create a snapshot
    let snap_info = vol.snapshot("snap1", 1000).unwrap();

    // Verify snapshot info
    assert_eq!(snap_info.name, "snap1");
    assert_eq!(snap_info.created_ms, 1000);
    assert!(snap_info.id > 0);
    assert_eq!(snap_info.extents, 2);

    // Overwrite the extents
    let new_data1 = data_for_extent(1, 2);
    let new_data2 = data_for_extent(2, 2);
    vol.write_extent(ExtentId(1), &new_data1, 1000).unwrap();
    vol.write_extent(ExtentId(2), &new_data2, 1000).unwrap();

    // Read from snapshot - should get old data
    let snap_data1 = vol.read_snapshot_extent(snap_info.id, ExtentId(1)).unwrap();
    let snap_data2 = vol.read_snapshot_extent(snap_info.id, ExtentId(2)).unwrap();
    assert_eq!(snap_data1, data1);
    assert_eq!(snap_data2, data2);

    // Read current - should get new data
    let cur_data1 = vol.read_extent(ExtentId(1)).unwrap();
    let cur_data2 = vol.read_extent(ExtentId(2)).unwrap();
    assert_eq!(cur_data1, new_data1);
    assert_eq!(cur_data2, new_data2);
}

#[test]
fn snapshot_not_found_for_unpinned_extent() {
    let (vol, _media) = create_volume(2);

    // Write extent 1 only
    let data1 = data_for_extent(1, 1);
    vol.write_extent(ExtentId(1), &data1, 1000).unwrap();

    // Create snapshot
    let snap_info = vol.snapshot("snap1", 1000).unwrap();

    // Try to read extent 2 from snapshot - should fail with NotFound
    let err = vol.read_snapshot_extent(snap_info.id, ExtentId(2)).unwrap_err();
    assert_eq!(err.code, ErrorCode::NotFound);
}

#[test]
fn snapshots_list() {
    let (vol, _media) = create_volume(3);

    let data1 = data_for_extent(1, 1);
    vol.write_extent(ExtentId(1), &data1, 1000).unwrap();

    // Create multiple snapshots
    let snap1 = vol.snapshot("snap1", 1000).unwrap();
    let snap2 = vol.snapshot("snap2", 2000).unwrap();

    let snapshots = vol.snapshots().unwrap();
    assert_eq!(snapshots.len(), 2);

    // Should be ordered by id (creation order)
    assert_eq!(snapshots[0].id, snap1.id);
    assert_eq!(snapshots[0].name, "snap1");
    assert_eq!(snapshots[0].created_ms, 1000);

    assert_eq!(snapshots[1].id, snap2.id);
    assert_eq!(snapshots[1].name, "snap2");
    assert_eq!(snapshots[1].created_ms, 2000);
}

#[test]
fn release_snapshot_removes_pins() {
    let (vol, _media) = create_volume(4);

    let data1 = data_for_extent(1, 1);
    vol.write_extent(ExtentId(1), &data1, 1000).unwrap();

    let snap_info = vol.snapshot("snap1", 1000).unwrap();

    // Overwrite extent
    let new_data = data_for_extent(1, 2);
    vol.write_extent(ExtentId(1), &new_data, 1000).unwrap();

    // Verify snapshot still works
    let snap_data = vol.read_snapshot_extent(snap_info.id, ExtentId(1)).unwrap();
    assert_eq!(snap_data, data1);

    // Release snapshot
    vol.release_snapshot(snap_info.id, 5000).unwrap();

    // Now snapshot should be gone
    let err = vol.read_snapshot_extent(snap_info.id, ExtentId(1)).unwrap_err();
    assert_eq!(err.code, ErrorCode::NotFound);

    // And snapshots list should be empty
    let snapshots = vol.snapshots().unwrap();
    assert_eq!(snapshots.len(), 0);
}

/// A snapshot read serves the bytes of the generation it pinned, not the ones the extent holds now, and the pinned
/// generation survives the overwrite: it is live data, not garbage.
#[test]
fn snapshot_read_returns_pre_overwrite_bytes() {
    let (vol, media) = create_volume(5);

    let original = data_for_extent(0, 1);
    let replacement = data_for_extent(0, 2);
    assert_ne!(original, replacement, "the overwrite must really change the bytes");

    vol.write_extent(ExtentId(0), &original, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    assert_eq!(snap_info.extents, 1);

    // The overwrite writes a NEW generation; the pinned one is still live data
    let outcome = vol.write_extent(ExtentId(0), &replacement, 1000).unwrap();
    assert_eq!(outcome.generation.get(), 2);
    let pinned = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
    assert!(media.list().unwrap().contains(&pinned), "the overwrite must not release the pinned generation");

    // The snapshot still serves the bytes of the generation it pinned, and verifies them: the map holds the digest
    // of the CURRENT generation, so the snapshot's own digest is the one that must be used
    assert_eq!(vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap(), original);

    // The volume itself serves the new bytes
    assert_eq!(vol.read_extent(ExtentId(0)).unwrap(), replacement);
}

/// A snapshot is replayed from the journal, so it outlives the process that took it.
#[test]
fn snapshot_survives_reopen() {
    let dir = Dir::new(6);
    let (vol, media) = create_volume(6);

    let data = data_for_extent(0, 1);
    vol.write_extent(ExtentId(0), &data, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    drop(vol);

    // The snapshot is reconstructed by the journal replay
    let (vol, report) = Volume::open(&dir.0, media, VolumeConfig::default()).unwrap();
    assert!(report.torn_tail.is_none() && report.damaged.is_empty(), "the journal replays cleanly");
    let snapshots = vol.snapshots().unwrap();
    assert_eq!(snapshots.len(), 1, "the snapshot survives the reopen");
    assert_eq!(snapshots[0].id, snap_info.id);
    assert_eq!(snapshots[0].created_ms, 1000);
    assert_eq!(snapshots[0].extents, 1);
    // The journal record carries no name: a replayed snapshot is listed under its generated name
    assert_eq!(snapshots[0].name, format!("snapshot-{}", snap_info.id));

    // And it still serves the bytes it pinned
    assert_eq!(vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap(), data);

    // A snapshot taken after the reopen does not reuse the id of the replayed one
    assert_eq!(vol.snapshot("snap2", 2000).unwrap().id, snap_info.id + 1);
}

/// A generation a live snapshot pins is live data: the orphan collector of the next open must leave it alone.
#[test]
fn pinned_generation_is_not_collected_as_orphan() {
    let dir = Dir::new(7);
    let (vol, media) = create_volume(7);

    let original = data_for_extent(0, 1);
    let replacement = data_for_extent(0, 2);
    vol.write_extent(ExtentId(0), &original, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();

    // The overwrite leaves the pinned generation in the backend, unreferenced by the map
    vol.write_extent(ExtentId(0), &replacement, 1000).unwrap();
    drop(vol);

    let (vol, report) = Volume::open(&dir.0, media.clone(), VolumeConfig::default()).unwrap();
    assert!(report.orphans_removed.is_empty(), "a pinned generation is live data, not an orphan: {:?}", report.orphans_removed);
    let pinned = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
    assert!(media.list().unwrap().contains(&pinned), "the orphan collector kept the pinned generation");

    // The snapshot still reads its own bytes
    assert_eq!(vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap(), original);
    // While the volume serves the overwritten bytes
    assert_eq!(vol.read_extent(ExtentId(0)).unwrap(), replacement);
}

/// A release under an unexpired retention policy is refused, and the refusal changes nothing: the snapshot stays,
/// its pinned generation stays in the backend and its bytes are still readable.
#[test]
fn release_snapshot_under_retention_is_refused_and_keeps_snapshot() {
    let dir = Dir::new(8);
    let (vol, media) = create_volume(8);

    let original = data_for_extent(0, 1);
    vol.write_extent(ExtentId(0), &original, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    // The extent is overwritten afterwards: the pinned generation is no longer the live one, so a release that
    // removed before it checked the policy would drop it from the backend
    vol.write_extent(ExtentId(0), &data_for_extent(0, 2), 1000).unwrap();
    drop(vol);

    // A retention policy for the snapshot, not yet expired
    inject_retention(&dir.0, snap_info.id, 60_000, true);
    let (vol, _report) = Volume::open(&dir.0, media.clone(), VolumeConfig::default()).unwrap();
    assert_eq!(vol.snapshots().unwrap().len(), 1, "the policy is replayed together with the snapshot");

    // The release is refused: the deadline has not passed
    let err = vol.release_snapshot(snap_info.id, 2000).unwrap_err();
    assert_eq!(err.code, ErrorCode::Retained, "{err}");

    // A refused release changes nothing: the snapshot and its bytes are still there
    let snapshots = vol.snapshots().unwrap();
    assert_eq!(snapshots.len(), 1, "the refused release kept the snapshot");
    assert_eq!(snapshots[0].id, snap_info.id);
    assert_eq!(vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap(), original);
    let pinned = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
    assert!(media.list().unwrap().contains(&pinned), "the refused release removed nothing");
}

/// An expired policy does not hold the snapshot any more.
#[test]
fn release_snapshot_after_retention_expires_succeeds() {
    let dir = Dir::new(9);
    let (vol, media) = create_volume(9);

    let data = data_for_extent(0, 1);
    vol.write_extent(ExtentId(0), &data, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    // The extent is overwritten, so the pinned generation is no longer the live one
    vol.write_extent(ExtentId(0), &data_for_extent(0, 2), 1000).unwrap();
    drop(vol);

    // A retention policy that has expired
    inject_retention(&dir.0, snap_info.id, 2000, true);
    let (vol, _report) = Volume::open(&dir.0, media, VolumeConfig::default()).unwrap();

    assert!(vol.release_snapshot(snap_info.id, 5000).is_ok(), "an expired policy does not hold the snapshot");
    assert!(vol.snapshots().unwrap().is_empty());
    let err = vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap_err();
    assert_eq!(err.code, ErrorCode::NotFound);
}

/// A snapshot read of an extent whose stored bytes were tampered with is refused: the read never returns other
/// bytes than the ones the snapshot pinned.
#[test]
fn snapshot_read_of_a_tampered_extent_is_refused() {
    let dir = Dir::new(10);
    // The fault adapter stores other bytes than it was given, with an honest receipt
    let media = Arc::new(FaultMedia::over(Arc::new(MemoryMedia::new())));
    let geo = Geometry::new(512, 4, 16).unwrap();
    let vol = Volume::create(
        &dir.0,
        geo,
        [10; 16],
        0,
        media.clone(),
        VolumeConfig { verify_readback: false, ..VolumeConfig::default() },
    ).unwrap();

    let original = data_for_extent(0, 1);
    // The device marker was already put, so the next put is the extent: the backend stores other bytes than it
    // was given, with an honest receipt
    media.add(Rule { op: MediaOp::Put, fault: Fault::SilentCorruptWrite, skip: 0, times: 1 });
    vol.write_extent(ExtentId(0), &original, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    // The extent is overwritten, so the map no longer holds the digest of the pinned generation
    vol.write_extent(ExtentId(0), &data_for_extent(0, 2), 1000).unwrap();

    // The stored bytes are not the ones the snapshot pinned: the read must refuse them, never return them
    let err = vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap_err();
    assert_eq!(err.code, ErrorCode::Corruption, "the snapshot read must refuse the tampered bytes");
}

/// A snapshot read of an extent whose bytes rot on the way out of the backend is refused too.
#[test]
fn snapshot_read_of_rotted_bytes_is_refused() {
    let dir = Dir::new(11);
    let media = Arc::new(FaultMedia::over(Arc::new(MemoryMedia::new())));
    let geo = Geometry::new(512, 4, 16).unwrap();
    let vol = Volume::create(&dir.0, geo, [11; 16], 0, media.clone(), VolumeConfig::default()).unwrap();

    let original = data_for_extent(0, 1);
    vol.write_extent(ExtentId(0), &original, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    vol.write_extent(ExtentId(0), &data_for_extent(0, 2), 1001).unwrap();

    // The next read of the backend comes back with one bit flipped
    media.add(Rule { op: MediaOp::Get, fault: Fault::BitRot, skip: 0, times: 1 });
    let err = vol.read_snapshot_extent(snap_info.id, ExtentId(0)).unwrap_err();
    assert_eq!(err.code, ErrorCode::Corruption, "the snapshot read must refuse the rotten bytes");
}

// ---------------------------------------------------------------------------------------------
// The two tests below state the behaviour the snapshot and retention model requires: a policy recorded
// for a snapshot holds that snapshot, and a release never releases the generation the map still points
// at. They were written before the volume was corrected and they pass now.
// ---------------------------------------------------------------------------------------------

/// A retention policy belongs to the snapshot it was recorded for, whatever extents that snapshot pins. The
/// volume keeps such a policy under the snapshot id and consults it on the snapshot, so a policy recorded for a
/// snapshot that pins extent 1 refuses the release of that snapshot.
#[test]
fn release_snapshot_under_retention_is_refused_for_every_pinned_extent() {
    let dir = Dir::new(12);
    let (vol, media) = create_volume(12);

    let data = data_for_extent(1, 1);
    vol.write_extent(ExtentId(1), &data, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();
    drop(vol);

    // A retention policy for the snapshot, not yet expired
    inject_retention(&dir.0, snap_info.id, 60_000, true);
    let (vol, _report) = Volume::open(&dir.0, media.clone(), VolumeConfig::default()).unwrap();

    let err = vol.release_snapshot(snap_info.id, 2000).unwrap_err();
    assert_eq!(err.code, ErrorCode::Retained, "{err}");

    // The refused release kept the snapshot
    let snapshots = vol.snapshots().unwrap();
    assert_eq!(snapshots.len(), 1, "the refused release kept the snapshot");
    assert_eq!(snapshots[0].id, snap_info.id);
    assert_eq!(vol.read_snapshot_extent(snap_info.id, ExtentId(1)).unwrap(), data);
}

/// Releasing a snapshot that pins the generation the map still points at must not release that generation: the
/// map holds it, so the volume is still serving it. The release skips it, and the next open accepts the journal.
#[test]
fn releasing_a_snapshot_of_the_live_generation_keeps_the_volume_openable() {
    let dir = Dir::new(13);
    let (vol, media) = create_volume(13);

    let data = data_for_extent(1, 1);
    vol.write_extent(ExtentId(1), &data, 1000).unwrap();
    let snap_info = vol.snapshot("snap1", 1000).unwrap();

    // The extent is not overwritten: the snapshot pins the generation the map still points at
    vol.release_snapshot(snap_info.id, 2000).unwrap();
    assert!(vol.snapshots().unwrap().is_empty(), "the snapshot is released");
    assert_eq!(vol.read_extent(ExtentId(1)).unwrap(), data, "the volume still serves the extent");
    drop(vol);

    // The volume must open again: the released generation is the live one, it was never garbage
    let (vol, _report) = Volume::open(&dir.0, media, VolumeConfig::default()).unwrap();
    assert_eq!(vol.read_extent(ExtentId(1)).unwrap(), data);
}
