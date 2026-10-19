//! The volume journal: an append-only file of `vnx.record/1` records (docs/V14_CONTRACTS.md §3,
//! docs/V14_VOLUME_AND_IO.md §2).
//!
//! **Mirrored layout**: every record is stored twice, back to back (`copy 1 | copy 2`, identical bytes). One damaged
//! copy is repaired from the other at `open` (and reported in [`Opened::repaired`]); only damage to both copies of
//! the same record is unrecoverable. This removes the single-record ambiguity of an unmirrored journal, where a bit
//! flip in the last complete record could not be told apart from a torn append.
//!
//! * `append` writes one record pair and calls `sync_data` before returning: a record is durable when `append`
//!   returns `Ok`, and not before.
//! * `open` reads the pairs in order. A pair is *torn* (an append interrupted by a crash) when the file ends inside
//!   it: copy 1 decodes but copy 2 is incomplete, or neither copy decodes, copy 1's header does not even parse, the
//!   remainder is shorter than the largest pair and nothing after it decodes. A torn pair is cut off (reported in
//!   [`Opened::torn_tail`]). Anything else that does not decode — both copies of a complete pair damaged, or damage
//!   followed by decodable records — is damage inside the committed journal: `open` refuses with CORRUPTION and
//!   changes nothing (committed records after the damage must never be discarded silently). A journal whose first
//!   two frames are different valid records was written without mirroring (the P2/P3 layout): VERSION_MISMATCH.
//! * a failed append whose rollback also fails poisons the journal: every later append fails, because the file may
//!   hold a record whose outcome is unknown.
//! * `rewrite` replaces the whole journal atomically (write a temporary file, fsync it, rename it over the journal,
//!   fsync the directory): the compaction step, also used to restore the mirror after a repair.

use crate::error::{err, ErrorCode, Result, VnxError};
use crate::record::{Record, HEADER_LEN, MAGIC, MAX_BATCH_EXTENTS, MAX_RECORD_LEN, TRAILER_LEN};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// Smallest encoded record (a generation release).
const MIN_RECORD_LEN: usize = HEADER_LEN + 24 + TRAILER_LEN;

pub struct Journal {
    path: PathBuf,
    file: File,
    len: u64,
    poisoned: bool,
}

pub struct Opened {
    pub journal: Journal,
    pub records: Vec<Record>,
    /// why the tail was cut, if it was (the file was truncated to the last complete pair)
    pub torn_tail: Option<VnxError>,
    /// byte offsets of record pairs that were read from their intact copy (one copy damaged)
    pub repaired: Vec<u64>,
}

/// The bytes `append` writes for `rec`: the encoded record twice.
pub fn encode_pair(rec: &Record) -> Result<Vec<u8>> {
    let one = rec.encode()?;
    Ok([&one[..], &one[..]].concat())
}

enum Pair {
    Ok { rec: Record, used: usize, repaired: bool },
    Torn(VnxError),
    Damaged(String),
}

/// Total length announced by a record header, if the header parses (magic and a plausible payload length).
fn announced_len(rest: &[u8]) -> Option<usize> {
    if rest.len() < HEADER_LEN || rest[0..4] != MAGIC {
        return None;
    }
    let n = u32::from_le_bytes([rest[8], rest[9], rest[10], rest[11]]) as usize;
    let total = HEADER_LEN + n + TRAILER_LEN;
    (MIN_RECORD_LEN..=MAX_RECORD_LEN).contains(&total).then_some(total)
}

/// The largest record pair a journal of a device with `extent_count` extents can hold: a map batch of
/// min(extent_count, MAX_BATCH_EXTENTS) entries, or a single map update.
pub fn max_pair_len(extent_count: u64) -> usize {
    let n = extent_count.min(MAX_BATCH_EXTENTS as u64) as usize;
    let batch = if n >= 2 { HEADER_LEN + 16 + 64 * n + TRAILER_LEN } else { 0 };
    2 * batch.max(HEADER_LEN + 72 + TRAILER_LEN)
}

fn decodes_anywhere(rest: &[u8]) -> bool {
    (1..rest.len()).any(|i| rest[i..].starts_with(&MAGIC) && Record::decode(&rest[i..]).is_ok())
}

/// Is `b` all zeros (a range the filesystem allocated but the crashed append never wrote)?
fn zeros(b: &[u8]) -> bool {
    b.iter().all(|&x| x == 0)
}

