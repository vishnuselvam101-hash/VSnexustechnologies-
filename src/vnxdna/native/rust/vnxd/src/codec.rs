//! The DNA codec boundary of the media adapters (docs/V14_MEDIA_ADAPTERS.md §2): the [`Codec`] trait, the strand
//! pool file `vnx.dna-pool/1`, and the generation frame and envelopes shared by the `dna-sim` and `dna-lab`
//! adapters.
//!
//! vnxd never implements the VNX-DNA codec. The production codec is [`WorkerCodec`]: a pool of Python workers
//! (`python -m vnxdna.device.dnasim`, [`crate::worker::WorkerPool`]) that run the existing archive container, encoder,
//! ECC and decoder (`vnxdna.pipeline.stream`) and the existing staged channel simulator (`vnxdna.simulation.engine`)
//! through `vnx.codec-ipc/1` `encode_extent` / `decode_extent`. [`ToyCodec`] is a TEST-ONLY stand-in (2 bits per
//! base, majority vote, no ECC) that lets the adapters' own logic be tested without Python; it is never the codec.
//!
//! Integrity: what the codec encodes is a *frame* (magic, extent, generation, length, bytes), so a pool handed back
//! for another generation, a truncated decode or an empty extent can never be confused with the bytes asked for; a
//! decode is accepted only when its output has exactly the SHA-256 and length recorded at `put` and its frame names
//! the generation asked for. Anything else is CORRUPTION, never other bytes.

use crate::addr::{GenerationRef, MAX_EXTENT_BYTES};
use crate::digest::{hex, sha256};
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::fault::SplitMix64;
use crate::json::{obj, Value};
use crate::worker::{unavailable, WorkerConfig, WorkerPool};
use std::collections::BTreeMap;

/// One simulated sequencing run: a shipped `vnx.channel-model/1` name and a seed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChannelRun {
    pub model: String,
    pub seed: u64,
}

/// Encodes framed extents to strand pools and decodes pools or reads back. Implementations must never return bytes
/// that do not have the expected SHA-256 and length.
pub trait Codec: Send + Sync {
    /// "vnxdna" for the real codec; anything else is a test stand-in.
    fn name(&self) -> &'static str;
    /// Encode `framed` (a [`frame`]) for `at`; returns a `vnx.dna-pool/1` file with a `strands` body.
    fn encode(&self, at: GenerationRef, framed: &[u8]) -> Result<Vec<u8>>;
    /// Decode a pool file (strands, or reads with `channel` = None) for `at`. With `channel`, the strands first pass
    /// the simulated channel. The output must have `expect_len` bytes with SHA-256 `expect_sha256` or the call fails
    /// CORRUPTION.
    fn decode(&self, at: GenerationRef, pool: &[u8], expect_sha256: &[u8; 32], expect_len: u64, channel: Option<&ChannelRun>) -> Result<Vec<u8>>;
}

// ------------------------------------------------------------------------------------------------ pool file format

pub const POOL_MAGIC: &[u8] = b"VNXPOOL/1\n";
pub const POOL_FORMAT: &str = "vnx.dna-pool/1";
const MAX_HEADER: usize = 64 << 10;

/// A parsed `vnx.dna-pool/1` file (format owned by `vnxdna.device.dnasim`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PoolFile {
    pub header: BTreeMap<String, Value>,
    pub body: Vec<u8>,
}

