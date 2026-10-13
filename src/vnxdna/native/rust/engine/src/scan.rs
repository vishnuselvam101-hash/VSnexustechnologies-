//! Record scanner over a byte buffer filled block by block (the IO layer carries an incomplete trailing record over).
//!
//! It reproduces `vnxdna.dnaenc.reads.iter_reads` for FASTQ (4-line records: header line starting with '@' after
//! stripping trailing CRs, blank header lines skipped, sequence, '+' line, quality line of the sequence's length) and
//! FASTA (records start at a line beginning with '>', every other non-empty line is stripped of ASCII whitespace and
//! appended). Lines are split at '\n'; an unterminated last line counts only when it is not empty. Whatever the
//! reference would reject or treat specially (no header, truncation, '+' or quality mismatch, records or lines over
//! the reference limits) is refused with `Err`: the caller then falls back to the reference path, which raises the
//! reference error. Codes: A/C/G/T (either case) -> 0..3, every other byte -> 4.

use crate::config::{MAX_LINE, MAX_READ_NT};

pub static LUT: [u8; 256] = {
    let mut t = [4u8; 256];
    t[b'A' as usize] = 0;
    t[b'C' as usize] = 1;
    t[b'G' as usize] = 2;
    t[b'T' as usize] = 3;
    t[b'a' as usize] = 0;
    t[b'c' as usize] = 1;
    t[b'g' as usize] = 2;
    t[b't' as usize] = 3;
    t
};

/// One record: sequence bytes at `seq` (in the block buffer, or in the scanner's side buffer when `side`), `len`
/// bytes; FASTQ quality bytes at `qual` in the block buffer.
#[derive(Clone, Copy)]
pub struct Rec {
    pub side: bool,
    pub seq: usize,
    pub len: usize,
    pub qual: usize,
}

/// Position of the next '\n' at or after `from` (SSE2 on x86-64, scalar elsewhere).
#[inline]
pub fn find_nl(buf: &[u8], from: usize) -> Option<usize> {
    let mut i = from;
    let n = buf.len();
    #[cfg(target_arch = "x86_64")]
    {
        use std::arch::x86_64::*;
        // SAFETY: SSE2 is part of the x86-64 baseline; loads stay inside buf (i + 16 <= n).
        unsafe {
            let nl = _mm_set1_epi8(b'\n' as i8);
            while i + 16 <= n {
                let v = _mm_loadu_si128(buf.as_ptr().add(i) as *const __m128i);
                let m = _mm_movemask_epi8(_mm_cmpeq_epi8(v, nl)) as u32;
                if m != 0 {
                    return Some(i + m.trailing_zeros() as usize);
                }
                i += 16;
            }
        }
    }
    while i < n {
        if buf[i] == b'\n' {
            return Some(i);
        }
        i += 1;
    }
    None
}

#[inline]
fn rstrip_cr(buf: &[u8], a: usize, mut b: usize) -> usize {
    while b > a && buf[b - 1] == b'\r' {
        b -= 1;
    }
    b
}

