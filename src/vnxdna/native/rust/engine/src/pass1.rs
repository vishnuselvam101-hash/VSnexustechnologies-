//! The per-read pass-1 function of the default recovery configuration, over flat buffers: exactly what
//! `vnxdna.recovery.pass1._try` / `_process` (flat path, no smart/soft recovery, no quality-weighted consensus, no retry
//! band) compute for each read. A read's outcome never depends on the other reads of the call, so any subset of a batch
//! (the reads the address scheduler attempts) and any chunking give the same per-read results.

use crate::config::{Cfg, HEADER_BYTES, INF};
use crate::ffi;
use crate::frame::{decode_frames, pack4, tentative, Fields, FrameScratch};

pub const PATH_FAST: u8 = 1;
pub const PATH_SYNC: u8 = 2;
pub const PATH_SPEC: u8 = 5;

static RC: [u8; 5] = [3, 2, 1, 0, 4];

/// Reads given as ranges of a flat code buffer (codes 0..4), optional qualities over the same buffer.
pub struct Flat<'a> {
    pub codes: &'a [u8],
    pub quals: Option<&'a [u8]>,
    pub offs: &'a [i64],
    pub lens: &'a [i64],
}

/// Per-read outcome of `_try` (rows in input order).
#[derive(Default)]
pub struct TryOut {
    pub n: usize,
    pub acc: Vec<bool>,
    pub path: Vec<u8>,
    pub cost: Vec<i64>,
    pub fields: Vec<Fields>,
    pub payload: Vec<u8>,
    pub hard: Vec<u8>,
    pub proj: Vec<u8>,
    /// corrected frame bytes of accepted reads
    pub frame: Vec<u8>,
}

impl TryOut {
    fn reset(&mut self, cfg: &Cfg, n: usize) {
        self.n = n;
        self.acc.clear();
        self.acc.resize(n, false);
        self.path.clear();
        self.path.resize(n, 0);
        self.cost.clear();
        self.cost.resize(n, INF);
        self.fields.clear();
        self.fields.resize(n, Fields::default());
        self.payload.clear();
        self.payload.resize(n * cfg.p, 0);
        self.hard.clear();
        self.hard.resize(n * cfg.f, 0);
        self.proj.clear();
        self.proj.resize(n * cfg.f, 4);
        self.frame.clear();
        self.frame.resize(n * cfg.fb, 0);
    }
}

/// Thread-local scratch for the attempt stage.
#[derive(Default)]
pub struct Scratch {
    fs: FrameScratch,
    idx: Vec<usize>,
    offs: Vec<i64>,
    lens: Vec<i64>,
    hard: Vec<u8>,
    proj: Vec<u8>,
    frames: Vec<u8>,
    erb: Vec<u8>,
    mm: Vec<i64>,
    good: Vec<bool>,
    fields: Vec<Fields>,
    payload: Vec<u8>,
    corr: Vec<u8>,
    sok: Vec<u8>,
    sbases: Vec<u8>,
    serased: Vec<u8>,
    sind: Vec<i64>,
    sel: Vec<usize>,
    bases2: Vec<u8>,
    erased2: Vec<u8>,
    ok8: Vec<u8>,
    ins: Vec<i64>,
    del: Vec<i64>,
    dmm: Vec<i64>,
    dcost: Vec<i64>,
    has_q: Vec<u8>,
    // reverse-complement retry
    bound: Vec<i64>,
    rc_codes: Vec<u8>,
    rc_quals: Vec<u8>,
    rc_offs: Vec<i64>,
    rc_lens: Vec<i64>,
    retry_out: Option<Box<TryOut>>,
    retry_scratch: Option<Box<Scratch>>,
}

fn gather_offs(src: &[i64], idx: &[usize], out: &mut Vec<i64>) {
    out.clear();
    out.extend(idx.iter().map(|&i| src[i]));
}