impl PoolFile {
    /// Strict parse: magic, canonical header line with the format and the SIMULATED label, body SHA-256.
    pub fn parse(data: &[u8]) -> Result<PoolFile> {
        fn bad<T>(m: &str) -> Result<T> {
            err(ErrorCode::Corruption, format!("pool file: {m}"))
        }
        let Some(rest) = data.strip_prefix(POOL_MAGIC) else { return bad("not a vnx.dna-pool/1 file") };
        let Some(end) = rest.iter().take(MAX_HEADER + 1).position(|b| *b == b'\n') else { return bad("header missing or too long") };
        let text = std::str::from_utf8(&rest[..end]).or_else(|_| bad("header is not UTF-8"))?;
        let v = Value::parse(text).or_else(|e| bad(&format!("header: {}", e.message)))?;
        if v.canonical() != text {
            return bad("header is not canonical JSON");
        }
        let Value::Obj(header) = v else { return bad("header is not an object") };
        if header.get("format").and_then(Value::as_str) != Some(POOL_FORMAT) || header.get("evidence").and_then(Value::as_str) != Some("SIMULATED") {
            return bad("wrong format or missing SIMULATED label");
        }
        let body = rest[end + 1..].to_vec();
        if header.get("body_sha256").and_then(Value::as_str) != Some(hex(&sha256(&body)).as_str()) {
            return bad("body does not match its SHA-256");
        }
        Ok(PoolFile { header, body })
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = POOL_MAGIC.to_vec();
        out.extend_from_slice(Value::Obj(self.header.clone()).canonical().as_bytes());
        out.push(b'\n');
        out.extend_from_slice(&self.body);
        out
    }

    /// The same pool with another body ("strands" or "reads"); the header's body fields follow.
    pub fn with_body(&self, kind: &str, body: Vec<u8>) -> PoolFile {
        let mut header = self.header.clone();
        header.insert("body".into(), Value::Str(kind.into()));
        header.insert("body_sha256".into(), Value::Str(hex(&sha256(&body))));
        PoolFile { header, body }
    }

    pub fn body_kind(&self) -> Option<&str> {
        self.header.get("body").and_then(Value::as_str)
    }

    /// The generation the pool was encoded for.
    pub fn generation_ref(&self) -> Option<(i64, i64)> {
        Some((self.header.get("extent")?.as_int()?, self.header.get("generation")?.as_int()?))
    }

    /// Strand sequences of a FASTA `strands` body, in file order.
    pub fn strands(&self) -> Result<Vec<String>> {
        parse_fasta(&self.body)
    }
}

/// Sequences of a FASTA text (one or more lines per record); only A, C, G, T are accepted.
pub fn parse_fasta(body: &[u8]) -> Result<Vec<String>> {
    let text = std::str::from_utf8(body).or_else(|_| err(ErrorCode::Corruption, "FASTA is not UTF-8"))?;
    let mut out: Vec<String> = Vec::new();
    for line in text.lines() {
        if line.starts_with('>') {
            out.push(String::new());
        } else if let Some(s) = out.last_mut() {
            if !line.bytes().all(|b| matches!(b, b'A' | b'C' | b'G' | b'T')) {
                return err(ErrorCode::Corruption, "FASTA sequence outside ACGT");
            }
            s.push_str(line);
        } else if !line.is_empty() {
            return err(ErrorCode::Corruption, "FASTA sequence before a header");
        }
    }
    Ok(out)
}

// --------------------------------------------------------------------------------------------- generation frame

const FRAME_MAGIC: &[u8; 8] = b"VNXFRAM1";
pub const FRAME_OVERHEAD: usize = 32;

/// What a DNA adapter hands the codec: magic, extent, generation and length (u64 big-endian), then the bytes.
pub fn frame(at: GenerationRef, data: &[u8]) -> Vec<u8> {
    let mut f = Vec::with_capacity(FRAME_OVERHEAD + data.len());
    f.extend_from_slice(FRAME_MAGIC);
    f.extend_from_slice(&at.extent.0.to_be_bytes());
    f.extend_from_slice(&at.generation.get().to_be_bytes());
    f.extend_from_slice(&(data.len() as u64).to_be_bytes());
    f.extend_from_slice(data);
    f
}

/// The bytes of a frame, which must name `at` and hold exactly its stated length: else CORRUPTION.
pub fn unframe(at: GenerationRef, f: &[u8]) -> Result<Vec<u8>> {
    let u = |b: &[u8]| u64::from_be_bytes(b.try_into().expect("8 bytes"));
    if f.len() < FRAME_OVERHEAD || &f[..8] != FRAME_MAGIC {
        return err(ErrorCode::Corruption, format!("{at:?}: decoded bytes are not a frame"));
    }
    if u(&f[8..16]) != at.extent.0 || u(&f[16..24]) != at.generation.get() {
        return err(ErrorCode::Corruption, format!("{at:?}: decoded frame belongs to another generation"));
    }
    if u(&f[24..32]) != (f.len() - FRAME_OVERHEAD) as u64 {
        return err(ErrorCode::Corruption, format!("{at:?}: decoded frame length mismatch"));
    }
    Ok(f[FRAME_OVERHEAD..].to_vec())
}

