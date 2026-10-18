//! Engine configuration: the C-ABI struct filled by the Python binding (vnxdna.native.engine) and its validated,
//! owned copy. Every pointer is read once, here, with the length the struct states, after the lengths were checked.

use std::slice;

pub const INF: i64 = 1 << 28;
pub const HEADER_BYTES: usize = 10;
pub const CRC_BYTES: usize = 4;
pub const MAX_READ_NT: usize = 100_000;
pub const MAX_LINE: usize = MAX_READ_NT * 2 + 4096;

/// Mirror of `vnxdna.native.engine._Cfg` (every field 8 bytes; order is the ABI).
#[repr(C)]
pub struct RawCfg {
    pub abi: i64,
    pub t: i64,
    pub frame_nt: i64,
    pub frame_bytes: i64,
    pub payload: i64,
    pub parity: i64,
    pub nm: i64,
    pub nblocks: i64,
    pub mpos: *const i64,
    pub want: *const u8,
    pub block: *const i64,
    pub frame_pos64: *const i64,
    pub tpl16: *const i16,
    pub seg_of: *const i32,
    pub prev_seg: *const i32,
    pub next_seg: *const i32,
    pub n_segments: i64,
    pub frame_pos32: *const i32,
    pub seg_frame: *const i32,
    pub band: i64,
    pub c_mm: i64,
    pub c_ins: i64,
    pub c_del: i64,
    pub c_x: i64,
    pub guard: i64,
    pub min_q: i64,
    pub rc: i64,
    pub rc_bounded: i64,
    pub spec: i64,
    pub orient_margin: i64,
    pub orient_shift: i64,
    pub ks: *const u8,
    pub span: i64,
    pub threads: i64,
    pub batch: i64,
    pub buckets: i64,
    pub align_lanes: i64,
    pub rs_level: i64,
    pub max_reads: i64,
    pub fmt: i64,
    pub dedup: i64,
    pub frame_version: i64,
    pub cancel: *const i64,
    pub io_block: i64,
    pub table_bits: i64,
}

#[derive(Clone)]
pub struct Cfg {
    pub t: usize,
    pub f: usize,
    pub fb: usize,
    pub p: usize,
    pub r: usize,
    pub mpos: Vec<i64>,
    pub want: Vec<u8>,
    pub block: Vec<i64>,
    pub nblocks: usize,
    pub frame_pos64: Vec<i64>,
    pub tpl16: Vec<i16>,
    pub seg_of: Vec<i32>,
    pub prev_seg: Vec<i32>,
    pub next_seg: Vec<i32>,
    pub n_segments: i32,
    pub frame_pos32: Vec<i32>,
    pub seg_frame: Vec<i32>,
    pub band: i64,
    pub c_mm: i64,
    pub c_ins: i64,
    pub c_del: i64,
    pub c_x: i64,
    pub guard: i64,
    pub min_q: i64,
    pub rc: bool,
    pub rc_bounded: bool,
    pub spec: bool,
    pub orient_margin: i64,
    pub orient_shift: i64,
    pub ks: Vec<u8>,
    pub span: usize,
    pub threads: usize,
    pub batch: usize,
    pub buckets: u64,
    pub align_lanes: i32,
    pub rs_level: i32,
    pub max_reads: i64,
    pub fastq: bool,
    pub dedup: bool,
    /// adaptive scheduler (raw dedup = 2): every skip needs the strand check (sched::strand_unconfirmed)
    pub strand: bool,
    pub frame_version: u8,
    pub cancel: usize, // address of an i64 the caller may set to non-zero (0 = none)
    pub io_block: usize,
    pub table_bits: i64,
    /// for every template position: frame base index (>= 0) or -(marker index) - 1
    pub tpl_map: Vec<i32>,
}

unsafe fn copy<T: Copy>(p: *const T, n: usize) -> Result<Vec<T>, String> {
    if n == 0 {
        return Ok(Vec::new());
    }
    if p.is_null() {
        return Err("null array pointer".into());
    }
    Ok(slice::from_raw_parts(p, n).to_vec())
}

