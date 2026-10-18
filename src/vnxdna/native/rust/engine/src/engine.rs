//! The streaming pass-1 driver: IO blocks -> scan -> batch preparation (codes, orientation, header key) -> address
//! scheduler -> decode attempts -> spill records. Memory is bounded by one IO block, its carry and one batch.

use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use crate::config::{Cfg, HEADER_BYTES};
use crate::frame::tentative;
use crate::ffi;
use crate::io::Map;
use crate::pool::Pool;
use crate::pass1::{process, Fate, Flat, ProcOut, Scratch, PATH_FAST, PATH_SPEC, PATH_SYNC};
use crate::scan::{convert, Rec, Scanner};
use crate::sched::{block_firsts, frame_header, header_dist, header_raw, orient_estimate, oriented, strand_budget,
                   strand_geometry, strand_project, strand_unconfirmed, strand_windows, win_shifts, Bloom, FpMap, FrameStore,
                   Lossy, StrandGeom, WinMap, NFP, NS, NW, WIN_POS};

pub enum EngErr {
    /// input outside what the engine reproduces exactly: the caller falls back to the reference path
    Unsupported(String),
    Io(String),
    Kernel(String),
    Cancelled,
}

impl From<std::io::Error> for EngErr {
    fn from(e: std::io::Error) -> Self {
        EngErr::Io(e.to_string())
    }
}

// ---------------------------------------------------------------------------------------------------- statistics
pub const ST_READS: usize = 0;
pub const ST_FAST: usize = 1;
pub const ST_SYNC: usize = 2;
pub const ST_SPEC: usize = 3;
pub const ST_RC: usize = 4;
pub const ST_PENDING: usize = 5;
pub const ST_ORPHANS: usize = 6;
pub const ST_UNALIGNED: usize = 7;
pub const ST_RC_SKIPPED: usize = 8;
pub const ST_ATTEMPTED: usize = 9;
pub const ST_SKIPPED: usize = 10;
pub const ST_BYTES: usize = 11;
pub const ST_BATCHES: usize = 12;
pub const ST_ROUNDS: usize = 13;
pub const ST_NS_IO: usize = 14;
pub const ST_NS_SCAN: usize = 15;
pub const ST_NS_PREP: usize = 16;
pub const ST_NS_ATTEMPT: usize = 17;
pub const ST_NS_SPILL: usize = 18;
pub const ST_NS_TOTAL: usize = 19;
pub const ST_ACC_RECORDS: usize = 20;
pub const ST_MAX_BATCH: usize = 21;
pub const ST_TABLE_EVICTIONS: usize = 22;
pub const ST_TABLE_BYTES: usize = 23;
pub const ST_CHEAP: usize = 24;
pub const ST_FULL: usize = 25;
pub const ST_NS_CHEAP: usize = 26;
pub const ST_NS_FULL: usize = 27;
pub const ST_NS_GROUP: usize = 28;
pub const ST_SPLIT_GROUPS: usize = 29;
// scheduler decisions (docs/V14_SCHEDULER.md): every read not attempted has exactly one skip reason
pub const ST_SKIP_SCREEN_GROUP: usize = 32;
pub const ST_SKIP_CLAIM_HEADER: usize = 33;
pub const ST_SKIP_CLAIM_NEAR: usize = 34;
pub const ST_SKIP_CLAIM_DEFERRED: usize = 35;
pub const ST_SKIP_SCREEN_STRAND: usize = 36;
pub const ST_SKIP_CLAIM_STRAND: usize = 37;
pub const ST_STRAND_CHECKS: usize = 38;
pub const ST_STRAND_REJECTS: usize = 39;
pub const ST_REDUNDANT_ACCEPTS: usize = 40;
pub const ST_STORE_EVICTIONS: usize = 41;
pub const ST_SCREEN_CANDIDATES: usize = 42;
pub const ST_SKIP_OTHER: usize = 43;
pub const ST_SCHED_RESCUED: usize = 44;
pub const ST_STORE_PARITY_FIXED: usize = 45;
pub const ST_STORE_REJECTED: usize = 46;
pub const N_STATS: usize = 48;

// skip reasons (Batch::why)
const WHY_SCREEN_GROUP: u8 = 1;
const WHY_CLAIM_HEADER: u8 = 2;
const WHY_CLAIM_NEAR: u8 = 3;
const WHY_CLAIM_DEFERRED: u8 = 4;
const WHY_SCREEN_STRAND: u8 = 5;
const WHY_CLAIM_STRAND: u8 = 6;

// ---------------------------------------------------------------------------------------------------- thread pool
/// Run `f(chunk)` for chunks 0..nchunks on the pool's threads (the caller's included); chunks are claimed from an
/// atomic counter (dynamic balancing). Worker t uses `slots[t]` only.
fn par_chunks<S: Send, F>(pool: &Pool, nchunks: usize, slots: &mut [S], f: F) -> Result<(), EngErr>
where
    F: Fn(usize, &mut S) -> Result<(), EngErr> + Sync,
{
    let next = AtomicUsize::new(0);
    let err: Mutex<Option<EngErr>> = Mutex::new(None);
    let nslots = slots.len();
    let sp = SendPtr(slots.as_mut_ptr());
    let job = |t: usize| {
        if t >= nslots {
            return;
        }
        // SAFETY: worker t is the only user of slots[t] during this call.
        let slot = unsafe { &mut *sp.get().add(t) };
        loop {
            let c = next.fetch_add(1, Ordering::Relaxed);
            if c >= nchunks {
                break;
            }
            if let Err(e) = f(c, slot) {
                *err.lock().unwrap() = Some(e);
                next.store(nchunks, Ordering::Relaxed);
                break;
            }
        }
    };
    if nchunks <= 1 || pool.threads() == 1 {
        job(0);
    } else if !pool.run(&job) {
        return Err(EngErr::Kernel("panic in a worker thread".into()));
    }
    match err.into_inner().unwrap() {
        Some(e) => Err(e),
        None => Ok(()),
    }
}

/// The IO + scan driver: hands every window of complete records to `sink` (records index `buf`, or the scanner's side
/// buffer). Memory-mapped when `use_map` and the file can be mapped (consumed pages leave the resident set), else read
/// in blocks of `block` bytes behind the carried incomplete record. `cfg` (optional) is polled for cancellation.
pub fn scan_file<F>(path: &Path, fastq: bool, block: usize, use_map: bool, cfg: Option<&Cfg>, bytes: &mut u64,
                    ns_scan: &mut i64, mut sink: F) -> Result<(), EngErr>
where
    F: FnMut(&[u8], &[u8], &[Rec]) -> Result<(), EngErr>,
{
    let mut f = File::open(path)?;
    let mut scanner = Scanner::new(fastq);
    let mut recs: Vec<Rec> = Vec::new();
    let cancelled = || cfg.map_or(false, |c| c.cancelled());
    if use_map {
        if let Some(mut map) = Map::open(&f) {
            let len = map.len();
            *bytes = len as u64;
            let mut pos = 0usize;
            let mut win = block;
            while pos < len {
                if cancelled() {
                    return Err(EngErr::Cancelled);
                }
                let end = (pos + win).min(len);
                let eof = end == len;
                map.will_need(end, win);
                let ts = Instant::now();
                recs.clear();
                let data = &map.bytes()[pos..end];
                let used = scanner.scan(data, eof, &mut recs).map_err(EngErr::Unsupported)?;
                *ns_scan += ns(ts);
                if used == 0 && !eof {
                    win *= 2; // one record longer than the window
                    continue;
                }
                sink(data, &scanner.side, &recs)?;
                if eof && used < data.len() {
                    return Err(EngErr::Unsupported("unparsed trailing input".into()));
                }
                pos += used;
                map.release(pos);
            }
            return Ok(());
        }
    }
    let mut buf: Vec<u8> = vec![0; block.max(1)];
    let mut have = 0usize;
    let mut eof = false;
    loop {
        if cancelled() {
            return Err(EngErr::Cancelled);
        }
        if have == buf.len() {
            buf.resize(buf.len() * 2, 0);
        }
        while !eof && have < buf.len() {
            let k = f.read(&mut buf[have..])?;
            if k == 0 {
                eof = true;
            } else {
                have += k;
                *bytes += k as u64;
            }
        }
        let ts = Instant::now();
        recs.clear();
        let used = scanner.scan(&buf[..have], eof, &mut recs).map_err(EngErr::Unsupported)?;
        *ns_scan += ns(ts);
        sink(&buf[..have], &scanner.side, &recs)?;
        buf.copy_within(used..have, 0);
        have -= used;
        if eof {
            if have > 0 {
                return Err(EngErr::Unsupported("unparsed trailing input".into()));
            }
            return Ok(());
        }
    }
}

