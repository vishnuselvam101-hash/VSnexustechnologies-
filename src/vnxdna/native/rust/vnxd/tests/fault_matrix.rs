//! The P4 fault-injection matrix (docs/V14_FAILURE_AND_RECOVERY_MODEL.md §6): every row injects one fault into a
//! volume behind the fallback layer and checks the RECORDED expected outcome, 20 times with different data. The
//! invariant behind every row: no request ever returns bytes other than the last completed write of that range.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use vnxd::addr::{ExtentId, Geometry};
use vnxd::error::{err, ErrorCode};
use vnxd::fallback::{BackendState, HealthConfig, ResilientMedia, RetryPolicy};
use vnxd::fault::{Fault, FaultMedia, MediaOp, Rule};
use vnxd::media::MediaAdapter;
use vnxd::state::WriteState;
use vnxd::volume::{FaultHook, Stage, Volume, VolumeConfig, JOURNAL_FILE};

const RUNS: u64 = 20;
const EXT: usize = 2048;

struct Dir(std::path::PathBuf);
impl Dir {
    fn new(tag: &str) -> Dir {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!("vnxd-fm-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
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

struct Rig {
    d: Dir,
    fm: Arc<FaultMedia>,
    rm: Arc<ResilientMedia>,
    v: Volume,
    old: Vec<u8>,
    new: Vec<u8>,
}

fn rig(run: u64, cfg: VolumeConfig) -> Rig {
    let d = Dir::new("rig");
    let fm = Arc::new(FaultMedia::new());
    let health = HealthConfig { degrade_after: 1, unavailable_after: 3, probe_interval: Duration::from_secs(3600), score_alpha: 0.2 };
    let retry = RetryPolicy { max_attempts: 3, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    let rm = Arc::new(ResilientMedia::new(fm.clone(), retry, health).unwrap());
    let v = Volume::create(&d.0, Geometry::new(512, 4, 16).unwrap(), [run as u8; 16], 0, rm.clone(), cfg).unwrap();
    let old: Vec<u8> = (0..EXT).map(|i| (i as u64 * 7 + run) as u8).collect();
    let new: Vec<u8> = (0..EXT).map(|i| (i as u64 * 13 + run * 3 + 1) as u8).collect();
    v.write_extent(ExtentId(1), &old, 1000).unwrap();
    v.write_extent(ExtentId(2), &old, 1000).unwrap();
    Rig { d, fm, rm, v, old, new }
}

/// Every live generation is referenced, nothing else is stored (+ the marker).
fn no_orphans(v: &Volume, m: &dyn MediaAdapter) {
    let mapped = (0..4).filter(|e| v.mapping(ExtentId(*e)).unwrap().is_some()).count();
    assert_eq!(m.list().unwrap().len(), mapped + 1);
}

#[test]
fn crash_at_every_stage_gives_the_recorded_state() {
    for (stage, new_survives) in [
        (Stage::AfterPut, false),
        (Stage::BeforeMapRecord, false),
        (Stage::TornMapRecord, false),
        (Stage::AfterMapRecord, true),
        (Stage::AfterRemove, true),
    ] {
        for run in 0..RUNS {
            let r = rig(run, VolumeConfig::default());
            let hook: FaultHook = Arc::new(move |s, _| if s == stage { err(ErrorCode::Internal, "crash") } else { Ok(()) });
            let Rig { d, fm, rm, v, old, new } = r;
            let v = v.with_fault_hook(hook);
            assert!(v.write_extent(ExtentId(1), &new, 1000).is_err());
            drop(v);
            let (v, rep) = Volume::open(&d.0, rm.clone(), VolumeConfig::default()).unwrap();
            assert_eq!(&v.read_extent(ExtentId(1)).unwrap(), if new_survives { &new } else { &old }, "{stage:?}");
            assert_eq!(v.read_extent(ExtentId(2)).unwrap(), old);
            assert_eq!(rep.torn_tail.is_some(), stage == Stage::TornMapRecord);
            no_orphans(&v, fm.raw());
        }
    }
}

#[test]
fn transient_backend_errors_are_absorbed_by_retries() {
    for fault in [Fault::Unavailable, Fault::Busy, Fault::Timeout, Fault::AckLost] {
        for run in 0..RUNS {
            let r = rig(run, VolumeConfig::default());
            r.fm.add(Rule { op: MediaOp::Put, fault, skip: 0, times: 2.min(1 + run % 2) });
            assert_eq!(r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap().state, WriteState::Verified, "{fault:?}");
            assert_eq!(r.v.read_extent(ExtentId(1)).unwrap(), r.new);
            no_orphans(&r.v, r.fm.raw());
        }
    }
}

#[test]
fn a_persistently_unavailable_backend_turns_the_device_read_only() {
    for run in 0..RUNS {
        let r = rig(run, VolumeConfig::default());
        r.fm.add(Rule::always(MediaOp::Put, Fault::Unavailable));
        assert_eq!(r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap_err().code, ErrorCode::BackendUnavailable);
        let mut codes = Vec::new();
        for _ in 0..4 {
            codes.push(r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap_err().code);
            assert_eq!(r.v.read_extent(ExtentId(1)).unwrap(), r.old, "reads keep working");
        }
        assert_eq!(r.rm.health().state, BackendState::Unavailable);
        assert_eq!(*codes.last().unwrap(), ErrorCode::ReadOnly);
    }
}

#[test]
fn lying_or_corrupting_writes_are_caught_by_readback() {
    for fault in [Fault::TornPut, Fault::WrongReceipt, Fault::SilentCorruptWrite, Fault::CorruptAckLost] {
        for run in 0..RUNS {
            let r = rig(run, VolumeConfig::default());
            r.fm.add(Rule::once(MediaOp::Put, fault));
            assert_eq!(r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap_err().code, ErrorCode::Corruption, "{fault:?}");
            assert_eq!(r.v.read_extent(ExtentId(1)).unwrap(), r.old, "{fault:?}: the mapping is unchanged");
            no_orphans(&r.v, r.fm.raw());
        }
    }
}

#[test]
fn without_readback_corrupt_media_is_refused_at_read_and_found_by_scrub() {
    for run in 0..RUNS {
        let r = rig(run, VolumeConfig { verify_readback: false, ..Default::default() });
        r.fm.add(Rule::once(MediaOp::Put, Fault::SilentCorruptWrite));
        assert_eq!(r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap().state, WriteState::BackendCommitted);
        assert_eq!(r.v.read_extent(ExtentId(1)).unwrap_err().code, ErrorCode::Corruption, "never the corrupt bytes");
        let s = r.v.scrub().unwrap();
        assert_eq!((s.checked, s.failed.iter().map(|(e, _)| *e).collect::<Vec<_>>()), (2, vec![ExtentId(1)]));
        r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap();
        assert!(r.v.scrub().unwrap().failed.is_empty(), "a whole-extent write repairs it");
    }
}

#[test]
fn bad_reads_are_corruption_never_data() {
    for fault in [Fault::BitRot, Fault::ShortRead, Fault::Lost] {
        for run in 0..RUNS {
            let r = rig(run, VolumeConfig::default());
            r.fm.add(Rule::once(MediaOp::Get, fault));
            assert_eq!(r.v.read_extent(ExtentId(1)).unwrap_err().code, ErrorCode::Corruption, "{fault:?}");
            assert_eq!(r.v.read_extent(ExtentId(1)).unwrap(), r.old, "{fault:?}: a transient fault, the next read is good");
        }
    }
}

#[test]
fn failed_garbage_collection_and_sync_are_recovered() {
    for run in 0..RUNS {
        let r = rig(run, VolumeConfig::default());
        r.fm.add(Rule::always(MediaOp::Remove, Fault::Unavailable));
        r.v.write_extent(ExtentId(1), &r.new, 1000).unwrap(); // the old generation cannot be released
        r.fm.clear();
        let Rig { d, fm, rm, v, new, .. } = r;
        drop(v);
        let (v, rep) = Volume::open(&d.0, rm.clone(), VolumeConfig::default()).unwrap();
        assert_eq!(rep.orphans_removed.len(), 1, "the next open collects it");
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), new);
        no_orphans(&v, fm.raw());
        fm.add(Rule::always(MediaOp::Sync, Fault::Busy));
        assert_eq!(v.flush().unwrap_err().code, ErrorCode::BackendBusy, "a failed flush is reported, retryable");
    }
}

#[test]
fn corrupt_metadata_is_repaired_or_refused() {
    for run in 0..RUNS {
        let r = rig(run, VolumeConfig::default());
        let (d, fm, rm) = (r.d, r.fm.clone(), r.rm.clone());
        drop(r.v);
        let p = d.0.join(JOURNAL_FILE);
        let clean = std::fs::read(&p).unwrap();
        // one copy of any record: repaired
        let at = (run as usize * 37) % clean.len();
        let mut one = clean.clone();
        one[at] ^= 0x20;
        std::fs::write(&p, &one).unwrap();
        let (v, rep) = Volume::open(&d.0, rm.clone(), VolumeConfig::default()).unwrap();
        assert_eq!(rep.journal_repaired, 1, "flip at {at}");
        assert_eq!(v.read_extent(ExtentId(1)).unwrap(), r.old);
        drop(v);
        // both copies of the first map record: refused, nothing collected
        let mut both = clean.clone();
        both[104 + 30] ^= 1;
        both[104 + 88 + 30] ^= 1;
        std::fs::write(&p, &both).unwrap();
        let before = fm.raw().list().unwrap();
        assert_eq!(Volume::open(&d.0, rm.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::Corruption);
        assert_eq!(fm.raw().list().unwrap(), before);
    }
}
