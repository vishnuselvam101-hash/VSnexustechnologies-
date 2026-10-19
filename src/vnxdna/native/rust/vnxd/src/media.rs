//! Media-adapter contract (master directive §13; docs/V14_CONTRACTS.md §7).
//!
//! A media adapter stores immutable generations of extents. It is the only way the service reaches a backend. The
//! contract every adapter must meet (checked by [`conformance`]):
//!
//! * `put` of a generation that already exists is refused (generations are immutable); a successful `put` returns a
//!   receipt whose length and SHA-256 describe exactly the bytes given, and means the backend reached the commit
//!   point its [`Durability`] names;
//! * `get` returns exactly the bytes of the last successful `put` of that generation, or an error; never other bytes;
//! * `remove` of a missing generation is NOT_FOUND; after a successful `remove`, `get` is NOT_FOUND;
//! * `list` returns the stored generations in ascending order;
//! * failures are [`VnxError`]s with the documented classes (BACKEND_UNAVAILABLE and BACKEND_BUSY are retryable).
//!
//! V14 adapters: the electronic image backend, the fault-injection backend and the DNA simulator (P7). This module
//! also provides [`MemoryMedia`], a volatile in-process adapter used to test the layers above the media boundary; it
//! declares [`Durability::Volatile`] and is never used as a storage backend of a device.

use crate::addr::GenerationRef;
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result};
use std::collections::BTreeMap;
use std::sync::Mutex;

/// What a successful `put` guarantees.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Durability {
    /// lost when the process exits; never reported to the host as durable
    Volatile,
    /// written to a file and fsynced on the host's storage (electronic, not DNA)
    FileSync,
    /// handed to the DNA channel simulator; a simulation, not physical storage
    SimulatedDna,
}

impl Durability {
    pub fn as_str(self) -> &'static str {
        match self {
            Durability::Volatile => "volatile",
            Durability::FileSync => "file-fsync",
            Durability::SimulatedDna => "simulated-dna",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MediaInfo {
    /// adapter name, e.g. "memory", "image", "faulty", "dna-sim"
    pub kind: &'static str,
    pub durability: Durability,
    /// operations the backend accepts concurrently (the load balancer never exceeds it)
    pub max_in_flight: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommitReceipt {
    pub at: GenerationRef,
    pub length: u64,
    pub sha256: [u8; 32],
}

pub trait MediaAdapter: Send + Sync {
    fn info(&self) -> MediaInfo;
    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt>;
    fn get(&self, at: GenerationRef) -> Result<Vec<u8>>;
    fn remove(&self, at: GenerationRef) -> Result<()>;
    fn list(&self) -> Result<Vec<GenerationRef>>;
    /// Make every earlier successful `put` and `remove` reach the adapter's commit point.
    fn sync(&self) -> Result<()>;
    /// Whether this adapter seals every generation before it reaches the backend (`seal::SealedMedia`).
    fn sealed(&self) -> bool {
        false
    }
}

/// Volatile in-process adapter (tests of the layers above the media boundary).
#[derive(Default)]
pub struct MemoryMedia {
    store: Mutex<BTreeMap<GenerationRef, Vec<u8>>>,
}

impl MemoryMedia {
    pub fn new() -> MemoryMedia {
        MemoryMedia::default()
    }

    fn lock(&self) -> Result<std::sync::MutexGuard<'_, BTreeMap<GenerationRef, Vec<u8>>>> {
        self.store.lock().or_else(|_| err(ErrorCode::Internal, "memory media lock poisoned"))
    }
}

impl MediaAdapter for MemoryMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "memory", durability: Durability::Volatile, max_in_flight: 64 }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let mut s = self.lock()?;
        if s.contains_key(&at) {
            return err(ErrorCode::InvalidArgument, format!("generation {at:?} already stored"));
        }
        s.insert(at, data.to_vec());
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        match self.lock()?.get(&at) {
            Some(d) => Ok(d.clone()),
            None => err(ErrorCode::NotFound, format!("generation {at:?} not stored")),
        }
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        match self.lock()?.remove(&at) {
            Some(_) => Ok(()),
            None => err(ErrorCode::NotFound, format!("generation {at:?} not stored")),
        }
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        Ok(self.lock()?.keys().copied().collect())
    }

    fn sync(&self) -> Result<()> {
        Ok(())
    }
}