/// A raw pointer that may cross threads; every use writes a range no other thread writes.
#[derive(Clone, Copy)]
struct SendPtr<T>(*mut T);
unsafe impl<T> Send for SendPtr<T> {}
unsafe impl<T> Sync for SendPtr<T> {}

impl<T> SendPtr<T> {
    #[inline]
    fn get(&self) -> *mut T {
        self.0
    }
}

// ---------------------------------------------------------------------------------------------------- batch state
#[derive(Default)]
struct Slot {
    scratch: Scratch,
    pout: ProcOut,
    codes: Vec<u8>,
    quals: Vec<u8>,
    offs: Vec<i64>,
    lens: Vec<i64>,
    flip: Vec<bool>,
    flip8: Vec<u8>,
}

/// Per-read screen result (no correction applied): content fingerprints for grouping, the raw header reading, and
/// an attempt-order score.
#[derive(Clone, Copy)]
struct Screen {
    /// window fingerprints of the likelier orientation, window-major over SHIFTS (shift 0 first); 0 = none
    win: [u64; NFP],
    /// raw header bases of the likelier orientation and the address fingerprint they read (0 = not a valid header)
    hdr: u128,
    hk: u64,
    score: i64,
    /// orientation estimate of the screen (reverse complement more likely)
    rc: bool,
}

impl Default for Screen {
    fn default() -> Self {
        Screen { win: [0; NFP], hdr: 0, hk: 0, score: 0, rc: false }
    }
}

#[derive(Default)]
struct Batch {
    codes: Vec<u8>,
    quals: Vec<u8>,
    offs: Vec<i64>,
    lens: Vec<i64>,
    screen: Vec<Screen>,
    // outcome of attempted reads
    fate: Vec<Fate>,
    attempted: Vec<bool>,
    aligned_fail: Vec<bool>,
    header_erased: Vec<bool>,
    rc_used: Vec<bool>,
    path: Vec<u8>,
    vkey: Vec<u64>,
    vhdr: Vec<u128>,
    vwin: Vec<[u64; NW]>,
    fields: Vec<[i64; 4]>,
    payload: Vec<u8>,
    pend_alt: Vec<[i64; 4]>,
    proj: Vec<u8>,
    /// corrected frame bytes of accepted reads (n x fb)
    vframe: Vec<u8>,
    /// skip reason of reads not attempted (WHY_*; 0 = none)
    why: Vec<u8>,
    /// address of the verified frame a skipped read was counted as a copy of ([-1; 4] = not known)
    conf: Vec<[i64; 4]>,
    /// marker-anchored projections of the reads (n x fb bytes, n x fb read flags, computed flag), filled on demand
    pproj: Vec<u8>,
    pproj2: Vec<u8>,
    pok: Vec<u8>,
    pdone: Vec<u8>,
    /// store version at a read's last screen against the stored frames (u64::MAX = never screened)
    sver: Vec<u64>,
}

pub struct Engine<'c> {
    cfg: &'c Cfg,
    slots: Vec<Slot>,
    done: Lossy,
    windex: Lossy,
    bloom: Bloom,
    acc_w: Vec<BufWriter<File>>,
    pend_w: Vec<BufWriter<File>>,
    pub stats: [i64; N_STATS],
    b: Batch,
    dbg: u32,
    firsts: Vec<usize>,
    pool: Pool,
    gmap: FpMap,
    hmap: FpMap,
    // adaptive scheduler (cfg.strand): verified frames, window -> address candidates, strand-check geometry
    store: FrameStore,
    wmap: WinMap,
    geom: Option<StrandGeom>,
    budget: Option<usize>,
    /// number of insert events into the frame store / window map / Bloom filter (strand mode): a screen against an
    /// unchanged store repeats its earlier answer, so it is not run again
    store_version: u64,
    /// diagnostic per-read decision trace (VNX_ENG_TRACE=path; text, one line per read)
    trace: Option<BufWriter<File>>,
}

/// Add an address fingerprint to a screen candidate list (at most 4, no repeats).
#[inline]
fn push_cand(cand: &mut [u64; 4], nc: &mut usize, v: u64) {
    if *nc < cand.len() && !cand[..*nc].contains(&v) {
        cand[*nc] = v;
        *nc += 1;
    }
}

/// The marker-anchored projection of read `codes` (screen orientation `rc`) into `proj`/`ok`, computed once per read
/// (`done`): it depends on the read alone, so every frame the read is compared with reuses it.
#[allow(clippy::too_many_arguments)]
#[inline]
fn ensure_proj(cfg: &Cfg, g: &StrandGeom, codes: &[u8], rc: bool, buf: &mut Vec<u8>, proj: &mut [u8], proj2: &mut [u8],
               ok: &mut [u8], done: &mut u8) {
    if *done == 0 {
        let rd = oriented(codes, rc, buf);
        strand_project(g, &cfg.mpos, &cfg.want, rd, proj, proj2, ok);
        *done = 1;
    }
}

/// Strand screen of one read against the stored verified frames (adaptive scheduler): the candidates are the address
/// its raw header reads and the addresses of verified strands holding one of its windows (at most 4, `except` left
/// out); a candidate is only a lead, the strand check decides. Returns (frames checked, the address of the confirming
/// frame, read from its corrected header).
#[allow(clippy::too_many_arguments)]
fn strand_screen(cfg: &Cfg, store: &FrameStore, wmap: &WinMap, bloom: &Bloom, g: &StrandGeom, budget: usize,
                 sc: &Screen, codes: &[u8], except: u64, buf: &mut Vec<u8>, proj: &mut [u8], proj2: &mut [u8],
                 ok: &mut [u8], done: &mut u8) -> (usize, Option<[i64; 4]>) {
    let mut cand = [0u64; 4];
    let mut nc = 0usize;
    if sc.hk != 0 && sc.hk != except && bloom.maybe(sc.hk) {
        push_cand(&mut cand, &mut nc, sc.hk);
    }
    for &x in sc.win.iter() {
        if x != 0 && bloom.maybe(x) {
            if let Some(v) = wmap.get(x) {
                if v != except {
                    push_cand(&mut cand, &mut nc, v);
                }
            }
        }
    }
    let mut checked = 0;
    for &v in &cand[..nc] {
        let Some(fr) = store.get(v) else { continue };
        checked += 1;
        ensure_proj(cfg, g, codes, sc.rc, buf, proj, proj2, ok, done);
        if strand_unconfirmed(g, proj, proj2, ok, fr) <= budget {
            let a = tentative(cfg, &fr[..HEADER_BYTES]).map_or([-1; 4], |h| [h.kind, h.tag, h.group, h.symbol]);
            return (checked, Some(a));
        }
    }
    (checked, None)
}

fn ns(t: Instant) -> i64 {
    t.elapsed().as_nanos() as i64
}

