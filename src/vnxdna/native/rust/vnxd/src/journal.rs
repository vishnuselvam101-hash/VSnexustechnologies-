//! The volume journal: an append-only file of `vnx.record/1` records (docs/V14_CONTRACTS.md §3,
//! docs/V14_VOLUME_AND_IO.md §2).
//!
//! * `append` writes one encoded record and calls `sync_data` before returning: a record is durable when `append`
//!   returns `Ok`, and not before.
//! * `open` reads the file and keeps every record up to the first one that does not decode. If what follows is a torn
//!   last append (shorter than one record, with no decodable record after it), the file is truncated there and the
//!   reason is reported in [`Opened::torn_tail`]; anything else is damage inside the journal and `open` refuses with
//!   CORRUPTION without changing the file (committed records after the damage must never be discarded silently).
//! * a failed append whose rollback also fails poisons the journal: every later append fails, because the file may
//!   hold a record whose outcome is unknown.
//! * `rewrite` replaces the whole journal atomically (write a temporary file, fsync it, rename it over the journal,
//!   fsync the directory): the compaction step used after garbage collection.

use crate::error::{err, ErrorCode, Result, VnxError};
use crate::record::{scan, Record, HEADER_LEN, TRAILER_LEN};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// Largest payload of any record kind (map_update): a torn append is shorter than one whole record.
const MAX_PAYLOAD: usize = 72;

pub struct Journal {
    path: PathBuf,
    file: File,
    len: u64,
    poisoned: bool,
}

pub struct Opened {
    pub journal: Journal,
    pub records: Vec<Record>,
    /// why the tail was cut, if it was (the file was truncated to the last valid record)
    pub torn_tail: Option<VnxError>,
}

fn io(what: &str, path: &Path, e: std::io::Error) -> VnxError {
    let code = match e.kind() {
        std::io::ErrorKind::NotFound => ErrorCode::NotFound,
        _ => ErrorCode::BackendUnavailable,
    };
    VnxError::new(code, format!("{what} {}: {e}", path.display()))
}

fn sync_dir(path: &Path) -> Result<()> {
    let dir = path.parent().unwrap_or_else(|| Path::new("."));
    File::open(dir).and_then(|d| d.sync_all()).map_err(|e| io("fsync directory", dir, e))
}