#[inline]
fn is_ws(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

/// The next line [a, b) starting at `pos` and the position after it; None when the line is incomplete (no '\n' and
/// not at EOF, or an empty unterminated remainder at EOF).
#[inline]
fn line(buf: &[u8], pos: usize, eof: bool) -> Result<Option<(usize, usize, usize)>, String> {
    match find_nl(buf, pos) {
        Some(e) => {
            if e - pos > MAX_LINE {
                return Err("line longer than the reference limit".into());
            }
            Ok(Some((pos, e, e + 1)))
        }
        None => {
            if buf.len() - pos > MAX_LINE {
                return Err("line longer than the reference limit".into());
            }
            if eof && buf.len() > pos {
                Ok(Some((pos, buf.len(), buf.len())))
            } else {
                Ok(None)
            }
        }
    }
}

pub struct Scanner {
    pub fastq: bool,
    pub side: Vec<u8>,
}

impl Scanner {
    pub fn new(fastq: bool) -> Scanner {
        Scanner { fastq, side: Vec::new() }
    }

    /// Scan complete records of `buf`; returns the number of bytes consumed (the rest is an incomplete record that
    /// must be carried over and completed with more input, unless `eof`). Clears `side` first: records of an earlier
    /// call must have been consumed.
    pub fn scan(&mut self, buf: &[u8], eof: bool, out: &mut Vec<Rec>) -> Result<usize, String> {
        self.side.clear();
        if self.fastq {
            self.scan_fastq(buf, eof, out)
        } else {
            self.scan_fasta(buf, eof, out)
        }
    }

    fn scan_fastq(&mut self, buf: &[u8], eof: bool, out: &mut Vec<Rec>) -> Result<usize, String> {
        let mut pos = 0usize;
        loop {
            let start = pos;
            let (ha, hb, after) = match line(buf, pos, eof)? {
                Some(x) => x,
                None => return Ok(start),
            };
            let hb = rstrip_cr(buf, ha, hb);
            if hb == ha {
                pos = after;
                continue; // blank header line
            }
            if buf[ha] != b'@' {
                return Err("malformed FASTQ record (expected '@')".into());
            }
            let mut p = after;
            let mut take = || -> Result<Option<(usize, usize)>, String> {
                match line(buf, p, eof)? {
                    Some((a, b, nx)) => {
                        p = nx;
                        Ok(Some((a, rstrip_cr(buf, a, b))))
                    }
                    None => Ok(None),
                }
            };
            let seq = take()?;
            let plus = if seq.is_some() { take()? } else { None };
            let qual = if plus.is_some() { take()? } else { None };
            let (seq, plus, qual) = match (seq, plus, qual) {
                (Some(s), Some(pl), Some(q)) => (s, pl, q),
                _ => {
                    if eof {
                        return Err("truncated FASTQ record".into());
                    }
                    return Ok(start);
                }
            };
            let slen = seq.1 - seq.0;
            if plus.1 == plus.0 || buf[plus.0] != b'+' || qual.1 - qual.0 != slen {
                return Err("malformed FASTQ record".into());
            }
            if slen > MAX_READ_NT {
                return Err("read longer than the reference limit".into());
            }
            out.push(Rec { side: false, seq: seq.0, len: slen, qual: qual.0 });
            pos = p;
        }
    }

    fn scan_fasta(&mut self, buf: &[u8], eof: bool, out: &mut Vec<Rec>) -> Result<usize, String> {
        // a record is complete when the next header line (or EOF) is seen; otherwise the open record is carried over
        // from its header line
        let mut pos = 0usize;
        let mut open: Option<usize> = None;
        let mut segs: Vec<(usize, usize)> = Vec::new();
        let mut size = 0usize;
        loop {
            let (a, b, after) = match line(buf, pos, eof)? {
                Some(x) => x,
                None => {
                    if eof {
                        if open.is_some() {
                            self.emit(buf, &segs, out);
                        }
                        return Ok(buf.len());
                    }
                    return Ok(open.unwrap_or(pos));
                }
            };
            let b = rstrip_cr(buf, a, b);
            if b > a && buf[a] == b'>' {
                if open.is_some() {
                    self.emit(buf, &segs, out);
                }
                open = Some(a);
                segs.clear();
                size = 0;
            } else if b > a {
                if open.is_none() {
                    return Err("sequence data before the first FASTA header".into());
                }
                size += b - a;
                if size > MAX_READ_NT {
                    return Err("record longer than the reference limit".into());
                }
                let (mut sa, mut sb) = (a, b);
                while sa < sb && is_ws(buf[sa]) {
                    sa += 1;
                }
                while sb > sa && is_ws(buf[sb - 1]) {
                    sb -= 1;
                }
                if sb > sa {
                    segs.push((sa, sb));
                }
            }
            pos = after;
        }
    }

    fn emit(&mut self, buf: &[u8], segs: &[(usize, usize)], out: &mut Vec<Rec>) {
        if segs.len() == 1 {
            out.push(Rec { side: false, seq: segs[0].0, len: segs[0].1 - segs[0].0, qual: usize::MAX });
            return;
        }
        let at = self.side.len();
        for &(a, b) in segs {
            self.side.extend_from_slice(&buf[a..b]);
        }
        out.push(Rec { side: true, seq: at, len: self.side.len() - at, qual: usize::MAX });
    }
}

/// codes[i] = LUT[src[i]] (A/C/G/T either case -> 0..3, anything else -> 4); SSE2 on x86-64.
#[inline]
pub fn convert(src: &[u8], dst: &mut [u8]) {
    let n = src.len().min(dst.len());
    let mut i = 0;
    #[cfg(target_arch = "x86_64")]
    {
        use std::arch::x86_64::*;
        // SAFETY: SSE2 is baseline on x86-64; loads/stores stay inside src/dst (i + 16 <= n).
        unsafe {
            let lo = _mm_set1_epi8(0x20);
            let (a, c, g, t) = (_mm_set1_epi8(b'a' as i8), _mm_set1_epi8(b'c' as i8), _mm_set1_epi8(b'g' as i8),
                                _mm_set1_epi8(b't' as i8));
            let three = _mm_set1_epi8(3);
            let four = _mm_set1_epi8(4);
            while i + 16 <= n {
                let v = _mm_loadu_si128(src.as_ptr().add(i) as *const __m128i);
                let l = _mm_or_si128(v, lo);
                let ok = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi8(l, a), _mm_cmpeq_epi8(l, c)),
                                      _mm_or_si128(_mm_cmpeq_epi8(l, g), _mm_cmpeq_epi8(l, t)));
                // ((c >> 1) ^ (c >> 2)) & 3 maps A C G T (and a c g t) to 0 1 2 3; bytes are shifted as 16-bit lanes,
                // the & 3 keeps only bits that came from the same byte
                let code = _mm_and_si128(_mm_xor_si128(_mm_srli_epi16(v, 1), _mm_srli_epi16(v, 2)), three);
                let out = _mm_or_si128(_mm_and_si128(ok, code), _mm_andnot_si128(ok, four));
                _mm_storeu_si128(dst.as_mut_ptr().add(i) as *mut __m128i, out);
                i += 16;
            }
        }
    }
    while i < n {
        dst[i] = LUT[src[i] as usize];
        i += 1;
    }
}