// -------------------------------------------------------------------------------------------- stored envelopes

const ENVELOPE_MAGIC: &[u8; 8] = b"VNXDENV1";
const ENVELOPE_HEAD: usize = 8 + 1 + 32 + 8;

/// What a DNA adapter keeps in its electronic store for one generation.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Envelope {
    /// a simulated strand pool (`dna-sim`) or a lab catalogue entry (`dna-lab`): the frame's SHA-256 and length,
    /// then the payload
    Dna { frame_sha256: [u8; 32], frame_len: u64, payload: Vec<u8> },
    /// bytes kept only in the electronic metadata region (the volume's binding marker, whose extent number is beyond
    /// the codec protocol's range); `sha256` is that of `bytes`
    Metadata { sha256: [u8; 32], bytes: Vec<u8> },
}

impl Envelope {
    pub fn to_bytes(&self) -> Vec<u8> {
        let (kind, sha, len, payload) = match self {
            Envelope::Dna { frame_sha256, frame_len, payload } => (b'D', frame_sha256, *frame_len, payload),
            Envelope::Metadata { sha256, bytes } => (b'M', sha256, bytes.len() as u64, bytes),
        };
        let mut out = Vec::with_capacity(ENVELOPE_HEAD + payload.len());
        out.extend_from_slice(ENVELOPE_MAGIC);
        out.push(kind);
        out.extend_from_slice(sha);
        out.extend_from_slice(&len.to_be_bytes());
        out.extend_from_slice(payload);
        out
    }

    pub fn parse(at: GenerationRef, b: &[u8]) -> Result<Envelope> {
        if b.len() < ENVELOPE_HEAD || &b[..8] != ENVELOPE_MAGIC {
            return err(ErrorCode::Corruption, format!("{at:?}: stored object is not an envelope"));
        }
        let sha: [u8; 32] = b[9..41].try_into().expect("32 bytes");
        let len = u64::from_be_bytes(b[41..49].try_into().expect("8 bytes"));
        let payload = b[ENVELOPE_HEAD..].to_vec();
        match b[8] {
            b'D' if len <= MAX_EXTENT_BYTES + FRAME_OVERHEAD as u64 => Ok(Envelope::Dna { frame_sha256: sha, frame_len: len, payload }),
            b'M' if len == payload.len() as u64 && sha256(&payload) == sha => Ok(Envelope::Metadata { sha256: sha, bytes: payload }),
            _ => err(ErrorCode::Corruption, format!("{at:?}: stored envelope is damaged")),
        }
    }
}

/// Generations a DNA adapter keeps in the electronic metadata region instead of encoding them: those whose extent
/// number the codec protocol cannot carry (the volume's binding marker, extent u64::MAX).
pub fn metadata_only(at: GenerationRef) -> bool {
    i64::try_from(at.extent.0).is_err() || i64::try_from(at.generation.get()).is_err()
}

// ----------------------------------------------------------------------------------------- the production codec

/// The VNX-DNA codec in Python worker processes (`python -m vnxdna.device.dnasim --staging DIR`).
pub struct WorkerCodec {
    pool: WorkerPool,
}

/// Prefix of the worker's INVALID_ARGUMENT messages about the request's options (an unknown channel model, bad
/// options): only those are a verdict about the request. Any other INVALID_ARGUMENT (a staging file that cannot be
/// created, ...) is a failure of the codec service.
pub const OPTIONS_REFUSAL: &str = "options: ";

/// Verdicts about the data (CORRUPTION), refusals of the request's options and transport failures pass through;
/// anything else the worker reports is a failure of the codec service (retryable), never damage.
fn codec_verdict(e: VnxError) -> VnxError {
    match e.code {
        ErrorCode::Corruption | ErrorCode::Timeout | ErrorCode::BackendUnavailable => e,
        ErrorCode::InvalidArgument if e.message.starts_with(OPTIONS_REFUSAL) => e,
        _ => unavailable(format!("codec worker: {e}")),
    }
}

