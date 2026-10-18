//! Address scheduler support: which reads are worth attempting.
//!
//! Reads are recognised as copies of a strand that is already verified (or of a read already claimed in the same batch)
//! by two kinds of fingerprints, both read without any correction:
//!   * the header key: (kind, tag, group, symbol) of the frame header read from the raw read prefix (`alt` reading of
//!     pass 1), forward and as a reverse complement;
//!   * window fingerprints: 24-nt windows of the strand at fixed template positions (WIN_POS), tolerant to a net shift
//!     of +-1 nt (an indel before the window), for the more likely orientation.
//! A read is skipped only when both kinds agree (a readable header naming a verified address and a matching window),
//! or, with an unreadable header, when every usable window matches: one kind alone never skips a read.
//! A fingerprint only decides whether a read is attempted; it never decides what is decoded. Accepted data always
//! comes from a frame verified by the inner RS code and the CRC.
//!
//! Memory is bounded: the tables are fixed-size, set-associative and lossy (an insert into a full bucket overwrites
//! one entry). A lost entry only means that a later copy is attempted again; pass 2 resolves duplicates as always.

use crate::config::{Cfg, HEADER_BYTES};
use crate::frame::{pack4, tentative};

pub const WIN: usize = 24;
pub const WIN_POS: [usize; 4] = [44, 70, 96, 122];
/// shifts tried for every window (net indel before the window)
pub const SHIFTS: [i64; 3] = [0, -1, 1];
pub const NS: usize = SHIFTS.len();
/// window fingerprints per screened read: NW windows x NS shifts (window-major, shift 0 first)
pub const NFP: usize = NW * NS;
pub const NW: usize = WIN_POS.len();

#[inline]
pub fn fmix(mut k: u64) -> u64 {
    k ^= k >> 33;
    k = k.wrapping_mul(0xff51_afd7_ed55_8ccd);
    k ^= k >> 33;
    k = k.wrapping_mul(0xc4ce_b9fe_1a85_ec53);
    k ^= k >> 33;
    k | 1
}

/// Fixed-size Bloom prefilter (2 bits per fingerprint) in front of the lossy tables: small enough to stay in the
/// CPU caches, so a fingerprint that was never inserted is rejected without touching the large table.
pub struct Bloom {
    bits: Vec<u64>,
    mask: u64,
}

impl Bloom {
    pub fn new(bits_log2: u32) -> Bloom {
        let n = 1usize << bits_log2.max(9);
        Bloom { bits: vec![0; n / 64], mask: n as u64 - 1 }
    }

    #[inline]
    fn idx(&self, fp: u64) -> (usize, usize) {
        let a = (fp >> 7) & self.mask;
        let b = (fp.rotate_left(29) ^ (fp >> 40)) & self.mask;
        (a as usize, b as usize)
    }

    #[inline]
    pub fn insert(&mut self, fp: u64) {
        let (a, b) = self.idx(fp);
        self.bits[a / 64] |= 1 << (a % 64);
        self.bits[b / 64] |= 1 << (b % 64);
    }

    #[inline]
    pub fn maybe(&self, fp: u64) -> bool {
        let (a, b) = self.idx(fp);
        (self.bits[a / 64] >> (a % 64)) & 1 != 0 && (self.bits[b / 64] >> (b % 64)) & 1 != 0
    }

    pub fn bytes(&self) -> usize {
        self.bits.len() * 8
    }
}

/// Fixed-size set-associative fingerprint set (8 x u64 per bucket = one cache line).
pub struct Lossy {
    slots: Vec<u64>,
    mask: usize,
    pub inserts: u64,
    pub evictions: u64,
}

impl Lossy {
    pub fn new(bits: u32) -> Lossy {
        let buckets = 1usize << bits.saturating_sub(3).max(1);
        Lossy { slots: vec![0; buckets * 8], mask: buckets - 1, inserts: 0, evictions: 0 }
    }

    #[inline]
    pub fn prefetch(&self, fp: u64) {
        let b = (fp as usize >> 1) & self.mask;
        #[cfg(target_arch = "x86_64")]
        // SAFETY: prefetch of an address inside the slots vector (a hint, never faults)
        unsafe {
            use std::arch::x86_64::*;
            _mm_prefetch(self.slots.as_ptr().add(b * 8) as *const i8, _MM_HINT_T0);
        }
        #[cfg(not(target_arch = "x86_64"))]
        let _ = b;
    }

