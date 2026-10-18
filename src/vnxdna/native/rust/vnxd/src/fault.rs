//! Fault-injection media adapter (master directive §10, §13; docs/V14_FAILURE_AND_RECOVERY_MODEL.md §6).
//!
//! [`FaultMedia`] stores generations like [`MemoryMedia`] and misbehaves on command: each [`Rule`] names an
//! operation, a [`Fault`], how many matching operations to let through first (`skip`) and how many to fail (`times`).
//! It is the backend of the fault-injection matrix and of the fallback tests; it is never a device backend.
//! Unlike every real adapter it may violate the media contract on purpose (lying receipts, rotten or short reads):
//! the layers above must turn every such violation into a typed error, never into wrong data.

use crate::addr::GenerationRef;
use crate::digest::sha256;
use crate::error::{err, ErrorCode, Result};
use crate::media::{CommitReceipt, Durability, MediaAdapter, MediaInfo, MemoryMedia};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MediaOp {
    Put,
    Get,
    Remove,
    List,
    Sync,
    /// any of the above
    Any,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Fault {
    /// fail with BACKEND_UNAVAILABLE, no effect
    Unavailable,
    /// fail with BACKEND_BUSY, no effect
    Busy,
    /// fail with TIMEOUT, no effect
    Timeout,
    /// put: store the bytes, then fail with BACKEND_UNAVAILABLE (the acknowledgement is lost)
    AckLost,
    /// put: store only the first half, return a receipt claiming all of it (a lying backend)
    TornPut,
    /// put: store the bytes, return a receipt with a wrong SHA-256
    WrongReceipt,
    /// get: return the bytes with one bit flipped
    BitRot,
    /// get: return the bytes without their last byte
    ShortRead,
    /// get: NOT_FOUND although the generation is stored (a lost object)
    Lost,
    /// put: store other bytes than given (silent corruption on the write path), honest receipt of the given bytes
    SilentCorruptWrite,
    /// put: store other bytes than given, then fail with BACKEND_UNAVAILABLE (a retry finds the wrong bytes)
    CorruptAckLost,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rule {
    pub op: MediaOp,
    pub fault: Fault,
    /// matching operations let through before the fault applies
    pub skip: u64,
    /// matching operations that fail (u64::MAX = all)
    pub times: u64,
}

impl Rule {
    pub fn always(op: MediaOp, fault: Fault) -> Rule {
        Rule { op, fault, skip: 0, times: u64::MAX }
    }

    pub fn once(op: MediaOp, fault: Fault) -> Rule {
        Rule { op, fault, skip: 0, times: 1 }
    }
}

#[derive(Default)]
pub struct FaultMedia {
    inner: MemoryMedia,
    rules: Mutex<Vec<Rule>>,
    /// operations per kind (put, get, remove, list, sync)
    pub calls: [AtomicU64; 5],
    pub injected: AtomicU64,
}

fn idx(op: MediaOp) -> usize {
    match op {
        MediaOp::Put => 0,
        MediaOp::Get => 1,
        MediaOp::Remove => 2,
        MediaOp::List => 3,
        MediaOp::Sync | MediaOp::Any => 4,
    }
}

impl FaultMedia {
    pub fn new() -> FaultMedia {
        FaultMedia::default()
    }

    pub fn add(&self, rule: Rule) {
        self.rules.lock().unwrap_or_else(|p| p.into_inner()).push(rule);
    }

    pub fn clear(&self) {
        self.rules.lock().unwrap_or_else(|p| p.into_inner()).clear();
    }

    /// Direct access to the stored objects, bypassing every rule (test setup and inspection).
    pub fn raw(&self) -> &MemoryMedia {
        &self.inner
    }

    /// The fault that applies to this call of `op`, if any (consuming one of its `times`).
    fn take(&self, op: MediaOp) -> Option<Fault> {
        self.calls[idx(op)].fetch_add(1, Ordering::Relaxed);
        let mut rules = self.rules.lock().unwrap_or_else(|p| p.into_inner());
        for r in rules.iter_mut() {
            if r.op != op && r.op != MediaOp::Any {
                continue;
            }
            if r.skip > 0 {
                r.skip -= 1;
                continue;
            }
            if r.times == 0 {
                continue;
            }
            if r.times != u64::MAX {
                r.times -= 1;
            }
            self.injected.fetch_add(1, Ordering::Relaxed);
            return Some(r.fault);
        }
        None
    }
}

fn plain_failure(f: Fault) -> Option<ErrorCode> {
    match f {
        Fault::Unavailable => Some(ErrorCode::BackendUnavailable),
        Fault::Busy => Some(ErrorCode::BackendBusy),
        Fault::Timeout => Some(ErrorCode::Timeout),
        _ => None,
    }
}

impl MediaAdapter for FaultMedia {
    fn info(&self) -> MediaInfo {
        MediaInfo { kind: "faulty", durability: Durability::Volatile, max_in_flight: 64 }
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let fault = self.take(MediaOp::Put);
        if let Some(code) = fault.and_then(plain_failure) {
            return err(code, "injected put failure");
        }
        match fault {
            Some(Fault::AckLost) => {
                self.inner.put(at, data)?;
                err(ErrorCode::BackendUnavailable, "injected: put stored, acknowledgement lost")
            }
            Some(Fault::TornPut) => {
                self.inner.put(at, &data[..data.len() / 2])?;
                Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
            }
            Some(Fault::WrongReceipt) => {
                let mut r = self.inner.put(at, data)?;
                r.sha256[0] ^= 1;
                Ok(r)
            }
            Some(Fault::CorruptAckLost) => {
                let mut bad = data.to_vec();
                if let Some(b) = bad.last_mut() {
                    *b ^= 0x01;
                }
                self.inner.put(at, &bad)?;
                err(ErrorCode::BackendUnavailable, "injected: other bytes stored, acknowledgement lost")
            }
            Some(Fault::SilentCorruptWrite) => {
                let mut bad = data.to_vec();
                if let Some(b) = bad.first_mut() {
                    *b ^= 0x80;
                }
                self.inner.put(at, &bad)?;
                Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
            }
            _ => self.inner.put(at, data),
        }
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let fault = self.take(MediaOp::Get);
        if let Some(code) = fault.and_then(plain_failure) {
            return err(code, "injected get failure");
        }
        match fault {
            Some(Fault::Lost) => err(ErrorCode::NotFound, format!("injected: generation {at:?} lost")),
            Some(Fault::BitRot) => self.inner.get(at).map(|mut d| {
                let mid = d.len() / 2;
                if let Some(b) = d.get_mut(mid) {
                    *b ^= 0x04;
                }
                d
            }),
            Some(Fault::ShortRead) => self.inner.get(at).map(|mut d| {
                d.pop();
                d
            }),
            _ => self.inner.get(at),
        }
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        if let Some(code) = self.take(MediaOp::Remove).and_then(plain_failure) {
            return err(code, "injected remove failure");
        }
        self.inner.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        if let Some(code) = self.take(MediaOp::List).and_then(plain_failure) {
            return err(code, "injected list failure");
        }
        self.inner.list()
    }

    fn sync(&self) -> Result<()> {
        if let Some(code) = self.take(MediaOp::Sync).and_then(plain_failure) {
            return err(code, "injected sync failure");
        }
        self.inner.sync()
    }
}