/// Check decoded bytes against what was recorded at `put`, whatever the codec claims: CORRUPTION if they differ.
pub fn verify_decoded(at: GenerationRef, framed: &[u8], frame_sha256: &[u8; 32], frame_len: u64) -> Result<()> {
    if framed.len() as u64 != frame_len || sha256(framed) != *frame_sha256 {
        return err(ErrorCode::Corruption, format!("{at:?}: decoded bytes do not match the recorded SHA-256"));
    }
    Ok(())
}

impl WorkerCodec {
    pub fn new(cfg: WorkerConfig) -> Result<WorkerCodec> {
        Ok(WorkerCodec { pool: WorkerPool::new(cfg, "dsim", "codec")? })
    }
}

fn protocol_ref(at: GenerationRef) -> Result<(i64, i64)> {
    let e = i64::try_from(at.extent.0).or_else(|_| err(ErrorCode::Overflow, "extent beyond the protocol range"))?;
    let g = i64::try_from(at.generation.get()).or_else(|_| err(ErrorCode::Overflow, "generation beyond the protocol range"))?;
    Ok((e, g))
}

impl Codec for WorkerCodec {
    fn name(&self) -> &'static str {
        "vnxdna"
    }

    fn encode(&self, at: GenerationRef, framed: &[u8]) -> Result<Vec<u8>> {
        let (extent, generation) = protocol_ref(at)?;
        let out = self.pool.file_job(
            "encode_extent",
            framed,
            |job, iname, isha, oname| {
                obj([
                    ("job", Value::Int(job as i64)),
                    ("extent", Value::Int(extent)),
                    ("generation", Value::Int(generation)),
                    ("input", Value::Str(iname.into())),
                    ("input_sha256", Value::Str(isha.into())),
                    ("output", Value::Str(oname.into())),
                    ("options", Value::Obj(BTreeMap::new())),
                ])
            },
            codec_verdict,
        )?;
        let p = PoolFile::parse(&out)?;
        if p.generation_ref() != Some((extent, generation)) || p.body_kind() != Some("strands") {
            return err(ErrorCode::Corruption, "the codec worker returned a pool for another generation");
        }
        Ok(out)
    }

    fn decode(&self, at: GenerationRef, pool: &[u8], expect_sha256: &[u8; 32], expect_len: u64, channel: Option<&ChannelRun>) -> Result<Vec<u8>> {
        let (extent, generation) = protocol_ref(at)?;
        let expect_bytes = i64::try_from(expect_len).ok().filter(|n| *n >= 1).ok_or_else(|| VnxError::new(ErrorCode::InvalidArgument, "expected length out of range"))?;
        let options = match channel {
            None => Value::Obj(BTreeMap::new()),
            Some(c) => {
                let seed = i64::try_from(c.seed).or_else(|_| err(ErrorCode::InvalidArgument, "channel seed beyond 63 bits"))?;
                obj([("channel", obj([("model", Value::Str(c.model.clone())), ("seed", Value::Int(seed))]))])
            }
        };
        let out = self.pool.file_job(
            "decode_extent",
            pool,
            |job, iname, _isha, oname| {
                obj([
                    ("job", Value::Int(job as i64)),
                    ("extent", Value::Int(extent)),
                    ("generation", Value::Int(generation)),
                    ("input", Value::Str(iname.into())),
                    ("output", Value::Str(oname.into())),
                    ("expect_sha256", Value::Str(hex(expect_sha256))),
                    ("expect_bytes", Value::Int(expect_bytes)),
                    ("options", options),
                ])
            },
            codec_verdict,
        )?;
        // never trust the worker's own check alone
        if out.len() as u64 != expect_len || sha256(&out) != *expect_sha256 {
            return err(ErrorCode::Corruption, format!("{at:?}: decoded bytes do not match the recorded SHA-256"));
        }
        Ok(out)
    }
}

// ------------------------------------------------------------------------------------ TEST-ONLY stand-in codec