    #[inline]
    pub fn contains(&self, fp: u64) -> bool {
        let b = (fp as usize >> 1) & self.mask;
        self.slots[b * 8..b * 8 + 8].iter().any(|&x| x == fp)
    }

    #[inline]
    pub fn insert(&mut self, fp: u64) {
        let b = (fp as usize >> 1) & self.mask;
        let row = &mut self.slots[b * 8..b * 8 + 8];
        if row.iter().any(|&x| x == fp) {
            return;
        }
        self.inserts += 1;
        if let Some(e) = row.iter_mut().find(|x| **x == 0) {
            *e = fp;
            return;
        }
        self.evictions += 1;
        row[(fp >> 60) as usize & 7] = fp;
    }

    pub fn bytes(&self) -> usize {
        self.slots.len() * 8
    }
}

/// Fingerprint of a verified or tentative address.
#[inline]
pub fn key_fp(kind: i64, tag: i64, group: i64, symbol: i64) -> u64 {
    fmix(((kind as u64 & 15) << 60) ^ ((tag as u64 & 0xFFFF) << 44) ^ ((symbol as u64 & 0xFFFF) << 28)
        ^ (group as u64).rotate_left(17) ^ 0x9e37_79b9_7f4a_7c15)
}

/// Fingerprint of window `w` with 2-bit content `v` (48 bits).
#[inline]
pub fn win_fp(w: usize, v: u64) -> u64 {
    fmix(v ^ ((w as u64 + 1) << 52) ^ 0x2545_f491_4f6c_dd1d)
}

/// 2-bit packed window of codes[a..a + WIN] (None when a base is not A/C/G/T or the window leaves the read).
#[inline]
pub fn win_fwd(codes: &[u8], a: i64) -> Option<u64> {
    if a < 0 || a as usize + WIN > codes.len() {
        return None;
    }
    let mut v = 0u64;
    for &c in &codes[a as usize..a as usize + WIN] {
        if c > 3 {
            return None;
        }
        v = (v << 2) | c as u64;
    }
    Some(v)
}

/// The same window of the read taken as a reverse complement: RC(codes)[a..a + WIN].
#[inline]
pub fn win_rc(codes: &[u8], a: i64) -> Option<u64> {
    let l = codes.len() as i64;
    if a < 0 || a + WIN as i64 > l {
        return None;
    }
    let mut v = 0u64;
    for k in 0..WIN as i64 {
        let c = codes[(l - 1 - (a + k)) as usize];
        if c > 3 {
            return None;
        }
        v = (v << 2) | (3 - c) as u64;
    }
    Some(v)
}

/// The raw header reading of a read (forward, or as reverse complement): the 40 bases at the header's template
/// positions, clipped to A/C/G/T (N -> T, past the end -> A), packed 2 bits per base (first base highest), and the
/// fingerprint of the address it reads (None when the version nibble is wrong or the kind is not data/superblock).
#[inline]
pub fn header_raw(cfg: &Cfg, codes: &[u8], rc: bool) -> (u128, Option<u64>) {
    let l = codes.len() as i64;
    let mut raw = [0u8; 4 * HEADER_BYTES];
    let mut v: u128 = 0;
    for k in 0..4 * HEADER_BYTES {
        let p = cfg.frame_pos64[k];
        raw[k] = if p < l {
            let c = if rc { codes[(l - 1 - p) as usize] } else { codes[p as usize] };
            let c = if rc && c < 4 { 3 - c } else { c };
            c.min(3)
        } else {
            0
        };
        v = (v << 2) | raw[k] as u128;
    }
    let mut hb = [0u8; HEADER_BYTES];
    for k in 0..HEADER_BYTES {
        hb[k] = pack4(&raw[4 * k..4 * k + 4]);
    }
    let fp = tentative(cfg, &hb).and_then(|h| if h.kind <= 1 { Some(key_fp(h.kind, h.tag, h.group, h.symbol)) } else { None });
    (v, fp)
}

/// The header bases of a frame (its first 10 bytes), packed like `header_raw`.
#[inline]
pub fn frame_header(frame: &[u8]) -> u128 {
    frame[..HEADER_BYTES].iter().fold(0u128, |v, &b| (v << 8) | b as u128)
}

