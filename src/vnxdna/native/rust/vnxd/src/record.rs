//! Persisted record format `vnx.record/1` (docs/V14_CONTRACTS.md §3).
//!
//! Every record the service persists (device superblock, volume journal) has the same frame, little-endian:
//!
//! ```text
//! offset  size  field
//!      0     4  magic "VNXR"
//!      4     2  format version (1)
//!      6     2  kind
//!      8     4  payload length n (must equal the kind's fixed payload size)
//!     12     n  payload
//!   12+n     4  CRC-32 (zlib) of bytes [0, 12+n)
//! ```
//!
//! Decoding is strict: wrong magic, unknown version or kind, a length that is not the kind's, a CRC mismatch, a
//! reserved bit set or an inconsistent field is an error. A journal is a concatenation of records; reading it stops
//! at the first record that does not decode (a torn append), and the offset of that record is where the next append
//! starts (the V13 volume journal rule).

use crate::addr::{ExtentId, Generation, GenerationRef, Geometry};
use crate::digest::{crc32, hex, parse_hex};
use crate::json::{obj, Value};
use crate::error::{err, ErrorCode, Result, VnxError};

pub const MAGIC: [u8; 4] = *b"VNXR";
pub const FORMAT_VERSION: u16 = 1;
pub const HEADER_LEN: usize = 12;
pub const TRAILER_LEN: usize = 4;

pub const KIND_SUPERBLOCK: u16 = 1;
pub const KIND_MAP_UPDATE: u16 = 2;
pub const KIND_GENERATION_RELEASED: u16 = 3;
pub const KIND_MAP_BATCH: u16 = 4;
/// Journal record: a snapshot of an extent at a point in time.
pub const KIND_SNAPSHOT: u16 = 5;
/// Journal record: a retention policy (retention deadline and optional WORM flag).
pub const KIND_RETENTION: u16 = 6;

/// Most extents one `map_batch` record (one atomic multi-extent transaction) may remap.
pub const MAX_BATCH_EXTENTS: usize = 1024;

/// Superblock flag: extents are sealed (AES-256-GCM by the security layer) before they reach a backend.
pub const FLAG_SEALED: u32 = 1;
/// Superblock flag: volume is WORM (write-once-read-many) - no extent may be rewritten after retention deadline.
pub const FLAG_WORM: u32 = 2;
const KNOWN_FLAGS: u32 = FLAG_SEALED | FLAG_WORM;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Superblock {
    pub device_id: [u8; 16],
    pub geometry: Geometry,
    pub flags: u32,
}

/// Journal record: from transaction `txid` on, `extent` is served by `generation` (`previous` = 0 when the extent
/// had no generation). `length` and `sha256` describe the stored generation's bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MapUpdate {
    pub txid: u64,
    pub at: GenerationRef,
    pub previous: Option<Generation>,
    pub length: u64,
    pub sha256: [u8; 32],
}

/// Journal record: `generation` of `extent` is no longer referenced and its backend copy was removed (GC).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GenerationReleased {
    pub txid: u64,
    pub at: GenerationRef,
}

/// One extent of a [`MapBatch`]: the same fields as a [`MapUpdate`] without the txid.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BatchEntry {
    pub at: GenerationRef,
    pub previous: Option<Generation>,
    pub length: u64,
    pub sha256: [u8; 32],
}

/// Journal record: transaction `txid` remaps every extent of `entries` at once (all or none). Two to
/// [`MAX_BATCH_EXTENTS`] entries, extents strictly increasing.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MapBatch {
    pub txid: u64,
    pub entries: Vec<BatchEntry>,
}

/// Journal record: a snapshot of extent `at` taken at transaction `txid` with snapshot id `id`.
/// `timestamp_ms` is the time the snapshot was created (for ordering and retention checks).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Snapshot {
    pub txid: u64,
    pub id: u64,
    pub at: GenerationRef,
    pub timestamp_ms: i64,
}

/// Journal record: a retention policy for snapshot `id` created at transaction `txid`.
/// `until_ms` is the retention deadline; `worm` indicates WORM (write-once-read-many) mode.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Retention {
    pub txid: u64,
    pub id: u64,
    pub until_ms: i64,
    pub worm: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Record {
    Superblock(Superblock),
    MapUpdate(MapUpdate),
    GenerationReleased(GenerationReleased),
    MapBatch(MapBatch),
    Snapshot(Snapshot),
    Retention(Retention),
}

