//! The NBD wire protocol (docs/V14_DEVICE_INTERFACE.md §3): fixed newstyle negotiation on both sides and the
//! transmission-phase message formats. Safe code only; the kernel attach (`ioctl`) lives in [`crate::nbdsys`] and
//! the request processing in [`crate::nbdserve`].
//!
//! Reference: the NBD protocol specification (`doc/proto.md` of the nbd project), the subset this service speaks:
//!
//! * handshake: fixed newstyle only (`NBDMAGIC`, `IHAVEOPT`, flags FIXED_NEWSTYLE | NO_ZEROES); the old style is
//!   never offered. Options: `EXPORT_NAME`, `GO`, `INFO`, `LIST`, `ABORT`; every other option (STARTTLS,
//!   STRUCTURED_REPLY, META_CONTEXT, ...) is answered `NBD_REP_ERR_UNSUP` and negotiation continues;
//! * transmission: simple replies only; commands `READ`, `WRITE` (flag `FUA`), `FLUSH`, `TRIM`, `DISC`. Every other
//!   command is answered `EINVAL`.
//!
//! Every length read from the peer is bounded before anything is allocated ([`MAX_OPTION_BYTES`]), the number of
//! options per negotiation is bounded ([`MAX_OPTIONS`]), and every malformed message is a PROTOCOL error that ends
//! the connection; nothing in this module panics on peer input.

use crate::error::{err, ErrorCode, Result, VnxError};
use std::io::{Read, Write};

pub const NBDMAGIC: u64 = 0x4e42_444d_4147_4943; // "NBDMAGIC"
pub const IHAVEOPT: u64 = 0x4948_4156_454f_5054; // "IHAVEOPT"
pub const OPT_REPLY_MAGIC: u64 = 0x0003_e889_0455_65a9;
pub const REQUEST_MAGIC: u32 = 0x2560_9513;
pub const SIMPLE_REPLY_MAGIC: u32 = 0x6744_6698;

// handshake flags (server) and client flags
pub const FLAG_FIXED_NEWSTYLE: u16 = 1 << 0;
pub const FLAG_NO_ZEROES: u16 = 1 << 1;
pub const CLIENT_FIXED_NEWSTYLE: u32 = 1 << 0;
pub const CLIENT_NO_ZEROES: u32 = 1 << 1;

// options
pub const OPT_EXPORT_NAME: u32 = 1;
pub const OPT_ABORT: u32 = 2;
pub const OPT_LIST: u32 = 3;
pub const OPT_STARTTLS: u32 = 5;
pub const OPT_INFO: u32 = 6;
pub const OPT_GO: u32 = 7;
pub const OPT_STRUCTURED_REPLY: u32 = 8;

// option replies
pub const REP_ACK: u32 = 1;
pub const REP_SERVER: u32 = 2;
pub const REP_INFO: u32 = 3;
pub const REP_ERR_UNSUP: u32 = (1 << 31) + 1;
pub const REP_ERR_POLICY: u32 = (1 << 31) + 2;
pub const REP_ERR_INVALID: u32 = (1 << 31) + 3;
pub const REP_ERR_UNKNOWN: u32 = (1 << 31) + 6;
pub const REP_ERR_SHUTDOWN: u32 = (1 << 31) + 7;

// NBD_INFO_* items of REP_INFO
pub const INFO_EXPORT: u16 = 0;
pub const INFO_NAME: u16 = 1;
pub const INFO_DESCRIPTION: u16 = 2;
pub const INFO_BLOCK_SIZE: u16 = 3;

// transmission flags
pub const TF_HAS_FLAGS: u16 = 1 << 0;
pub const TF_READ_ONLY: u16 = 1 << 1;
pub const TF_SEND_FLUSH: u16 = 1 << 2;
pub const TF_SEND_FUA: u16 = 1 << 3;
pub const TF_ROTATIONAL: u16 = 1 << 4;
pub const TF_SEND_TRIM: u16 = 1 << 5;

// commands and command flags
pub const CMD_READ: u16 = 0;
pub const CMD_WRITE: u16 = 1;
pub const CMD_DISC: u16 = 2;
pub const CMD_FLUSH: u16 = 3;
pub const CMD_TRIM: u16 = 4;
pub const CMD_FLAG_FUA: u16 = 1 << 0;

