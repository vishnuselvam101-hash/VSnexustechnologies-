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
// only when the read's marker-anchored projection (strand_project: one shift per marker period, set by the read's own
// markers, never by K) agrees with K's stored frame on all but at most `budget` bytes, a Hamming count over frame bytes.
// The stored frame is an inner Reed-Solomon codeword (re-checked before it is stored) with r parity bytes: MDS, minimum
// distance r + 1 bytes over the whole frame, header and CRC included. Indels are allowed only as whole unconfirmed
// segments (a shift change costs the segment holding it) and must be confirmed by two exact markers, so an error-free
// reading of any other codeword K' is compared unshifted and leaves >= r + 1 unconfirmed bytes, whatever the payload
// (chosen plaintexts included); with channel errors it passes only if the channel turned margin = r + 1 - budget of the
// differing bytes into exactly K's bytes, or misplaced markers on both sides of segments (two exact false markers per
// segment). That last part is a probability under the channel model, not a guarantee; a skip only withholds a read.
// Payload windows, the header key and the CRC are never evidence on their own.

/// Margin (bytes another codeword must lose to the channel to pass as K) when the inner code is long enough.
pub const STRAND_MARGIN: usize = 8;
/// Smallest margin ever used: shorter inner codes (r + 1 < 8) need exact agreement on every frame byte (budget 0, margin
/// r + 1); below this the strand check is never applied.
pub const STRAND_MARGIN_MIN: usize = 5;

/// Unconfirmed-byte budget of the strand check for an inner code with `r` parity bytes: r + 1 - min(STRAND_MARGIN,
/// r + 1), or None when r + 1 < STRAND_MARGIN_MIN (the check is then never applied and every read is attempted).
pub fn strand_budget(r: usize) -> Option<usize> {
    if r + 1 < STRAND_MARGIN_MIN {
        return None;
    }
    Some(r + 1 - (r + 1).min(STRAND_MARGIN))
}

/// Strand-check geometry: the frame's bytes in segments (runs of whole bytes contiguous on the template, i.e. one marker
/// period), each with the marker block just before and just after it (None = the read start / the read end), and the
/// marker blocks as ranges of marker indices.
pub struct StrandGeom {
    pub segs: Vec<StrandSeg>,
    pub blocks: Vec<(usize, usize)>,
    pub fb: usize,
    pub t: usize,
    pub band: i64,
    /// first frame byte of the CRC + inner parity tail: a segment reaching into it is never read across an indel
    pub tail: usize,
}

/// Unconfirmed bytes charged for every shift change (indel segment) a comparison uses, on top of its unmatched bytes.
pub const SHIFT_CHARGE: usize = 1;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StrandSeg {
    pub q0: i64,
    pub j0: usize,
    pub nb: usize,
    pub left: Option<usize>,
    pub right: Option<usize>,
}

/// Geometry of the strand check, or None when a frame byte's bases are not contiguous on the template or a gap between
/// frame runs is not a marker block (the check is then never applied).
pub fn strand_geometry(fp: &[i64], fb: usize, mpos: &[i64], block: &[i64], t: usize, band: i64, tail: usize)
                       -> Option<StrandGeom> {
    if fp.len() < 4 * fb || block.len() != mpos.len() {
        return None;
    }
    let mut blocks: Vec<(usize, usize)> = Vec::new();
    for m in 0..mpos.len() {
        if m == 0 || block[m] != block[m - 1] {
            blocks.push((m, m + 1));
        } else {
            blocks.last_mut().unwrap().1 = m + 1;
        }
    }
    let block_at = |q: i64| -> Option<usize> {
        let m = mpos.iter().position(|&p| p == q)?;
        blocks.iter().position(|&(a, e)| a <= m && m < e)
    };
    let mut segs: Vec<StrandSeg> = Vec::new();
    for j in 0..fb {
        let q = fp[4 * j];
        if (1..4).any(|k| fp[4 * j + k] != q + k as i64) {
            return None;
        }
        match segs.last_mut() {
            Some(sg) if sg.q0 + 4 * sg.nb as i64 == q => sg.nb += 1,
            _ => {
                let left = if q == 0 { None } else { Some(block_at(q - 1)?) };
                segs.push(StrandSeg { q0: q, j0: j, nb: 1, left, right: None });
            }
        }
    }
    let n = segs.len();
    for k in 0..n {
        let end = segs[k].q0 + 4 * segs[k].nb as i64;
        segs[k].right = if k + 1 < n {
            segs[k + 1].left
        } else if (end as usize) < t {
            Some(block_at(end)?)
        } else {
            None
        };
    }
    Some(StrandGeom { segs, blocks, fb, t, band, tail })
}