const SUPERBLOCK_LEN: usize = 16 + 4 + 4 + 8 + 4;
const MAP_UPDATE_LEN: usize = 8 * 5 + 32;
const RELEASED_LEN: usize = 8 * 3;
const BATCH_HEAD_LEN: usize = 8 + 4 + 4;
const BATCH_ENTRY_LEN: usize = 8 * 4 + 32;
/// Snapshot payload: txid(8) + id(8) + extent(8) + generation(8) + timestamp_ms(8) = 40
const SNAPSHOT_LEN: usize = 8 * 5;
/// Retention payload: txid(8) + id(8) + until_ms(8) + worm(1) + padding(7) = 32
const RETENTION_LEN: usize = 8 * 3 + 1 + 7;
/// Largest encoded record (a full map batch).
pub const MAX_RECORD_LEN: usize = HEADER_LEN + BATCH_HEAD_LEN + BATCH_ENTRY_LEN * MAX_BATCH_EXTENTS + TRAILER_LEN;

fn corrupt<T>(msg: impl Into<String>) -> Result<T> {
    err(ErrorCode::Corruption, msg)
}

struct Reader<'a> {
    b: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn take<const N: usize>(&mut self) -> [u8; N] {
        let mut out = [0u8; N];
        out.copy_from_slice(&self.b[self.pos..self.pos + N]); // lengths validated before parsing
        self.pos += N;
        out
    }
    fn u32(&mut self) -> u32 {
        u32::from_le_bytes(self.take())
    }
    fn u64(&mut self) -> u64 {
        u64::from_le_bytes(self.take())
    }
    fn i64(&mut self) -> i64 {
        i64::from_le_bytes(self.take())
    }
    fn u8(&mut self) -> u8 {
        let b = self.b[self.pos];
        self.pos += 1;
        b
    }
}

fn generation(n: u64, what: &str) -> Result<Generation> {
    Generation::new(n).map_err(|_| VnxError::new(ErrorCode::Corruption, format!("{what}: generation 0")))
}

impl Record {
    pub fn kind(&self) -> u16 {
        match self {
            Record::Superblock(_) => KIND_SUPERBLOCK,
            Record::MapUpdate(_) => KIND_MAP_UPDATE,
            Record::GenerationReleased(_) => KIND_GENERATION_RELEASED,
            Record::MapBatch(_) => KIND_MAP_BATCH,
            Record::Snapshot(_) => KIND_SNAPSHOT,
            Record::Retention(_) => KIND_RETENTION,
        }
    }

    fn payload(&self) -> Vec<u8> {
        let mut p = Vec::new();
        match self {
            Record::Superblock(s) => {
                p.extend_from_slice(&s.device_id);
                p.extend_from_slice(&s.geometry.block_size().to_le_bytes());
                p.extend_from_slice(&s.geometry.blocks_per_extent().to_le_bytes());
                p.extend_from_slice(&s.geometry.capacity_blocks().to_le_bytes());
                p.extend_from_slice(&s.flags.to_le_bytes());
            }
            Record::MapUpdate(m) => {
                for v in [m.txid, m.at.extent.0, m.at.generation.get(), m.previous.map_or(0, |g| g.get()), m.length] {
                    p.extend_from_slice(&v.to_le_bytes());
                }
                p.extend_from_slice(&m.sha256);
            }
            Record::GenerationReleased(r) => {
                for v in [r.txid, r.at.extent.0, r.at.generation.get()] {
                    p.extend_from_slice(&v.to_le_bytes());
                }
            }
            Record::MapBatch(b) => {
                p.extend_from_slice(&b.txid.to_le_bytes());
                p.extend_from_slice(&(b.entries.len() as u32).to_le_bytes());
                p.extend_from_slice(&0u32.to_le_bytes());
                for e in &b.entries {
                    for v in [e.at.extent.0, e.at.generation.get(), e.previous.map_or(0, |g| g.get()), e.length] {
                        p.extend_from_slice(&v.to_le_bytes());
                    }
                    p.extend_from_slice(&e.sha256);
                }
            }
            Record::Snapshot(s) => {
                p.extend_from_slice(&s.txid.to_le_bytes());
                p.extend_from_slice(&s.id.to_le_bytes());
                p.extend_from_slice(&s.at.extent.0.to_le_bytes());
                p.extend_from_slice(&s.at.generation.get().to_le_bytes());
                p.extend_from_slice(&s.timestamp_ms.to_le_bytes());
            }
            Record::Retention(r) => {
                p.extend_from_slice(&r.txid.to_le_bytes());
                p.extend_from_slice(&r.id.to_le_bytes());
                p.extend_from_slice(&r.until_ms.to_le_bytes());
                p.extend_from_slice(&(r.worm as u8).to_le_bytes());
                p.extend_from_slice(&[0u8; 7]); // padding to 32 bytes
            }
        }
        p
    }

