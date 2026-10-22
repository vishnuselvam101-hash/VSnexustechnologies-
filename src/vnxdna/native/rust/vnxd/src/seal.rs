//! Sealing at the extent boundary (docs/V14_SECURITY_MODEL.md §3).
//!
//! A volume created with `FLAG_SEALED` stores every extent generation sealed: [`SealedMedia`] wraps the backend
//! adapter and seals on `put`, opens on `get`. The cipher is the existing VNX-DNA security layer (AES-256-GCM with
//! HKDF-SHA256 key derivation, Python `cryptography`/OpenSSL): vnxd never implements a cipher and never holds the
//! key. It calls a [`Sealer`]; the production sealer is [`WorkerSealer`], a pool of Python worker processes
//! (`python -m vnxdna.device.worker`) speaking `vnx.codec-ipc/1` (`key_check`, `seal_extent`, `open_extent`), which
//! read the key file themselves.
//!
//! * the sealed object (format `vnx.sealed-extent/1`, specified with its Python implementation in
//!   `vnxdna.device.seal`) binds the device id, the extent and the generation as associated data, so an object moved
//!   to another extent, generation or device, or replayed from an older generation, fails authentication: CORRUPTION,
//!   never other bytes;
//! * the backend's binding marker is stored with the key's check value appended: opening a sealed volume with a
//!   different key is UNAUTHORIZED at `open`, before anything is read or garbage-collected;
//! * the volume's receipts and map digests stay those of the plaintext (the journal is host-local, inside the trust
//!   boundary); the backend only ever sees sealed bytes.