/// Marker-anchored projection of a read (`rd`, oriented) onto the frame. `ok[j]` bit 0: `proj[j]` is the read's byte at
/// frame byte j at the segment's shift; bit 2: the segment holds an indel (its two anchors differ by at most 2), then
/// `proj[j]` is the byte at the left anchor's shift (bit 0) and `proj2[j]` the byte at the right anchor's shift (bit 1).
/// ok[j] = 0: unread (counts as unconfirmed). Every marker block is located at the only exact match of all its bases
/// among the shifts c, c - 1, c + 1, c - 2, c + 2 (|shift| <= band) around the previous anchored shift c; a block with no
/// exact match, or several (ambiguous), has no anchor. The read start (shift 0) and the read end (shift = read length -
/// template length) anchor the first and last segment. A segment is read at shift d when both its anchors are d, or
/// when every anchor it has equals the established shift E (no change; E starts at 0 and becomes the shift of every
/// segment read). A segment whose two anchors are both present and differ holds an indel: it is read at both shifts,
/// unless it reaches into the CRC + parity tail (then it is unread); E becomes its right anchor. Any other case (a
/// change seen by one marker only) leaves the segment unread. So shift changes are read off the read's own markers
/// (two exact markers on either side), never chosen against a reference, and the projection depends on the read alone.
pub fn strand_project(g: &StrandGeom, mpos: &[i64], want: &[u8], rd: &[u8], proj: &mut [u8], proj2: &mut [u8],
                      ok: &mut [u8]) {
    let l = rd.len() as i64;
    let byte_at = |q: i64| -> Option<u8> {
        if q < 0 || q + 4 > l {
            return None;
        }
        let w = &rd[q as usize..q as usize + 4];
        if w.iter().all(|&x| x < 4) { Some((w[0] << 6) | (w[1] << 4) | (w[2] << 2) | w[3]) } else { None }
    };
    // Fast path, decision-identical to the general one: a read of template length whose every marker base reads at
    // shift 0 has shift 0 in every block's candidate set, so each anchor is 0 or none (ambiguous), the end anchor is 0
    // and the established shift stays 0: every segment is read at shift 0, none holds an indel.
    if l == g.t as i64 && mpos.iter().zip(want).all(|(&q, &w)| rd[q as usize] == w) {
        for sg in &g.segs {
            for b in 0..sg.nb {
                let j = sg.j0 + b;
                match byte_at(sg.q0 + 4 * b as i64) {
                    Some(v) => {
                        proj[j] = v;
                        ok[j] = 1;
                    }
                    None => ok[j] = 0,
                }
            }
        }
        return;
    }
    let mut anchor: Vec<Option<i64>> = Vec::with_capacity(g.blocks.len());
    let mut c = 0i64;
    for &(m0, m1) in &g.blocks {
        let mut found = None;
        let mut hits = 0;
        for d in [c, c - 1, c + 1, c - 2, c + 2] {
            if d.abs() > g.band {
                continue;
            }
            if (m0..m1).all(|m| {
                let q = mpos[m] + d;
                q >= 0 && q < l && rd[q as usize] == want[m]
            }) {
                found = Some(d);
                hits += 1;
            }
        }
        if hits > 1 {
            found = None;                                // ambiguous (the marker also reads at a neighbouring shift)
        }
        if let Some(d) = found {
            c = d;
        }
        anchor.push(found);
    }
    // est: the established shift; firm: est was confirmed by two exact markers (or is the read start's 0)
    let (mut est, mut firm) = (0i64, true);
    for sg in &g.segs {
        let la = sg.left.map_or(Some(0), |b| anchor[b]);
        let ra = sg.right.map_or(Some(l - g.t as i64), |b| anchor[b]);
        let (d, d2) = match (la, ra) {
            (Some(x), Some(y)) if x == y => (Some(x), None),
            (Some(x), Some(y)) if x == est && firm && (x - y).abs() <= 2 && sg.j0 + sg.nb <= g.tail => (Some(x), Some(y)),
            _ if firm && la.map_or(true, |x| x == est) && ra.map_or(true, |x| x == est) => (Some(est), None),
            _ => (None, None),
        };
        for b in 0..sg.nb {
            let j = sg.j0 + b;
            ok[j] = 0;
            let q = sg.q0 + 4 * b as i64;
            if let Some(d) = d {
                if let Some(v) = byte_at(q + d) {
                    proj[j] = v;
                    ok[j] |= 1;
                }
            }
            if let Some(d2) = d2 {
                ok[j] |= 4;
                if let Some(v) = byte_at(q + d2) {
                    proj2[j] = v;
                    ok[j] |= 2;
                }
            }
        }
        match (d, d2) {
            (Some(x), None) => {
                est = x;
                firm = true;
            }
            (_, Some(y)) => {
                est = y;                                 // seen by one marker so far: firm once a second one agrees
                firm = false;
            }
            _ => {
                if let Some(y) = ra {
                    est = y;
                    firm = false;
                }
            }
        }
    }
}