    /// Validate the invariants the decoder enforces, so that nothing invalid is ever written.
    pub fn validate(&self) -> Result<()> {
        match self {
            Record::Superblock(s) if s.flags & !KNOWN_FLAGS != 0 => corrupt(format!("unknown superblock flags {:#x}", s.flags)),
            Record::MapUpdate(m) if m.txid == 0 => corrupt("map update with txid 0"),
            Record::MapUpdate(m) if m.previous.map_or(false, |p| p >= m.at.generation) => {
                corrupt("map update: previous generation not older than the new one")
            }
            Record::GenerationReleased(r) if r.txid == 0 => corrupt("generation release with txid 0"),
            Record::MapBatch(b) => {
                if b.txid == 0 {
                    return corrupt("map batch with txid 0");
                }
                if !(2..=MAX_BATCH_EXTENTS).contains(&b.entries.len()) {
                    return corrupt(format!("map batch of {} extents (2..={MAX_BATCH_EXTENTS})", b.entries.len()));
                }
                for (i, e) in b.entries.iter().enumerate() {
                    if e.previous.map_or(false, |p| p >= e.at.generation) {
                        return corrupt("map batch: previous generation not older than the new one");
                    }
                    if i > 0 && b.entries[i - 1].at.extent >= e.at.extent {
                        return corrupt("map batch: extents not strictly increasing");
                    }
                }
                Ok(())
            }
            Record::Snapshot(s) if s.txid == 0 => corrupt("snapshot with txid 0"),
            Record::Snapshot(s) if s.id == 0 => corrupt("snapshot with id 0"),
            Record::Retention(r) if r.txid == 0 => corrupt("retention with txid 0"),
            Record::Retention(r) if r.id == 0 => corrupt("retention with id 0"),
            _ => Ok(()),
        }
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let payload = self.payload();
        let mut out = Vec::with_capacity(HEADER_LEN + payload.len() + TRAILER_LEN);
        out.extend_from_slice(&MAGIC);
        out.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
        out.extend_from_slice(&self.kind().to_le_bytes());
        out.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        out.extend_from_slice(&payload);
        let crc = crc32(&out);
        out.extend_from_slice(&crc.to_le_bytes());
        Ok(out)
    }