/// Number of differing bases between two packed headers.
#[inline]
pub fn header_dist(a: u128, b: u128) -> u32 {
    let x = a ^ b;
    ((x | (x >> 1)) & 0x5555_5555_5555_5555_5555_5555_5555_5555u128).count_ones()
}

/// Marker mismatches at the nominal template positions (forward reading, or as reverse complement); positions past
/// the read end count as mismatches.
#[inline]
pub fn marker_mm(cfg: &Cfg, codes: &[u8], rc: bool) -> i64 {
    let l = codes.len() as i64;
    let mut mm = 0;
    for (m, &p) in cfg.mpos.iter().enumerate() {
        if p >= l {
            mm += 1;
            continue;
        }
        let c = if rc {
            let c = codes[(l - 1 - p) as usize];
            if c < 4 { 3 - c } else { 4 }
        } else {
            codes[p as usize]
        };
        mm += (c != cfg.want[m]) as i64;
    }
    mm
}

/// Window fingerprints of a verified strand, from its corrected frame bytes (strand base at template position q = the
/// marker code, or frame base tpl_map[q]).
pub fn strand_windows(cfg: &Cfg, frame: &[u8], out: &mut [u64; NW]) -> usize {
    let mut n = 0;
    for (w, &a) in WIN_POS.iter().enumerate() {
        if a + WIN > cfg.t {
            continue;
        }
        let mut v = 0u64;
        for q in a..a + WIN {
            let m = cfg.tpl_map[q];
            let b = if m >= 0 {
                (frame[m as usize / 4] >> (6 - 2 * (m as usize % 4))) & 3
            } else {
                cfg.want[(-m - 1) as usize]
            };
            v = (v << 2) | b as u64;
        }
        out[n] = win_fp(w, v);
        n += 1;
    }
    n
}

/// Fingerprints of window `w` at strand position `a` for shifts -1, 0, +1 (SHIFTS order 0, -1, +1) of the read taken
/// forward or as reverse complement, from one 26-base span; 0 where a window leaves the read or holds an N.
#[inline]
pub fn win_shifts(codes: &[u8], w: usize, a: i64, rc: bool, out: &mut [u64]) {
    let l = codes.len() as i64;
    let base = |q: i64| -> u8 {
        if rc {
            let c = codes[(l - 1 - q) as usize];
            if c < 4 { 3 - c } else { 4 }
        } else {
            codes[q as usize]
        }
    };
    let (lo, hi) = (a - 1, a + WIN as i64 + 1);
    if lo >= 0 && hi <= l {
        let mut v = 0u64;
        let mut bad = false;
        for q in lo..hi {
            let c = base(q);
            bad |= c > 3;
            v = (v << 2) | (c & 3) as u64;
        }
        if !bad {
            let m48 = (1u64 << 48) - 1;
            out[0] = win_fp(w, (v >> 2) & m48);
            out[1] = win_fp(w, v >> 4);
            out[2] = win_fp(w, v & m48);
            return;
        }
    }
    for (k, &s) in SHIFTS.iter().enumerate() {
        let v = if rc { win_rc(codes, a + s) } else { win_fwd(codes, a + s) };
        out[k] = v.map_or(0, |v| win_fp(w, v));
    }
}

/// Orientation estimate for the screen only (the attempts use the exact pre-pass of pass 1): per marker block, the best
/// agreement over shifts 0, -1, +1, -2, +2, summed over the first `SCREEN_BLOCKS` blocks, forward and as reverse
/// complement. Returns (reverse complement more likely, best score of the chosen orientation, markers scored).
pub const SCREEN_BLOCKS: usize = 4;

#[inline]
fn block_score(cfg: &Cfg, codes: &[u8], rc: bool, nblk: usize, first: &[usize]) -> i64 {
    let l = codes.len() as i64;
    let mut total = 0i64;
    for b in 0..nblk {
        let (m0, m1) = (first[b], first[b + 1]);
        let mut best = 0i64;
        for s in [0i64, -1, 1, -2, 2] {
            let mut hit = 0i64;
            for m in m0..m1 {
                let p = cfg.mpos[m] + s;
                if p < 0 || p >= l {
                    continue;
                }
                let c = if rc {
                    let c = codes[(l - 1 - p) as usize];
                    if c < 4 { 3 - c } else { 4 }
                } else {
                    codes[p as usize]
                };
                hit += (c == cfg.want[m]) as i64;
            }
            if hit > best {
                best = hit;
                if best == (m1 - m0) as i64 {
                    break;
                }
            }
        }
        total += best;
    }
    total
}