impl<'c> Engine<'c> {
    pub fn new(cfg: &'c Cfg, spill_dir: &Path) -> Result<Engine<'c>, EngErr> {
        let mut acc_w = Vec::new();
        let mut pend_w = Vec::new();
        for b in 0..cfg.buckets {
            let o = |name: String| OpenOptions::new().append(true).create(true).open(spill_dir.join(name));
            acc_w.push(BufWriter::with_capacity(1 << 16, o(format!("acc{}.bin", b))?));
            pend_w.push(BufWriter::with_capacity(1 << 16, o(format!("pend{}.bin", b))?));
        }
        let slots = (0..cfg.threads).map(|_| Slot::default()).collect();
        let bits = cfg.table_bits as u32;
        // the adaptive scheduler keeps verified frames instead of the V13 fingerprint tables (similar memory)
        let (vb, sb) = if cfg.strand { (4, bits.saturating_sub(3)) } else { (bits, 2) };
        let geom = if cfg.strand {
            strand_geometry(&cfg.frame_pos64, cfg.fb, &cfg.mpos, &cfg.block, cfg.t, cfg.band, HEADER_BYTES + cfg.p)
        } else {
            None
        };
        let budget = geom.as_ref().and_then(|_| strand_budget(cfg.r));
        Ok(Engine { cfg, slots, done: Lossy::new(vb.saturating_sub(1)), windex: Lossy::new(vb),
                    bloom: Bloom::new(bits + 2), acc_w, pend_w,
                    store: FrameStore::new(sb, cfg.fb), wmap: WinMap::new(if cfg.strand { bits.saturating_sub(2) } else { 2 }),
                    geom, budget, store_version: 0,
                    trace: match std::env::var_os("VNX_ENG_TRACE") {
                        Some(p) => Some(BufWriter::new(OpenOptions::new().append(true).create(true).open(p)?)),
                        None => None,
                    },
                    stats: [0; N_STATS], b: Batch::default(),
                    dbg: std::env::var("VNX_ENG_DEBUG").ok().and_then(|v| v.parse().ok()).unwrap_or(0),
                    firsts: block_firsts(cfg), pool: Pool::new(cfg.threads), gmap: FpMap::new(), hmap: FpMap::new() })
    }

    /// Pass 1 over a whole read file: memory-mapped when possible, else read in blocks.
    pub fn run_file(&mut self, path: &Path) -> Result<(), EngErr> {
        let t0 = Instant::now();
        let cfg = self.cfg;
        let mut bytes = 0u64;
        let mut ns_scan = 0i64;
        scan_file(path, cfg.fastq, cfg.io_block, true, Some(cfg), &mut bytes, &mut ns_scan, |buf, side, recs| {
            let bsz = cfg.batch;
            let mut a = 0;
            while a < recs.len() {
                let e = (a + bsz).min(recs.len());
                self.batch(buf, side, &recs[a..e])?;
                a = e;
            }
            if self.stats[ST_READS] > cfg.max_reads {
                return Err(EngErr::Unsupported("more reads than max_reads".into()));
            }
            Ok(())
        })?;
        self.stats[ST_BYTES] = bytes as i64;
        self.stats[ST_NS_SCAN] = ns_scan;
        let tw = Instant::now();
        for w in self.acc_w.iter_mut().chain(self.pend_w.iter_mut()).chain(self.trace.iter_mut()) {
            w.flush()?;
        }
        self.stats[ST_NS_SPILL] += ns(tw);
        self.stats[ST_TABLE_EVICTIONS] = (self.done.evictions + self.windex.evictions + self.wmap.evictions) as i64;
        self.stats[ST_STORE_EVICTIONS] = self.store.evictions as i64;
        self.stats[ST_TABLE_BYTES] = (self.done.bytes() + self.windex.bytes() + self.bloom.bytes() + self.store.bytes()
                                      + self.wmap.bytes()) as i64;
        self.stats[ST_NS_TOTAL] = ns(t0);
        Ok(())
    }

    /// One batch of records: convert + screen, group, attempt in rounds, spill.
    fn batch(&mut self, buf: &[u8], side: &[u8], recs: &[Rec]) -> Result<(), EngErr> {
        let cfg = self.cfg;
        let n = recs.len();
        self.stats[ST_READS] += n as i64;
        self.stats[ST_BATCHES] += 1;
        self.stats[ST_MAX_BATCH] = self.stats[ST_MAX_BATCH].max(n as i64);
        let tp = Instant::now();
        let want_q = cfg.min_q > 0 && cfg.fastq;
        {
            let b = &mut self.b;
            b.offs.clear();
            b.lens.clear();
            let mut pos = 0i64;
            for r in recs {
                b.offs.push(pos);
                b.lens.push(r.len as i64);
                pos += r.len as i64;
            }
            b.codes.resize(pos as usize, 0);
            if want_q {
                b.quals.resize(pos as usize, 0);
            }
            b.screen.clear();
            b.screen.resize(n, Screen::default());
        }
        let chunk = 2048usize;
        let nchunks = (n + chunk - 1) / chunk;
        {
            let b = &mut self.b;
            let codes = SendPtr(b.codes.as_mut_ptr());
            let quals = SendPtr(b.quals.as_mut_ptr());
            let scr = SendPtr(b.screen.as_mut_ptr());
            let firsts = &self.firsts;
            let offs = &b.offs;
            let lens = &b.lens;
            let dedup = cfg.dedup;
            let t = cfg.t as i64;
            par_chunks(&self.pool, nchunks, &mut self.slots, |c, _slot: &mut Slot| {
                let a = c * chunk;
                let e = (a + chunk).min(n);
                let base = offs[a] as usize;
                let end = (offs[e - 1] + lens[e - 1]) as usize;
                // SAFETY: chunks cover disjoint, consecutive read ranges, so these sub-slices never overlap.
                let cs = unsafe { std::slice::from_raw_parts_mut(codes.get().add(base), end - base) };
                let mut qs = if want_q {
                    Some(unsafe { std::slice::from_raw_parts_mut(quals.get().add(base), end - base) })
                } else {
                    None
                };
                for i in a..e {
                    let r = &recs[i];
                    let src = if r.side { &side[r.seq..r.seq + r.len] } else { &buf[r.seq..r.seq + r.len] };
                    let o = offs[i] as usize - base;
                    convert(src, &mut cs[o..o + r.len]);
                    if let Some(q) = qs.as_mut() {
                        for (d, &s) in q[o..o + r.len].iter_mut().zip(buf[r.qual..r.qual + r.len].iter()) {
                            *d = (s as i16 - 33).clamp(0, 93) as u8;
                        }
                    }
                }
                if !dedup {
                    return Ok(());
                }
                for i in a..e {
                    let o = offs[i] as usize - base;
                    let rd = &cs[o..o + lens[i] as usize];
                    let mut sc = Screen::default();
                    let (rc, agree, total) = orient_estimate(cfg, rd, firsts);
                    sc.rc = rc;
                    sc.score = (rd.len() as i64 - t).abs() * 64 + (total - agree);
                    let (hdr, hk) = header_raw(cfg, rd, rc);
                    sc.hdr = hdr;
                    sc.hk = hk.unwrap_or(0);
                    for (w, &p) in WIN_POS.iter().enumerate() {
                        win_shifts(rd, w, p as i64, rc, &mut sc.win[w * NS..(w + 1) * NS]);
                    }
                    // SAFETY: read i belongs to this chunk only.
                    unsafe { *scr.get().add(i) = sc };
                }
                Ok(())
            })?;
        }
        self.stats[ST_NS_PREP] += ns(tp);
        let ta = Instant::now();
        {
            let b = &mut self.b;
            b.fate.clear();
            b.fate.resize(n, Fate::Dropped);
            b.attempted.clear();
            b.attempted.resize(n, false);
            b.aligned_fail.clear();
            b.aligned_fail.resize(n, false);
            b.header_erased.clear();
            b.header_erased.resize(n, false);
            b.rc_used.clear();
            b.rc_used.resize(n, false);
            b.path.clear();
            b.path.resize(n, 0);
            b.vkey.clear();
            b.vkey.resize(n, 0);
            b.vhdr.clear();
            b.vhdr.resize(n, 0);
            b.vwin.clear();
            b.vwin.resize(n, [0; NW]);
            b.fields.clear();
            b.fields.resize(n, [0; 4]);
            b.pend_alt.clear();
            b.pend_alt.resize(n, [0; 4]);
            b.payload.resize(n * cfg.p, 0);
            b.proj.resize(n * cfg.f, 0);
            b.vframe.resize(n * cfg.fb, 0);
            b.why.clear();
            b.why.resize(n, 0);
            b.conf.clear();
            b.conf.resize(n, [-1; 4]);
            if cfg.strand {
                b.pproj.resize(n * cfg.fb, 0);
                b.pproj2.resize(n * cfg.fb, 0);
                b.pok.resize(n * cfg.fb, 0);
                b.pdone.clear();
                b.pdone.resize(n, 0);
                b.sver.clear();
                b.sver.resize(n, u64::MAX);
            }
        }
        // ---- grouping (serial). Reads are linked by their content: a read joins every provisional group one of its
        // windows (shifts -1, 0, +1) matches a shift-0 window of; union-find merges such groups. Header readings never
        // link groups (one substitution turns an address into its neighbour); a read without any usable window joins
        // the group of an earlier read with the same header reading.
        let tg = Instant::now();
        let mut ranges: Vec<(usize, usize)> = Vec::new(); // claim c = members[ranges[c].0..ranges[c].1]
        let mut bulk_init: Vec<usize> = Vec::new(); // adaptive scheduler: claims attempted in bulk from round 2
        let mut members: Vec<usize> = Vec::with_capacity(n);
        if cfg.dedup {
            let mut parent: Vec<u32> = Vec::with_capacity(n);
            fn root(parent: &mut [u32], mut x: u32) -> u32 {
                while parent[x as usize] != x {
                    parent[x as usize] = parent[parent[x as usize] as usize];
                    x = parent[x as usize];
                }
                x
            }
            let local = &mut self.gmap;
            local.reset(n * NW);
            let by_hk = &mut self.hmap;
            by_hk.reset(n);
            let mut group_of: Vec<u32> = vec![u32::MAX; n];
            for i in 0..n {
                let sc = &self.b.screen[i];
                let mut g = u32::MAX;
                let mut any_win = false;
                // shift-0 windows first; the shifted readings only when no shift-0 window links the read
                for pass in 0..2 {
                    for (k, &x) in sc.win.iter().enumerate() {
                        if x == 0 || (k % NS == 0) != (pass == 0) {
                            continue;
                        }
                        any_win = true;
                        if let Some(h) = local.get(x) {
                            let r = root(&mut parent, h);
                            if g == u32::MAX {
                                g = r;
                            } else if r != g {
                                let (lo, hi) = if r < g { (r, g) } else { (g, r) };
                                parent[hi as usize] = lo;
                                g = lo;
                            }
                        }
                    }
                    if g != u32::MAX {
                        break;
                    }
                }
                if g == u32::MAX && !any_win && sc.hk != 0 {
                    if let Some(h) = by_hk.get(sc.hk) {
                        g = root(&mut parent, h);
                    }
                }
                if g == u32::MAX {
                    parent.push(parent.len() as u32);
                    g = parent.len() as u32 - 1;
                }
                group_of[i] = g;
                for (k, &x) in sc.win.iter().enumerate() {
                    if x != 0 && k % NS == 0 {
                        local.insert_first(x, g);
                    }
                }
                if sc.hk != 0 {
                    by_hk.insert_first(sc.hk, g);
                }
            }
            // CSR of the final groups, members in (score, read) order
            let mut cid: Vec<u32> = vec![u32::MAX; parent.len()];
            let mut count: Vec<usize> = Vec::new();
            for i in 0..n {
                let r = root(&mut parent, group_of[i]) as usize;
                if cid[r] == u32::MAX {
                    cid[r] = count.len() as u32;
                    count.push(0);
                }
                group_of[i] = cid[r];
                count[cid[r] as usize] += 1;
            }
            let mut cstart = vec![0usize];
            for &c in &count {
                cstart.push(cstart.last().unwrap() + c);
            }
            let mut mb = vec![0usize; n];
            let mut fill = cstart.clone();
            for i in 0..n {
                let c = group_of[i] as usize;
                mb[fill[c]] = i;
                fill[c] += 1;
            }
            let screen = &self.b.screen;
            for c in 0..count.len() {
                mb[cstart[c]..cstart[c + 1]].sort_by_key(|&i| (screen[i].score, i));
            }
            // ---- global screen per group (parallel, read-only tables): a group is skipped only on corroborated evidence
            // that a member is a copy of a verified strand: its raw header reads a verified address AND at least one of
            // its windows matches a window of that verified set (Bloom prefilter, then the table); a member whose
            // header is unreadable needs every usable window (two or more) to match. A window match alone never skips
            // a read: distinct strands can share a 24-nt window (padding, parity patterns), and a header key alone never
            // skips either: a raw header read with an error can name a neighbouring, verified address.
            let ng = count.len();
            let mut gskip = vec![false; ng];
            // adaptive scheduler: a per-read screen instead (below); a read is skipped only when the strand check confirms
            // it against a stored verified frame, and the rest of its group is attempted as usual
            let mut rskip = vec![false; if cfg.strand { n } else { 0 }];
            if cfg.strand {
                if let (0, Some(budget)) = (self.dbg & 2, self.budget) {
                    let rp = SendPtr(rskip.as_mut_ptr());
                    let cp = SendPtr(self.b.conf.as_mut_ptr());
                    let (store, wmap, g) = (&self.store, &self.wmap, self.geom.as_ref().unwrap());
                    let (codes, offs, lens) = (&self.b.codes, &self.b.offs, &self.b.lens);
                    let fb = cfg.fb;
                    let (pp, po, pd) = (SendPtr(self.b.pproj.as_mut_ptr()), SendPtr(self.b.pok.as_mut_ptr()),
                                        SendPtr(self.b.pdone.as_mut_ptr()));
                    let p2 = SendPtr(self.b.pproj2.as_mut_ptr());
                    let (sv, version) = (SendPtr(self.b.sver.as_mut_ptr()), self.store_version);
                    let (ncand, nchk, nrej) = (AtomicUsize::new(0), AtomicUsize::new(0), AtomicUsize::new(0));
                    let rch = 1024usize;
                    let bloom = &self.bloom;
                    par_chunks(&self.pool, (n + rch - 1) / rch, &mut self.slots, |c, _s: &mut Slot| {
                        let (mut k0, mut k1, mut k2) = (0usize, 0usize, 0usize);
                        let mut buf = Vec::new();
                        for j in c * rch..((c + 1) * rch).min(n) {
                            let rd = &codes[offs[j] as usize..(offs[j] + lens[j]) as usize];
                            // SAFETY: read j (its projection row and flag) belongs to this chunk only.
                            let (proj, proj2, ok, done) = unsafe {
                                (std::slice::from_raw_parts_mut(pp.get().add(j * fb), fb),
                                 std::slice::from_raw_parts_mut(p2.get().add(j * fb), fb),
                                 std::slice::from_raw_parts_mut(po.get().add(j * fb), fb), &mut *pd.get().add(j))
                            };
                            let (k, hit) = strand_screen(cfg, store, wmap, bloom, g, budget, &screen[j], rd, 0, &mut buf,
                                                         proj, proj2, ok, done);
                            k0 += (k > 0) as usize;
                            k1 += k;
                            k2 += k - hit.is_some() as usize;
                            // SAFETY: read j belongs to this chunk only.
                            unsafe {
                                *sv.get().add(j) = version;
                                *rp.get().add(j) = hit.is_some();
                                if let Some(a) = hit {
                                    *cp.get().add(j) = a;
                                }
                            }
                        }
                        ncand.fetch_add(k0, Ordering::Relaxed);
                        nchk.fetch_add(k1, Ordering::Relaxed);
                        nrej.fetch_add(k2, Ordering::Relaxed);
                        Ok(())
                    })?;
                    self.stats[ST_SCREEN_CANDIDATES] += ncand.load(Ordering::Relaxed) as i64;
                    self.stats[ST_STRAND_CHECKS] += nchk.load(Ordering::Relaxed) as i64;
                    self.stats[ST_STRAND_REJECTS] += nrej.load(Ordering::Relaxed) as i64;
                }
            } else {
                let gp = SendPtr(gskip.as_mut_ptr());
                let (done, windex, bloom) = (&self.done, &self.windex, &self.bloom);
                let (cs, mbr) = (&cstart, &mb);
                let gch = 1024usize;
                let dbg = self.dbg;
                par_chunks(&self.pool, (ng + gch - 1) / gch, &mut self.slots, |c, _s: &mut Slot| {
                    for g in c * gch..((c + 1) * gch).min(ng) {
                        let hit = dbg & 2 == 0 && mbr[cs[g]..cs[g + 1]].iter().any(|&i| {
                            let sc = &screen[i];
                            let (mut usable, mut matched) = (0usize, 0usize);
                            for w in 0..NW {
                                let ws = &sc.win[w * NS..(w + 1) * NS];
                                if ws.iter().any(|&x| x != 0) {
                                    usable += 1;
                                    if ws.iter().any(|&x| x != 0 && bloom.maybe(x) && windex.contains(x)) {
                                        matched += 1;
                                    }
                                }
                            }
                            if sc.hk != 0 {
                                matched >= 1 && bloom.maybe(sc.hk) && done.contains(sc.hk)
                            } else {
                                usable >= 2 && matched == usable
                            }
                        });
                        // SAFETY: group g belongs to this chunk only.
                        unsafe { *gp.get().add(g) = hit };
                    }
                    Ok(())
                })?;
            }
            // a group whose members read two or more distinct addresses, each from at least two reads, holds copies
            // of several strands with identical payload windows: it is split by address (single noisy readings join
            // the nearest supported address); any other group is one claim
            let mut keys: Vec<(u64, u128, usize)> = Vec::new();
            let mut kpos: std::collections::HashMap<u64, usize> = std::collections::HashMap::new();
            let mut fgrp: Vec<usize> = Vec::new();
            for g in 0..ng {
                if gskip[g] {
                    for &i in &mb[cstart[g]..cstart[g + 1]] {
                        self.b.why[i] = WHY_SCREEN_GROUP;
                    }
                    continue;
                }
                let grp: &[usize] = if cfg.strand {
                    fgrp.clear();
                    for &i in &mb[cstart[g]..cstart[g + 1]] {
                        if rskip[i] {
                            self.b.why[i] = WHY_SCREEN_STRAND;
                        } else {
                            fgrp.push(i);
                        }
                    }
                    if fgrp.is_empty() {
                        continue;
                    }
                    &fgrp
                } else {
                    &mb[cstart[g]..cstart[g + 1]]
                };
                keys.clear();
                kpos.clear();
                if grp.len() >= 4 {
                    for &i in grp {
                        let sc = &screen[i];
                        if sc.hk == 0 {
                            continue;
                        }
                        match kpos.get(&sc.hk) {
                            Some(&k) => keys[k].2 += 1,
                            None => {
                                kpos.insert(sc.hk, keys.len());
                                keys.push((sc.hk, sc.hdr, 1));
                            }
                        }
                    }
                    keys.retain(|k| k.2 >= 2);
                    kpos.clear();
                    for (k, key) in keys.iter().enumerate() {
                        kpos.insert(key.0, k);
                    }
                }
                if keys.len() < 2 {
                    let a = members.len();
                    members.extend_from_slice(grp);
                    ranges.push((a, members.len()));
                    continue;
                }
                self.stats[ST_SPLIT_GROUPS] += 1;
                let base = ranges.len();
                let mut parts: Vec<Vec<usize>> = vec![Vec::new(); keys.len()];
                let mut singles: Vec<usize> = Vec::new();
                for &i in grp {
                    let sc = &screen[i];
                    match kpos.get(&sc.hk) {
                        Some(&k) => parts[k].push(i),
                        // adaptive scheduler: reads without a supported address wait in one bulk claim (screened against
                        // the frames verified meanwhile, then attempted together); V13: the nearest supported address
                        None if cfg.strand => singles.push(i),
                        None => parts[(0..keys.len()).min_by_key(|&k| header_dist(keys[k].1, sc.hdr)).unwrap()].push(i),
                    }
                }
                for p in parts {
                    let a = members.len();
                    members.extend_from_slice(&p);
                    ranges.push((a, members.len()));
                }
                if !singles.is_empty() {
                    let a = members.len();
                    members.extend_from_slice(&singles);
                    ranges.push((a, members.len()));
                    bulk_init.push(ranges.len() - 1);
                }
                let _ = base;
            }
        } else {
            members.extend(0..n);
            ranges.extend((0..n).map(|i| (i, i + 1)));
        }
        self.stats[ST_NS_GROUP] += ns(tg);
        // ---- rounds. Cheap attempts (exact path + speculative projection) go through the members of every open claim,
        // best score first, one per round. When a member verifies address K, the claim closes for the members that
        // are copies of K by their header (same address, at most 2 differing header bases, or no valid header); members
        // reading a clearly different address (identical payload windows at another address) form a new claim. A claim
        // whose members all failed the cheap paths gets full attempts (DP, reverse-complement retry, pending records),
        // best first, one per round, until one verifies.
        // adaptive scheduler: the projections the claim closes will need (every member but the first of each claim; a
        // projection depends on its read alone), computed here in the thread pool instead of serially in the rounds
        if let (true, Some(g)) = (cfg.strand && cfg.dedup && self.budget.is_some(), self.geom.as_ref()) {
            // (a member whose read is identical to the claim's first read reuses that read's projection: same codes, same
            // screen orientation, same projection)
            let mut need: Vec<usize> = Vec::new();
            let rd_of = |b: &Batch, j: usize| -> std::ops::Range<usize> { b.offs[j] as usize..(b.offs[j] + b.lens[j]) as usize };
            for (ci, &(a, e)) in ranges.iter().enumerate() {
                if !bulk_init.contains(&ci) && e - a > 1 {
                    let first = members[a];
                    need.push(first);
                    for &j in &members[a + 1..e] {
                        if self.b.codes[rd_of(&self.b, j)] != self.b.codes[rd_of(&self.b, first)] {
                            need.push(j);
                        }
                    }
                }
            }
            let fb = cfg.fb;
            let b = &mut self.b;
            let (pp, po, pd) = (SendPtr(b.pproj.as_mut_ptr()), SendPtr(b.pok.as_mut_ptr()), SendPtr(b.pdone.as_mut_ptr()));
            let p2 = SendPtr(b.pproj2.as_mut_ptr());
            let (codes, offs, lens, screen) = (&b.codes, &b.offs, &b.lens, &b.screen);
            let need = &need;
            let pch = 1024usize;
            par_chunks(&self.pool, (need.len() + pch - 1) / pch, &mut self.slots, |c, _s: &mut Slot| {
                let mut buf = Vec::new();
                for &j in &need[c * pch..((c + 1) * pch).min(need.len())] {
                    let rd = &codes[offs[j] as usize..(offs[j] + lens[j]) as usize];
                    // SAFETY: every read appears in one claim only, so row j is written by one chunk only.
                    unsafe {
                        ensure_proj(cfg, g, rd, screen[j].rc, &mut buf,
                                    std::slice::from_raw_parts_mut(pp.get().add(j * fb), fb),
                                    std::slice::from_raw_parts_mut(p2.get().add(j * fb), fb),
                                    std::slice::from_raw_parts_mut(po.get().add(j * fb), fb), &mut *pd.get().add(j));
                    }
                }
                Ok(())
            })?;
        }
        let mut next: Vec<usize> = vec![0; ranges.len()];
        let mut full_next: Vec<usize> = vec![0; ranges.len()];
        let mut open: Vec<usize> = (0..ranges.len()).collect();
        let mut cheap_todo: Vec<usize> = Vec::new();
        let mut full_todo: Vec<usize> = Vec::new();
        let per_round = 1usize;
        let mut obuf: Vec<u8> = Vec::new();
        // adaptive scheduler state: bulk claims, first round of a claim, reads whose cheap attempt already failed
        let mut bulk: Vec<bool> = vec![false; ranges.len()];
        let mut start: Vec<usize> = vec![1; ranges.len()];
        for &ci in &bulk_init {
            bulk[ci] = true;
            start[ci] = 2;
        }
        let mut cheap_done: Vec<bool> = vec![false; if cfg.strand { n } else { 0 }];
        let mut round = 0usize;
        loop {
            cheap_todo.clear();
            full_todo.clear();
            round += 1;
            let mut waiting = false;
            for &ci in &open {
                let (a, e) = ranges[ci];
                let c = &members[a..e];
                if cfg.strand && cfg.dedup {
                    if start[ci] > round {
                        waiting = true;
                        continue;
                    }
                    // a read is attempted only after the screen against the frames verified so far (this batch's
                    // earlier rounds included) found no confirmed copy; a failed cheap attempt is never repeated
                    let budget = self.budget;
                    let b = &mut self.b;
                    let (store, wmap, bloom, geom) = (&self.store, &self.wmap, &self.bloom, &self.geom);
                    let fb = cfg.fb;
                    let version = self.store_version;
                    let mut screened = |j: usize, why: &mut [u8], conf: &mut [[i64; 4]], stats: &mut [i64; N_STATS]| -> bool {
                        let (Some(budget), Some(g)) = (budget, geom.as_ref()) else { return false };
                        // decision-identical shortcut: the store has not changed since this read's last screen
                        if b.sver[j] == version {
                            return false;
                        }
                        b.sver[j] = version;
                        let rd = &b.codes[b.offs[j] as usize..(b.offs[j] + b.lens[j]) as usize];
                        let (k, hit) = strand_screen(cfg, store, wmap, bloom, g, budget, &b.screen[j], rd, 0, &mut obuf,
                                                     &mut b.pproj[j * fb..(j + 1) * fb], &mut b.pproj2[j * fb..(j + 1) * fb],
                                                     &mut b.pok[j * fb..(j + 1) * fb], &mut b.pdone[j]);
                        stats[ST_STRAND_CHECKS] += k as i64;
                        stats[ST_STRAND_REJECTS] += (k - hit.is_some() as usize) as i64;
                        if let Some(a) = hit {
                            why[j] = WHY_SCREEN_STRAND;
                            conf[j] = a;
                        }
                        hit.is_some()
                    };
                    let resolved = |j: usize, why: &[u8], attempted: &[bool]| attempted[j] || why[j] != 0;
                    if bulk[ci] {
                        // cheap stage, then (next round, or at once when the cheap stage had nothing to attempt) the
                        // full stage for every member still unresolved
                        let mut pushed = false;
                        if next[ci] < c.len() {
                            for &j in c {
                                if resolved(j, &b.why, &b.attempted) || cheap_done[j] {
                                    continue;
                                }
                                if !screened(j, &mut b.why, &mut b.conf, &mut self.stats) {
                                    cheap_todo.push(j);
                                    pushed = true;
                                }
                            }
                            next[ci] = c.len();
                        }
                        if !pushed && full_next[ci] < c.len() {
                            for &j in c {
                                if !resolved(j, &b.why, &b.attempted) && !screened(j, &mut b.why, &mut b.conf, &mut self.stats) {
                                    full_todo.push(j);
                                }
                            }
                            full_next[ci] = c.len();
                        }
                        continue;
                    }
                    let mut picked = false;
                    while next[ci] < c.len() {
                        let j = c[next[ci]];
                        next[ci] += 1;
                        if resolved(j, &b.why, &b.attempted) || cheap_done[j] || screened(j, &mut b.why, &mut b.conf, &mut self.stats) {
                            continue;
                        }
                        cheap_todo.push(j);
                        picked = true;
                        break;
                    }
                    while !picked && full_next[ci] < c.len() {
                        let j = c[full_next[ci]];
                        full_next[ci] += 1;
                        if resolved(j, &b.why, &b.attempted) || screened(j, &mut b.why, &mut b.conf, &mut self.stats) {
                            continue;
                        }
                        full_todo.push(j);
                        picked = true;
                    }
                    continue;
                }
                if !cfg.dedup {
                    full_todo.extend_from_slice(c);
                    full_next[ci] = c.len();
                } else if next[ci] < c.len() {
                    let take = (c.len() - next[ci]).min(per_round);
                    cheap_todo.extend_from_slice(&c[next[ci]..next[ci] + take]);
                    next[ci] += take;
                } else if full_next[ci] < c.len() {
                    let take = (c.len() - full_next[ci]).min(1);
                    full_todo.extend_from_slice(&c[full_next[ci]..full_next[ci] + take]);
                    full_next[ci] += take;
                }
            }
            if cheap_todo.is_empty() && full_todo.is_empty() {
                if waiting {
                    continue;
                }
                break;
            }
            self.stats[ST_ROUNDS] += 1;
            if cfg.strand {
                for &j in &cheap_todo {
                    cheap_done[j] = true;
                }
            }
            for (todo, cheap) in [(&mut cheap_todo, true), (&mut full_todo, false)] {
                if todo.is_empty() {
                    continue;
                }
                todo.sort_unstable();
                self.stats[if cheap { ST_CHEAP } else { ST_FULL }] += todo.len() as i64;
                let tc = Instant::now();
                self.attempt(todo, cheap)?;
                self.stats[if cheap { ST_NS_CHEAP } else { ST_NS_FULL }] += ns(tc);
                // record the verified addresses and strand windows (prefetch the table lines first: the inserts are
                // random accesses into tables larger than the caches)
                if cfg.strand {
                    let fb = cfg.fb;
                    // a frame accepted on the clean path (no erasure, CRC verified) was never checked by the inner code,
                    // and the CRC does not cover the parity bytes: re-check every frame before it becomes a reference
                    let mut fresh: Vec<usize> = Vec::new();
                    let mut seen: std::collections::HashSet<u64> = std::collections::HashSet::new();
                    for &i in todo.iter() {
                        if self.b.fate[i] == Fate::Accepted {
                            let vk = self.b.vkey[i];
                            if self.store.get(vk).is_some() || !seen.insert(vk) {
                                self.stats[ST_REDUNDANT_ACCEPTS] += 1;
                            } else {
                                fresh.push(i);
                            }
                        }
                    }
                    let keep = self.codeword_check(&fresh)?;
                    if keep.iter().any(|&k| k) {
                        self.store_version += 1;
                    }
                    for (&i, &kept) in fresh.iter().zip(&keep) {
                        if kept {
                            let vk = self.b.vkey[i];
                            self.store.insert(vk, &self.b.vframe[i * fb..(i + 1) * fb]);
                            self.bloom.insert(vk);
                            for &w in &self.b.vwin[i] {
                                if w != 0 {
                                    self.wmap.insert(w, vk);
                                    self.bloom.insert(w);
                                }
                            }
                        }
                    }
                    continue;
                }
                for &i in todo.iter() {
                    if self.b.fate[i] == Fate::Accepted {
                        if self.done.contains(self.b.vkey[i]) {
                            self.stats[ST_REDUNDANT_ACCEPTS] += 1;
                        }
                        self.done.prefetch(self.b.vkey[i]);
                        for &w in &self.b.vwin[i] {
                            if w != 0 {
                                self.windex.prefetch(w);
                            }
                        }
                    }
                }
                for &i in todo.iter() {
                    if self.b.fate[i] == Fate::Accepted {
                        self.done.insert(self.b.vkey[i]);
                        self.bloom.insert(self.b.vkey[i]);
                        for &w in &self.b.vwin[i] {
                            if w != 0 {
                                self.windex.insert(w);
                                self.bloom.insert(w);
                            }
                        }
                    }
                }
            }
            if !cfg.dedup {
                break;
            }
            // close claims with a verified member; split off members that read another address: a group holds reads
            // that share payload windows, which distinct strands can do (one reading each, so the split by address
            // above did not apply), and such a member must still be attempted, never counted as a copy
            let mut still: Vec<usize> = Vec::with_capacity(open.len());
            for &ci in &open {
                let (a, e) = ranges[ci];
                if cfg.strand && (start[ci] > round || bulk[ci]) {
                    if start[ci] > round || full_next[ci] < e - a {
                        still.push(ci);
                    }
                    continue;
                }
                let tried = next[ci].max(full_next[ci]);
                let acc = members[a..a + tried].iter().copied().find(|&i| self.b.fate[i] == Fate::Accepted);
                match acc {
                    None => {
                        if full_next[ci] < e - a {
                            still.push(ci);
                        }
                    }
                    Some(i) if cfg.strand => {
                        // adaptive scheduler: every unresolved member (never attempted, or a failed cheap attempt) is
                        // closed only when the strand check confirms it against the verified frame; the others are split
                        // off and attempted, whatever their header reads
                        let fb = cfg.fb;
                        let mut other: Vec<usize> = Vec::new();
                        for &j in &members[a..e] {
                            if self.b.attempted[j] || self.b.why[j] != 0 {
                                continue;
                            }
                            // the reference is the STORED frame of the claim's address (re-checked as a codeword
                            // before it was stored), not the accepted read's own frame
                            let ok = match (self.budget, self.geom.as_ref(), self.store.get(self.b.vkey[i])) {
                                (Some(budget), Some(g), Some(fr)) => {
                                    self.stats[ST_STRAND_CHECKS] += 1;
                                    let b = &mut self.b;
                                    // a read identical to the verified member's read has the same projection
                                    let (rj, ri) = (b.offs[j] as usize..(b.offs[j] + b.lens[j]) as usize,
                                                    b.offs[i] as usize..(b.offs[i] + b.lens[i]) as usize);
                                    let src = if b.pdone[j] == 0 && b.codes[rj] == b.codes[ri] { i } else { j };
                                    let rd = &b.codes[b.offs[src] as usize..(b.offs[src] + b.lens[src]) as usize];
                                    ensure_proj(cfg, g, rd, b.screen[src].rc, &mut obuf,
                                                &mut b.pproj[src * fb..(src + 1) * fb],
                                                &mut b.pproj2[src * fb..(src + 1) * fb],
                                                &mut b.pok[src * fb..(src + 1) * fb], &mut b.pdone[src]);
                                    strand_unconfirmed(g, &b.pproj[src * fb..(src + 1) * fb],
                                                       &b.pproj2[src * fb..(src + 1) * fb],
                                                       &b.pok[src * fb..(src + 1) * fb], fr) <= budget
                                }
                                _ => false,
                            };
                            // not a copy of this claim's strand: maybe a copy of another verified strand (a read whose
                            // damaged header put it in this claim), checked against the stored frames
                            let ok = ok || match (self.budget, self.geom.as_ref()) {
                                (Some(budget), Some(g)) => {
                                    self.stats[ST_STRAND_REJECTS] += 1;
                                    let b = &mut self.b;
                                    let rd = &b.codes[b.offs[j] as usize..(b.offs[j] + b.lens[j]) as usize];
                                    let (k, hit) = strand_screen(cfg, &self.store, &self.wmap, &self.bloom, g, budget,
                                                                 &b.screen[j], rd, b.vkey[i], &mut obuf,
                                                                 &mut b.pproj[j * fb..(j + 1) * fb],
                                                                 &mut b.pproj2[j * fb..(j + 1) * fb],
                                                                 &mut b.pok[j * fb..(j + 1) * fb], &mut b.pdone[j]);
                                    self.stats[ST_STRAND_CHECKS] += k as i64;
                                    self.stats[ST_STRAND_REJECTS] += (k - hit.is_some() as usize) as i64;
                                    if let Some(a) = hit {
                                        self.b.conf[j] = a;
                                    }
                                    hit.is_some()
                                }
                                _ => false,
                            };
                            if ok {
                                self.b.why[j] = WHY_CLAIM_STRAND;
                                if self.b.conf[j][0] < 0 {
                                    self.b.conf[j] = self.b.fields[i];
                                }
                            } else {
                                other.push(j);
                            }
                        }
                        // the rest are reads of strands not verified yet: one claim per address read by two or more of
                        // them (as in the split before the rounds), the remaining ones together
                        if !other.is_empty() {
                            let mut parts: Vec<(u64, Vec<usize>)> = Vec::new();
                            if other.len() >= 4 {
                                for &j in &other {
                                    let hk = self.b.screen[j].hk;
                                    if hk == 0 {
                                        continue;
                                    }
                                    match parts.iter_mut().find(|p| p.0 == hk) {
                                        Some(p) => p.1.push(j),
                                        None => parts.push((hk, vec![j])),
                                    }
                                }
                                parts.retain(|p| p.1.len() >= 2);
                            }
                            let rest: Vec<usize> = other.iter().copied()
                                .filter(|&j| !parts.iter().any(|p| p.0 == self.b.screen[j].hk))
                                .collect();
                            // the rest: one bulk claim when it came from a split (several unverified strands), else one
                            // ordinary claim
                            let rest_bulk = other.len() >= 4;
                            // a member the strand check rejected goes straight to the full attempt (it runs the cheap
                            // paths first, with the same result for a read they accept: pass1::try_reads), instead of a
                            // cheap attempt followed by a full one
                            for &j in &other {
                                cheap_done[j] = true;
                            }
                            for (part, is_bulk) in parts.into_iter().map(|p| (p.1, false)).chain(std::iter::once((rest, rest_bulk))) {
                                if part.is_empty() {
                                    continue;
                                }
                                let s = members.len();
                                members.extend_from_slice(&part);
                                ranges.push((s, members.len()));
                                next.push(0);
                                full_next.push(0);
                                bulk.push(is_bulk);
                                start.push(round + 1);
                                still.push(ranges.len() - 1);
                                self.stats[ST_SPLIT_GROUPS] += 1;
                            }
                        }
                    }
                    Some(i) => {
                        let vk = self.b.vkey[i];
                        let href = self.b.screen[i].hdr;
                        let other: Vec<usize> = members[a + tried..e]
                            .iter()
                            .copied()
                            .filter(|&j| {
                                let sc = &self.b.screen[j];
                                if sc.hk != 0 { sc.hk != vk } else { header_dist(sc.hdr, href) > 2 }
                            })
                            .collect();
                        // skip reasons of the members this close leaves unattempted (V13 rule)
                        for &j in &members[a..a + tried] {
                            if !self.b.attempted[j] {
                                self.b.why[j] = WHY_CLAIM_DEFERRED;
                                self.b.conf[j] = self.b.fields[i];
                            }
                        }
                        for &j in &members[a + tried..e] {
                            let sc = &self.b.screen[j];
                            if sc.hk != 0 && sc.hk == vk {
                                self.b.why[j] = WHY_CLAIM_HEADER;
                                self.b.conf[j] = self.b.fields[i];
                            } else if sc.hk == 0 && header_dist(sc.hdr, href) <= 2 {
                                self.b.why[j] = WHY_CLAIM_NEAR;
                                self.b.conf[j] = self.b.fields[i];
                            }
                        }
                        if !other.is_empty() {
                            let s = members.len();
                            members.extend_from_slice(&other);
                            ranges.push((s, members.len()));
                            next.push(0);
                            full_next.push(0);
                            bulk.push(false);
                            start.push(0);
                            still.push(ranges.len() - 1);
                            self.stats[ST_SPLIT_GROUPS] += 1;
                        }
                    }
                }
            }
            open = still;
        }
        // safety net (adaptive scheduler): a read that is neither attempted nor skipped with a reason is attempted in full
        // here (never expected; counted)
        if cfg.strand && cfg.dedup {
            full_todo.clear();
            full_todo.extend((0..n).filter(|&j| !self.b.attempted[j] && self.b.why[j] == 0));
            if !full_todo.is_empty() {
                self.stats[ST_SCHED_RESCUED] += full_todo.len() as i64;
                self.stats[ST_FULL] += full_todo.len() as i64;
                let tc = Instant::now();
                self.attempt(&full_todo, false)?;
                self.stats[ST_NS_FULL] += ns(tc);
            }
        }
        self.stats[ST_NS_ATTEMPT] += ns(ta);
        // ---- statistics and spill records, in read order
        let tw = Instant::now();
        let p = cfg.p;
        let f = cfg.f;
        let bk = cfg.buckets;
        let mut rec: Vec<u8> = Vec::with_capacity(9 + 32 + f.max(p));
        if let Some(tw) = self.trace.as_mut() {
            // read index, decision (A accepted / P pending / D dropped / S<reason> skipped), address (accepted: verified;
            // skipped: the frame it was counted a copy of; else -1)
            let first = self.stats[ST_READS] - n as i64;
            for i in 0..n {
                let (d, a) = if !self.b.attempted[i] {
                    (format!("S{}", self.b.why[i]), self.b.conf[i])
                } else {
                    match self.b.fate[i] {
                        Fate::Accepted => ("A".to_string(), self.b.fields[i]),
                        Fate::Pending => ("P".to_string(), [-1; 4]),
                        _ => ("D".to_string(), [-1; 4]),
                    }
                };
                writeln!(tw, "{} {} {} {} {} {}", first + i as i64, d, a[0], a[1], a[2], a[3])?;
            }
        }
        for i in 0..n {
            if !self.b.attempted[i] {
                self.stats[ST_SKIPPED] += 1;
                self.stats[match self.b.why[i] {
                    WHY_SCREEN_GROUP => ST_SKIP_SCREEN_GROUP,
                    WHY_CLAIM_HEADER => ST_SKIP_CLAIM_HEADER,
                    WHY_CLAIM_NEAR => ST_SKIP_CLAIM_NEAR,
                    WHY_CLAIM_DEFERRED => ST_SKIP_CLAIM_DEFERRED,
                    WHY_SCREEN_STRAND => ST_SKIP_SCREEN_STRAND,
                    WHY_CLAIM_STRAND => ST_SKIP_CLAIM_STRAND,
                    _ => ST_SKIP_OTHER,
                }] += 1;
                continue;
            }
            self.stats[ST_ATTEMPTED] += 1;
            rec.clear();
            match self.b.fate[i] {
                Fate::Accepted => {
                    match self.b.path[i] {
                        PATH_FAST => self.stats[ST_FAST] += 1,
                        PATH_SYNC => self.stats[ST_SYNC] += 1,
                        PATH_SPEC => {
                            self.stats[ST_SYNC] += 1;
                            self.stats[ST_SPEC] += 1;
                        }
                        _ => {}
                    }
                    if self.b.rc_used[i] {
                        self.stats[ST_RC] += 1;
                    }
                    let fl = self.b.fields[i];
                    rec.push(fl[0] as u8);
                    rec.extend_from_slice(&(fl[1] as u16).to_be_bytes());
                    rec.extend_from_slice(&(fl[2] as u32).to_be_bytes());
                    rec.extend_from_slice(&(fl[3] as u16).to_be_bytes());
                    rec.extend_from_slice(&self.b.payload[i * p..(i + 1) * p]);
                    self.acc_w[(fl[2] as u64 % bk) as usize].write_all(&rec)?;
                    self.stats[ST_ACC_RECORDS] += 1;
                }
                Fate::Pending => {
                    self.stats[ST_PENDING] += 1;
                    if self.b.header_erased[i] {
                        self.stats[ST_ORPHANS] += 1;
                    }
                    let fl = self.b.fields[i];
                    rec.push(fl[0] as u8);
                    rec.extend_from_slice(&(fl[1] as u16).to_be_bytes());
                    rec.extend_from_slice(&(fl[2] as u32).to_be_bytes());
                    rec.extend_from_slice(&(fl[3] as u16).to_be_bytes());
                    for v in self.b.pend_alt[i] {
                        rec.extend_from_slice(&v.to_be_bytes());
                    }
                    rec.extend_from_slice(&self.b.proj[i * f..(i + 1) * f]);
                    self.pend_w[(fl[2] as u64 % bk) as usize].write_all(&rec)?;
                }
                Fate::Deferred => {}
                Fate::Dropped => {
                    if self.b.aligned_fail[i] {
                        if self.b.header_erased[i] {
                            self.stats[ST_ORPHANS] += 1;
                        }
                    } else {
                        self.stats[ST_UNALIGNED] += 1;
                    }
                }
            }
        }
        self.stats[ST_NS_SPILL] += ns(tw);
        Ok(())
    }

    /// Make the corrected frames of accepted reads `idx` inner-code codewords before they are stored as references: each
    /// is decoded by the inner RS code without erasures; a codeword stays as is, a frame whose parity bytes (only) were
    /// wrong gets the re-derived parity (`store_parity_fixed`), anything else is not stored (`store_rejected`). Returns
    /// which frames may be stored. The accepted records themselves are unchanged.
    fn codeword_check(&mut self, idx: &[usize]) -> Result<Vec<bool>, EngErr> {
        let cfg = self.cfg;
        let (fb, r, m) = (cfg.fb, cfg.r, idx.len());
        if m == 0 {
            return Ok(Vec::new());
        }
        if r == 0 {
            return Ok(vec![true; m]);
        }
        let mut cw = Vec::with_capacity(m * fb);
        for &i in idx {
            cw.extend_from_slice(&self.b.vframe[i * fb..(i + 1) * fb]);
        }
        let mut out = vec![0u8; m * fb];
        let mut ok = vec![0u8; m];
        let mut errata = vec![0i64; m];
        let (op, kp, ep) = (SendPtr(out.as_mut_ptr()), SendPtr(ok.as_mut_ptr()), SendPtr(errata.as_mut_ptr()));
        let cwr = &cw;
        let ch = 1024usize;
        par_chunks(&self.pool, (m + ch - 1) / ch, &mut self.slots, |c, _s: &mut Slot| {
            let (a, e) = (c * ch, ((c + 1) * ch).min(m));
            // SAFETY: rows a..e of cw/out and entries a..e of ok/errata, disjoint per chunk; no erasure array (null).
            let rc = unsafe {
                ffi::vnx_rs_decode_batch((e - a) as i64, fb as i32, r as i32, cwr[a * fb..].as_ptr(), std::ptr::null(),
                                         op.get().add(a * fb), kp.get().add(a), ep.get().add(a), cfg.rs_level)
            };
            if rc != 0 {
                return Err(EngErr::Kernel(format!("inner RS kernel error {}", rc)));
            }
            Ok(())
        })?;
        let msg = fb - r;
        let mut keep = vec![false; m];
        for (k, &i) in idx.iter().enumerate() {
            let (a, b) = (&cw[k * fb..(k + 1) * fb], &out[k * fb..(k + 1) * fb]);
            if ok[k] == 0 || a[..msg] != b[..msg] {
                self.stats[ST_STORE_REJECTED] += 1;
            } else {
                if a != b {
                    self.stats[ST_STORE_PARITY_FIXED] += 1;
                    self.b.vframe[i * fb..(i + 1) * fb].copy_from_slice(b);
                }
                keep[k] = true;
            }
        }
        Ok(keep)
    }

    /// Orient and run the per-read function on reads `todo` (ascending) of the batch, in parallel chunks. Each chunk
    /// copies its reads into thread-local buffers, runs the orientation scan there and flips the reads it selects.
    fn attempt(&mut self, todo: &[usize], cheap: bool) -> Result<(), EngErr> {
        let cfg = self.cfg;
        let m = todo.len();
        let per = (m / (cfg.threads * 4)).clamp(64, 1024);
        let nchunks = (m + per - 1) / per;
        let b = &mut self.b;
        let want_q = cfg.min_q > 0 && cfg.fastq;
        let codes = &b.codes;
        let quals = &b.quals;
        let offs = &b.offs;
        let lens = &b.lens;
        let out = (
            SendPtr(b.fate.as_mut_ptr()),
            SendPtr(b.attempted.as_mut_ptr()),
            SendPtr(b.aligned_fail.as_mut_ptr()),
            SendPtr(b.header_erased.as_mut_ptr()),
            SendPtr(b.rc_used.as_mut_ptr()),
            SendPtr(b.path.as_mut_ptr()),
            SendPtr(b.vkey.as_mut_ptr()),
            SendPtr(b.fields.as_mut_ptr()),
            SendPtr(b.pend_alt.as_mut_ptr()),
            SendPtr(b.payload.as_mut_ptr()),
            SendPtr(b.proj.as_mut_ptr()),
            SendPtr(b.vwin.as_mut_ptr()),
            SendPtr(b.vhdr.as_mut_ptr()),
            SendPtr(b.vframe.as_mut_ptr()),
        );
        let rc_skipped = AtomicUsize::new(0);
        par_chunks(&self.pool, nchunks, &mut self.slots, |c, slot: &mut Slot| {
            let sel = &todo[c * per..((c + 1) * per).min(m)];
            slot.codes.clear();
            slot.quals.clear();
            slot.offs.clear();
            slot.lens.clear();
            for &i in sel {
                let a = offs[i] as usize;
                let l = lens[i] as usize;
                slot.offs.push(slot.codes.len() as i64);
                slot.lens.push(l as i64);
                slot.codes.extend_from_slice(&codes[a..a + l]);
                if want_q {
                    slot.quals.extend_from_slice(&quals[a..a + l]);
                }
            }
            // orientation pre-pass (pass1._orientation, native kernel) and flips, on the local copies
            let k = sel.len();
            slot.flip8.clear();
            slot.flip8.resize(k, 0);
            if cfg.rc && !cfg.mpos.is_empty() {
                // SAFETY: offsets/lengths describe reads inside slot.codes; flip8 has k entries.
                let rc = unsafe {
                    ffi::vnx_or_scan(k as i64, slot.codes.as_ptr(), slot.codes.len() as i64, slot.lens.as_ptr(),
                                     slot.offs.as_ptr(), cfg.mpos.len() as i64, cfg.mpos.as_ptr(), cfg.want.as_ptr(),
                                     cfg.block.as_ptr(), cfg.nblocks as i64, cfg.orient_margin, cfg.orient_shift,
                                     slot.flip8.as_mut_ptr())
                };
                if rc != 0 {
                    return Err(EngErr::Kernel(format!("orientation kernel error {}", rc)));
                }
            }
            slot.flip.clear();
            for j in 0..k {
                let fl = slot.flip8[j] != 0;
                slot.flip.push(fl);
                if fl {
                    let a = slot.offs[j] as usize;
                    let l = slot.lens[j] as usize;
                    let rd = &mut slot.codes[a..a + l];
                    rd.reverse();
                    for c in rd.iter_mut() {
                        *c = if *c < 4 { 3 - *c } else { 4 };
                    }
                    if want_q {
                        slot.quals[a..a + l].reverse();
                    }
                }
            }
            let inp = Flat { codes: &slot.codes, quals: if want_q { Some(&slot.quals) } else { None }, offs: &slot.offs,
                             lens: &slot.lens };
            process(cfg, &inp, &slot.flip, &mut slot.scratch, &mut slot.pout, cheap).map_err(EngErr::Kernel)?;
            rc_skipped.fetch_add(slot.pout.rc_skipped as usize, Ordering::Relaxed);
            let po = &slot.pout;
            let p = cfg.p;
            let f = cfg.f;
            let fb = cfg.fb;
            for (j, &i) in sel.iter().enumerate() {
                let fate = po.fate[j];
                let mut vw = [0u64; NW];
                let mut vk = 0u64;
                let mut vh = 0u128;
                if fate == Fate::Accepted {
                    let fl = po.t.fields[j];
                    vk = crate::sched::key_fp(fl.kind, fl.tag, fl.group, fl.symbol);
                    vh = frame_header(&po.t.frame[j * fb..(j + 1) * fb]);
                    strand_windows(cfg, &po.t.frame[j * fb..(j + 1) * fb], &mut vw);
                }
                // SAFETY: read i belongs to exactly one chunk; every write below touches index i (or its row) only.
                unsafe {
                    *out.1.get().add(i) = fate != Fate::Deferred;
                    *out.4.get().add(i) = po.rc_used[j];
                    *out.3.get().add(i) = po.header_erased[j];
                    *out.2.get().add(i) = fate == Fate::Dropped && po.pend_fields[j] == [-2; 4];
                    if fate == Fate::Accepted {
                        let fl = po.t.fields[j];
                        *out.5.get().add(i) = po.t.path[j];
                        *out.6.get().add(i) = vk;
                        *out.12.get().add(i) = vh;
                        *out.11.get().add(i) = vw;
                        *out.7.get().add(i) = [fl.kind, fl.tag, fl.group, fl.symbol];
                        std::ptr::copy_nonoverlapping(po.t.payload[j * p..].as_ptr(), out.9.get().add(i * p), p);
                        std::ptr::copy_nonoverlapping(po.t.frame[j * fb..].as_ptr(), out.13.get().add(i * fb), fb);
                    } else if fate == Fate::Pending {
                        *out.7.get().add(i) = po.pend_fields[j];
                        *out.8.get().add(i) = po.pend_alt[j];
                        std::ptr::copy_nonoverlapping(po.t.proj[j * f..].as_ptr(), out.10.get().add(i * f), f);
                    }
                    *out.0.get().add(i) = fate;
                }
            }
            Ok(())
        })?;
        self.stats[ST_RC_SKIPPED] += rc_skipped.load(Ordering::Relaxed) as i64;
        Ok(())
    }
}