/// Largest option payload accepted during negotiation (names are at most 4096 bytes by the specification).
pub const MAX_OPTION_BYTES: u32 = 8 * 1024;
/// Options per negotiation before the server gives up on the client.
pub const MAX_OPTIONS: usize = 64;
pub const REQUEST_BYTES: usize = 28;
pub const REPLY_BYTES: usize = 16;

fn proto_err<T>(msg: impl Into<String>) -> Result<T> {
    err(ErrorCode::Protocol, msg)
}

/// An I/O error on the connection: the peer went away or the socket failed.
pub fn io_err(what: &str, e: std::io::Error) -> VnxError {
    let code = if e.kind() == std::io::ErrorKind::UnexpectedEof { ErrorCode::Protocol } else { ErrorCode::BackendUnavailable };
    VnxError::new(code, format!("nbd {what}: {e}"))
}

fn read_exact(r: &mut impl Read, buf: &mut [u8], what: &str) -> Result<()> {
    r.read_exact(buf).map_err(|e| io_err(what, e))
}

fn rd_u16(r: &mut impl Read, what: &str) -> Result<u16> {
    let mut b = [0u8; 2];
    read_exact(r, &mut b, what)?;
    Ok(u16::from_be_bytes(b))
}

fn rd_u32(r: &mut impl Read, what: &str) -> Result<u32> {
    let mut b = [0u8; 4];
    read_exact(r, &mut b, what)?;
    Ok(u32::from_be_bytes(b))
}

fn rd_u64(r: &mut impl Read, what: &str) -> Result<u64> {
    let mut b = [0u8; 8];
    read_exact(r, &mut b, what)?;
    Ok(u64::from_be_bytes(b))
}

fn send(w: &mut impl Write, bytes: &[u8], what: &str) -> Result<()> {
    w.write_all(bytes).and_then(|_| w.flush()).map_err(|e| io_err(what, e))
}

/// What the server tells a client about the export.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExportInfo {
    pub name: String,
    pub size: u64,
    /// transmission flags (TF_*), TF_HAS_FLAGS included
    pub flags: u16,
    /// the device block size: minimum and preferred block size of NBD_INFO_BLOCK_SIZE
    pub block_size: u32,
    /// the largest request payload the server accepts
    pub max_payload: u32,
}

/// How the negotiation ended on the server side.
#[derive(Debug, PartialEq, Eq)]
pub enum Negotiated {
    /// enter the transmission phase
    Transmission,
    /// the client aborted or left during negotiation (not an error)
    Closed,
}

fn opt_reply(w: &mut impl Write, opt: u32, rtype: u32, data: &[u8]) -> Result<()> {
    let len = u32::try_from(data.len()).or_else(|_| proto_err("option reply too large"))?;
    let mut m = Vec::with_capacity(20 + data.len());
    m.extend_from_slice(&OPT_REPLY_MAGIC.to_be_bytes());
    m.extend_from_slice(&opt.to_be_bytes());
    m.extend_from_slice(&rtype.to_be_bytes());
    m.extend_from_slice(&len.to_be_bytes());
    m.extend_from_slice(data);
    send(w, &m, "option reply")
}

fn info_export(e: &ExportInfo) -> Vec<u8> {
    let mut d = INFO_EXPORT.to_be_bytes().to_vec();
    d.extend_from_slice(&e.size.to_be_bytes());
    d.extend_from_slice(&e.flags.to_be_bytes());
    d
}

fn info_block_size(e: &ExportInfo) -> Vec<u8> {
    let mut d = INFO_BLOCK_SIZE.to_be_bytes().to_vec();
    d.extend_from_slice(&e.block_size.to_be_bytes()); // minimum
    d.extend_from_slice(&e.block_size.to_be_bytes()); // preferred
    d.extend_from_slice(&e.max_payload.to_be_bytes()); // maximum
    d
}

/// Parse the data of NBD_OPT_INFO / NBD_OPT_GO: name and the requested info items.
fn parse_info_request(data: &[u8]) -> Option<(String, Vec<u16>)> {
    let n = u32::from_be_bytes(data.get(0..4)?.try_into().ok()?) as usize;
    let name = data.get(4..4usize.checked_add(n)?)?;
    let rest = &data[4 + n..];
    let count = u16::from_be_bytes(rest.get(0..2)?.try_into().ok()?) as usize;
    let items = rest.get(2..)?;
    if items.len() != count * 2 {
        return None;
    }
    let reqs = items.chunks_exact(2).map(|c| u16::from_be_bytes([c[0], c[1]])).collect();
    Some((String::from_utf8(name.to_vec()).ok()?, reqs))
}