/// `_try` (flat, default configuration) for every read of `inp`. With `cheap`, only the exact-length path and the
/// speculative projection run (the DP is skipped): a read they accept gets exactly the `_try` result (both run before the
/// DP there, and acceptances are final); the others must be attempted again in full before anything is kept of them.
pub fn try_reads(cfg: &Cfg, inp: &Flat, s: &mut Scratch, out: &mut TryOut, cheap: bool) -> Result<(), String> {
    let n = inp.lens.len();
    let f = cfg.f;
    let fb = cfg.fb;
    let p = cfg.p;
    let t = cfg.t as i64;
    out.reset(cfg, n);
    let qptr = |q: Option<&[u8]>| q.map_or(std::ptr::null(), |q| q.as_ptr());
    let quals_for_q = if cfg.min_q > 0 { inp.quals } else { None };
    let ncodes = inp.codes.len() as i64;
    // ---- exact-length path
    s.idx.clear();
    s.idx.extend((0..n).filter(|&i| inp.lens[i] == t));
    let mut hopeless = vec![false; n];
    if !s.idx.is_empty() {
        let m = s.idx.len();
        gather_offs(inp.offs, &s.idx, &mut s.offs);
        s.hard.resize(m * f, 0);
        s.proj.resize(m * f, 0);
        s.frames.resize(m * fb, 0);
        s.erb.resize(m * fb, 0);
        s.mm.resize(m, 0);
        // SAFETY: every output is sized m x f / m x fb / m; offs index reads of exactly T codes inside `codes`.
        let rc = unsafe {
            ffi::vnx_or_pack_exact(m as i64, inp.codes.as_ptr(), ncodes, s.offs.as_ptr(), qptr(quals_for_q), cfg.min_q,
                                   t, cfg.mpos.len() as i64, cfg.mpos.as_ptr(), cfg.want.as_ptr(), f as i64,
                                   cfg.frame_pos64.as_ptr(), s.hard.as_mut_ptr(), s.proj.as_mut_ptr(),
                                   s.frames.as_mut_ptr(), s.erb.as_mut_ptr(), s.mm.as_mut_ptr())
        };
        if rc != 0 {
            return Err(format!("pack_exact kernel error {}", rc));
        }
        s.good.resize(m, false);
        s.fields.resize(m, Fields::default());
        s.payload.resize(m * p, 0);
        s.corr.resize(m * fb, 0);
        decode_frames(cfg, &s.frames, &s.erb, m, true, &mut s.fs, &mut s.good, &mut s.fields, &mut s.payload, &mut s.corr)?;
        for (k, &i) in s.idx.iter().enumerate() {
            if s.good[k] {
                out.acc[i] = true;
                out.path[i] = PATH_FAST;
                out.fields[i] = s.fields[k];
                out.payload[i * p..(i + 1) * p].copy_from_slice(&s.payload[k * p..(k + 1) * p]);
                out.frame[i * fb..(i + 1) * fb].copy_from_slice(&s.corr[k * fb..(k + 1) * fb]);
            }
            out.cost[i] = s.mm[k] * cfg.c_mm;
            out.hard[i * f..(i + 1) * f].copy_from_slice(&s.hard[k * f..(k + 1) * f]);
            out.proj[i * f..(i + 1) * f].copy_from_slice(&s.proj[k * f..(k + 1) * f]);
            if !cfg.mpos.is_empty() && s.mm[k] == 0 {
                hopeless[i] = true;
            }
        }
    }
    let mut rest: Vec<usize> = (0..n).filter(|&i| !out.acc[i] && !hopeless[i]).collect();
    // ---- marker-anchored speculative projection (V13 B1)
    if !rest.is_empty() && cfg.spec && !cfg.mpos.is_empty() {
        let m = rest.len();
        gather_offs(inp.offs, &rest, &mut s.offs);
        gather_offs(inp.lens, &rest, &mut s.lens);
        s.sok.resize(m, 0);
        s.sbases.resize(m * f, 0);
        s.serased.resize(m * f, 0);
        s.mm.resize(m, 0);
        s.sind.resize(m, 0);
        // SAFETY: outputs sized for m reads; reads lie inside `codes` (checked by the kernel too).
        let rc = unsafe {
            ffi::vnx_or_spec_project(m as i64, inp.codes.as_ptr(), ncodes, s.lens.as_ptr(), s.offs.as_ptr(),
                                     qptr(quals_for_q), cfg.min_q, t, cfg.mpos.len() as i64, cfg.mpos.as_ptr(),
                                     cfg.want.as_ptr(), cfg.block.as_ptr(), cfg.nblocks as i64, f as i64,
                                     cfg.frame_pos64.as_ptr(), cfg.band, s.sok.as_mut_ptr(), s.sbases.as_mut_ptr(),
                                     s.serased.as_mut_ptr(), s.mm.as_mut_ptr(), s.sind.as_mut_ptr())
        };
        if rc != 0 {
            return Err(format!("spec_project kernel error {}", rc));
        }
        s.sel.clear();
        s.sel.extend((0..m).filter(|&k| s.sok[k] != 0));
        let q = s.sel.len();
        if q > 0 {
            s.bases2.resize(q * f, 0);
            s.erased2.resize(q * f, 0);
            for (j, &k) in s.sel.iter().enumerate() {
                s.bases2[j * f..(j + 1) * f].copy_from_slice(&s.sbases[k * f..(k + 1) * f]);
                s.erased2[j * f..(j + 1) * f].copy_from_slice(&s.serased[k * f..(k + 1) * f]);
            }
            pack_projection(cfg, q, &s.bases2, &s.erased2, &mut s.hard, &mut s.proj, &mut s.frames, &mut s.erb)?;
            s.good.resize(q, false);
            s.fields.resize(q, Fields::default());
            s.payload.resize(q * p, 0);
            s.corr.resize(q * fb, 0);
            decode_frames(cfg, &s.frames, &s.erb, q, false, &mut s.fs, &mut s.good, &mut s.fields, &mut s.payload, &mut s.corr)?;
            let indel = cfg.c_ins.min(cfg.c_del);
            for (j, &k) in s.sel.iter().enumerate() {
                if !s.good[j] {
                    continue;
                }
                let i = rest[k];
                out.acc[i] = true;
                out.path[i] = PATH_SPEC;
                out.fields[i] = s.fields[j];
                out.payload[i * p..(i + 1) * p].copy_from_slice(&s.payload[j * p..(j + 1) * p]);
                out.frame[i * fb..(i + 1) * fb].copy_from_slice(&s.corr[j * fb..(j + 1) * fb]);
                out.cost[i] = s.mm[k] * cfg.c_mm + s.sind[k] * indel;
                out.hard[i * f..(i + 1) * f].copy_from_slice(&s.hard[j * f..(j + 1) * f]);
                out.proj[i * f..(i + 1) * f].copy_from_slice(&s.proj[j * f..(j + 1) * f]);
            }
            rest.retain(|&i| !out.acc[i]);
        }
    }
    // ---- marker-template DP (sync path)
    if !rest.is_empty() && !cheap {
        let m = rest.len();
        s.bases2.clear();
        s.bases2.resize(m * f, 4);
        s.erased2.clear();
        s.erased2.resize(m * f, 1);
        s.ok8.clear();
        s.ok8.resize(m, 0);
        s.dcost.clear();
        s.dcost.resize(m, INF);
        // usable reads (|L - T| <= band) go to the DP kernel; the others stay unaligned (bases 4, all erased)
        s.sel.clear();
        s.sel.extend((0..m).filter(|&k| (inp.lens[rest[k]] - t).abs() <= cfg.band));
        let u = s.sel.len();
        if u > 0 {
            s.offs.clear();
            s.lens.clear();
            for &k in &s.sel {
                s.offs.push(inp.offs[rest[k]]);
                s.lens.push(inp.lens[rest[k]]);
            }
            let mut ub = vec![0u8; u * f];
            let mut ue = vec![0u8; u * f];
            let mut uok = vec![0u8; u];
            s.ins.resize(u, 0);
            s.del.resize(u, 0);
            s.dmm.resize(u, 0);
            let mut ucost = vec![0i64; u];
            let qp = qptr(quals_for_q);
            if quals_for_q.is_some() {
                s.has_q.clear();
                s.has_q.resize(u, 1);
            }
            let hq = if quals_for_q.is_some() { s.has_q.as_ptr() } else { std::ptr::null() };
            // SAFETY: outputs sized for u reads; read ranges validated by the kernel against total_len.
            let rc = unsafe {
                ffi::vnx_align_batch_sel_lv(u as i64, inp.codes.as_ptr(), s.offs.as_ptr(), s.lens.as_ptr(), ncodes, qp,
                                            hq, cfg.t as i32, cfg.tpl16.as_ptr(), cfg.seg_of.as_ptr(),
                                            cfg.prev_seg.as_ptr(), cfg.next_seg.as_ptr(), cfg.n_segments, f as i32,
                                            cfg.frame_pos32.as_ptr(), cfg.seg_frame.as_ptr(), cfg.band as i32,
                                            cfg.c_mm as i32, cfg.c_ins as i32, cfg.c_del as i32, cfg.c_x as i32,
                                            cfg.guard as i32, cfg.min_q as i32, ub.as_mut_ptr(), ue.as_mut_ptr(),
                                            uok.as_mut_ptr(), s.ins.as_mut_ptr(), s.del.as_mut_ptr(),
                                            s.dmm.as_mut_ptr(), ucost.as_mut_ptr(), cfg.align_lanes)
            };
            if rc != 0 {
                return Err(format!("align kernel error {}", rc));
            }
            for (j, &k) in s.sel.iter().enumerate() {
                s.bases2[k * f..(k + 1) * f].copy_from_slice(&ub[j * f..(j + 1) * f]);
                s.erased2[k * f..(k + 1) * f].copy_from_slice(&ue[j * f..(j + 1) * f]);
                s.ok8[k] = uok[j];
                s.dcost[k] = ucost[j];
            }
        }
        pack_projection(cfg, m, &s.bases2, &s.erased2, &mut s.hard, &mut s.proj, &mut s.frames, &mut s.erb)?;
        // frames of unaligned reads can never be accepted (ok = P.ok & pr.ok): verify the aligned ones only
        s.sel.clear();
        s.sel.extend((0..m).filter(|&k| s.ok8[k] != 0));
        let q = s.sel.len();
        if q > 0 {
            let mut fr = vec![0u8; q * fb];
            let mut er = vec![0u8; q * fb];
            for (j, &k) in s.sel.iter().enumerate() {
                fr[j * fb..(j + 1) * fb].copy_from_slice(&s.frames[k * fb..(k + 1) * fb]);
                er[j * fb..(j + 1) * fb].copy_from_slice(&s.erb[k * fb..(k + 1) * fb]);
            }
            s.good.resize(q, false);
            s.fields.resize(q, Fields::default());
            s.payload.resize(q * p, 0);
            s.corr.resize(q * fb, 0);
            decode_frames(cfg, &fr, &er, q, false, &mut s.fs, &mut s.good, &mut s.fields, &mut s.payload, &mut s.corr)?;
            for (j, &k) in s.sel.iter().enumerate() {
                if s.good[j] {
                    let i = rest[k];
                    out.acc[i] = true;
                    out.path[i] = PATH_SYNC;
                    out.fields[i] = s.fields[j];
                    out.payload[i * p..(i + 1) * p].copy_from_slice(&s.payload[j * p..(j + 1) * p]);
                    out.frame[i * fb..(i + 1) * fb].copy_from_slice(&s.corr[j * fb..(j + 1) * fb]);
                }
            }
        }
        for (k, &i) in rest.iter().enumerate() {
            out.cost[i] = if s.ok8[k] != 0 { s.dcost[k] } else { INF };
            out.hard[i * f..(i + 1) * f].copy_from_slice(&s.hard[k * f..(k + 1) * f]);
            out.proj[i * f..(i + 1) * f].copy_from_slice(&s.proj[k * f..(k + 1) * f]);
        }
    }
    Ok(())
}