/// Read the record pair at the start of `rest`. `at_start`: this is the first pair (the superblock), which is never
/// cut as torn: a journal is only created once its superblock pair is durable. `max_pair`: the largest pair this
/// journal can hold (from its geometry), the bound of a zero-filled torn append.
fn read_pair(rest: &[u8], at_start: bool, max_pair: usize) -> Pair {
    match Record::decode(rest) {
        Ok((rec, l)) => {
            // what follows copy 1, decoded on its own (not cut to l): a valid DIFFERENT record there means the file
            // is not mirrored (or two records were swapped), never a damaged copy 2, whose CRC could not match
            match Record::decode(&rest[l..]) {
                Ok((twin, used)) if used == l && twin == rec => return Pair::Ok { rec, used: 2 * l, repaired: false },
                Ok(_) if at_start => return Pair::Damaged("unmirrored journal (V14 P2/P3 layout)".into()),
                Ok(_) => return Pair::Damaged("the record after a valid copy is a different valid record".into()),
                Err(_) => {}
            }
            if rest.len() >= 2 * l {
                return Pair::Ok { rec, used: 2 * l, repaired: true };
            }
            if at_start {
                return Pair::Damaged("unmirrored journal (V14 P2/P3 layout) or a superblock without its copy".into());
            }
            // the file ends inside copy 2: a torn append, provided nothing decodable follows
            if decodes_anywhere(&rest[l..]) {
                return Pair::Damaged("damage after a valid copy".into());
            }
            Pair::Torn(VnxError::new(ErrorCode::Corruption, "torn append: second copy incomplete"))
        }
        Err(e1) => {
            // copy 1 is damaged: copy 2 starts at q and is exactly q bytes long
            for q in MIN_RECORD_LEN..=(rest.len() / 2).min(MAX_RECORD_LEN) {
                if rest[q..].starts_with(&MAGIC) {
                    if let Ok((rec, used)) = Record::decode(&rest[q..]) {
                        if used == q {
                            return Pair::Ok { rec, used: 2 * q, repaired: true };
                        }
                    }
                }
            }
            if at_start || decodes_anywhere(rest) {
                return Pair::Damaged(e1.to_string());
            }
            // only the last append can be torn (every append is fsynced), and a crash leaves a prefix of it or a
            // zero-filled range; anything else is damage to committed records
            let torn = match announced_len(rest) {
                // header written: the file ends inside the pair, or the rest of the pair was never written (zeros)
                Some(l) => rest.len() < 2 * l || (rest.len() == 2 * l && zeros(&rest[rest.len() - 8..])),
                // header never written: a zero-filled range no longer than the largest pair this journal can hold
                None => {
                    (zeros(rest) && rest.len() <= max_pair)
                        // or the append stopped inside the header itself
                        || (rest.len() < HEADER_LEN && MAGIC.starts_with(&rest[..rest.len().min(4)]))
                }
            };
            if torn {
                Pair::Torn(e1)
            } else {
                Pair::Damaged(e1.to_string())
            }
        }
    }
}

/// What a mirrored journal's bytes hold (see the module documentation); `open` acts on it, `vnxd inspect-journal`
/// only prints it.
#[derive(Debug)]
pub struct Parsed {
    pub records: Vec<Record>,
    /// bytes covered by complete record pairs (where a torn tail is cut)
    pub valid_len: usize,
    pub torn_tail: Option<VnxError>,
    /// byte offsets of pairs read from their intact copy
    pub repaired: Vec<u64>,
}