/// Does a client asking for `name` mean this export? The empty name is the default export.
fn names_match(asked: &str, export: &str) -> bool {
    asked.is_empty() || asked == export
}

/// Server side of the fixed newstyle negotiation. `export.flags` are the transmission flags the server will honour.
pub fn server_negotiate(s: &mut (impl Read + Write), export: &ExportInfo) -> Result<Negotiated> {
    let mut hello = Vec::with_capacity(18);
    hello.extend_from_slice(&NBDMAGIC.to_be_bytes());
    hello.extend_from_slice(&IHAVEOPT.to_be_bytes());
    hello.extend_from_slice(&(FLAG_FIXED_NEWSTYLE | FLAG_NO_ZEROES).to_be_bytes());
    send(s, &hello, "handshake")?;
    let cflags = rd_u32(s, "client flags")?;
    if cflags & CLIENT_FIXED_NEWSTYLE == 0 {
        return proto_err("client does not speak fixed newstyle negotiation");
    }
    if cflags & !(CLIENT_FIXED_NEWSTYLE | CLIENT_NO_ZEROES) != 0 {
        return proto_err(format!("unknown client flags {cflags:#x}"));
    }
    let no_zeroes = cflags & CLIENT_NO_ZEROES != 0;
    for _ in 0..MAX_OPTIONS {
        let magic = match rd_u64(s, "option magic") {
            Ok(m) => m,
            Err(e) if e.code == ErrorCode::Protocol => return Ok(Negotiated::Closed), // client left
            Err(e) => return Err(e),
        };
        if magic != IHAVEOPT {
            return proto_err(format!("bad option magic {magic:#x}"));
        }
        let opt = rd_u32(s, "option")?;
        let len = rd_u32(s, "option length")?;
        if len > MAX_OPTION_BYTES {
            return proto_err(format!("option {opt} carries {len} bytes (limit {MAX_OPTION_BYTES})"));
        }
        let mut data = vec![0u8; len as usize];
        read_exact(s, &mut data, "option data")?;
        match opt {
            OPT_EXPORT_NAME => {
                let name = String::from_utf8_lossy(&data);
                if !names_match(&name, &export.name) {
                    // the specification has no error reply for EXPORT_NAME: the server closes the connection
                    return proto_err(format!("unknown export {name:?}"));
                }
                let mut m = export.size.to_be_bytes().to_vec();
                m.extend_from_slice(&export.flags.to_be_bytes());
                if !no_zeroes {
                    m.extend_from_slice(&[0u8; 124]);
                }
                send(s, &m, "export reply")?;
                return Ok(Negotiated::Transmission);
            }
            OPT_ABORT => {
                let _ = opt_reply(s, opt, REP_ACK, &[]); // the client may already have closed
                return Ok(Negotiated::Closed);
            }
            OPT_LIST => {
                if !data.is_empty() {
                    opt_reply(s, opt, REP_ERR_INVALID, b"LIST takes no data")?;
                    continue;
                }
                let mut d = (export.name.len() as u32).to_be_bytes().to_vec();
                d.extend_from_slice(export.name.as_bytes());
                opt_reply(s, opt, REP_SERVER, &d)?;
                opt_reply(s, opt, REP_ACK, &[])?;
            }
            OPT_INFO | OPT_GO => {
                let Some((name, reqs)) = parse_info_request(&data) else {
                    opt_reply(s, opt, REP_ERR_INVALID, b"malformed INFO/GO request")?;
                    continue;
                };
                if !names_match(&name, &export.name) {
                    opt_reply(s, opt, REP_ERR_UNKNOWN, b"no such export")?;
                    continue;
                }
                opt_reply(s, opt, REP_INFO, &info_export(export))?;
                // block size information is always sent: the server needs aligned requests
                opt_reply(s, opt, REP_INFO, &info_block_size(export))?;
                if reqs.contains(&INFO_NAME) {
                    let mut d = INFO_NAME.to_be_bytes().to_vec();
                    d.extend_from_slice(export.name.as_bytes());
                    opt_reply(s, opt, REP_INFO, &d)?;
                }
                opt_reply(s, opt, REP_ACK, &[])?;
                if opt == OPT_GO {
                    return Ok(Negotiated::Transmission);
                }
            }
            _ => opt_reply(s, opt, REP_ERR_UNSUP, b"option not supported")?,
        }
    }
    let _ = opt_reply(s, 0, REP_ERR_POLICY, b"too many options");
    proto_err(format!("more than {MAX_OPTIONS} options during negotiation"))
}