/// First marker index of every block, plus the marker count at the end (computed once per config).
pub fn block_firsts(cfg: &Cfg) -> Vec<usize> {
    let mut out = Vec::new();
    for (m, &b) in cfg.block.iter().enumerate() {
        if m == 0 || b != cfg.block[m - 1] {
            out.push(m);
        }
    }
    out.push(cfg.mpos.len());
    out
}

#[inline]
pub fn orient_estimate(cfg: &Cfg, codes: &[u8], first: &[usize]) -> (bool, i64, i64) {
    let nblk = (first.len() - 1).min(SCREEN_BLOCKS);
    if nblk == 0 {
        return (false, 0, 0);
    }
    let total = first[nblk] as i64;
    let f = block_score(cfg, codes, false, nblk, first);
    if !cfg.rc || f + 2 >= total {
        return (false, f, total);
    }
    let r = block_score(cfg, codes, true, nblk, first);
    if r > f + 1 {
        (true, r, total)
    } else {
        (false, f, total)
    }
}

/// Open-addressing map fingerprint -> u32 for one batch (linear probing, cleared per batch).
pub struct FpMap {
    keys: Vec<u64>,
    vals: Vec<u32>,
    mask: usize,
}

impl FpMap {
    pub fn new() -> FpMap {
        FpMap { keys: Vec::new(), vals: Vec::new(), mask: 0 }
    }

    /// Clear and size for up to `n` entries (load <= 1/2).
    pub fn reset(&mut self, n: usize) {
        let cap = (2 * n.max(8)).next_power_of_two();
        if self.keys.len() != cap {
            self.keys = vec![0; cap];
            self.vals = vec![0; cap];
        } else {
            self.keys.iter_mut().for_each(|k| *k = 0);
        }
        self.mask = cap - 1;
    }

    #[inline]
    pub fn get(&self, fp: u64) -> Option<u32> {
        let mut i = (fp >> 3) as usize & self.mask;
        loop {
            let k = self.keys[i];
            if k == fp {
                return Some(self.vals[i]);
            }
            if k == 0 {
                return None;
            }
            i = (i + 1) & self.mask;
        }
    }

    /// Insert unless present (first value wins).
    #[inline]
    pub fn insert_first(&mut self, fp: u64, v: u32) {
        let mut i = (fp >> 3) as usize & self.mask;
        loop {
            let k = self.keys[i];
            if k == fp {
                return;
            }
            if k == 0 {
                self.keys[i] = fp;
                self.vals[i] = v;
                return;
            }
            i = (i + 1) & self.mask;
        }
    }
}

// ------------------------------------------------------------------------------------------- strand corroboration
// The adaptive scheduler's identity test (docs/V14_SCHEDULER.md). A read is counted as a copy of a verified strand K
// only when the read, projected onto K's template, agrees with K's corrected frame on all but at most `budget` frame
// bytes. The frame is an inner Reed-Solomon codeword with r parity bytes (MDS, minimum distance r + 1 bytes over the
// whole frame, header and CRC included), so every other frame K' differs from K in at least r + 1 bytes and a reading
// of K' can only pass when the channel turned at least margin = r + 1 - budget of those bytes into exactly K's bytes.
// Payload windows, the header key and the CRC are never evidence on their own.

/// Margin (bytes another codeword must lose to the channel to pass as K) when the inner code is long enough.
pub const STRAND_MARGIN: usize = 8;
/// Smallest margin ever used: shorter inner codes (r + 1 < 8) need exact agreement on every frame byte (budget 0, margin
/// r + 1); below this the strand check is never applied.
pub const STRAND_MARGIN_MIN: usize = 5;
/// Longest run of frame bytes compared at one shift (a segment between markers is split into such pieces).
pub const SEG_MAX: usize = 6;