impl Journal {
    /// Create a new journal holding `first` (the superblock). Fails if the file exists.
    pub fn create(path: &Path, first: &Record) -> Result<Journal> {
        let bytes = first.encode()?;
        let mut file =
            OpenOptions::new().read(true).append(true).create_new(true).open(path).map_err(|e| io("create", path, e))?;
        file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|e| io("write", path, e))?;
        sync_dir(path)?;
        Ok(Journal { path: path.to_path_buf(), file, len: bytes.len() as u64, poisoned: false })
    }

    pub fn open(path: &Path) -> Result<Opened> {
        let mut file = OpenOptions::new().read(true).append(true).open(path).map_err(|e| io("open", path, e))?;
        let mut buf = Vec::new();
        file.read_to_end(&mut buf).map_err(|e| io("read", path, e))?;
        let s = scan(&buf);
        if s.valid_len < buf.len() {
            let rest = &buf[s.valid_len..];
            // a torn append writes a prefix of ONE record; a decodable record later in the file means the damage is
            // inside the committed journal
            let later = (1..rest.len()).any(|i| Record::decode(&rest[i..]).is_ok());
            if rest.len() >= HEADER_LEN + MAX_PAYLOAD + TRAILER_LEN || later {
                return err(
                    ErrorCode::Corruption,
                    format!(
                        "journal {} is damaged at byte {} ({}) with {} bytes after it; refusing to truncate",
                        path.display(),
                        s.valid_len,
                        s.tail_error.as_ref().map_or(String::new(), |e| e.to_string()),
                        rest.len()
                    ),
                );
            }
            file.set_len(s.valid_len as u64).and_then(|_| file.sync_all()).map_err(|e| io("truncate", path, e))?;
        }
        file.seek(SeekFrom::End(0)).map_err(|e| io("seek", path, e))?;
        let journal = Journal { path: path.to_path_buf(), file, len: s.valid_len as u64, poisoned: false };
        Ok(Opened { journal, records: s.records, torn_tail: s.tail_error })
    }

    pub fn append(&mut self, rec: &Record) -> Result<()> {
        let bytes = rec.encode()?;
        self.append_raw(&bytes)
    }

    /// Append already-encoded bytes (also used by the fault-injection tests to write a torn record).
    pub fn append_raw(&mut self, bytes: &[u8]) -> Result<()> {
        if self.poisoned {
            return err(ErrorCode::Internal, format!("journal {} is poisoned by an earlier failed append", self.path.display()));
        }
        if let Err(e) = self.file.write_all(bytes).and_then(|_| self.file.sync_data()) {
            // cut back to the last complete record; if even that fails, the record may still reach the disk
            if self.file.set_len(self.len).and_then(|_| self.file.sync_data()).is_err() {
                self.poisoned = true;
            }
            return Err(io("append", &self.path, e));
        }
        self.len += bytes.len() as u64;
        Ok(())
    }

    /// Atomically replace the journal's content with `records`.
    pub fn rewrite(&mut self, records: &[Record]) -> Result<()> {
        if records.is_empty() {
            return err(ErrorCode::InvalidArgument, "a journal keeps at least its superblock");
        }
        let tmp = self.path.with_extension("compact");
        let mut bytes = Vec::new();
        for r in records {
            bytes.extend(r.encode()?);
        }
        // the new handle is opened on the temporary file BEFORE the rename: once the rename succeeds it is the journal,
        // so appends can never go to the unlinked old inode
        match std::fs::remove_file(&tmp) {
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => return Err(io("remove stale", &tmp, e)),
            _ => {}
        }
        let mut f = OpenOptions::new().read(true).append(true).create_new(true).open(&tmp)
            .map_err(|e| io("create", &tmp, e))?;
        f.write_all(&bytes).and_then(|_| f.sync_all()).map_err(|e| io("write", &tmp, e))?;
        std::fs::rename(&tmp, &self.path).map_err(|e| io("rename", &tmp, e))?;
        self.file = f;
        self.len = bytes.len() as u64;
        sync_dir(&self.path)
    }

    pub fn is_poisoned(&self) -> bool {
        self.poisoned
    }

    pub fn len(&self) -> u64 {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation, GenerationRef, Geometry};
    use crate::record::{GenerationReleased, Superblock};

    /// A fresh directory under the system temp dir, removed on drop.
    pub(crate) struct TempDir(pub PathBuf);
    impl TempDir {
        pub(crate) fn new(tag: &str) -> TempDir {
            static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
            let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let p = std::env::temp_dir().join(format!("vnxd-{tag}-{}-{n}", std::process::id()));
            let _ = std::fs::remove_dir_all(&p);
            std::fs::create_dir_all(&p).unwrap();
            TempDir(p)
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn sb() -> Record {
        Record::Superblock(Superblock { device_id: [7; 16], geometry: Geometry::new(4096, 4, 64).unwrap(), flags: 0 })
    }

    fn rel(t: u64) -> Record {
        let at = GenerationRef { extent: ExtentId(t), generation: Generation::FIRST };
        Record::GenerationReleased(GenerationReleased { txid: t, at })
    }

    #[test]
    fn create_append_reopen() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let mut j = Journal::create(&p, &sb()).unwrap();
        assert!(Journal::create(&p, &sb()).is_err(), "create never overwrites");
        for t in 1..=3 {
            j.append(&rel(t)).unwrap();
        }
        drop(j);
        let o = Journal::open(&p).unwrap();
        assert_eq!(o.records, vec![sb(), rel(1), rel(2), rel(3)]);
        assert!(o.torn_tail.is_none());
    }

    #[test]
    fn torn_tail_is_truncated_and_appends_continue() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let mut j = Journal::create(&p, &sb()).unwrap();
        j.append(&rel(1)).unwrap();
        let good = j.len();
        let torn = rel(2).encode().unwrap();
        j.append_raw(&torn[..torn.len() / 2]).unwrap();
        drop(j);
        let mut o = Journal::open(&p).unwrap();
        assert_eq!(o.records, vec![sb(), rel(1)]);
        assert_eq!(o.torn_tail.as_ref().unwrap().code, ErrorCode::Corruption);
        assert_eq!(std::fs::metadata(&p).unwrap().len(), good);
        o.journal.append(&rel(3)).unwrap();
        drop(o);
        assert_eq!(Journal::open(&p).unwrap().records, vec![sb(), rel(1), rel(3)]);
    }

    #[test]
    fn rewrite_is_atomic_replacement() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let mut j = Journal::create(&p, &sb()).unwrap();
        for t in 1..=5 {
            j.append(&rel(t)).unwrap();
        }
        j.rewrite(&[sb(), rel(5)]).unwrap();
        j.append(&rel(6)).unwrap();
        drop(j);
        assert_eq!(Journal::open(&p).unwrap().records, vec![sb(), rel(5), rel(6)]);
        assert!(!p.with_extension("compact").exists());
        let mut j = Journal::open(&p).unwrap().journal;
        assert_eq!(j.rewrite(&[]).unwrap_err().code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn damage_inside_the_journal_is_refused_not_truncated() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let mut j = Journal::create(&p, &sb()).unwrap();
        for t in 1..=3 {
            j.append(&rel(t)).unwrap();
        }
        drop(j);
        let mut bytes = std::fs::read(&p).unwrap();
        let first_rel = rel(1).encode().unwrap().len();
        let at = sb().encode().unwrap().len() + first_rel / 2; // inside the first release record
        bytes[at] ^= 1;
        std::fs::write(&p, &bytes).unwrap();
        assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption);
        assert_eq!(std::fs::read(&p).unwrap(), bytes, "the damaged journal is left untouched");
        // a flip in the LAST record is indistinguishable from a torn append: that one record is dropped
        let mut bytes = std::fs::read(&p).unwrap();
        bytes[at] ^= 1;
        let n = bytes.len();
        bytes[n - 3] ^= 1;
        std::fs::write(&p, &bytes).unwrap();
        let o = Journal::open(&p).unwrap();
        assert_eq!(o.records, vec![sb(), rel(1), rel(2)]);
        // garbage longer than any record after the valid prefix is damage, not a torn append
        drop(o);
        let mut f = OpenOptions::new().append(true).open(&p).unwrap();
        f.write_all(&[0u8; 200]).unwrap();
        drop(f);
        assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption);
    }

    #[test]
    fn missing_journal_is_not_found() {
        let d = TempDir::new("journal");
        assert_eq!(Journal::open(&d.0.join("nope")).err().unwrap().code, ErrorCode::NotFound);
    }
}
