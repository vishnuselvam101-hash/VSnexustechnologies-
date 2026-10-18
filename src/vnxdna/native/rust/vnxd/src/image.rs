//! Electronic image backend (master directive §13 B; docs/V14_MEDIA_ADAPTERS.md §3).
//!
//! [`ImageMedia`] keeps every extent generation as one immutable object file in an image directory on the host's
//! storage. It is ELECTRONIC storage, not DNA: its durability class is `file-fsync`, and its numbers say nothing about
//! any DNA medium. It persists across process restarts.
//!
//! Layout (format `vnx.media-image/1`):
//!
//! ```text
//! <root>/IMAGE                       "vnx.media-image/1\n" + a one-line statement that this is electronic storage
//! <root>/LOCK                        the owning process id (one process per image)
//! <root>/objects/<extent>-<gen>.obj  16 lowercase hex digits each; header + bytes
//! <root>/objects/.tmp-<pid>-<n>      a write in progress (never read; swept at open)
//! ```
//!
//! Object file: magic `VNXIMG01`, extent (u64 LE), generation (u64 LE), length (u64 LE), SHA-256 of the bytes
//! (32 bytes), then the bytes (64-byte header).
//!
//! * **crash-safe writes**: a `put` writes a temporary file (created exclusively, mode 0600), fsyncs it, links it to
//!   its final name with `link(2)` (atomic, and fails if the name exists: a generation can never be replaced, not
//!   even by another process), fsyncs the directory, then removes the temporary name. A crash at any point leaves
//!   either no object or the whole object; temporary files are swept when the image is opened;
//! * **fsync discipline**: a receipt is only returned after the file data and the directory entry were fsynced;
//!   `remove` fsyncs the directory before it returns; `sync` fsyncs the directory again;
//! * **integrity-checked reads**: `get` checks the magic, that the header names the generation asked for, the length
//!   and the SHA-256 before returning anything; a damaged, truncated, swapped or oversized object is CORRUPTION,
//!   never other bytes; a missing one is NOT_FOUND;
//! * host I/O failures are BACKEND_UNAVAILABLE (retryable); a second process opening the same image is BACKEND_BUSY.

use crate::addr::{ExtentId, Generation, GenerationRef, MAX_EXTENT_BYTES};
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo};
use std::fs::{File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

pub const IMAGE_FORMAT: &str = "vnx.media-image/1";
const IMAGE_FILE: &str = "IMAGE";
const LOCK_FILE: &str = "LOCK";
const OBJECTS: &str = "objects";
const MAGIC: &[u8; 8] = b"VNXIMG01";
pub const OBJECT_HEADER: u64 = 64;
/// Default largest object an image accepts or reads back (an extent plus room for an envelope or seal);
/// see [`ImageMedia::with_max_object`].
pub const MAX_OBJECT_BYTES: u64 = MAX_EXTENT_BYTES + (1 << 20);
const STATEMENT: &str = "electronic file-image backend of vnxd: host storage, NOT DNA";

fn io_err(what: &str, p: &Path, e: std::io::Error) -> VnxError {
    VnxError::new(ErrorCode::BackendUnavailable, format!("image {what} {}: {e}", p.display()))
}

fn fsync_dir(p: &Path) -> Result<()> {
    File::open(p).and_then(|d| d.sync_all()).map_err(|e| io_err("fsync", p, e))
}

pub struct ImageMedia {
    root: PathBuf,
    objects: PathBuf,
    tmp_seq: AtomicU64,
    /// the canonical root this instance holds in [`HELD`]
    key: PathBuf,
    max_object: u64,
}

/// Image roots held by an `ImageMedia` of this process (a LOCK naming our own pid but no entry here was left by an
/// earlier incarnation with the same pid, e.g. pid 1 in a restarted container).
static HELD: Mutex<Vec<PathBuf>> = Mutex::new(Vec::new());

/// What a scrub found wrong with one stored object.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScrubFinding {
    pub at: GenerationRef,
    pub code: ErrorCode,
    pub message: String,
}

impl ImageMedia {
    /// Create a new, empty image at `root` (which must not exist, or be an empty directory).
    pub fn create(root: &Path) -> Result<ImageMedia> {
        if root.exists() {
            let empty = std::fs::read_dir(root).map_err(|e| io_err("read", root, e))?.next().is_none();
            if !empty {
                return err(ErrorCode::InvalidArgument, format!("image {}: directory is not empty", root.display()));
            }
        }
        let objects = root.join(OBJECTS);
        std::fs::create_dir_all(&objects).map_err(|e| io_err("create", &objects, e))?;
        let id = root.join(IMAGE_FILE);
        let tmp = root.join(".IMAGE.tmp");
        let _ = std::fs::remove_file(&tmp);
        let mut f = OpenOptions::new().write(true).create_new(true).open(&tmp).map_err(|e| io_err("create", &tmp, e))?;
        f.write_all(format!("{IMAGE_FORMAT}\n{STATEMENT}\n").as_bytes()).and_then(|_| f.sync_all()).map_err(|e| io_err("write", &tmp, e))?;
        std::fs::rename(&tmp, &id).map_err(|e| io_err("rename", &id, e))?;
        fsync_dir(&objects)?;
        fsync_dir(root)?;
        ImageMedia::open(root)
    }