/// Unconfirmed-byte budget of the strand check for an inner code with `r` parity bytes: r + 1 - min(STRAND_MARGIN,
/// r + 1), or None when r + 1 < STRAND_MARGIN_MIN (the check is then never applied and every read is attempted).
pub fn strand_budget(r: usize) -> Option<usize> {
    if r + 1 < STRAND_MARGIN_MIN {
        return None;
    }
    Some(r + 1 - (r + 1).min(STRAND_MARGIN))
}

/// Segments of the strand check: (template position of the first base, first frame byte, bytes); every segment is a
/// run of whole frame bytes whose bases are contiguous on the template (no marker inside), at most SEG_MAX bytes.
/// Empty when a frame byte's four bases are not contiguous (the check is then never applied).
pub fn strand_segments(fp: &[i64], fb: usize) -> Vec<(i64, usize, usize)> {
    let mut out = Vec::new();
    if fp.len() < 4 * fb {
        return out;
    }
    for j in 0..fb {
        let q = fp[4 * j];
        if (1..4).any(|k| fp[4 * j + k] != q + k as i64) {
            return Vec::new();
        }
        match out.last_mut() {
            Some((q0, _, nb)) if *nb < SEG_MAX && *q0 + 4 * *nb as i64 == q => *nb += 1,
            _ => out.push((q, j, 1usize)),
        }
    }
    out
}

/// The four bases of every frame byte as one little-endian word (first base in the low byte): the unit the strand
/// check compares.
pub fn frame_words(frame: &[u8], out: &mut Vec<u32>) {
    out.clear();
    out.extend(frame.iter().map(|&b| {
        ((b >> 6) & 3) as u32 | (((b >> 4) & 3) as u32) << 8 | (((b >> 2) & 3) as u32) << 16 | ((b & 3) as u32) << 24
    }));
}

/// The read in the given orientation: the codes themselves, or their reverse complement written into `buf` (N stays a
/// non-base code that never equals a frame base).
pub fn oriented<'a>(codes: &'a [u8], rc: bool, buf: &'a mut Vec<u8>) -> &'a [u8] {
    if !rc {
        return codes;
    }
    buf.clear();
    buf.extend(codes.iter().rev().map(|&c| if c < 4 { 3 - c } else { 4 }));
    &buf[..]
}

/// Strand check of one read (`rd`, oriented) against a verified frame (`fw`, from frame_words): walks the segments
/// with a running shift d (read position = template position + d), taking for each segment the shift in d, d - 1,
/// d + 1, d - 2, d + 2 (|d| <= band) with the most frame bytes equal (all four bases); the segment counts only when at
/// least half its bytes agree at that shift (else every byte of it is unconfirmed and d is kept). Returns the number of
/// unconfirmed bytes, stopping early once it exceeds `budget` (the read is then not a confirmed copy). N bases and
/// positions outside the read never agree. A pre-check scores the last two segments first, anchored at the read's end
/// (shift = read length - template length `t`, +-2): distinct strands with identical payloads differ there (CRC and
/// inner parity), so they are rejected without the walk; the pre-check can only reject, never confirm.
pub fn strand_unconfirmed(segs: &[(i64, usize, usize)], band: i64, t: usize, rd: &[u8], fw: &[u32], budget: usize)
                          -> usize {
    let l = rd.len() as i64;
    #[inline(always)]
    fn word(c: &[u8]) -> u32 {
        u32::from_le_bytes([c[0], c[1], c[2], c[3]])
    }
    let count = |q0: i64, j0: usize, nb: usize, s: i64| -> usize {
        let a = q0 + s;
        let want = &fw[j0..j0 + nb];
        if a >= 0 && a + 4 * nb as i64 <= l {
            let a = a as usize;
            rd[a..a + 4 * nb].chunks_exact(4).zip(want).filter(|(c, &w)| word(c) == w).count()
        } else {
            (0..nb).filter(|&b| {
                let q = a + 4 * b as i64;
                q >= 0 && q + 4 <= l && word(&rd[q as usize..q as usize + 4]) == want[b]
            }).count()
        }
    };
    let score = |q0: i64, j0: usize, nb: usize, d: i64| -> (usize, i64) {
        let mut best = count(q0, j0, nb, d);
        let mut bd = d;
        if best + 1 < nb {
            for s in [d - 1, d + 1, d - 2, d + 2] {
                if s.abs() > band {
                    continue;
                }
                let c = count(q0, j0, nb, s);
                if c > best {
                    best = c;
                    bd = s;
                }
            }
        }
        if best > 0 && 2 * best >= nb { (nb - best, bd) } else { (nb, d) }
    };
    if segs.len() >= 4 {
        let d_end = (l - t as i64).clamp(-band, band);
        let tail: usize = segs[segs.len() - 2..].iter().map(|&(q0, j0, nb)| score(q0, j0, nb, d_end).0).sum();
        if tail > budget {
            return tail;
        }
    }
    let mut d = 0i64;
    let mut unconf = 0usize;
    for &(q0, j0, nb) in segs {
        let (u, nd) = score(q0, j0, nb, d);
        d = nd;
        unconf += u;
        if unconf > budget {
            break;
        }
    }
    unconf
}