fn pack_projection(cfg: &Cfg, n: usize, bases: &[u8], erased: &[u8], hard: &mut Vec<u8>, proj: &mut Vec<u8>,
                   frames: &mut Vec<u8>, erb: &mut Vec<u8>) -> Result<(), String> {
    let f = cfg.f;
    hard.resize(n * f, 0);
    proj.resize(n * f, 0);
    frames.resize(n * cfg.fb, 0);
    erb.resize(n * cfg.fb, 0);
    // SAFETY: inputs n x f, outputs n x f / n x f/4.
    let rc = unsafe {
        ffi::vnx_or_pack_projection(n as i64, bases.as_ptr(), erased.as_ptr(), f as i64, hard.as_mut_ptr(),
                                    proj.as_mut_ptr(), frames.as_mut_ptr(), erb.as_mut_ptr())
    };
    if rc != 0 {
        return Err(format!("pack_projection kernel error {}", rc));
    }
    Ok(())
}

/// What pass 1 keeps of one attempted read.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Fate {
    /// cheap attempt failed: nothing is kept, the read must be attempted in full
    Deferred,
    Accepted,
    Pending,
    Dropped,
}

/// `_process` output for one call (rows in input order).
#[derive(Default)]
pub struct ProcOut {
    pub t: TryOut,
    pub rc_used: Vec<bool>,
    pub fate: Vec<Fate>,
    pub pend_fields: Vec<[i64; 4]>,
    pub pend_alt: Vec<[i64; 4]>,
    pub header_erased: Vec<bool>,
    pub rc_skipped: u64,
}