    /// Decode one record from the start of `buf`; returns it with the number of bytes it occupies.
    pub fn decode(buf: &[u8]) -> Result<(Record, usize)> {
        if buf.len() < HEADER_LEN {
            return corrupt(format!("truncated header ({} bytes)", buf.len()));
        }
        if buf[0..4] != MAGIC {
            return corrupt("bad record magic");
        }
        let version = u16::from_le_bytes([buf[4], buf[5]]);
        if version != FORMAT_VERSION {
            return err(ErrorCode::VersionMismatch, format!("record format version {version} (supported: 1)"));
        }
        let kind = u16::from_le_bytes([buf[6], buf[7]]);
        let n = u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]) as usize;
        let fixed = match kind {
            KIND_SUPERBLOCK => Some(SUPERBLOCK_LEN),
            KIND_MAP_UPDATE => Some(MAP_UPDATE_LEN),
            KIND_GENERATION_RELEASED => Some(RELEASED_LEN),
            KIND_SNAPSHOT => Some(SNAPSHOT_LEN),
            KIND_RETENTION => Some(RETENTION_LEN),
            KIND_MAP_BATCH => None,
            _ => return corrupt(format!("unknown record kind {kind}")),
        };
        match fixed {
            Some(want) if n != want => return corrupt(format!("record kind {kind}: payload length {n}, expected {want}")),
            None if n < BATCH_HEAD_LEN + 2 * BATCH_ENTRY_LEN
                || n > BATCH_HEAD_LEN + MAX_BATCH_EXTENTS * BATCH_ENTRY_LEN
                || (n - BATCH_HEAD_LEN) % BATCH_ENTRY_LEN != 0 =>
            {
                return corrupt(format!("map batch payload length {n}"))
            }
            _ => {}
        }
        let total = HEADER_LEN + n + TRAILER_LEN;
        if buf.len() < total {
            return corrupt(format!("truncated record ({} of {total} bytes)", buf.len()));
        }
        let stored = u32::from_le_bytes([buf[total - 4], buf[total - 3], buf[total - 2], buf[total - 1]]);
        if crc32(&buf[..HEADER_LEN + n]) != stored {
            return corrupt("record CRC-32 mismatch");
        }
        let mut r = Reader { b: &buf[HEADER_LEN..HEADER_LEN + n], pos: 0 };
        let rec = match kind {
            KIND_SUPERBLOCK => {
                let device_id = r.take::<16>();
                let (bs, bpe, cap, flags) = (r.u32(), r.u32(), r.u64(), r.u32());
                let geometry = Geometry::new(bs, bpe, cap)
                    .map_err(|e| VnxError::new(ErrorCode::Corruption, format!("superblock geometry: {}", e.message)))?;
                Record::Superblock(Superblock { device_id, geometry, flags })
            }
            KIND_MAP_UPDATE => {
                let (txid, extent, gen, prev, length) = (r.u64(), r.u64(), r.u64(), r.u64(), r.u64());
                let sha256 = r.take::<32>();
                let at = GenerationRef { extent: ExtentId(extent), generation: generation(gen, "map update")? };
                let previous = if prev == 0 { None } else { Some(generation(prev, "map update")?) };
                Record::MapUpdate(MapUpdate { txid, at, previous, length, sha256 })
            }
            KIND_GENERATION_RELEASED => {
                let (txid, extent, gen) = (r.u64(), r.u64(), r.u64());
                let at = GenerationRef { extent: ExtentId(extent), generation: generation(gen, "release")? };
                Record::GenerationReleased(GenerationReleased { txid, at })
            }
            KIND_SNAPSHOT => {
                let (txid, id, extent, gen, timestamp_ms) = (r.u64(), r.u64(), r.u64(), r.u64(), r.i64());
                let at = GenerationRef { extent: ExtentId(extent), generation: generation(gen, "snapshot")? };
                Record::Snapshot(Snapshot { txid, id, at, timestamp_ms })
            }
            KIND_RETENTION => {
                let (txid, id, until_ms) = (r.u64(), r.u64(), r.i64());
                let worm = r.u8() != 0;
                let _padding = r.take::<7>(); // skip padding
                Record::Retention(Retention { txid, id, until_ms, worm })
            }
            _ => {
                let (txid, count, reserved) = (r.u64(), r.u32() as usize, r.u32());
                if reserved != 0 {
                    return corrupt("map batch: reserved field set");
                }
                if count != (n - BATCH_HEAD_LEN) / BATCH_ENTRY_LEN {
                    return corrupt(format!("map batch: count {count} does not match its payload length {n}"));
                }
                let mut entries = Vec::with_capacity(count);
                for _ in 0..count {
                    let (extent, gen, prev, length) = (r.u64(), r.u64(), r.u64(), r.u64());
                    let sha256 = r.take::<32>();
                    let at = GenerationRef { extent: ExtentId(extent), generation: generation(gen, "map batch")? };
                    let previous = if prev == 0 { None } else { Some(generation(prev, "map batch")?) };
                    entries.push(BatchEntry { at, previous, length, sha256 });
                }
                Record::MapBatch(MapBatch { txid, entries })
            }
        };
        rec.validate()?;
        Ok((rec, total))
    }
}

/// The result of reading a journal: every record up to the first one that does not decode.
#[derive(Debug)]
pub struct Scan {
    pub records: Vec<Record>,
    /// bytes of `buf` covered by `records` (the truncation point for the next append)
    pub valid_len: usize,
    /// why reading stopped before the end of the buffer, if it did
    pub tail_error: Option<VnxError>,
}