/// Client side of the negotiation (used by the kernel attach, which hands the negotiated socket to the driver):
/// fixed newstyle, NO_ZEROES, `NBD_OPT_GO` with a block-size request. Refuses a server that does not send the
/// export size and flags or announces a block size the kernel cannot use.
pub fn client_negotiate(s: &mut (impl Read + Write), name: &str) -> Result<ExportInfo> {
    let magic = rd_u64(s, "server magic")?;
    let opt_magic = rd_u64(s, "server option magic")?;
    if magic != NBDMAGIC || opt_magic != IHAVEOPT {
        return proto_err("server does not speak newstyle negotiation");
    }
    let hflags = rd_u16(s, "handshake flags")?;
    if hflags & FLAG_FIXED_NEWSTYLE == 0 {
        return proto_err("server does not speak fixed newstyle negotiation");
    }
    let mut cflags = CLIENT_FIXED_NEWSTYLE;
    if hflags & FLAG_NO_ZEROES != 0 {
        cflags |= CLIENT_NO_ZEROES;
    }
    send(s, &cflags.to_be_bytes(), "client flags")?;
    let mut data = (name.len() as u32).to_be_bytes().to_vec();
    data.extend_from_slice(name.as_bytes());
    data.extend_from_slice(&1u16.to_be_bytes());
    data.extend_from_slice(&INFO_BLOCK_SIZE.to_be_bytes());
    let mut m = IHAVEOPT.to_be_bytes().to_vec();
    m.extend_from_slice(&OPT_GO.to_be_bytes());
    m.extend_from_slice(&(data.len() as u32).to_be_bytes());
    m.extend_from_slice(&data);
    send(s, &m, "GO")?;
    let (mut size, mut flags, mut bs, mut max) = (None, None, None, None);
    for _ in 0..MAX_OPTIONS {
        if rd_u64(s, "reply magic")? != OPT_REPLY_MAGIC {
            return proto_err("bad option reply magic");
        }
        let opt = rd_u32(s, "reply option")?;
        let rtype = rd_u32(s, "reply type")?;
        let len = rd_u32(s, "reply length")?;
        if opt != OPT_GO || len > MAX_OPTION_BYTES {
            return proto_err(format!("unexpected reply to GO (option {opt}, {len} bytes)"));
        }
        let mut d = vec![0u8; len as usize];
        read_exact(s, &mut d, "reply data")?;
        match rtype {
            REP_ACK => {
                let (Some(size), Some(flags)) = (size, flags) else {
                    return proto_err("server acknowledged GO without NBD_INFO_EXPORT");
                };
                return Ok(ExportInfo {
                    name: name.to_string(),
                    size,
                    flags,
                    block_size: bs.unwrap_or(512),
                    max_payload: max.unwrap_or(32 << 20),
                });
            }
            REP_INFO if d.len() >= 2 => {
                let kind = u16::from_be_bytes([d[0], d[1]]);
                match (kind, d.len()) {
                    (INFO_EXPORT, 12) => {
                        size = Some(u64::from_be_bytes(d[2..10].try_into().expect("8 bytes")));
                        flags = Some(u16::from_be_bytes([d[10], d[11]]));
                    }
                    (INFO_BLOCK_SIZE, 14) => {
                        bs = Some(u32::from_be_bytes(d[6..10].try_into().expect("4 bytes"))); // preferred
                        max = Some(u32::from_be_bytes(d[10..14].try_into().expect("4 bytes")));
                    }
                    (INFO_EXPORT | INFO_BLOCK_SIZE, _) => return proto_err("malformed NBD_REP_INFO"),
                    _ => {} // other information items are optional
                }
            }
            t if t & (1 << 31) != 0 => {
                return err(ErrorCode::NotFound, format!("server refused GO: reply {t:#x} {}", String::from_utf8_lossy(&d)))
            }
            t => return proto_err(format!("unexpected reply type {t:#x} to GO")),
        }
    }
    proto_err("too many replies to GO")
}