    /// Open an existing image: check its format, take the lock, sweep writes a crash left unfinished.
    pub fn open(root: &Path) -> Result<ImageMedia> {
        let id = root.join(IMAGE_FILE);
        let text = match std::fs::read_to_string(&id) {
            Ok(t) => t,
            Err(e) if e.kind() == ErrorKind::NotFound => {
                return err(ErrorCode::NotFound, format!("{} is not a vnxd image (no {IMAGE_FILE})", root.display()))
            }
            Err(e) => return Err(io_err("read", &id, e)),
        };
        match text.lines().next() {
            Some(IMAGE_FORMAT) => {}
            Some(l) if l.starts_with("vnx.media-image/") => {
                return err(ErrorCode::VersionMismatch, format!("image format {l}; this vnxd reads {IMAGE_FORMAT}"))
            }
            _ => return err(ErrorCode::Corruption, format!("{}: damaged image identifier", id.display())),
        }
        let key = lock(root)?;
        let objects = root.join(OBJECTS);
        let m = ImageMedia { root: root.to_path_buf(), objects, tmp_seq: AtomicU64::new(0), key, max_object: MAX_OBJECT_BYTES };
        // from here on, dropping `m` on an error releases the lock
        let entries = std::fs::read_dir(&m.objects).map_err(|e| io_err("read", &m.objects, e))?;
        let mut swept = false;
        for e in entries.flatten() {
            if e.file_name().to_string_lossy().starts_with(".tmp-") {
                let _ = std::fs::remove_file(e.path());
                swept = true;
            }
        }
        if swept {
            fsync_dir(&m.objects)?;
        }
        Ok(m)
    }

    /// Raise or lower the largest object this image accepts and reads back (default [`MAX_OBJECT_BYTES`]). A pool
    /// store of the DNA simulator needs several times the extent size (strands + ECC).
    pub fn with_max_object(mut self, bytes: u64) -> ImageMedia {
        self.max_object = bytes;
        self
    }

    /// Open the image at `root`, creating it if the directory does not exist or is empty.
    pub fn open_or_create(root: &Path) -> Result<ImageMedia> {
        if root.join(IMAGE_FILE).exists() {
            ImageMedia::open(root)
        } else {
            ImageMedia::create(root)
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    fn path(&self, at: GenerationRef) -> PathBuf {
        self.objects.join(format!("{:016x}-{:016x}.obj", at.extent.0, at.generation.get()))
    }

    /// Verify every stored object (header, length, SHA-256); returns what is wrong, empty when all is well.
    pub fn scrub(&self) -> Result<Vec<ScrubFinding>> {
        let mut out = Vec::new();
        for at in self.list()? {
            if let Err(e) = self.get(at) {
                out.push(ScrubFinding { at, code: e.code, message: e.message });
            }
        }
        Ok(out)
    }
}

impl Drop for ImageMedia {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(self.root.join(LOCK_FILE));
        HELD.lock().unwrap_or_else(|p| p.into_inner()).retain(|k| k != &self.key);
    }
}

/// One process per image (advisory). LOCK holds "<pid> <token>" and is created by linking a complete, fsynced
/// temporary file, so it is never seen empty. A LOCK whose pid no longer runs, or names this process while no
/// `ImageMedia` of this process holds the root, is stale and taken over; the takeover removes it only if it still
/// holds the stale content, then re-reads the new LOCK to confirm ownership.
fn lock(root: &Path) -> Result<PathBuf> {
    static SEQ: AtomicU64 = AtomicU64::new(0);
    let key = std::fs::canonicalize(root).map_err(|e| io_err("resolve", root, e))?;
    let mut held = HELD.lock().unwrap_or_else(|p| p.into_inner());
    if held.contains(&key) {
        return err(ErrorCode::BackendBusy, format!("image {} is already open in this process", root.display()));
    }
    let p = root.join(LOCK_FILE);
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos());
    let me = format!("{} {:x}-{}\n", std::process::id(), nanos, SEQ.fetch_add(1, Ordering::Relaxed));
    let tmp = root.join(format!(".LOCK.{}.{}", std::process::id(), SEQ.fetch_add(1, Ordering::Relaxed)));
    let _ = std::fs::remove_file(&tmp);
    let mut f = OpenOptions::new().write(true).create_new(true).open(&tmp).map_err(|e| io_err("lock", &tmp, e))?;
    f.write_all(me.as_bytes()).and_then(|_| f.sync_all()).map_err(|e| io_err("lock", &tmp, e))?;
    drop(f);
    let r = (|| {
        for _ in 0..3 {
            match std::fs::hard_link(&tmp, &p) {
                Ok(()) => {
                    if std::fs::read_to_string(&p).ok().as_deref() == Some(me.as_str()) {
                        return Ok(());
                    }
                    return err(ErrorCode::BackendBusy, format!("image {}: lost the lock to another process", root.display()));
                }
                Err(e) if e.kind() == ErrorKind::AlreadyExists => {
                    let owner = std::fs::read_to_string(&p).unwrap_or_default();
                    let pid = owner.split_whitespace().next().and_then(|t| t.parse::<u32>().ok());
                    let alive = match pid {
                        Some(pid) if pid == std::process::id() => false, // not in HELD: an earlier incarnation
                        Some(pid) => Path::new(&format!("/proc/{pid}")).exists(),
                        None => false,
                    };
                    if alive {
                        return err(ErrorCode::BackendBusy, format!("image {} is in use by process {}", root.display(), owner.trim()));
                    }
                    if std::fs::read_to_string(&p).unwrap_or_default() == owner {
                        let _ = std::fs::remove_file(&p); // stale: its owner is gone
                    }
                }
                Err(e) => return Err(io_err("lock", &p, e)),
            }
        }
        err(ErrorCode::BackendBusy, format!("image {}: could not take the lock", root.display()))
    })();
    let _ = std::fs::remove_file(&tmp);
    r?;
    held.push(key.clone());
    Ok(key)
}