use crate::addr::GenerationRef;
use crate::digest::{hex, parse_hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::{obj, Value};
use crate::media::{CommitReceipt, MediaAdapter, MediaInfo};
use crate::volume::MARKER_EXTENT;
use std::sync::Arc;

/// Seals and opens extent generations of one device. Implementations must never return bytes that failed
/// authentication.
pub trait Sealer: Send + Sync {
    fn seal(&self, device_id: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>>;
    fn open(&self, device_id: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>>;
    /// The key's check value for this device (identifies the key without revealing it).
    fn key_check(&self, device_id: &[u8; 16]) -> Result<[u8; 32]>;
}

/// Bytes a sealed object adds to its extent (header + tag of `vnx.sealed-extent/1`).
pub const SEAL_OVERHEAD: u64 = 40 + 16;

pub struct SealedMedia {
    inner: Arc<dyn MediaAdapter>,
    sealer: Arc<dyn Sealer>,
    device_id: [u8; 16],
    /// largest object the backend may hand back (the largest extent + overhead): anything bigger is refused before
    /// it is staged
    max_object: u64,
}

impl SealedMedia {
    /// `max_extent_bytes`: the device's extent size (`Geometry::extent_bytes`).
    pub fn new(inner: Arc<dyn MediaAdapter>, sealer: Arc<dyn Sealer>, device_id: [u8; 16], max_extent_bytes: u64) -> SealedMedia {
        SealedMedia { inner, sealer, device_id, max_object: max_extent_bytes.saturating_add(SEAL_OVERHEAD) }
    }
}

impl MediaAdapter for SealedMedia {
    fn info(&self) -> MediaInfo {
        self.inner.info()
    }

    fn sealed(&self) -> bool {
        true
    }

    fn put(&self, at: GenerationRef, data: &[u8]) -> Result<CommitReceipt> {
        let stored = if at.extent == MARKER_EXTENT {
            [data, &self.sealer.key_check(&self.device_id)?[..]].concat()
        } else {
            self.sealer.seal(&self.device_id, at, data)?
        };
        let r = self.inner.put(at, &stored)?;
        if r.length != stored.len() as u64 || r.sha256 != sha256(&stored) {
            return err(ErrorCode::Corruption, format!("backend receipt for {at:?} does not describe the sealed bytes"));
        }
        Ok(CommitReceipt { at, length: data.len() as u64, sha256: sha256(data) })
    }

    fn get(&self, at: GenerationRef) -> Result<Vec<u8>> {
        let stored = self.inner.get(at)?;
        if stored.len() as u64 > self.max_object {
            return err(ErrorCode::Corruption, format!("{at:?}: the backend returned {} bytes, more than any sealed extent", stored.len()));
        }
        if at.extent == MARKER_EXTENT {
            if stored.len() < 32 {
                return err(ErrorCode::Unauthorized, "the backend was not created sealed (its marker carries no key check)");
            }
            let (marker, kcv) = stored.split_at(stored.len() - 32);
            if kcv != self.sealer.key_check(&self.device_id)? {
                return err(ErrorCode::Unauthorized, "the key does not match the key this device was sealed with");
            }
            return Ok(marker.to_vec());
        }
        // the device's key was verified at open (the marker): a per-object key-check or version mismatch is damage
        // to this object, reported like any other, so a scrub lists the extent instead of stopping
        self.sealer.open(&self.device_id, at, &stored).map_err(|e| match e.code {
            ErrorCode::Unauthorized | ErrorCode::VersionMismatch => {
                VnxError::new(ErrorCode::Corruption, format!("{at:?}: {}", e.message))
            }
            _ => e,
        })
    }

    fn remove(&self, at: GenerationRef) -> Result<()> {
        self.inner.remove(at)
    }

    fn list(&self) -> Result<Vec<GenerationRef>> {
        self.inner.list()
    }

    fn sync(&self) -> Result<()> {
        self.inner.sync()
    }
}

pub use crate::worker::WorkerConfig;
use crate::worker::{unavailable, WorkerPool};

/// A pool of Python sealing workers (`vnx.codec-ipc/1`, [`WorkerPool`]). A worker that dies, hangs or breaks the
/// protocol is killed and replaced at the next request; its request fails with BACKEND_UNAVAILABLE or TIMEOUT
/// (retryable). Staging files are named "seal-<pid hex>-<start ns hex>-<job>.in|out"; plaintext left behind by a
/// process that died mid-call is swept when the sealer starts.
pub struct WorkerSealer {
    pool: WorkerPool,
}

/// Only verdicts about the data or the key pass through; anything else the worker reports (a missing staging file,
/// an existing output, INTERNAL, ...) is a failure of the sealing service, never "not stored" or damage.
fn sealing_verdict(e: VnxError) -> VnxError {
    match e.code {
        ErrorCode::Corruption | ErrorCode::Unauthorized | ErrorCode::VersionMismatch | ErrorCode::Timeout
        | ErrorCode::BackendUnavailable => e,
        _ => unavailable(format!("sealing worker: {e}")),
    }
}

impl WorkerSealer {
    pub fn new(cfg: WorkerConfig) -> Result<WorkerSealer> {
        Ok(WorkerSealer { pool: WorkerPool::new(cfg, "seal", "sealing")? })
    }

    fn call(&self, op: &str, args: Value) -> Result<Value> {
        self.pool.call(op, args).map_err(sealing_verdict)
    }

    fn transform(&self, op: &str, device_id: &[u8; 16], at: GenerationRef, input: &[u8]) -> Result<Vec<u8>> {
        let extent = i64::try_from(at.extent.0).or_else(|_| err(ErrorCode::Overflow, "extent beyond the protocol range"))?;
        let generation = i64::try_from(at.generation.get()).or_else(|_| err(ErrorCode::Overflow, "generation beyond the protocol range"))?;
        self.pool.file_job(
            op,
            input,
            |job, iname, isha, oname| {
                obj([
                    ("job", Value::Int(job as i64)),
                    ("device_id", Value::Str(hex(device_id))),
                    ("extent", Value::Int(extent)),
                    ("generation", Value::Int(generation)),
                    ("input", Value::Str(iname.into())),
                    ("input_sha256", Value::Str(isha.into())),
                    ("output", Value::Str(oname.into())),
                ])
            },
            sealing_verdict,
        )
    }
}

impl Sealer for WorkerSealer {
    fn seal(&self, device_id: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
        self.transform("seal_extent", device_id, at, plain)
    }

    fn open(&self, device_id: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
        self.transform("open_extent", device_id, at, sealed)
    }

    fn key_check(&self, device_id: &[u8; 16]) -> Result<[u8; 32]> {
        let r = self.call("key_check", obj([("device_id", Value::Str(hex(device_id)))]))?;
        r.as_obj()
            .and_then(|o| o.get("key_check"))
            .and_then(Value::as_str)
            .and_then(parse_hex::<32>)
            .ok_or_else(|| unavailable("key_check result"))
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation, Geometry};
    use crate::journal::tests::TempDir;
    use crate::media::MemoryMedia;
    use crate::record::FLAG_SEALED;
    use crate::volume::{Volume, VolumeConfig};

    /// TEST-ONLY stand-in for the Python sealer: a SHA-256 keystream and a keyed SHA-256 tag. Never shipped, never a
    /// cipher; it only exercises SealedMedia's contract (binding, tamper refusal, key check).
    pub(crate) struct ToySealer(pub [u8; 32]);

    impl ToySealer {
        fn bind(&self, d: &[u8; 16], at: GenerationRef) -> Vec<u8> {
            [&self.0[..], d, &at.extent.0.to_be_bytes(), &at.generation.get().to_be_bytes()].concat()
        }
        fn stream(&self, b: &[u8], n: usize) -> Vec<u8> {
            (0..n.div_ceil(32) as u64).flat_map(|i| sha256(&[b, &i.to_be_bytes()[..]].concat())).take(n).collect()
        }
    }

    impl Sealer for ToySealer {
        fn seal(&self, d: &[u8; 16], at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
            let b = self.bind(d, at);
            let ct: Vec<u8> = plain.iter().zip(self.stream(&b, plain.len())).map(|(p, k)| p ^ k).collect();
            Ok([&ct[..], &sha256(&[&b[..], &ct[..]].concat())[..]].concat())
        }
        fn open(&self, d: &[u8; 16], at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
            if sealed.len() < 32 {
                return err(ErrorCode::Corruption, "short");
            }
            let (ct, tag) = sealed.split_at(sealed.len() - 32);
            let b = self.bind(d, at);
            if sha256(&[&b[..], ct].concat())[..] != *tag {
                return err(ErrorCode::Corruption, "sealed extent failed authentication");
            }
            Ok(ct.iter().zip(self.stream(&b, ct.len())).map(|(c, k)| c ^ k).collect())
        }
        fn key_check(&self, d: &[u8; 16]) -> Result<[u8; 32]> {
            Ok(sha256(&[&self.0[..], d, b"kcv"].concat()))
        }
    }

    const DEV: [u8; 16] = [5; 16];

    fn sealed_vol(d: &TempDir, raw: Arc<MemoryMedia>, key: u8) -> Result<Volume> {
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(raw, Arc::new(ToySealer([key; 32])), DEV, 2048));
        Volume::create(&d.0, Geometry::new(512, 4, 16).unwrap(), DEV, FLAG_SEALED, m, VolumeConfig::default())
    }

    fn reopen(d: &TempDir, raw: Arc<MemoryMedia>, key: u8) -> Result<Volume> {
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(raw, Arc::new(ToySealer([key; 32])), DEV, 2048));
        Volume::open(&d.0, m, VolumeConfig::default()).map(|(v, _)| v)
    }

    #[test]
    fn a_sealed_volume_never_hands_plaintext_to_the_backend() {
        let d = TempDir::new("seal");
        let raw = Arc::new(MemoryMedia::new());
        let v = sealed_vol(&d, raw.clone(), 1).unwrap();
        let data = b"top secret extent payload ".repeat(79);
        let data = &data[..2048];
        v.write_extent(ExtentId(2), data, 1000).unwrap();
        assert_eq!(v.read_extent(ExtentId(2)).unwrap(), data);
        for at in raw.list().unwrap() {
            let stored = raw.get(at).unwrap();
            assert!(!stored.windows(10).any(|w| w == b"top secret"), "plaintext reached the backend at {at:?}");
        }
        drop(v);
        assert_eq!(reopen(&d, raw.clone(), 1).unwrap().read_extent(ExtentId(2)).unwrap(), data);
    }

    #[test]
    fn moved_replayed_or_tampered_objects_are_corruption_never_data() {
        let d = TempDir::new("seal");
        let raw = Arc::new(MemoryMedia::new());
        let v = sealed_vol(&d, raw.clone(), 1).unwrap();
        v.write_extent(ExtentId(0), &[1u8; 2048], 1000).unwrap();
        v.write_extent(ExtentId(1), &[2u8; 2048], 1000).unwrap();
        let g = |e: u64| GenerationRef { extent: ExtentId(e), generation: v.mapping(ExtentId(e)).unwrap().unwrap() };
        // extent 1's object copied over extent 0's: the sealer refuses it (bound to its extent)
        let one = raw.get(g(1)).unwrap();
        raw.remove(g(0)).unwrap();
        raw.put(g(0), &one).unwrap();
        assert_eq!(v.read_extent(ExtentId(0)).unwrap_err().code, ErrorCode::Corruption);
        // a flipped byte
        let mut t = raw.get(g(1)).unwrap();
        t[5] ^= 1;
        raw.remove(g(1)).unwrap();
        raw.put(g(1), &t).unwrap();
        assert_eq!(v.read_extent(ExtentId(1)).unwrap_err().code, ErrorCode::Corruption);
        // an older generation's object replayed under the current generation
        v.write_extent(ExtentId(3), &[3u8; 2048], 1000).unwrap();
        let old = raw.get(g(3)).unwrap();
        v.write_extent(ExtentId(3), &[4u8; 2048], 1000).unwrap();
        raw.remove(g(3)).unwrap();
        raw.put(g(3), &old).unwrap();
        assert_eq!(v.read_extent(ExtentId(3)).unwrap_err().code, ErrorCode::Corruption);
    }

    /// A sealer whose every open fails with `code` (a worker reporting it).
    struct Failing(ErrorCode);
    impl Sealer for Failing {
        fn seal(&self, _: &[u8; 16], _: GenerationRef, p: &[u8]) -> Result<Vec<u8>> {
            Ok(p.to_vec())
        }
        fn open(&self, _: &[u8; 16], _: GenerationRef, _: &[u8]) -> Result<Vec<u8>> {
            err(self.0, "injected")
        }
        fn key_check(&self, _: &[u8; 16]) -> Result<[u8; 32]> {
            Ok([0; 32])
        }
    }

    #[test]
    fn per_object_key_or_version_mismatches_are_damage_and_scrub_lists_them() {
        for code in [ErrorCode::Unauthorized, ErrorCode::VersionMismatch] {
            let raw = Arc::new(MemoryMedia::new());
            let m = SealedMedia::new(raw.clone(), Arc::new(Failing(code)), DEV, 2048);
            let at = GenerationRef { extent: ExtentId(0), generation: Generation::FIRST };
            m.put(at, &[1u8; 2048]).unwrap();
            assert_eq!(m.get(at).unwrap_err().code, ErrorCode::Corruption, "{code:?}");
        }
        let d = TempDir::new("seal-scrub");
        let raw = Arc::new(MemoryMedia::new());
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(raw, Arc::new(Failing(ErrorCode::Unauthorized)), DEV, 2048));
        let v = Volume::create(&d.0, Geometry::new(512, 4, 16).unwrap(), DEV, FLAG_SEALED, m, VolumeConfig { verify_readback: false, ..Default::default() }).unwrap();
        v.write_extent(ExtentId(0), &[1u8; 2048], 1000).unwrap();
        v.write_extent(ExtentId(1), &[2u8; 2048], 1000).unwrap();
        let s = v.scrub().unwrap();
        assert_eq!(s.failed.len(), 2, "both extents listed, the scrub did not stop");
        // an oversized object from the backend is refused before staging
        let raw = Arc::new(MemoryMedia::new());
        let m = SealedMedia::new(raw.clone(), Arc::new(ToySealer([1; 32])), DEV, 2048);
        let at = GenerationRef { extent: ExtentId(3), generation: Generation::FIRST };
        raw.put(at, &vec![0u8; 2048 + SEAL_OVERHEAD as usize + 1]).unwrap();
        assert_eq!(m.get(at).unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn the_wrong_key_or_a_mismatched_adapter_is_refused_at_open() {
        let d = TempDir::new("seal");
        let raw = Arc::new(MemoryMedia::new());
        let v = sealed_vol(&d, raw.clone(), 1).unwrap();
        v.write_extent(ExtentId(0), &[9u8; 2048], 1000).unwrap();
        drop(v);
        let before = raw.list().unwrap();
        assert_eq!(reopen(&d, raw.clone(), 2).err().unwrap().code, ErrorCode::Unauthorized);
        assert_eq!(raw.list().unwrap(), before, "nothing collected under the wrong key");
        // a sealed volume over a plain adapter, a plain volume over the sealing adapter
        assert_eq!(Volume::open(&d.0, raw.clone(), VolumeConfig::default()).err().unwrap().code, ErrorCode::InvalidArgument);
        let d2 = TempDir::new("seal");
        let m: Arc<dyn MediaAdapter> = Arc::new(SealedMedia::new(Arc::new(MemoryMedia::new()), Arc::new(ToySealer([1; 32])), DEV, 2048));
        assert_eq!(
            Volume::create(&d2.0, Geometry::new(512, 4, 16).unwrap(), DEV, 0, m, VolumeConfig::default()).err().unwrap().code,
            ErrorCode::InvalidArgument
        );
        // an unsealed backend opened through the sealing adapter: its marker carries no key check
        let d3 = TempDir::new("seal");
        let plain = Arc::new(MemoryMedia::new());
        drop(Volume::create(&d3.0, Geometry::new(512, 4, 16).unwrap(), DEV, 0, plain.clone(), VolumeConfig::default()).unwrap());
        let s = SealedMedia::new(plain, Arc::new(ToySealer([1; 32])), DEV, 2048);
        let marker = GenerationRef { extent: MARKER_EXTENT, generation: Generation::FIRST };
        assert_eq!(s.get(marker).unwrap_err().code, ErrorCode::Unauthorized);
    }
}