/// Bases per strand of [`ToyCodec`] (excluding its 8-base index).
const TOY_PAYLOAD_NT: usize = 64;

/// TEST-ONLY stand-in for the VNX-DNA codec: 2 bits per base, an 8-base strand index, per-base majority vote over the
/// reads of each index, no ECC. It exists so that the adapters' own logic (framing, envelopes, verification, job handling) can be
/// tested without Python. It is never shipped as a codec and nothing measured with it is a codec result.
pub struct ToyCodec;

const BASES: [u8; 4] = *b"ACGT";

fn toy_strands(framed: &[u8]) -> Vec<String> {
    let mut nt: Vec<u8> = framed.iter().flat_map(|b| (0..4).rev().map(move |i| BASES[((b >> (2 * i)) & 3) as usize])).collect();
    nt.resize(nt.len().div_ceil(TOY_PAYLOAD_NT) * TOY_PAYLOAD_NT, b'A'); // every strand has the same length
    nt.chunks(TOY_PAYLOAD_NT)
        .enumerate()
        .map(|(i, c)| {
            let idx: Vec<u8> = (0..8).rev().map(|k| BASES[((i >> (2 * k)) & 3) as usize]).collect();
            String::from_utf8([&idx[..], c].concat()).expect("ACGT")
        })
        .collect()
}

/// Sequences of FASTA or FASTQ reads (FASTQ: 4 lines per record).
pub fn parse_reads(body: &[u8]) -> Result<Vec<String>> {
    if body.first() == Some(&b'>') {
        return parse_fasta(body);
    }
    let text = std::str::from_utf8(body).or_else(|_| err(ErrorCode::Corruption, "reads are not UTF-8"))?;
    let lines: Vec<&str> = text.lines().collect();
    if lines.len() % 4 != 0 {
        return err(ErrorCode::Corruption, "FASTQ record count is not a multiple of 4 lines");
    }
    lines
        .chunks(4)
        .map(|r| {
            if !r[0].starts_with('@') || !r[2].starts_with('+') || r[1].len() != r[3].len() {
                return err(ErrorCode::Corruption, "malformed FASTQ record");
            }
            Ok(r[1].to_string())
        })
        .collect()
}

/// A SIMULATED read channel used by the test codec and the simulated lab: strand dropout, `coverage` reads per
/// surviving strand, i.i.d. substitutions; FASTQ with Phred 30 on unchanged and Phred 10 on substituted bases.
/// Rates are in parts per million. Not fitted to any instrument (the realistic channel is the Python engine's).
pub fn simple_channel(strands: &[String], coverage: u32, substitution_ppm: u32, dropout_ppm: u32, seed: u64) -> Vec<u8> {
    let mut rng = SplitMix64::new(seed);
    let mut out = Vec::new();
    let mut n = 0u64;
    for s in strands {
        if rng.below_ppm(dropout_ppm) {
            continue;
        }
        for _ in 0..coverage {
            let mut seq = Vec::with_capacity(s.len());
            let mut qual = Vec::with_capacity(s.len());
            for &b in s.as_bytes() {
                if rng.below_ppm(substitution_ppm) {
                    let others: Vec<u8> = BASES.iter().copied().filter(|x| *x != b).collect();
                    seq.push(others[(rng.next_u64() % 3) as usize]);
                    qual.push(b'+'); // Phred 10
                } else {
                    seq.push(b);
                    qual.push(b'?'); // Phred 30
                }
            }
            out.extend_from_slice(format!("@sim{n}\n").as_bytes());
            out.extend_from_slice(&seq);
            out.extend_from_slice(b"\n+\n");
            out.extend_from_slice(&qual);
            out.push(b'\n');
            n += 1;
        }
    }
    out
}