fn hdr_fields(o: Option<Fields>) -> [i64; 4] {
    match o {
        Some(f) => [f.kind, f.tag, f.group, f.symbol],
        None => [-1, -1, -1, -1],
    }
}

/// `_process` minus the orientation pre-pass, for reads already oriented (`flip` = the orientation the scan chose).
/// With `cheap` (see try_reads): no DP, no reverse-complement retry and no pending records; reads not accepted are
/// `Deferred`.
pub fn process(cfg: &Cfg, inp: &Flat, flip: &[bool], s: &mut Scratch, out: &mut ProcOut, cheap: bool)
               -> Result<(), String> {
    let n = inp.lens.len();
    let f = cfg.f;
    let p = cfg.p;
    try_reads(cfg, inp, s, &mut out.t, cheap)?;
    out.rc_used.clear();
    out.rc_used.extend_from_slice(&flip[..n]);
    out.rc_skipped = 0;
    if cfg.rc && !cheap {
        let mut bad: Vec<usize> = (0..n).filter(|&i| !out.t.acc[i] && out.t.cost[i] > 2 * cfg.c_ins).collect();
        if !bad.is_empty() && cfg.rc_bounded {
            gather_offs(inp.offs, &bad, &mut s.offs);
            gather_offs(inp.lens, &bad, &mut s.lens);
            s.bound.resize(bad.len(), 0);
            // SAFETY: bad reads lie inside codes; out sized bad.len().
            let rc = unsafe {
                ffi::vnx_or_rc_bound(bad.len() as i64, inp.codes.as_ptr(), inp.codes.len() as i64, s.lens.as_ptr(),
                                     s.offs.as_ptr(), cfg.mpos.len() as i64, cfg.mpos.as_ptr(), cfg.want.as_ptr(),
                                     cfg.block.as_ptr(), cfg.nblocks as i64, cfg.band, cfg.c_mm, cfg.c_ins,
                                     cfg.c_del, s.bound.as_mut_ptr())
            };
            if rc != 0 {
                return Err(format!("rc_bound kernel error {}", rc));
            }
            let before = bad.len();
            let mut k = 0;
            bad.retain(|&i| {
                let keep = s.bound[k] < out.t.cost[i];
                k += 1;
                keep
            });
            out.rc_skipped = (before - bad.len()) as u64;
        }
        if !bad.is_empty() {
            // the retried reads reversed and complemented into a flat buffer (pass1._rc_flat)
            s.rc_codes.clear();
            s.rc_quals.clear();
            s.rc_offs.clear();
            s.rc_lens.clear();
            let mut pos = 0i64;
            for &i in &bad {
                let a = inp.offs[i] as usize;
                let l = inp.lens[i] as usize;
                s.rc_offs.push(pos);
                s.rc_lens.push(l as i64);
                pos += l as i64;
                s.rc_codes.extend(inp.codes[a..a + l].iter().rev().map(|&c| RC[c.min(4) as usize]));
                if let Some(q) = inp.quals {
                    s.rc_quals.extend(q[a..a + l].iter().rev());
                }
            }
            let mut o2 = s.retry_out.take().unwrap_or_default();
            let mut s2 = s.retry_scratch.take().unwrap_or_default();
            let rcq = if inp.quals.is_some() { Some(&s.rc_quals[..]) } else { None };
            let rin = Flat { codes: &s.rc_codes, quals: rcq, offs: &s.rc_offs, lens: &s.rc_lens };
            let res = try_reads(cfg, &rin, &mut s2, &mut o2, false);
            if let Err(e) = res {
                return Err(e);
            }
            for (j, &i) in bad.iter().enumerate() {
                if o2.acc[j] || o2.cost[j] < out.t.cost[i] {
                    let t = &mut out.t;
                    t.acc[i] = o2.acc[j];
                    t.fields[i] = o2.fields[j];
                    t.payload[i * p..(i + 1) * p].copy_from_slice(&o2.payload[j * p..(j + 1) * p]);
                    let fb = cfg.fb;
                    t.frame[i * fb..(i + 1) * fb].copy_from_slice(&o2.frame[j * fb..(j + 1) * fb]);
                    t.proj[i * f..(i + 1) * f].copy_from_slice(&o2.proj[j * f..(j + 1) * f]);
                    t.hard[i * f..(i + 1) * f].copy_from_slice(&o2.hard[j * f..(j + 1) * f]);
                    t.cost[i] = o2.cost[j];
                    t.path[i] = o2.path[j];
                    out.rc_used[i] = !out.rc_used[i];
                }
            }
            s.retry_out = Some(o2);
            s.retry_scratch = Some(s2);
        }
    }
    // pending: every failed but aligned read, with its two header readings
    out.fate.clear();
    out.pend_fields.clear();
    out.pend_alt.clear();
    out.header_erased.clear();
    out.fate.resize(n, Fate::Dropped);
    out.pend_fields.resize(n, [-1; 4]);
    out.pend_alt.resize(n, [-1; 4]);
    out.header_erased.resize(n, false);
    let hnt = 4 * HEADER_BYTES;
    let mut hb = [0u8; HEADER_BYTES];
    let mut raw = [0u8; 4 * HEADER_BYTES];
    for i in 0..n {
        if out.t.acc[i] {
            out.fate[i] = Fate::Accepted;
            continue;
        }
        if cheap {
            out.fate[i] = Fate::Deferred;
            continue;
        }
        if out.t.cost[i] >= INF {
            continue;
        }
        out.header_erased[i] = out.t.proj[i * f..i * f + hnt].iter().any(|&b| b > 3);
        for k in 0..HEADER_BYTES {
            hb[k] = pack4(&out.t.hard[i * f + 4 * k..i * f + 4 * k + 4]);
        }
        let ta = tentative(cfg, &hb);
        // second reading: the raw (flip-oriented) read prefix at the header's template positions, clipped to 3
        let a = inp.offs[i] as usize;
        let l = inp.lens[i];
        for k in 0..hnt {
            let pos = cfg.frame_pos64[k];
            raw[k] = if pos < l { inp.codes[a + pos as usize].min(3) } else { 0 };
        }
        for k in 0..HEADER_BYTES {
            hb[k] = pack4(&raw[4 * k..4 * k + 4]);
        }
        let alt = tentative(cfg, &hb);
        if ta.is_none() && alt.is_none() {
            out.fate[i] = Fate::Dropped;
            // still counted as pending-but-unaddressed (pass1: pend includes it, good excludes it)
            out.pend_fields[i] = [-2; 4];
            continue;
        }
        out.fate[i] = Fate::Pending;
        let (tf, af) = (hdr_fields(ta), hdr_fields(alt));
        out.pend_fields[i] = if ta.is_some() { tf } else { af };
        out.pend_alt[i] = if alt.is_some() { af } else { tf };
    }
    Ok(())
}