pub fn scan(buf: &[u8]) -> Scan {
    let mut records = Vec::new();
    let mut pos = 0;
    let mut tail_error = None;
    while pos < buf.len() {
        match Record::decode(&buf[pos..]) {
            Ok((rec, used)) => {
                records.push(rec);
                pos += used;
            }
            Err(e) => {
                tail_error = Some(e);
                break;
            }
        }
    }
    Scan { records, valid_len: pos, tail_error }
}

/// JSON description of a record (inspection output and the cross-language golden files). Integers above
/// `i64::MAX` cannot be described and are an OVERFLOW error.
pub fn to_json(rec: &Record) -> Result<Value> {
    let int = |v: u64| i64::try_from(v).map(Value::Int).or_else(|_| err(ErrorCode::Overflow, format!("{v} exceeds the JSON integer range")));
    Ok(match rec {
        Record::Superblock(s) => obj([
            ("kind", Value::Str("superblock".into())),
            ("device_id", Value::Str(hex(&s.device_id))),
            ("block_size", int(s.geometry.block_size() as u64)?),
            ("blocks_per_extent", int(s.geometry.blocks_per_extent() as u64)?),
            ("capacity_blocks", int(s.geometry.capacity_blocks())?),
            ("flags", int(s.flags as u64)?),
        ]),
        Record::MapUpdate(m) => obj([
            ("kind", Value::Str("map_update".into())),
            ("txid", int(m.txid)?),
            ("extent", int(m.at.extent.0)?),
            ("generation", int(m.at.generation.get())?),
            ("previous", int(m.previous.map_or(0, |g| g.get()))?),
            ("length", int(m.length)?),
            ("sha256", Value::Str(hex(&m.sha256))),
        ]),
        Record::GenerationReleased(r) => obj([
            ("kind", Value::Str("generation_released".into())),
            ("txid", int(r.txid)?),
            ("extent", int(r.at.extent.0)?),
            ("generation", int(r.at.generation.get())?),
        ]),
        Record::MapBatch(b) => {
            let mut entries = Vec::with_capacity(b.entries.len());
            for e in &b.entries {
                entries.push(obj([
                    ("extent", int(e.at.extent.0)?),
                    ("generation", int(e.at.generation.get())?),
                    ("previous", int(e.previous.map_or(0, |g| g.get()))?),
                    ("length", int(e.length)?),
                    ("sha256", Value::Str(hex(&e.sha256))),
                ]));
            }
            obj([("kind", Value::Str("map_batch".into())), ("txid", int(b.txid)?), ("entries", Value::Arr(entries))])
        }
        Record::Snapshot(s) => obj([
            ("kind", Value::Str("snapshot".into())),
            ("txid", int(s.txid)?),
            ("id", int(s.id)?),
            ("extent", int(s.at.extent.0)?),
            ("generation", int(s.at.generation.get())?),
            ("timestamp_ms", int(s.timestamp_ms as u64)?),
        ]),
        Record::Retention(r) => obj([
            ("kind", Value::Str("retention".into())),
            ("txid", int(r.txid)?),
            ("id", int(r.id)?),
            ("until_ms", int(r.until_ms as u64)?),
            ("worm", Value::Bool(r.worm)),
        ]),
    })
}