fn parse_name(name: &str) -> Option<GenerationRef> {
    let stem = name.strip_suffix(".obj")?;
    let (e, g) = stem.split_once('-')?;
    if e.len() != 16 || g.len() != 16 || !(e.bytes().chain(g.bytes())).all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return None;
    }
    let extent = u64::from_str_radix(e, 16).ok()?;
    let generation = Generation::new(u64::from_str_radix(g, 16).ok()?).ok()?;
    Some(GenerationRef { extent: ExtentId(extent), generation })
}

impl MediaAdapter for ImageMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "image", durability: Durability::FileSync, max_in_flight: 16 }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        if data.len() as u64 > self.max_object {
            return err(ErrorCode::InvalidArgument, format!("object of {} bytes exceeds the image limit", data.len()));
        }
        let fin = self.path(at);
        if fin.exists() {
            return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored"));
        }
        let digest = sha256(data);
        let mut head = Vec::with_capacity(OBJECT_HEADER as usize);
        head.extend_from_slice(MAGIC);
        head.extend_from_slice(&at.extent.0.to_le_bytes());
        head.extend_from_slice(&at.generation.get().to_le_bytes());
        head.extend_from_slice(&(data.len() as u64).to_le_bytes());
        head.extend_from_slice(&digest);
        let tmp = self.objects.join(format!(".tmp-{:x}-{}", std::process::id(), self.tmp_seq.fetch_add(1, Ordering::Relaxed)));
        let r = (|| {
            use std::os::unix::fs::OpenOptionsExt;
            let mut f = OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp).map_err(|e| io_err("create", &tmp, e))?;
            f.write_all(&head).and_then(|_| f.write_all(data)).and_then(|_| f.sync_all()).map_err(|e| io_err("write", &tmp, e))?;
            drop(f);
            match std::fs::hard_link(&tmp, &fin) {
                Ok(()) => {}
                Err(e) if e.kind() == ErrorKind::AlreadyExists => {
                    return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored"))
                }
                Err(e) => return Err(io_err("link", &fin, e)),
            }
            fsync_dir(&self.objects)
        })();
        let _ = std::fs::remove_file(&tmp);
        r?;
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: digest })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let p = self.path(at);
        let mut f = match File::open(&p) {
            Ok(f) => f,
            Err(e) if e.kind() == ErrorKind::NotFound => return err(ErrorCode::NotFound, format!("generation {at:?} not stored")),
            Err(e) => return Err(io_err("open", &p, e)),
        };
        let size = f.metadata().map_err(|e| io_err("stat", &p, e))?.len();
        let corrupt = |m: &str| err(ErrorCode::Corruption, format!("image object {at:?}: {m}"));
        if size < OBJECT_HEADER || size - OBJECT_HEADER > self.max_object {
            return corrupt(&format!("size {size} is impossible"));
        }
        let mut buf = Vec::with_capacity(size as usize);
        Read::by_ref(&mut f).take(size).read_to_end(&mut buf).map_err(|e| io_err("read", &p, e))?;
        if buf.len() as u64 != size {
            return corrupt("changed size while being read");
        }
        let u = |o: usize| u64::from_le_bytes(buf[o..o + 8].try_into().expect("8 bytes"));
        if &buf[..8] != MAGIC {
            return corrupt("bad magic");
        }
        if u(8) != at.extent.0 || u(16) != at.generation.get() {
            return corrupt("header names another generation");
        }
        if u(24) != size - OBJECT_HEADER {
            return corrupt("length does not match the file");
        }
        let data = buf.split_off(OBJECT_HEADER as usize);
        if sha256(&data)[..] != buf[32..64] {
            return corrupt("SHA-256 mismatch");
        }
        Ok(data)
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        let p = self.path(at);
        match std::fs::remove_file(&p) {
            Ok(()) => fsync_dir(&self.objects),
            Err(e) if e.kind() == ErrorKind::NotFound => err(ErrorCode::NotFound, format!("generation {at:?} not stored")),
            Err(e) => Err(io_err("remove", &p, e)),
        }
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        let mut out: Vec<GenerationRef> = std::fs::read_dir(&self.objects)
            .map_err(|e| io_err("read", &self.objects, e))?
            .flatten()
            .filter_map(|e| parse_name(&e.file_name().to_string_lossy()))
            .collect();
        out.sort();
        Ok(out)
    }

    fn sync(&self) -> Result<()> {
        fsync_dir(&self.objects)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::tests::TempDir;
    use crate::media::conformance;

    fn at(e: u64, g: u64) -> GenerationRef {
        GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
    }

    #[test]
    fn image_media_passes_conformance() {
        let d = TempDir::new("img");
        let m = ImageMedia::create(&d.0.join("img")).unwrap();
        conformance(&m).unwrap();
        assert_eq!(m.info().durability, Durability::FileSync);
        assert!(m.scrub().unwrap().is_empty());
    }

    #[test]
    fn names_round_trip_and_foreign_names_are_ignored() {
        let a = at(u64::MAX, 7);
        assert_eq!(parse_name(&format!("{:016x}-{:016x}.obj", a.extent.0, 7)), Some(a));
        for bad in ["0000000000000001-0000000000000000.obj", "1-1.obj", "000000000000000g-0000000000000001.obj", ".tmp-1-0", "x"] {
            assert_eq!(parse_name(bad), None, "{bad}");
        }
    }

    #[test]
    fn a_second_process_cannot_open_a_locked_image_and_a_stale_lock_is_taken_over() {
        let d = TempDir::new("img");
        let root = d.0.join("img");
        let m = ImageMedia::create(&root).unwrap();
        assert_eq!(ImageMedia::open(&root).err().unwrap().code, ErrorCode::BackendBusy);
        drop(m);
        std::fs::write(root.join(LOCK_FILE), "4000000000 x\n").unwrap(); // no such process
        let m = ImageMedia::open(&root).unwrap();
        // the lock always has content, and names this process
        assert!(std::fs::read_to_string(root.join(LOCK_FILE)).unwrap().starts_with(&format!("{} ", std::process::id())));
        drop(m);
        // a crash of an earlier incarnation with our own pid (pid reuse, pid 1 in a container) is not "in use"
        std::fs::write(root.join(LOCK_FILE), format!("{} old\n", std::process::id())).unwrap();
        let m = ImageMedia::open(&root).unwrap();
        assert_eq!(ImageMedia::open(&root).err().unwrap().code, ErrorCode::BackendBusy, "but a live instance is");
        drop(m);
        assert!(!root.join(LOCK_FILE).exists());
    }

    #[test]
    fn a_failed_open_releases_the_lock_and_the_object_limit_is_configurable() {
        let d = TempDir::new("img");
        let root = d.0.join("img");
        drop(ImageMedia::create(&root).unwrap());
        std::fs::rename(root.join(OBJECTS), root.join("moved")).unwrap();
        assert_eq!(ImageMedia::open(&root).err().unwrap().code, ErrorCode::BackendUnavailable);
        assert!(!root.join(LOCK_FILE).exists(), "the lock of a failed open is released");
        std::fs::rename(root.join("moved"), root.join(OBJECTS)).unwrap();
        let m = ImageMedia::open(&root).unwrap().with_max_object(10);
        assert_eq!(m.put(at(1, 1), &[0u8; 11]).unwrap_err().code, ErrorCode::InvalidArgument);
        m.put(at(1, 1), &[0u8; 10]).unwrap();
    }
}
