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

/// Superblock flag: extents are sealed (AES-256-GCM by the security layer) before they reach a backend.
pub const FLAG_SEALED: u32 = 1;
const KNOWN_FLAGS: u32 = FLAG_SEALED;

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

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Record {
    Superblock(Superblock),
    MapUpdate(MapUpdate),
    GenerationReleased(GenerationReleased),
}

const SUPERBLOCK_LEN: usize = 16 + 4 + 4 + 8 + 4;
const MAP_UPDATE_LEN: usize = 8 * 5 + 32;
const RELEASED_LEN: usize = 8 * 3;

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
        let want = match kind {
            KIND_SUPERBLOCK => SUPERBLOCK_LEN,
            KIND_MAP_UPDATE => MAP_UPDATE_LEN,
            KIND_GENERATION_RELEASED => RELEASED_LEN,
            _ => return corrupt(format!("unknown record kind {kind}")),
        };
        if n != want {
            return corrupt(format!("record kind {kind}: payload length {n}, expected {want}"));
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
            _ => {
                let (txid, extent, gen) = (r.u64(), r.u64(), r.u64());
                let at = GenerationRef { extent: ExtentId(extent), generation: generation(gen, "release")? };
                Record::GenerationReleased(GenerationReleased { txid, at })
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
        _ => Record::GenerationReleased(GenerationReleased {
            txid: u("txid")?,
            at: GenerationRef { extent: ExtentId(u("extent")?), generation: gen("generation")? },
        }),
    };
    Ok(rec)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::digest::sha256;

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
        ]
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
            sb.flags = 2;
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
        assert_eq!((full.records.len(), full.valid_len), (4, journal.len()));
        assert!(full.tail_error.is_none());
        let last_start = journal.len() - (HEADER_LEN + RELEASED_LEN + TRAILER_LEN);
        for cut in last_start..journal.len() {
            let s = scan(&journal[..cut]);
            assert_eq!((s.records.len(), s.valid_len), (3, last_start), "cut {cut}");
            assert_eq!(s.tail_error.is_some(), cut > last_start);
        }
    }
}