/// One transmission-phase request header.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Request {
    pub flags: u16,
    pub kind: u16,
    pub handle: u64,
    pub offset: u64,
    pub len: u32,
}

impl Request {
    pub fn parse(b: &[u8; REQUEST_BYTES]) -> Result<Request> {
        let magic = u32::from_be_bytes(b[0..4].try_into().expect("4 bytes"));
        if magic != REQUEST_MAGIC {
            return proto_err(format!("bad request magic {magic:#x}"));
        }
        Ok(Request {
            flags: u16::from_be_bytes([b[4], b[5]]),
            kind: u16::from_be_bytes([b[6], b[7]]),
            handle: u64::from_be_bytes(b[8..16].try_into().expect("8 bytes")),
            offset: u64::from_be_bytes(b[16..24].try_into().expect("8 bytes")),
            len: u32::from_be_bytes(b[24..28].try_into().expect("4 bytes")),
        })
    }

    pub fn encode(&self) -> [u8; REQUEST_BYTES] {
        let mut b = [0u8; REQUEST_BYTES];
        b[0..4].copy_from_slice(&REQUEST_MAGIC.to_be_bytes());
        b[4..6].copy_from_slice(&self.flags.to_be_bytes());
        b[6..8].copy_from_slice(&self.kind.to_be_bytes());
        b[8..16].copy_from_slice(&self.handle.to_be_bytes());
        b[16..24].copy_from_slice(&self.offset.to_be_bytes());
        b[24..28].copy_from_slice(&self.len.to_be_bytes());
        b
    }
}

