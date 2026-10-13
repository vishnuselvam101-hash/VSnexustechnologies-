//! Address scheduler support: which reads are worth attempting.
//!
//! Reads are recognised as copies of a strand that is already verified (or of a read already claimed in the same batch)
//! by two kinds of fingerprints, both read without any correction:
//!   * the header key: (kind, tag, group, symbol) of the frame header read from the raw read prefix (`alt` reading of
//!     pass 1), forward and as a reverse complement;
//!   * window fingerprints: 24-nt windows of the strand at fixed template positions (WIN_POS), tolerant to a net shift
//!     of +-1 nt (an indel before the window), for the more likely orientation.
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