fn rng(v: i64, lo: i64, hi: i64, what: &str) -> Result<i64, String> {
    if v < lo || v > hi {
        return Err(format!("{} = {} outside {}..{}", what, v, lo, hi));
    }
    Ok(v)
}

impl Cfg {
    /// # Safety
    /// `raw` must point to a RawCfg whose pointers are valid for the lengths its integer fields state.
    pub unsafe fn from_raw(raw: *const RawCfg) -> Result<Cfg, String> {
        if raw.is_null() {
            return Err("null config".into());
        }
        let c = &*raw;
        let t = rng(c.t, 1, 8192, "T")? as usize;
        let fb = rng(c.frame_bytes, 1, 255, "frame_bytes")? as usize;
        let f = rng(c.frame_nt, 4, 4 * 255, "frame_nt")? as usize;
        if f != 4 * fb || f > t {
            return Err("frame_nt must be 4 * frame_bytes and <= T".into());
        }
        let p = rng(c.payload, 1, 200, "payload")? as usize;
        let r = rng(c.parity, 0, 64, "parity")? as usize;
        if HEADER_BYTES + p + CRC_BYTES + r != fb {
            return Err("frame_bytes != header + payload + crc + parity".into());
        }
        let nm = rng(c.nm, 0, t as i64, "nm")? as usize;
        let nblocks = rng(c.nblocks, 0, nm as i64, "nblocks")? as usize;
        let span = rng(c.span, (HEADER_BYTES - 1) as i64, 256, "span")? as usize;
        if span != HEADER_BYTES - 1 + p + CRC_BYTES {
            return Err("span != 9 + payload + 4".into());
        }
        let n_segments = rng(c.n_segments, 1, f as i64, "n_segments")? as i32;
        let cfg = Cfg {
            t,
            f,
            fb,
            p,
            r,
            mpos: copy(c.mpos, nm)?,
            want: copy(c.want, nm)?,
            block: copy(c.block, nm)?,
            nblocks,
            frame_pos64: copy(c.frame_pos64, f)?,
            tpl16: copy(c.tpl16, t)?,
            seg_of: copy(c.seg_of, t)?,
            prev_seg: copy(c.prev_seg, t)?,
            next_seg: copy(c.next_seg, t)?,
            n_segments,
            frame_pos32: copy(c.frame_pos32, f)?,
            seg_frame: copy(c.seg_frame, f)?,
            band: rng(c.band, 1, 64, "band")?,
            c_mm: rng(c.c_mm, 0, 65536, "c_mm")?,
            c_ins: rng(c.c_ins, 0, 65536, "c_ins")?,
            c_del: rng(c.c_del, 0, 65536, "c_del")?,
            c_x: rng(c.c_x, 0, 65536, "c_x")?,
            guard: rng(c.guard, 0, 1024, "guard")?,
            min_q: rng(c.min_q, 0, 93, "min_q")?,
            rc: c.rc != 0,
            rc_bounded: c.rc_bounded != 0,
            spec: c.spec != 0,
            orient_margin: rng(c.orient_margin, 0, 1 << 20, "orient_margin")?,
            orient_shift: rng(c.orient_shift, 0, 64, "orient_shift")?,
            ks: copy(c.ks, 256 * span)?,
            span,
            threads: rng(c.threads, 1, 256, "threads")? as usize,
            batch: rng(c.batch, 64, 1 << 22, "batch")? as usize,
            buckets: rng(c.buckets, 1, 1 << 16, "buckets")? as u64,
            align_lanes: rng(c.align_lanes, 4, 16, "align_lanes")? as i32,
            rs_level: rng(c.rs_level, 0, 3, "rs_level")? as i32,
            max_reads: rng(c.max_reads, 0, i64::MAX, "max_reads")?,
            fastq: match c.fmt {
                1 => true,
                2 => false,
                _ => return Err("fmt must be 1 (FASTQ) or 2 (FASTA)".into()),
            },
            dedup: rng(c.dedup, 0, 2, "dedup")? != 0,
            strand: c.dedup == 2,
            frame_version: rng(c.frame_version, 0, 15, "frame_version")? as u8,
            cancel: c.cancel as usize,
            io_block: rng(c.io_block, 1 << 16, 1 << 30, "io_block")? as usize,
            table_bits: rng(c.table_bits, 10, 30, "table_bits")?,
            tpl_map: Vec::new(),
        };
        let mut cfg = cfg;
        for w in cfg.mpos.windows(2) {
            if w[1] <= w[0] {
                return Err("mpos not ascending".into());
            }
        }
        if cfg.mpos.iter().any(|&m| m < 0 || m >= t as i64) || cfg.frame_pos64.iter().any(|&x| x < 0 || x >= t as i64) {
            return Err("template position out of range".into());
        }
        if cfg.frame_pos32.iter().zip(cfg.frame_pos64.iter()).any(|(a, b)| *a as i64 != *b) {
            return Err("frame_pos32 != frame_pos64".into());
        }
        if cfg.block.iter().any(|&b| b < 0 || b >= nblocks as i64) {
            return Err("block index out of range".into());
        }
        let mut map = vec![i32::MIN; t];
        for (m, &p) in cfg.mpos.iter().enumerate() {
            map[p as usize] = -(m as i32) - 1;
        }
        for (f, &p) in cfg.frame_pos64.iter().enumerate() {
            map[p as usize] = f as i32;
        }
        if map.iter().any(|&x| x == i32::MIN) {
            return Err("template position neither frame nor marker".into());
        }
        cfg.tpl_map = map;
        if ![4, 8, 16].contains(&cfg.align_lanes) {
            return Err("align_lanes must be 4, 8 or 16".into());
        }
        Ok(cfg)
    }