impl Codec for ToyCodec {
    fn name(&self) -> &'static str {
        "toy-test-codec"
    }

    fn encode(&self, at: GenerationRef, framed: &[u8]) -> Result<Vec<u8>> {
        let (extent, generation) = protocol_ref(at)?;
        let mut body = Vec::new();
        for (i, s) in toy_strands(framed).iter().enumerate() {
            body.extend_from_slice(format!(">toy{i}\n{s}\n").as_bytes());
        }
        let mut header = BTreeMap::new();
        header.insert("format".into(), Value::Str(POOL_FORMAT.into()));
        header.insert("evidence".into(), Value::Str("SIMULATED".into()));
        header.insert("extent".into(), Value::Int(extent));
        header.insert("generation".into(), Value::Int(generation));
        header.insert("record".into(), obj([("length", Value::Int(framed.len() as i64)), ("sha256", Value::Str(hex(&sha256(framed))))]));
        Ok(PoolFile { header, body: vec![] }.with_body("strands", body).to_bytes())
    }

    fn decode(&self, at: GenerationRef, pool: &[u8], expect_sha256: &[u8; 32], expect_len: u64, channel: Option<&ChannelRun>) -> Result<Vec<u8>> {
        let p = PoolFile::parse(pool)?;
        if p.generation_ref() != Some(protocol_ref(at)?) {
            return err(ErrorCode::Corruption, format!("{at:?}: pool belongs to another generation"));
        }
        let reads = match channel {
            None => parse_reads(&p.body)?,
            Some(c) => {
                if p.body_kind() != Some("strands") {
                    return err(ErrorCode::InvalidArgument, "a channel can only be applied to strands");
                }
                // toy channel models: "clean" or "toy-sub-<ppm>"/"toy-drop-<ppm>" (5 reads per strand)
                let (sub, drop) = match c.model.as_str() {
                    "clean" => (0, 0),
                    m => match (m.strip_prefix("toy-sub-"), m.strip_prefix("toy-drop-")) {
                        (Some(v), _) => (v.parse().or_else(|_| err(ErrorCode::InvalidArgument, "toy channel"))?, 0),
                        (_, Some(v)) => (0, v.parse().or_else(|_| err(ErrorCode::InvalidArgument, "toy channel"))?),
                        _ => return err(ErrorCode::InvalidArgument, format!("unknown channel model {m}")),
                    },
                };
                parse_reads(&simple_channel(&p.strands()?, 5, sub, drop, c.seed))?
            }
        };
        if expect_len > 1 << 24 {
            return err(ErrorCode::Corruption, format!("{at:?}: expected length beyond the test codec's range"));
        }
        let strands = (expect_len as usize * 4).div_ceil(TOY_PAYLOAD_NT);
        // per-strand, per-position base counts (majority vote per base)
        let mut votes: Vec<Vec<[u32; 4]>> = vec![vec![[0; 4]; TOY_PAYLOAD_NT]; strands];
        for r in reads {
            if r.len() != 8 + TOY_PAYLOAD_NT {
                continue;
            }
            let idx = r.as_bytes()[..8].iter().try_fold(0usize, |a, b| BASES.iter().position(|x| x == b).map(|v| a * 4 + v));
            if let Some(i) = idx.filter(|i| *i < strands) {
                for (k, b) in r.as_bytes()[8..].iter().enumerate() {
                    if let Some(v) = BASES.iter().position(|x| x == b) {
                        votes[i][k][v] += 1;
                    }
                }
            }
        }
        let mut nt = Vec::with_capacity(strands * TOY_PAYLOAD_NT);
        for (i, v) in votes.iter().enumerate() {
            for counts in v {
                let (best, n) = counts.iter().enumerate().max_by_key(|(b, n)| (**n, std::cmp::Reverse(*b))).expect("4 bases");
                if *n == 0 {
                    return err(ErrorCode::Corruption, format!("{at:?}: strand {i} missing from every read"));
                }
                nt.push(BASES[best]);
            }
        }
        nt.truncate(expect_len as usize * 4);
        let out: Vec<u8> = nt
            .chunks(4)
            .map(|c| c.iter().fold(0u8, |a, b| (a << 2) | BASES.iter().position(|x| x == b).unwrap_or(0) as u8))
            .collect();
        if out.len() as u64 != expect_len || sha256(&out) != *expect_sha256 {
            return err(ErrorCode::Corruption, format!("{at:?}: decoded bytes do not match the recorded SHA-256"));
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::addr::{ExtentId, Generation};

    fn at(e: u64, g: u64) -> GenerationRef {
        GenerationRef { extent: ExtentId(e), generation: Generation::new(g).unwrap() }
    }

    #[test]
    fn frame_round_trip_and_refusals() {
        let f = frame(at(3, 2), b"abc");
        assert_eq!(unframe(at(3, 2), &f).unwrap(), b"abc");
        assert_eq!(unframe(at(3, 3), &f).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(unframe(at(3, 2), &f[..f.len() - 1]).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(unframe(at(3, 2), &frame(at(3, 2), b"")).unwrap(), b"");
    }

    #[test]
    fn envelopes_round_trip_and_detect_damage() {
        let a = at(1, 1);
        let m = Envelope::Metadata { sha256: sha256(b"marker"), bytes: b"marker".to_vec() };
        assert_eq!(Envelope::parse(a, &m.to_bytes()).unwrap(), m);
        let mut bad = m.to_bytes();
        *bad.last_mut().unwrap() ^= 1;
        assert_eq!(Envelope::parse(a, &bad).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(Envelope::parse(a, b"short").unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn toy_codec_round_trip_channel_and_refusal() {
        let a = at(2, 5);
        let data = frame(a, &(0..300u32).map(|i| (i * 7) as u8).collect::<Vec<u8>>());
        let pool = ToyCodec.encode(a, &data).unwrap();
        let p = PoolFile::parse(&pool).unwrap();
        assert_eq!(p.header["evidence"], Value::Str("SIMULATED".into()));
        let sha = sha256(&data);
        assert_eq!(ToyCodec.decode(a, &pool, &sha, data.len() as u64, None).unwrap(), data);
        let noisy = ChannelRun { model: "toy-sub-10000".into(), seed: 9 };
        assert_eq!(ToyCodec.decode(a, &pool, &sha, data.len() as u64, Some(&noisy)).unwrap(), data);
        let lossy = ChannelRun { model: "toy-drop-900000".into(), seed: 9 };
        assert_eq!(ToyCodec.decode(a, &pool, &sha, data.len() as u64, Some(&lossy)).unwrap_err().code, ErrorCode::Corruption);
        assert_eq!(ToyCodec.decode(at(2, 6), &pool, &sha, data.len() as u64, None).unwrap_err().code, ErrorCode::Corruption);
        let mut tampered = pool.clone();
        *tampered.last_mut().unwrap() = b'A';
        assert_eq!(ToyCodec.decode(a, &tampered, &sha, data.len() as u64, None).unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn only_option_refusals_and_data_verdicts_pass_through_as_permanent() {
        let v = |c, m: &str| codec_verdict(VnxError::new(c, m)).code;
        assert_eq!(v(ErrorCode::InvalidArgument, "options: unknown channel model"), ErrorCode::InvalidArgument);
        assert_eq!(v(ErrorCode::InvalidArgument, "staging output o1: File exists"), ErrorCode::BackendUnavailable);
        assert_eq!(v(ErrorCode::NotFound, "staging input"), ErrorCode::BackendUnavailable);
        assert_eq!(v(ErrorCode::Internal, "x"), ErrorCode::BackendUnavailable);
        assert_eq!(v(ErrorCode::Corruption, "x"), ErrorCode::Corruption);
        let a = at(1, 1);
        let f = frame(a, b"x");
        assert_eq!(ToyCodec.decode(a, &ToyCodec.encode(a, &f).unwrap(), &sha256(&f), 1 << 40, None).unwrap_err().code, ErrorCode::Corruption);
    }

    #[test]
    fn simple_channel_is_seeded() {
        let s = vec!["ACGTACGTAAAACCCCGGGGTTTT".to_string(); 20];
        assert_eq!(simple_channel(&s, 2, 50_000, 100_000, 1), simple_channel(&s, 2, 50_000, 100_000, 1));
        assert_ne!(simple_channel(&s, 2, 50_000, 100_000, 1), simple_channel(&s, 2, 50_000, 100_000, 2));
        assert_eq!(parse_reads(&simple_channel(&s, 3, 0, 0, 1)).unwrap().len(), 60);
    }
}