/// Fixed-size, 4-way set-associative store of verified frames keyed by the address fingerprint (lossy: an insert into
/// a full set replaces one entry; a lost frame only means a later copy is attempted).
pub struct FrameStore {
    keys: Vec<u64>,
    frames: Vec<u8>,
    fb: usize,
    mask: usize,
    pub inserts: u64,
    pub evictions: u64,
}

impl FrameStore {
    pub fn new(slots_log2: u32, fb: usize) -> FrameStore {
        let slots = 1usize << slots_log2.max(2);
        FrameStore { keys: vec![0; slots], frames: vec![0; slots * fb], fb, mask: slots / 4 - 1, inserts: 0, evictions: 0 }
    }

    #[inline]
    pub fn get(&self, key: u64) -> Option<&[u8]> {
        let b = (key as usize >> 1) & self.mask;
        (4 * b..4 * b + 4).find(|&s| self.keys[s] == key).map(|s| &self.frames[s * self.fb..(s + 1) * self.fb])
    }

    /// Insert (or refresh) the frame of `key`; the first frame stored for a key is kept.
    #[inline]
    pub fn insert(&mut self, key: u64, frame: &[u8]) {
        let b = (key as usize >> 1) & self.mask;
        if (4 * b..4 * b + 4).any(|s| self.keys[s] == key) {
            return;
        }
        self.inserts += 1;
        let s = match (4 * b..4 * b + 4).find(|&s| self.keys[s] == 0) {
            Some(s) => s,
            None => {
                self.evictions += 1;
                4 * b + ((key >> 60) as usize & 3)
            }
        };
        self.keys[s] = key;
        self.frames[s * self.fb..(s + 1) * self.fb].copy_from_slice(&frame[..self.fb]);
    }

    pub fn bytes(&self) -> usize {
        self.keys.len() * 8 + self.frames.len()
    }
}

/// Fixed-size, 4-way set-associative map window fingerprint -> address fingerprint of a verified strand holding that
/// window (candidates for the strand check only: a hit is never evidence). The last insert wins.
pub struct WinMap {
    keys: Vec<u64>,
    vals: Vec<u64>,
    mask: usize,
    pub evictions: u64,
}

impl WinMap {
    pub fn new(slots_log2: u32) -> WinMap {
        let slots = 1usize << slots_log2.max(2);
        WinMap { keys: vec![0; slots], vals: vec![0; slots], mask: slots / 4 - 1, evictions: 0 }
    }

    #[inline]
    pub fn get(&self, fp: u64) -> Option<u64> {
        let b = (fp as usize >> 1) & self.mask;
        (4 * b..4 * b + 4).find(|&s| self.keys[s] == fp).map(|s| self.vals[s])
    }

    #[inline]
    pub fn insert(&mut self, fp: u64, val: u64) {
        let b = (fp as usize >> 1) & self.mask;
        let s = match (4 * b..4 * b + 4).find(|&s| self.keys[s] == fp || self.keys[s] == 0) {
            Some(s) => s,
            None => {
                self.evictions += 1;
                4 * b + ((fp >> 60) as usize & 3)
            }
        };
        self.keys[s] = fp;
        self.vals[s] = val;
    }