/// Frame bytes a projection does not confirm against `frame` (a Hamming count over bytes), plus SHIFT_CHARGE per indel
/// segment. In an indel segment the bytes before one split point are compared at the left shift and the bytes from it
/// on at the right shift (one split per segment, the best one): the read's markers decide that the segment holds an
/// indel, the frame only locates it.
pub fn strand_unconfirmed(g: &StrandGeom, proj: &[u8], proj2: &[u8], ok: &[u8], frame: &[u8]) -> usize {
    let mut unconf = 0;
    for sg in &g.segs {
        let (a, e) = (sg.j0, sg.j0 + sg.nb);
        if ok[a] & 4 == 0 {
            unconf += (a..e).filter(|&j| ok[j] & 1 == 0 || proj[j] != frame[j]).count();
            continue;
        }
        let ml = |j: usize| (ok[j] & 1 != 0 && proj[j] == frame[j]) as usize;
        let mr = |j: usize| (ok[j] & 2 != 0 && proj2[j] == frame[j]) as usize;
        let mut cur: usize = (a..e).map(mr).sum();          // split at a: every byte at the right shift
        let mut best = cur;
        for j in a..e {
            cur = cur + ml(j) - mr(j);                        // move byte j to the left side
            best = best.max(cur);
        }
        unconf += sg.nb - best + SHIFT_CHARGE;
    }
    unconf
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

    /// the format's 3-base marker table (ACG, TGC, CAT, GTA), rotating
    const MK: [[u8; 3]; 4] = [[0, 1, 2], [3, 2, 1], [1, 0, 3], [2, 3, 0]];

    fn marker_base(q: usize) -> u8 {
        MK[(q / 27) % 4][(q - 24) % 27]
    }

    fn strand(fp: &[i64], t: usize, frame: &[u8]) -> Vec<u8> {
        let mut s: Vec<u8> = (0..t).map(|q| if q >= 24 && (q - 24) % 27 < 3 { marker_base(q) } else { 9 }).collect();
        for (f, &q) in fp.iter().enumerate() {
            s[q as usize] = (frame[f / 4] >> (6 - 2 * (f % 4))) & 3;
        }
        s
    }

    fn frame_of(seed: u64, fb: usize) -> Vec<u8> {
        (0..fb as u64).map(|i| (fmix(seed * 1000 + i) >> 13) as u8).collect()
    }

    /// marker positions (3 bases after every 24 frame bases, all code 1) and their block ids, for `geometry`
    fn markers(fb: usize) -> (Vec<i64>, Vec<u8>, Vec<i64>) {
        let (fp, t) = geometry(fb);
        let mut mpos = Vec::new();
        let mut q = 0usize;
        let mut fi = 0usize;
        while q < t {
            if fi < fp.len() && fp[fi] == q as i64 {
                fi += 1;
            } else {
                mpos.push(q as i64);
            }
            q += 1;
        }
        let block = mpos.iter().map(|&p| p / 27).collect();
        let want = mpos.iter().map(|&p| marker_base(p as usize)).collect();
        (mpos, want, block)
    }

    struct Check {
        g: StrandGeom,
        mpos: Vec<i64>,
        want: Vec<u8>,
        fp: Vec<i64>,
        t: usize,
    }

    impl Check {
        fn new(fb: usize) -> Check {
            let (fp, t) = geometry(fb);
            let (mpos, want, block) = markers(fb);
            Check { g: strand_geometry(&fp, fb, &mpos, &block, t, 8, fb - 20).unwrap(), mpos, want, fp, t }
        }
        fn unconfirmed(&self, rd: &[u8], frame: &[u8]) -> usize {
            let n = frame.len();
            let (mut p, mut p2, mut o) = (vec![0u8; n], vec![0u8; n], vec![0u8; n]);
            strand_project(&self.g, &self.mpos, &self.want, rd, &mut p, &mut p2, &mut o);
            strand_unconfirmed(&self.g, &p, &p2, &o, frame)
        }
    }

    #[test]
    fn geometry_follows_marker_periods() {
        let c = Check::new(70);
        assert_eq!(c.g.segs.len(), 12);
        assert_eq!(c.g.segs.iter().map(|s| s.nb).sum::<usize>(), 70);
        assert_eq!(c.g.segs[0], StrandSeg { q0: 0, j0: 0, nb: 6, left: None, right: Some(0) });
        assert_eq!(c.g.segs[1].q0, 27);
        assert_eq!(c.g.segs[11].right, None);
        let (fp, t) = geometry(70);
        let (mpos, _, block) = markers(70);
        let mut bad = fp.clone();
        bad[2] += 1;                                     // a byte split by a gap: the check is never applied
        assert!(strand_geometry(&bad, 70, &mpos, &block, t, 8, 50).is_none());
        assert!(strand_geometry(&fp[..10], 70, &mpos, &block, t, 8, 50).is_none());
        let flat: Vec<i64> = (0..280).collect();         // no markers: one segment anchored by the read ends
        let g = strand_geometry(&flat, 70, &[], &[], 280, 8, 50).unwrap();
        assert_eq!(g.segs.len(), 1);
    }

    #[test]
    fn strand_check_confirms_noisy_copies_and_charges_indels() {
        let fb = 70;
        let c = Check::new(fb);
        let k = frame_of(1, fb);
        let s = strand(&c.fp, c.t, &k);
        assert_eq!(c.unconfirmed(&s, &k), 0);
        let mut sub = s.clone();                         // three substitutions in three frame bytes
        for q in [5usize, 100, 200] {
            sub[q] = (sub[q] + 1) & 3;
        }
        assert_eq!(c.unconfirmed(&sub, &k), 3);
        let mut mk = s.clone();                          // a substituted marker base: no shift change, nothing lost
        mk[c.mpos[4] as usize] = (mk[c.mpos[4] as usize] + 1) & 3;
        assert_eq!(c.unconfirmed(&mk, &k), 0);
        let mut del = s.clone();                         // a deletion inside the header: the byte holding it plus the
        del.remove(10);                                  // charge for the shift change
        assert_eq!(c.unconfirmed(&del, &k), 1 + SHIFT_CHARGE);
        let mut ins = s.clone();                         // an insertion and a deletion in two payload segments
        ins.insert(120, 2);
        ins.remove(190);
        assert!(c.unconfirmed(&ins, &k) <= 2 * (1 + SHIFT_CHARGE));
        let mut tail = s.clone();                        // an indel in a CRC + parity segment: that segment is unread
        tail.remove(290);
        assert!(c.unconfirmed(&tail, &k) >= 4);
        let mut n = s.clone();
        n[50] = 4;                                       // an N never agrees
        assert_eq!(c.unconfirmed(&n, &k), 1);
        assert!(c.unconfirmed(&s[..150], &k) > 9);       // positions past the read end never agree
        let rc: Vec<u8> = s.iter().rev().map(|&x| 3 - x).collect();
        let mut buf = Vec::new();
        assert_eq!(c.unconfirmed(oriented(&rc, true, &mut buf), &k), 0);
    }

    #[test]
    fn exact_length_fast_path_equals_the_general_projection() {
        // the general path on a copy of the read with one extra base past the template end (length differs, so the fast
        // path is not taken) must read the same bytes as the fast path on the template-length read, apart from the
        // last segment, whose end anchor then differs
        let fb = 70;
        let c = Check::new(fb);
        for seed in 0..50u64 {
            let k = frame_of(100 + seed, fb);
            let mut s = strand(&c.fp, c.t, &k);
            s[(seed as usize * 37) % c.t] = ((seed as u8) * 3) & 3;      // one substitution anywhere (marker or frame)
            if seed % 7 == 0 {
                s[40] = 4;                                              // an N
            }
            let (mut p1, mut q1, mut o1) = (vec![0u8; fb], vec![0u8; fb], vec![0u8; fb]);
            strand_project(&c.g, &c.mpos, &c.want, &s, &mut p1, &mut q1, &mut o1);
            let mut s2 = s.clone();
            s2.push(0);
            let (mut p2, mut q2, mut o2) = (vec![0u8; fb], vec![0u8; fb], vec![0u8; fb]);
            strand_project(&c.g, &c.mpos, &c.want, &s2, &mut p2, &mut q2, &mut o2);
            let last = c.g.segs.last().unwrap().j0;
            for j in 0..last {
                assert_eq!((o1[j], o1[j] == 0 || p1[j] == p2[j]), (o2[j], true), "seed {} byte {}", seed, j);
            }
        }
    }

    #[test]
    fn a_channel_indel_absorbs_at_most_its_own_segment() {
        // the bounded indel allowance: K' = K with a planted one-base shift inside payload segment 3 (bytes 18..24)
        // and 11 more differing bytes elsewhere (17 in all); a reading of K' with a channel deletion exactly at the
        // planted shift lines that segment up with K, but the allowance stops there: >= 11 bytes stay unconfirmed
        let fb = 70;
        let c = Check::new(fb);
        let k = frame_of(9, fb);
        let s = strand(&c.fp, c.t, &k);
        let q = c.fp[4 * 20] as usize + 1;                   // inside segment 3
        let end = c.fp[4 * 23 + 3] as usize;                 // its last frame base
        let mut s2 = s.clone();
        s2.insert(q, 3);                                     // planted shift: one base in, the segment's last out
        s2.remove(end + 1);
        let mut k2 = unstrand(&c.fp, &s2, fb);
        for j in [2usize, 5, 8, 30, 33, 36, 40, 44, 52, 60, 66] {
            k2[j] ^= 0x10;
        }
        let ham = k.iter().zip(&k2).filter(|(x, y)| x != y).count();
        assert!(ham >= 13, "{}", ham);
        let mut read = strand(&c.fp, c.t, &k2);
        read.remove(q);                                      // the channel deletes the planted base
        let u = c.unconfirmed(&read, &k);
        assert!(u >= 11 && u > 9, "{} unconfirmed", u);
        assert!(c.unconfirmed(&strand(&c.fp, c.t, &k2), &k) >= ham);     // and an error-free reading is plain Hamming
    }

    #[test]
    fn near_minimum_distance_pairs_need_margin_channel_errors() {
        // K' differs from K in exactly r + 1 = 17 bytes (the MDS minimum): an error-free reading of K' leaves 17
        // unconfirmed bytes; only after the channel turned 8 of them into K's bytes (the margin) does it pass budget 9
        let fb = 70;
        let c = Check::new(fb);
        let k = frame_of(3, fb);
        let mut k2 = k.clone();
        let diff: Vec<usize> = (0..17).map(|i| 2 + 4 * i).collect();
        for &j in &diff {
            k2[j] ^= 0x41;
        }
        let read = strand(&c.fp, c.t, &k2);
        assert_eq!(c.unconfirmed(&read, &k), 17);
        let mut conv = k2.clone();
        for &j in &diff[..7] {
            conv[j] = k[j];
        }
        assert_eq!(c.unconfirmed(&strand(&c.fp, c.t, &conv), &k), 10);
        conv[diff[7]] = k[diff[7]];
        assert_eq!(c.unconfirmed(&strand(&c.fp, c.t, &conv), &k), 9);
    }

    fn unstrand(fp: &[i64], s: &[u8], fb: usize) -> Vec<u8> {
        let mut fr = vec![0u8; fb];
        for (f, &q) in fp.iter().enumerate() {
            fr[f / 4] |= (s[q as usize] & 3) << (6 - 2 * (f % 4));
        }
        fr
    }

    #[test]
    fn review_probe_shifted_frame_far_in_hamming_is_not_confirmed() {
        // review finding: K' = K with one base inserted and one deleted inside the frame bases (the markers stay where
        // the encoder writes them) and an address bit flipped is >= r + 1 bytes from K; a per-segment shift search
        // confirmed an error-free reading of K' as a copy of K. Marker-anchored projection compares it unshifted.
        let fb = 70;
        let c = Check::new(fb);
        let k = frame_of(1, fb);
        let s = strand(&c.fp, c.t, &k);
        let mut seen = 0;
        for a in 10..60 {
            for b in a + 2..66 {
                let (qa, qb) = (c.fp[4 * a] as usize, c.fp[4 * b] as usize);
                let mut s2 = s.clone();
                s2.insert(qa, 0);
                s2.remove(qb + 1);
                let mut k2 = unstrand(&c.fp, &s2, fb);
                k2[9] ^= 1;
                let ham = k.iter().zip(&k2).filter(|(x, y)| x != y).count();
                if ham >= 17 {
                    seen += 1;
                    let u = c.unconfirmed(&strand(&c.fp, c.t, &k2), &k);
                    assert!(u >= ham, "K' ({}, {}) {} bytes away: {} unconfirmed", a, b, ham, u);
                }
            }
        }
        assert!(seen > 100);
    }

    #[test]
    fn indel_variant_of_a_verified_frame_is_not_confirmed() {
        // K' = K with its frame bases shifted by one base from byte 30 on (a planted shift), read with a channel
        // insertion exactly where the shift starts: the read's content then lines up with K again, but its markers
        // move with the insertion, so the projection reads the content at the markers' shift and K is not confirmed
        let fb = 70;
        let c = Check::new(fb);
        let k = frame_of(5, fb);
        let s = strand(&c.fp, c.t, &k);
        let q = c.fp[4 * 30] as usize;
        let mut s2 = s.clone();
        s2.remove(q);
        s2.insert(c.fp[4 * 60] as usize, 1);             // keep the length
        let k2 = unstrand(&c.fp, &s2, fb);
        let mut read = strand(&c.fp, c.t, &k2);
        read.insert(q, s[q]);                            // channel insertion restores K's content after q
        let u = c.unconfirmed(&read, &k);
        assert!(u > 9, "{} unconfirmed", u);
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