/// A simple reply header (followed by the data of a successful READ).
pub fn reply_header(error: u32, handle: u64) -> [u8; REPLY_BYTES] {
    let mut b = [0u8; REPLY_BYTES];
    b[0..4].copy_from_slice(&SIMPLE_REPLY_MAGIC.to_be_bytes());
    b[4..8].copy_from_slice(&error.to_be_bytes());
    b[8..16].copy_from_slice(&handle.to_be_bytes());
    b
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixStream;

    fn export() -> ExportInfo {
        ExportInfo {
            name: "vnx".into(),
            size: 1 << 20,
            flags: TF_HAS_FLAGS | TF_SEND_FLUSH | TF_SEND_FUA | TF_SEND_TRIM,
            block_size: 4096,
            max_payload: 32 << 20,
        }
    }

    #[test]
    fn go_negotiation_round_trip() {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        let got = client_negotiate(&mut b, "").unwrap();
        assert_eq!(srv.join().unwrap().unwrap(), Negotiated::Transmission);
        assert_eq!(got.size, 1 << 20);
        assert_eq!(got.flags, export().flags);
        assert_eq!(got.block_size, 4096);
        assert_eq!(got.max_payload, 32 << 20);
    }

    #[test]
    fn unknown_export_is_refused_and_negotiation_continues() {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        let e = client_negotiate(&mut b, "other").unwrap_err();
        assert_eq!(e.code, ErrorCode::NotFound);
        drop(b); // the client leaves: the server sees a closed negotiation, not a crash
        assert_eq!(srv.join().unwrap().unwrap(), Negotiated::Closed);
    }

    fn raw_option(s: &mut UnixStream, opt: u32, data: &[u8]) {
        let mut m = IHAVEOPT.to_be_bytes().to_vec();
        m.extend_from_slice(&opt.to_be_bytes());
        m.extend_from_slice(&(data.len() as u32).to_be_bytes());
        m.extend_from_slice(data);
        s.write_all(&m).unwrap();
    }

    fn read_reply(s: &mut UnixStream) -> (u32, u32, Vec<u8>) {
        assert_eq!(rd_u64(s, "t").unwrap(), OPT_REPLY_MAGIC);
        let opt = rd_u32(s, "t").unwrap();
        let t = rd_u32(s, "t").unwrap();
        let n = rd_u32(s, "t").unwrap();
        let mut d = vec![0; n as usize];
        s.read_exact(&mut d).unwrap();
        (opt, t, d)
    }

    fn start(s: &mut UnixStream, flags: u32) {
        let mut h = [0u8; 18];
        s.read_exact(&mut h).unwrap();
        assert_eq!(u64::from_be_bytes(h[0..8].try_into().unwrap()), NBDMAGIC);
        assert_eq!(u16::from_be_bytes([h[16], h[17]]), FLAG_FIXED_NEWSTYLE | FLAG_NO_ZEROES);
        s.write_all(&flags.to_be_bytes()).unwrap();
    }

    #[test]
    fn list_unsupported_and_export_name() {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        start(&mut b, CLIENT_FIXED_NEWSTYLE); // zeroes wanted
        raw_option(&mut b, OPT_LIST, &[]);
        let (_, t, d) = read_reply(&mut b);
        assert_eq!(t, REP_SERVER);
        assert_eq!(&d[4..], b"vnx");
        assert_eq!(read_reply(&mut b).1, REP_ACK);
        for o in [OPT_STARTTLS, OPT_STRUCTURED_REPLY, 9, 10, 4242] {
            let (opt, t, _) = read_reply_after(&mut b, o);
            assert_eq!((opt, t), (o, REP_ERR_UNSUP));
        }
        raw_option(&mut b, OPT_INFO, &[0, 0, 0, 1]); // malformed
        assert_eq!(read_reply(&mut b).1, REP_ERR_INVALID);
        raw_option(&mut b, OPT_EXPORT_NAME, b"vnx");
        let mut m = [0u8; 8 + 2 + 124];
        b.read_exact(&mut m).unwrap();
        assert_eq!(u64::from_be_bytes(m[0..8].try_into().unwrap()), 1 << 20);
        assert!(m[10..].iter().all(|&z| z == 0));
        assert_eq!(srv.join().unwrap().unwrap(), Negotiated::Transmission);
    }

    fn read_reply_after(s: &mut UnixStream, opt: u32) -> (u32, u32, Vec<u8>) {
        raw_option(s, opt, b"");
        read_reply(s)
    }

    #[test]
    fn hostile_negotiation_is_a_protocol_error() {
        // oversized option
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        start(&mut b, CLIENT_FIXED_NEWSTYLE | CLIENT_NO_ZEROES);
        let mut m = IHAVEOPT.to_be_bytes().to_vec();
        m.extend_from_slice(&OPT_GO.to_be_bytes());
        m.extend_from_slice(&u32::MAX.to_be_bytes());
        b.write_all(&m).unwrap();
        assert_eq!(srv.join().unwrap().unwrap_err().code, ErrorCode::Protocol);
        // a client without fixed newstyle
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        start(&mut b, 0);
        assert_eq!(srv.join().unwrap().unwrap_err().code, ErrorCode::Protocol);
        // endless options are cut off
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        start(&mut b, CLIENT_FIXED_NEWSTYLE);
        let reader = std::thread::spawn(move || {
            for _ in 0..MAX_OPTIONS + 1 {
                raw_option(&mut b, 4242, b"");
            }
            b
        });
        assert_eq!(srv.join().unwrap().unwrap_err().code, ErrorCode::Protocol);
        drop(reader.join().unwrap());
        // wrong option magic
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        start(&mut b, CLIENT_FIXED_NEWSTYLE);
        b.write_all(&[0u8; 16]).unwrap();
        assert_eq!(srv.join().unwrap().unwrap_err().code, ErrorCode::Protocol);
    }

    #[test]
    fn abort_closes_cleanly() {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let srv = std::thread::spawn(move || server_negotiate(&mut a, &export()));
        start(&mut b, CLIENT_FIXED_NEWSTYLE);
        raw_option(&mut b, OPT_ABORT, b"");
        assert_eq!(srv.join().unwrap().unwrap(), Negotiated::Closed);
    }

    #[test]
    fn request_encoding_round_trip_and_bad_magic() {
        let r = Request { flags: CMD_FLAG_FUA, kind: CMD_WRITE, handle: 0xdead_beef, offset: 4096, len: 512 };
        assert_eq!(Request::parse(&r.encode()).unwrap(), r);
        let mut b = r.encode();
        b[0] ^= 1;
        assert_eq!(Request::parse(&b).unwrap_err().code, ErrorCode::Protocol);
        let h = reply_header(5, 7);
        assert_eq!(u32::from_be_bytes(h[0..4].try_into().unwrap()), SIMPLE_REPLY_MAGIC);
        assert_eq!(u32::from_be_bytes(h[4..8].try_into().unwrap()), 5);
    }
}