    pub fn bytes(&self) -> usize {
        self.keys.len() * 16
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- strand check (docs/V14_SCHEDULER.md)
    /// v4-balanced-like geometry: 24-nt frame runs, 3-nt markers; returns (frame_pos, template length).
    fn geometry(fb: usize) -> (Vec<i64>, usize) {
        let mut fp = Vec::new();
        let mut q = 0i64;
        for f in 0..4 * fb {
            if f > 0 && f % 24 == 0 {
                q += 3;
            }
            fp.push(q);
            q += 1;
        }
        (fp, q as usize)
    }

    fn strand(fp: &[i64], t: usize, frame: &[u8]) -> Vec<u8> {
        let mut s = vec![1u8; t];                         // markers: any fixed bases
        for (f, &q) in fp.iter().enumerate() {
            s[q as usize] = (frame[f / 4] >> (6 - 2 * (f % 4))) & 3;
        }
        s
    }

    fn frame_of(seed: u64, fb: usize) -> Vec<u8> {
        (0..fb as u64).map(|i| (fmix(seed * 1000 + i) >> 13) as u8).collect()
    }

    #[test]
    fn segments_follow_marker_runs_and_cap_at_six_bytes() {
        let (fp, _) = geometry(70);
        let segs = strand_segments(&fp, 70);
        assert_eq!(segs.iter().map(|s| s.2).sum::<usize>(), 70);
        assert!(segs.iter().all(|s| s.2 <= SEG_MAX));
        assert_eq!(segs[0], (0, 0, 6));
        assert_eq!(segs[1], (27, 6, 6));               // after the first marker
        let mut bad = fp.clone();
        bad[2] += 1;                                     // a byte split by a gap: the check is never applied
        assert!(strand_segments(&bad, 70).is_empty());
        assert!(strand_segments(&fp[..10], 70).is_empty());
    }

    #[test]
    fn strand_check_confirms_noisy_copies_and_rejects_other_frames() {
        let fb = 70;
        let (fp, t) = geometry(fb);
        let segs = strand_segments(&fp, fb);
        let k = frame_of(1, fb);
        let mut fw = Vec::new();
        frame_words(&k, &mut fw);
        let s = strand(&fp, t, &k);
        let u = |rd: &[u8]| strand_unconfirmed(&segs, 8, t, rd, &fw, 9);
        assert_eq!(u(&s), 0);
        let mut sub = s.clone();                         // three substitutions in three frame bytes
        for q in [5usize, 100, 200] {
            sub[q] = (sub[q] + 1) & 3;
        }
        assert_eq!(u(&sub), 3);
        let mut del = s.clone();                         // a deletion inside the header
        del.remove(10);
        assert!(u(&del) <= 4, "{}", u(&del));
        let mut ins = s.clone();                         // an insertion in the payload and a deletion later
        ins.insert(120, 2);
        ins.remove(250);
        assert!(u(&ins) <= 6, "{}", u(&ins));
        let mut n = s.clone();
        n[50] = 4;                                       // an N never agrees
        assert_eq!(u(&n), 1);
        assert!(u(&s[..150]) > 9);                       // positions past the read end never agree
        // a distinct frame with the same payload bytes 10..50 and a header one bit away: rejected
        let mut other = frame_of(2, fb);
        other[..10].copy_from_slice(&k[..10]);
        other[9] ^= 1;
        other[10..50].copy_from_slice(&k[10..50]);
        assert!(u(&strand(&fp, t, &other)) > 9);
        // the reverse complement of a copy, oriented back, is the copy
        let rc: Vec<u8> = s.iter().rev().map(|&c| 3 - c).collect();
        let mut buf = Vec::new();
        assert_eq!(u(oriented(&rc, true, &mut buf)), 0);
    }

    #[test]
    fn strand_budget_keeps_the_margin() {
        // v4-indel / v4-balanced / v4-dense / v6-high-dropout parities, and codes too short for the minimum margin
        assert_eq!(strand_budget(20), Some(13));
        assert_eq!(strand_budget(16), Some(9));
        assert_eq!(strand_budget(12), Some(5));
        assert_eq!(strand_budget(7), Some(0));
        assert_eq!(strand_budget(4), Some(0));               // exact agreement, margin r + 1 = 5
        assert_eq!(strand_budget(2), None);
        assert_eq!(strand_budget(0), None);
        for r in 4..=64 {
            assert!(r + 1 - strand_budget(r).unwrap() >= STRAND_MARGIN_MIN);
        }
    }

    #[test]
    fn frame_store_and_window_map_are_bounded_and_lossy() {
        let mut st = FrameStore::new(4, 3);              // 16 slots, 4 sets of 4
        for i in 1..=40u64 {
            st.insert(fmix(i), &[i as u8, 0, 0]);
        }
        assert!(st.evictions > 0 && st.bytes() == 16 * 8 + 16 * 3);
        let kept: Vec<u64> = (1..=40u64).filter(|&i| st.get(fmix(i)).is_some()).collect();
        assert!(kept.len() <= 16 && !kept.is_empty());
        for &i in &kept {
            assert_eq!(st.get(fmix(i)).unwrap()[0], i as u8);  // a stored key returns its own frame
        }
        st.insert(fmix(kept[0]), &[99, 0, 0]);           // the first frame stored for a key is kept
        assert_eq!(st.get(fmix(kept[0])).unwrap()[0], kept[0] as u8);
        let mut wm = WinMap::new(4);
        wm.insert(fmix(7), 70);
        wm.insert(fmix(7), 71);                          // the last insert wins
        assert_eq!(wm.get(fmix(7)), Some(71));
        assert_eq!(wm.get(fmix(8)), None);
    }

    #[test]
    fn lossy_table_inserts_finds_and_evicts() {
        let mut t = Lossy::new(6);                        // 8 buckets x 8 slots
        for i in 1..=64u64 {
            t.insert(fmix(i));
        }
        assert!((1..=64u64).filter(|&i| t.contains(fmix(i))).count() >= 48);
        t.insert(fmix(1));
        assert!(t.inserts <= 65 && t.evictions <= 64 && t.bytes() == 8 * 8 * 8);
        assert!(!t.contains(fmix(999_999)));              // never inserted (fingerprints are never 0)
    }

    #[test]
    fn bloom_never_rejects_an_inserted_fingerprint() {
        let mut b = Bloom::new(12);
        let fps: Vec<u64> = (1..500u64).map(fmix).collect();
        for &f in &fps {
            b.insert(f);
        }
        assert!(fps.iter().all(|&f| b.maybe(f)));
        assert!((500..5000u64).map(fmix).filter(|&f| b.maybe(f)).count() < 2000);
    }

    #[test]
    fn fpmap_first_value_wins() {
        let mut m = FpMap::new();
        m.reset(10);
        m.insert_first(fmix(7), 1);
        m.insert_first(fmix(7), 2);
        assert_eq!(m.get(fmix(7)), Some(1));
        assert_eq!(m.get(fmix(8)), None);
        m.reset(10);
        assert_eq!(m.get(fmix(7)), None);
    }

    #[test]
    fn windows_forward_and_reverse_complement_agree() {
        let codes: Vec<u8> = (0..64u8).map(|i| (i * 7 + 3) % 4).collect();
        let rc: Vec<u8> = codes.iter().rev().map(|&c| 3 - c).collect();
        for a in [0i64, 5, 40] {
            assert_eq!(win_rc(&codes, a), win_fwd(&rc, a));
        }
        assert_eq!(win_fwd(&codes, 41), None);
        let mut with_n = codes.clone();
        with_n[10] = 4;
        assert_eq!(win_fwd(&with_n, 0), None);
        let mut out = [0u64; NS];
        win_shifts(&codes, 0, 5, false, &mut out);
        assert_eq!(out[0], win_fp(0, win_fwd(&codes, 5).unwrap()));
        assert_eq!(out[1], win_fp(0, win_fwd(&codes, 4).unwrap()));
        assert_eq!(out[2], win_fp(0, win_fwd(&codes, 6).unwrap()));
    }

    #[test]
    fn header_distance_counts_bases() {
        let a: u128 = 0b11_00_01_10;
        assert_eq!(header_dist(a, a), 0);
        assert_eq!(header_dist(a, a ^ 0b01), 1);
        assert_eq!(header_dist(a, a ^ 0b11_00_00_00), 1);
        assert_eq!(header_dist(a, a ^ 0b10_00_00_01), 2);
    }

    #[test]
    fn fmix_is_odd_and_spreads() {
        assert!((0..1000u64).all(|i| fmix(i) & 1 == 1));
        assert_ne!(fmix(1), fmix(2));
    }
}