/// Read a mirrored journal without changing anything: CORRUPTION for damage inside the committed journal,
/// VERSION_MISMATCH for an unmirrored file.
pub fn parse(buf: &[u8]) -> Result<Parsed> {
    let (mut records, mut repaired, mut pos, mut torn_tail) = (Vec::new(), Vec::new(), 0usize, None);
    let mut max_pair = 2 * MAX_RECORD_LEN;
    while pos < buf.len() {
        match read_pair(&buf[pos..], pos == 0, max_pair) {
            Pair::Ok { rec, used, repaired: r } => {
                if r {
                    repaired.push(pos as u64);
                }
                if let (0, Record::Superblock(sb)) = (pos, &rec) {
                    max_pair = max_pair_len(sb.geometry.extent_count());
                }
                records.push(rec);
                pos += used;
            }
            Pair::Torn(e) => {
                torn_tail = Some(e);
                break;
            }
            Pair::Damaged(why) => {
                let code = if pos == 0 && why.starts_with("unmirrored") { ErrorCode::VersionMismatch } else { ErrorCode::Corruption };
                return err(code, format!("damaged at byte {pos} ({why}) with {} bytes after it; refusing to truncate", buf.len() - pos));
            }
        }
    }
    Ok(Parsed { records, valid_len: pos, torn_tail, repaired })
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
        let bytes = encode_pair(first)?;
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
        let p = parse(&buf).map_err(|e| VnxError::new(e.code, format!("journal {}: {}", path.display(), e.message)))?;
        if p.valid_len < buf.len() {
            file.set_len(p.valid_len as u64).and_then(|_| file.sync_all()).map_err(|e| io("truncate", path, e))?;
        }
        file.seek(SeekFrom::End(0)).map_err(|e| io("seek", path, e))?;
        let journal = Journal { path: path.to_path_buf(), file, len: p.valid_len as u64, poisoned: false };
        Ok(Opened { journal, records: p.records, torn_tail: p.torn_tail, repaired: p.repaired })
    }

    pub fn append(&mut self, rec: &Record) -> Result<()> {
        let bytes = encode_pair(rec)?;
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
            bytes.extend(encode_pair(r)?);
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

    fn sb_geometry() -> Geometry {
        Geometry::new(4096, 4, 64).unwrap()
    }

    fn sb() -> Record {
        Record::Superblock(Superblock { device_id: [7; 16], geometry: sb_geometry(), flags: 0 })
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

    fn write_journal(p: &Path, n: u64) -> Vec<u8> {
        let mut j = Journal::create(p, &sb()).unwrap();
        for t in 1..=n {
            j.append(&rel(t)).unwrap();
        }
        drop(j);
        std::fs::read(p).unwrap()
    }

    #[test]
    fn one_damaged_copy_is_repaired_anywhere_including_the_last_record() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let clean = write_journal(&p, 3);
        let (sbl, rl) = (sb().encode().unwrap().len(), rel(1).encode().unwrap().len());
        let all = vec![sb(), rel(1), rel(2), rel(3)];
        // every byte of every copy, one at a time (the superblock pair included)
        for at in 0..clean.len() {
            let mut bytes = clean.clone();
            bytes[at] ^= 0x10;
            std::fs::write(&p, &bytes).unwrap();
            let o = Journal::open(&p).unwrap_or_else(|e| panic!("flip at {at}: {e}"));
            assert_eq!(o.records, all, "flip at {at}");
            assert!(o.torn_tail.is_none(), "flip at {at}");
            let pair = if at < 2 * sbl { 0 } else { 2 * sbl + (at - 2 * sbl) / (2 * rl) * 2 * rl };
            assert_eq!(o.repaired, vec![pair as u64], "flip at {at}");
        }
        // repaired files are not modified by open (the volume restores the mirror by compaction)
        assert_eq!(std::fs::metadata(&p).unwrap().len(), clean.len() as u64);
    }

    #[test]
    fn damage_to_both_copies_is_refused_not_truncated() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let clean = write_journal(&p, 3);
        let (sbl, rl) = (sb().encode().unwrap().len(), rel(1).encode().unwrap().len());
        // both copies of the first release record (mid-journal) and of the last one (no longer mistaken for a tear)
        for start in [2 * sbl, clean.len() - 2 * rl] {
            let mut bytes = clean.clone();
            bytes[start + rl / 2] ^= 1;
            bytes[start + rl + rl / 2] ^= 1;
            std::fs::write(&p, &bytes).unwrap();
            assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption, "pair at {start}");
            assert_eq!(std::fs::read(&p).unwrap(), bytes, "the damaged journal is left untouched");
        }
        // garbage longer than the largest pair after the valid prefix is damage, not a torn append
        let mut bytes = clean.clone();
        bytes.extend(vec![0u8; 2 * MAX_RECORD_LEN]);
        std::fs::write(&p, &bytes).unwrap();
        assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption);
        // and so is damage followed by a decodable record
        let mut bytes = clean.clone();
        bytes.extend(vec![0u8; 50]);
        bytes.extend(rel(9).encode().unwrap());
        std::fs::write(&p, &bytes).unwrap();
        assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption);
    }

    #[test]
    fn torn_pairs_are_cut() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let clean = write_journal(&p, 2);
        let pair = encode_pair(&rel(3)).unwrap();
        // every prefix of the next pair, and a zero-filled tail of the pair's length
        let mut tails: Vec<Vec<u8>> = (1..pair.len()).map(|n| pair[..n].to_vec()).collect();
        tails.push(vec![0u8; pair.len()]);
        for tail in tails {
            let bytes = [&clean[..], &tail[..]].concat();
            std::fs::write(&p, &bytes).unwrap();
            let o = Journal::open(&p).unwrap_or_else(|e| panic!("tail of {}: {e}", tail.len()));
            assert_eq!(o.records, vec![sb(), rel(1), rel(2)], "tail of {}", tail.len());
            assert!(o.torn_tail.is_some());
            assert_eq!(std::fs::metadata(&p).unwrap().len(), clean.len() as u64);
        }
    }

    #[test]
    fn an_unmirrored_journal_is_a_version_mismatch() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let bytes = [sb().encode().unwrap(), rel(1).encode().unwrap(), rel(2).encode().unwrap()].concat();
        std::fs::write(&p, &bytes).unwrap();
        assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::VersionMismatch);
        assert_eq!(std::fs::read(&p).unwrap(), bytes);
    }

    #[test]
    fn unmirrored_journals_are_refused_never_truncated() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let sbb = sb().encode().unwrap();
        let mu = Record::MapUpdate(crate::record::MapUpdate {
            txid: 1,
            at: GenerationRef { extent: ExtentId(1), generation: Generation::FIRST },
            previous: None,
            length: 4 * 4096,
            sha256: [3; 32],
        })
        .encode()
        .unwrap();
        for bytes in [
            sbb.clone(),                                          // a fresh P2/P3 volume: superblock only
            [&sbb[..], &rel(1).encode().unwrap()].concat(),       // superblock + a release
            [&sbb[..], &mu[..]].concat(),                         // superblock + one map update
            [&sbb[..], &mu[..], &rel(2).encode().unwrap()].concat(),
        ] {
            std::fs::write(&p, &bytes).unwrap();
            assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::VersionMismatch, "{} bytes", bytes.len());
            assert_eq!(std::fs::read(&p).unwrap(), bytes, "left untouched");
        }
    }

    #[test]
    fn a_damaged_tail_longer_than_one_append_is_refused() {
        let d = TempDir::new("journal");
        let p = d.0.join("journal");
        let clean = write_journal(&p, 101);
        let max_pair = max_pair_len(sb_geometry().extent_count());
        assert!(max_pair < 4096);
        // a lost last page (zeros), and garbage over the last 1000 bytes: committed records, refused untouched
        let mut page = clean.clone();
        let n = page.len();
        page[n - 4096..].fill(0);
        let mut garbage = clean.clone();
        for (i, b) in garbage[n - 1000..].iter_mut().enumerate() {
            *b = (i * 7 + 1) as u8;
        }
        for bytes in [page, garbage] {
            std::fs::write(&p, &bytes).unwrap();
            assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption);
            assert_eq!(std::fs::read(&p).unwrap(), bytes);
        }
        // a zero-filled tail up to the largest pair, and a written header followed by zeros, are torn appends
        let pair = encode_pair(&rel(102)).unwrap();
        let mut header_then_zeros = pair.clone();
        header_then_zeros[HEADER_LEN..].fill(0);
        for tail in [vec![0u8; max_pair], header_then_zeros, pair[..5].to_vec()] {
            let bytes = [&clean[..], &tail[..]].concat();
            std::fs::write(&p, &bytes).unwrap();
            let o = Journal::open(&p).unwrap_or_else(|e| panic!("tail of {}: {e}", tail.len()));
            assert_eq!(o.records.len(), 102);
            assert!(o.torn_tail.is_some());
        }
        let bytes = [&clean[..], &vec![0u8; max_pair + 1][..]].concat();
        std::fs::write(&p, &bytes).unwrap();
        assert_eq!(Journal::open(&p).err().unwrap().code, ErrorCode::Corruption, "zeros longer than any append");
    }

    #[test]
    fn missing_journal_is_not_found() {
        let d = TempDir::new("journal");
        assert_eq!(Journal::open(&d.0.join("nope")).err().unwrap().code, ErrorCode::NotFound);
    }
}