/// The conformance suite every adapter must pass, run against an empty adapter. Returns the first violation.
pub fn conformance(m: &dyn MediaAdapter) -> std::result::Result<(), String> {
    use crate::addr::{ExtentId, Generation};
    let at = |e: u64, g: u64| GenerationRef { extent: ExtentId(e), generation: Generation::new(g).expect("g >= 1") };
    let fail = |what: &str, got: String| Err(format!("{}: {what} (got {got})", m.info().kind));
    if m.list().map_err(|e| e.to_string())? != vec![] {
        return fail("adapter must start empty", "stored generations".into());
    }
    let payloads: Vec<Vec<u8>> =
        vec![vec![], vec![0u8; 1], (0..=255u8).collect(), (0..70_000u32).map(|i| (i * 31 % 251) as u8).collect()];
    for (i, p) in payloads.iter().enumerate() {
        let r = m.put(at(i as u64, 1), p).map_err(|e| e.to_string())?;
        if r.length != p.len() as u64 || r.sha256 != sha256(p) || r.at != at(i as u64, 1) {
            return fail("receipt must describe the bytes given", format!("{r:?}"));
        }
    }
    match m.put(at(0, 1), b"other") {
        Err(e) if e.code == ErrorCode::InvalidArgument => {}
        other => return fail("re-put of a stored generation must be refused", format!("{other:?}")),
    }
    m.put(at(0, 2), b"second generation").map_err(|e| e.to_string())?;
    m.sync().map_err(|e| e.to_string())?;
    for (i, p) in payloads.iter().enumerate() {
        match m.get(at(i as u64, 1)) {
            Ok(d) if &d == p => {}
            other => return fail("get must return the stored bytes", format!("{:?}", other.map(|d| d.len()))),
        }
    }
    if m.get(at(0, 2)).map_err(|e| e.to_string())? != b"second generation" {
        return fail("generations of one extent are independent", "wrong bytes".into());
    }
    let want: Vec<GenerationRef> = vec![at(0, 1), at(0, 2), at(1, 1), at(2, 1), at(3, 1)];
    if m.list().map_err(|e| e.to_string())? != want {
        return fail("list must return every stored generation in order", format!("{:?}", m.list()));
    }
    m.remove(at(0, 1)).map_err(|e| e.to_string())?;
    // a remove touches only its own generation
    match m.get(at(0, 2)) {
        Ok(d) if d == b"second generation" => {}
        other => return fail("remove must not touch other generations of the extent", format!("{other:?}")),
    }
    for (i, p) in payloads.iter().enumerate().skip(1) {
        match m.get(at(i as u64, 1)) {
            Ok(d) if &d == p => {}
            other => return fail("remove must not touch other extents", format!("extent {i}: {:?}", other.map(|d| d.len()))),
        }
    }
    if m.list().map_err(|e| e.to_string())? != want[1..] {
        return fail("list must not report removed generations", format!("{:?}", m.list()));
    }
    for (what, r) in [("get after remove", m.get(at(0, 1)).map(|_| ())), ("remove twice", m.remove(at(0, 1))),
                      ("get of a never-stored generation", m.get(at(9, 9)).map(|_| ()))] {
        match r {
            Err(e) if e.code == ErrorCode::NotFound => {}
            other => return fail(&format!("{what} must be NOT_FOUND"), format!("{other:?}")),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_media_passes_conformance() {
        let m = MemoryMedia::new();
        conformance(&m).unwrap();
        assert_eq!(m.info().durability, Durability::Volatile);
    }

    /// An adapter that returns other bytes than it stored must fail the suite.
    struct Lying(MemoryMedia);
    impl MediaAdapter for Lying {
        fn info(&self) -> MediaInfo {
            self.0.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
            self.0.put(at, d)
        }
        fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
            self.0.get(at).map(|mut d| {
                if let Some(b) = d.first_mut() {
                    *b ^= 1;
                }
                d
            })
        }
        fn remove(&self, at: GenerationRef) -> Result<()> {
            self.0.remove(at)
        }
        fn list(&self) -> Result<Vec<GenerationRef>> {
            self.0.list()
        }
        fn sync(&self) -> Result<()> {
            Ok(())
        }
    }

    /// An adapter whose remove deletes every generation of the extent must fail the suite.
    struct Greedy(MemoryMedia);
    impl MediaAdapter for Greedy {
        fn info(&self) -> MediaInfo {
            self.0.info()
        }
        fn put(&self, at: GenerationRef, d: &[u8]) -> Result<CommitReceipt> {
            self.0.put(at, d)
        }
        fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
            self.0.get(at)
        }
        fn remove(&self, at: GenerationRef) -> Result<()> {
            self.0.remove(at)?;
            for other in self.0.list()?.into_iter().filter(|o| o.extent == at.extent) {
                self.0.remove(other)?;
            }
            Ok(())
        }
        fn list(&self) -> Result<Vec<GenerationRef>> {
            self.0.list()
        }
        fn sync(&self) -> Result<()> {
            Ok(())
        }
    }

    #[test]
    fn conformance_detects_a_remove_that_takes_too_much() {
        assert!(conformance(&Greedy(MemoryMedia::new())).unwrap_err().contains("other generations"));
    }

    #[test]
    fn conformance_detects_wrong_bytes() {
        assert!(conformance(&Lying(MemoryMedia::new())).unwrap_err().contains("stored bytes"));
    }
}
