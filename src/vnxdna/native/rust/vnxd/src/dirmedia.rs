//! A persistent media adapter for the block device: one file per generation in a directory, fsynced
//! ([`Durability::FileSync`], electronic storage, evidence category C). It is the backend the P8 device runs on
//! until the image backend of the media-adapter phase is wired in; the device only depends on [`MediaAdapter`].
//!
//! Layout: `<dir>/<extent:016x>-<generation:016x>.gen`. `put` writes a temporary file, fsyncs it, links it to its
//! final name (the link fails if the generation exists: generations are immutable even across processes) and
//! fsyncs the directory; `remove` unlinks and fsyncs the directory; `sync` fsyncs the directory. Temporary files
//! left by a crash are deleted when the adapter is opened. Names that do not parse are refused by `list` (an
//! unknown file in a backend directory is CORRUPTION, never silently ignored). An exclusive lock file
//! (`<dir>/.lock`, linked into place already holding the owner's pid) keeps two services off one backend; a lock
//! whose owner no longer runs is taken over (two openers taking over the same stale lock at the same instant, or a
//! pid seen from another pid namespace, are not covered; the device's default backend is ImageMedia).

use crate::addr::{ExtentId, Generation, GenerationRef};
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const SUFFIX: &str = ".gen";
const TMP_PREFIX: &str = ".tmp-";
const LOCK: &str = ".lock";

fn unavailable(what: &str, e: std::io::Error) -> VnxError {
    let code = match e.kind() {
        std::io::ErrorKind::NotFound => ErrorCode::NotFound,
        _ if e.raw_os_error() == Some(28) => ErrorCode::NoSpace, // ENOSPC
        _ => ErrorCode::BackendUnavailable,
    };
    VnxError::new(code, format!("{what}: {e}"))
}

pub struct DirMedia {
    dir: PathBuf,
    tmp_seq: AtomicU64,
}

fn name_of(at: GenerationRef) -> String {
    format!("{:016x}-{:016x}{SUFFIX}", at.extent.0, at.generation.get())
}

fn parse_name(n: &str) -> Option<GenerationRef> {
    let stem = n.strip_suffix(SUFFIX)?;
    let (e, g) = stem.split_once('-')?;
    if e.len() != 16 || g.len() != 16 || !(e.bytes().chain(g.bytes())).all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f')) {
        return None;
    }
    let generation = Generation::new(u64::from_str_radix(g, 16).ok()?).ok()?;
    Some(GenerationRef { extent: ExtentId(u64::from_str_radix(e, 16).ok()?), generation })
}

fn pid_alive(pid: u32) -> bool {
    Path::new(&format!("/proc/{pid}")).exists()
}

impl DirMedia {
    /// Open (creating it if needed) a backend directory and take its lock.
    pub fn open(dir: &Path) -> Result<DirMedia> {
        std::fs::create_dir_all(dir).map_err(|e| unavailable(&format!("create {}", dir.display()), e))?;
        let lock = dir.join(LOCK);
        for _ in 0..2 {
            // the lock appears with its owner's pid already in it (written to a private name, then linked): no
            // opener can ever read an empty lock and take it for a stale one
            let tmp = dir.join(format!("{TMP_PREFIX}lock-{}", std::process::id()));
            let _ = std::fs::remove_file(&tmp);
            let staged = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&tmp)
                .and_then(|mut f| f.write_all(std::process::id().to_string().as_bytes()).and_then(|_| f.sync_all()));
            if let Err(e) = staged {
                let _ = std::fs::remove_file(&tmp);
                return Err(unavailable("write lock", e));
            }
            let linked = std::fs::hard_link(&tmp, &lock);
            let _ = std::fs::remove_file(&tmp);
            match linked {
                Ok(()) => {
                    let m = DirMedia { dir: dir.to_path_buf(), tmp_seq: AtomicU64::new(0) };
                    m.sweep_tmp()?;
                    m.sync_dir()?;
                    return Ok(m);
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                    let owner = std::fs::read_to_string(&lock).ok().and_then(|s| s.trim().parse::<u32>().ok());
                    match owner {
                        Some(pid) if pid != std::process::id() && pid_alive(pid) => {
                            return err(ErrorCode::BackendBusy, format!("backend {} is in use by process {pid}", dir.display()))
                        }
                        Some(pid) if pid == std::process::id() => {
                            return err(ErrorCode::BackendBusy, format!("backend {} is already open in this process", dir.display()))
                        }
                        _ => {
                            // stale lock of a process that is gone: take it over
                            std::fs::remove_file(&lock).map_err(|e| unavailable("remove stale lock", e))?;
                        }
                    }
                }
                Err(e) => return Err(unavailable(&format!("lock {}", lock.display()), e)),
            }
        }
        err(ErrorCode::BackendBusy, format!("backend {} lock is contended", dir.display()))
    }

    fn sweep_tmp(&self) -> Result<()> {
        for e in std::fs::read_dir(&self.dir).map_err(|e| unavailable("list backend", e))? {
            let e = e.map_err(|e| unavailable("list backend", e))?;
            if e.file_name().to_str().map_or(false, |n| n.starts_with(TMP_PREFIX)) {
                std::fs::remove_file(e.path()).map_err(|e| unavailable("remove temporary file", e))?;
            }
        }
        Ok(())
    }

    fn sync_dir(&self) -> Result<()> {
        File::open(&self.dir).and_then(|d| d.sync_all()).map_err(|e| unavailable(&format!("fsync {}", self.dir.display()), e))
    }
}