/// Inverse of [`to_json`]; exact keys, every field validated.
pub fn from_json(v: &Value) -> Result<Record> {
    let o = v.as_obj().ok_or(()).or_else(|_| err(ErrorCode::InvalidArgument, "record description must be an object"))?;
    let kind = o.get("kind").and_then(Value::as_str).unwrap_or("");
    let keys: &[&str] = match kind {
        "superblock" => &["kind", "device_id", "block_size", "blocks_per_extent", "capacity_blocks", "flags"],
        "map_update" => &["kind", "txid", "extent", "generation", "previous", "length", "sha256"],
        "generation_released" => &["kind", "txid", "extent", "generation"],
        "map_batch" => &["kind", "txid", "entries"],
        "snapshot" => &["kind", "txid", "id", "extent", "generation", "timestamp_ms"],
        "retention" => &["kind", "txid", "id", "until_ms", "worm"],
        _ => return err(ErrorCode::InvalidArgument, format!("unknown record kind {kind:?}")),
    };
    if o.len() != keys.len() || keys.iter().any(|k| !o.contains_key(*k)) {
        return err(ErrorCode::InvalidArgument, format!("{kind}: fields must be exactly {keys:?}"));
    }
    let u = |k: &str| -> Result<u64> {
        match o[k] {
            Value::Int(i) if i >= 0 => Ok(i as u64),
            _ => err(ErrorCode::InvalidArgument, format!("{kind}.{k} must be an integer >= 0")),
        }
    };
    let u32f = |k: &str| -> Result<u32> {
        u32::try_from(u(k)?).or_else(|_| err(ErrorCode::InvalidArgument, format!("{kind}.{k} exceeds 32 bits")))
    };
    let gen = |k: &str| Generation::new(u(k)?);
    let rec = match kind {
        "superblock" => {
            let device_id = o["device_id"].as_str().and_then(parse_hex::<16>);
            let device_id = device_id.ok_or(()).or_else(|_| err(ErrorCode::InvalidArgument, "device_id must be 32 hex digits"))?;
            let geometry = Geometry::new(u32f("block_size")?, u32f("blocks_per_extent")?, u("capacity_blocks")?)?;
            Record::Superblock(Superblock { device_id, geometry, flags: u32f("flags")? })
        }
        "map_update" => {
            let sha = o["sha256"].as_str().and_then(parse_hex::<32>);
            let sha256 = sha.ok_or(()).or_else(|_| err(ErrorCode::InvalidArgument, "sha256 must be 64 hex digits"))?;
            let previous = match u("previous")? {
                0 => None,
                p => Some(Generation::new(p)?),
            };
            let at = GenerationRef { extent: ExtentId(u("extent")?), generation: gen("generation")? };
            Record::MapUpdate(MapUpdate { txid: u("txid")?, at, previous, length: u("length")?, sha256 })
        }
        "generation_released" => Record::GenerationReleased(GenerationReleased {
            txid: u("txid")?,
            at: GenerationRef { extent: ExtentId(u("extent")?), generation: gen("generation")? },
        }),
        "snapshot" => Record::Snapshot(Snapshot {
            txid: u("txid")?,
            id: u("id")?,
            at: GenerationRef { extent: ExtentId(u("extent")?), generation: gen("generation")? },
            timestamp_ms: u("timestamp_ms")? as i64,
        }),
        "retention" => Record::Retention(Retention {
            txid: u("txid")?,
            id: u("id")?,
            until_ms: u("until_ms")? as i64,
            worm: match o["worm"] { Value::Bool(b) => b, _ => return err(ErrorCode::InvalidArgument, "retention.worm must be a boolean") },
        }),
        _ => {
            let items = match &o["entries"] {
                Value::Arr(a) => a,
                _ => return err(ErrorCode::InvalidArgument, "map_batch.entries must be an array"),
            };
            let mut entries = Vec::with_capacity(items.len());
            for it in items {
                let mut one = match it.as_obj() {
                    Some(m) => m.clone(),
                    None => return err(ErrorCode::InvalidArgument, "map_batch entries must be objects"),
                };
                const ENTRY_KEYS: [&str; 5] = ["extent", "generation", "previous", "length", "sha256"];
                if one.len() != ENTRY_KEYS.len() || ENTRY_KEYS.iter().any(|k| !one.contains_key(*k)) {
                    return err(ErrorCode::InvalidArgument, format!("map_batch entry fields must be exactly {ENTRY_KEYS:?}"));
                }
                one.insert("kind".into(), Value::Str("map_update".into()));
                one.insert("txid".into(), Value::Int(1));
                match from_json(&Value::Obj(one))? {
                    Record::MapUpdate(m) => {
                        entries.push(BatchEntry { at: m.at, previous: m.previous, length: m.length, sha256: m.sha256 })
                    }
                    _ => return err(ErrorCode::InvalidArgument, "map_batch entry"),
                }
            }
            Record::MapBatch(MapBatch { txid: u("txid")?, entries })
        }
    };
    if let Record::MapBatch(_) = &rec {
        rec.validate().map_err(|e| VnxError::new(ErrorCode::InvalidArgument, e.message))?;
    }
    Ok(rec)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::digest::sha256;

    // Failing tests for new record kinds (D1)
    #[test]
    fn snapshot_record_encode_decode_round_trip() {
        let snap = Record::Snapshot(Snapshot {
            txid: 42,
            id: 1,
            at: GenerationRef { extent: ExtentId(7), generation: Generation::FIRST },
            timestamp_ms: 1_700_000_000_000,
        });
        let bytes = snap.encode().unwrap();
        let (back, used) = Record::decode(&bytes).unwrap();
        assert_eq!((back, used), (snap, bytes.len()));
    }

    #[test]
    fn retention_record_encode_decode_round_trip() {
        let ret = Record::Retention(Retention {
            txid: 43,
            id: 1,
            until_ms: 1_800_000_000_000,
            worm: true,
        });
        let bytes = ret.encode().unwrap();
        let (back, used) = Record::decode(&bytes).unwrap();
        assert_eq!((back, used), (ret, bytes.len()));
    }

    #[test]
    fn snapshot_record_invalid_tx_zero_rejected() {
        let snap = Record::Snapshot(Snapshot {
            txid: 0,
            id: 1,
            at: GenerationRef { extent: ExtentId(7), generation: Generation::FIRST },
            timestamp_ms: 1_700_000_000_000,
        });
        assert_eq!(snap.encode().unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn retention_record_invalid_tx_zero_rejected() {
        let ret = Record::Retention(Retention {
            txid: 0,
            id: 1,
            until_ms: 1_800_000_000_000,
            worm: false,
        });
        assert_eq!(ret.encode().unwrap_err().code, ErrorCode::Corruption);
    }

    pub(crate) fn samples() -> Vec<Record> {
        let g = Geometry::new(4096, 256, 262_144).unwrap();
        vec![
            Record::Superblock(Superblock { device_id: *b"0123456789abcdef", geometry: g, flags: FLAG_SEALED }),
            Record::MapUpdate(MapUpdate {
                txid: 1,
                at: GenerationRef { extent: ExtentId(7), generation: Generation::FIRST },
                previous: None,
                length: 1 << 20,
                sha256: sha256(b"extent 7 v1"),
            }),
            Record::MapUpdate(MapUpdate {
                txid: 2,
                at: GenerationRef { extent: ExtentId(7), generation: Generation::new(2).unwrap() },
                previous: Some(Generation::FIRST),
                length: 1 << 20,
                sha256: sha256(b"extent 7 v2"),
            }),
            Record::GenerationReleased(GenerationReleased {
                txid: 3,
                at: GenerationRef { extent: ExtentId(7), generation: Generation::FIRST },
            }),
            Record::MapBatch(MapBatch {
                txid: 4,
                entries: vec![
                    BatchEntry {
                        at: GenerationRef { extent: ExtentId(3), generation: Generation::new(5).unwrap() },
                        previous: Some(Generation::new(4).unwrap()),
                        length: 1 << 20,
                        sha256: sha256(b"extent 3 v5"),
                    },
                    BatchEntry {
                        at: GenerationRef { extent: ExtentId(8), generation: Generation::FIRST },
                        previous: None,
                        length: 4096,
                        sha256: sha256(b"extent 8 v1"),
                    },
                ],
            }),
        ]
    }

    #[test]
    fn map_batches_are_strict() {
        let good = samples().pop().unwrap();
        let Record::MapBatch(b) = good.clone() else { panic!() };
        // one entry, out-of-order or repeated extents, too many entries: never encoded
        let mut one = b.clone();
        one.entries.truncate(1);
        let mut swapped = b.clone();
        swapped.entries.reverse();
        let mut repeated = b.clone();
        repeated.entries[1].at.extent = repeated.entries[0].at.extent;
        let mut huge = b.clone();
        huge.entries = (0..=MAX_BATCH_EXTENTS as u64)
            .map(|i| BatchEntry { at: GenerationRef { extent: ExtentId(i), generation: Generation::FIRST }, previous: None, length: 1, sha256: [0; 32] })
            .collect();
        for bad in [one, swapped, repeated, huge] {
            assert_eq!(Record::MapBatch(bad).encode().unwrap_err().code, ErrorCode::Corruption);
        }
        // a count that disagrees with the length, a reserved bit, a length not a whole number of entries
        let bytes = good.encode().unwrap();
        let recrc = |mut b: Vec<u8>| {
            let n = b.len();
            let crc = crc32(&b[..n - 4]);
            b[n - 4..].copy_from_slice(&crc.to_le_bytes());
            b
        };
        let mut c = bytes.clone();
        c[HEADER_LEN + 8] = 3;
        let mut r = bytes.clone();
        r[HEADER_LEN + 12] = 1;
        let mut l = bytes[..bytes.len() - 4 - 8].to_vec();
        let ln = (l.len() - HEADER_LEN) as u32;
        l[8..12].copy_from_slice(&ln.to_le_bytes());
        l.extend([0; 4]);
        for bad in [recrc(c), recrc(r), recrc(l)] {
            assert_eq!(Record::decode(&bad).unwrap_err().code, ErrorCode::Corruption);
        }
        // the largest batch encodes, decodes and stays within MAX_RECORD_LEN
        let mut max = b;
        max.entries = (0..MAX_BATCH_EXTENTS as u64)
            .map(|i| BatchEntry { at: GenerationRef { extent: ExtentId(i), generation: Generation::FIRST }, previous: None, length: 1, sha256: [0; 32] })
            .collect();
        let enc = Record::MapBatch(max.clone()).encode().unwrap();
        assert_eq!(enc.len(), MAX_RECORD_LEN);
        assert_eq!(Record::decode(&enc).unwrap().0, Record::MapBatch(max));
    }

    #[test]
    fn round_trip() {
        for rec in samples() {
            let bytes = rec.encode().unwrap();
            let (back, used) = Record::decode(&bytes).unwrap();
            assert_eq!((back, used), (rec, bytes.len()));
        }
    }

    #[test]
    fn every_single_bit_flip_is_rejected() {
        for rec in samples() {
            let bytes = rec.encode().unwrap();
            for i in 0..bytes.len() {
                for bit in 0..8 {
                    let mut b = bytes.clone();
                    b[i] ^= 1 << bit;
                    assert!(Record::decode(&b).is_err(), "flip byte {i} bit {bit} of kind {}", rec.kind());
                }
            }
        }
    }

    #[test]
    fn every_truncation_is_rejected() {
        for rec in samples() {
            let bytes = rec.encode().unwrap();
            for n in 0..bytes.len() {
                assert_eq!(Record::decode(&bytes[..n]).unwrap_err().code, ErrorCode::Corruption);
            }
        }
    }

    #[test]
    fn invalid_records_are_never_encoded() {
        let mut s = samples();
        if let Record::MapUpdate(m) = &mut s[2] {
            m.previous = Some(Generation::new(2).unwrap());
        }
        assert_eq!(s[2].encode().unwrap_err().code, ErrorCode::Corruption);
        if let Record::Superblock(sb) = &mut s[0] {
            sb.flags = 4; // 4 is not a known flag (KNOWN_FLAGS = FLAG_SEALED(1) | FLAG_WORM(2) = 3)
        }
        assert_eq!(s[0].encode().unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn version_mismatch_is_its_own_error() {
        let mut b = samples()[1].encode().unwrap();
        b[4] = 2;
        let n = b.len();
        let crc = crc32(&b[..n - 4]);
        b[n - 4..].copy_from_slice(&crc.to_le_bytes());
        assert_eq!(Record::decode(&b).unwrap_err().code, ErrorCode::VersionMismatch);
    }

    #[test]
    fn scan_stops_at_a_torn_tail() {
        let mut journal = Vec::new();
        for rec in samples() {
            journal.extend(rec.encode().unwrap());
        }
        let full = scan(&journal);
        assert_eq!((full.records.len(), full.valid_len), (5, journal.len()));
        assert!(full.tail_error.is_none());
        let last_start = journal.len() - (HEADER_LEN + BATCH_HEAD_LEN + 2 * BATCH_ENTRY_LEN + TRAILER_LEN);
        for cut in last_start..journal.len() {
            let s = scan(&journal[..cut]);
            assert_eq!((s.records.len(), s.valid_len), (4, last_start), "cut {cut}");
            assert_eq!(s.tail_error.is_some(), cut > last_start);
        }
    }
}