    pub fn cancelled(&self) -> bool {
        if self.cancel == 0 {
            return false;
        }
        // SAFETY: the binding keeps the i64 alive for the whole call; volatile read of a flag another thread sets.
        unsafe { std::ptr::read_volatile(self.cancel as *const i64) != 0 }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn null_and_out_of_range_configs_are_refused() {
        // SAFETY: a null pointer is the documented refusal case.
        assert!(unsafe { Cfg::from_raw(std::ptr::null()) }.is_err());
        let raw = RawCfg {
            abi: 1, t: 0, frame_nt: 4, frame_bytes: 1, payload: 1, parity: 0, nm: 0, nblocks: 0,
            mpos: std::ptr::null(), want: std::ptr::null(), block: std::ptr::null(), frame_pos64: std::ptr::null(),
            tpl16: std::ptr::null(), seg_of: std::ptr::null(), prev_seg: std::ptr::null(), next_seg: std::ptr::null(),
            n_segments: 1, frame_pos32: std::ptr::null(), seg_frame: std::ptr::null(), band: 6, c_mm: 1, c_ins: 1,
            c_del: 1, c_x: 1, guard: 1, min_q: 0, rc: 1, rc_bounded: 1, spec: 1, orient_margin: 2, orient_shift: 2,
            ks: std::ptr::null(), span: 14, threads: 1, batch: 64, buckets: 1, align_lanes: 4, rs_level: 0,
            max_reads: 1, fmt: 1, dedup: 1, frame_version: 4, cancel: std::ptr::null(), io_block: 1 << 16,
            table_bits: 10,
        };
        // SAFETY: every array length is 0 or refused before the pointer is read (T = 0 fails first).
        let err = match unsafe { Cfg::from_raw(&raw) } {
            Ok(_) => panic!("T = 0 must be refused"),
            Err(e) => e,
        };
        assert!(err.contains("T"), "{}", err);
        assert_eq!(std::mem::size_of::<RawCfg>(), 45 * 8);
    }
}