impl Drop for DirMedia {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(self.dir.join(LOCK));
    }
}

impl MediaAdapter for DirMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "dir", durability: Durability::FileSync, max_in_flight: 8 }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let fin = self.dir.join(name_of(at));
        let tmp = self.dir.join(format!("{TMP_PREFIX}{}-{}", name_of(at), self.tmp_seq.fetch_add(1, Ordering::Relaxed)));
        let write = || -> std::io::Result<()> {
            let mut f = OpenOptions::new().write(true).create_new(true).open(&tmp)?;
            f.write_all(data)?;
            f.sync_all()
        };
        if let Err(e) = write() {
            let _ = std::fs::remove_file(&tmp);
            return Err(unavailable("write generation", e));
        }
        let linked = std::fs::hard_link(&tmp, &fin);
        let _ = std::fs::remove_file(&tmp);
        match linked {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored"));
            }
            Err(e) => return Err(unavailable("commit generation", e)),
        }
        self.sync_dir()?;
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        std::fs::read(self.dir.join(name_of(at))).map_err(|e| unavailable(&format!("read generation {at:?}"), e))
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        std::fs::remove_file(self.dir.join(name_of(at))).map_err(|e| unavailable(&format!("remove generation {at:?}"), e))?;
        self.sync_dir()
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        let mut v = Vec::new();
        for e in std::fs::read_dir(&self.dir).map_err(|e| unavailable("list backend", e))? {
            let e = e.map_err(|e| unavailable("list backend", e))?;
            let n = e.file_name();
            let n = n.to_string_lossy();
            if n == LOCK || n.starts_with(TMP_PREFIX) {
                continue;
            }
            match parse_name(&n) {
                Some(r) => v.push(r),
                None => return err(ErrorCode::Corruption, format!("unexpected file {n:?} in backend {}", self.dir.display())),
            }
        }
        v.sort();
        Ok(v)
    }

    fn sync(&self) -> Result<()> {
        self.sync_dir()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::tests::TempDir;
    use crate::media::conformance;

    #[test]
    fn passes_the_media_conformance_suite() {
        let t = TempDir::new("dirmedia-conf");
        let m = DirMedia::open(&t.0.join("b")).unwrap();
        conformance(&m).unwrap();
    }

    #[test]
    fn persists_across_reopen_and_locks_the_backend() {
        let t = TempDir::new("dirmedia-persist");
        let d = t.0.join("b");
        let at = GenerationRef { extent: ExtentId(7), generation: Generation::new(3).unwrap() };
        {
            let m = DirMedia::open(&d).unwrap();
            assert_eq!(DirMedia::open(&d).err().unwrap().code, ErrorCode::BackendBusy, "one owner per backend");
            m.put(at, b"persisted bytes").unwrap();
            std::fs::write(d.join(format!("{TMP_PREFIX}leftover")), b"torn").unwrap();
        }
        let m = DirMedia::open(&d).unwrap();
        assert_eq!(m.list().unwrap(), vec![at]);
        assert_eq!(m.get(at).unwrap(), b"persisted bytes");
        assert!(!d.join(format!("{TMP_PREFIX}leftover")).exists(), "temporary files are swept at open");
        assert_eq!(m.put(at, b"x").unwrap_err().code, ErrorCode::InvalidArgument);
        drop(m);
        // a stale lock of a dead process is taken over
        std::fs::write(d.join(LOCK), b"4194303").unwrap();
        let m = DirMedia::open(&d).unwrap();
        // an unknown file is corruption, not ignored
        std::fs::write(d.join("stray.txt"), b"?").unwrap();
        assert_eq!(m.list().unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn names_round_trip_and_reject_garbage() {
        let at = GenerationRef { extent: ExtentId(u64::MAX), generation: Generation::FIRST };
        assert_eq!(parse_name(&name_of(at)), Some(at));
        for bad in ["0-1.gen", "000000000000000g-0000000000000001.gen", "0000000000000000-0000000000000000.gen",
            "0000000000000000-0000000000000001", "0000000000000000-0000000000000001.genx", "0000000000000000-00000000000000001.gen"]
        {
            assert_eq!(parse_name(bad), None, "{bad}");
        }
    }
}
