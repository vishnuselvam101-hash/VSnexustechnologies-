//! The streaming pass-1 driver: IO blocks -> scan -> batch preparation (codes, orientation, header key) -> address
//! scheduler -> decode attempts -> spill records. Memory is bounded by one IO block, its carry and one batch.

use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use crate::config::Cfg;
use crate::ffi;
use crate::io::Map;
use crate::pool::Pool;
use crate::pass1::{process, Fate, Flat, ProcOut, Scratch, PATH_FAST, PATH_SPEC, PATH_SYNC};
use crate::scan::{convert, Rec, Scanner};
use crate::sched::{block_firsts, frame_header, header_dist, header_raw, orient_estimate, strand_windows, win_shifts, Bloom,
                   FpMap, Lossy, NFP, NS, NW, WIN_POS};

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
pub const N_STATS: usize = 32;

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
}

impl Default for Screen {
    fn default() -> Self {
        Screen { win: [0; NFP], hdr: 0, hk: 0, score: 0 }
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
        Ok(Engine { cfg, slots, done: Lossy::new(bits.saturating_sub(1)), windex: Lossy::new(bits),
                    bloom: Bloom::new(bits + 2), acc_w, pend_w,
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
        for w in self.acc_w.iter_mut().chain(self.pend_w.iter_mut()) {
            w.flush()?;
        }
        self.stats[ST_NS_SPILL] += ns(tw);
        self.stats[ST_TABLE_EVICTIONS] = (self.done.evictions + self.windex.evictions) as i64;
        self.stats[ST_TABLE_BYTES] = (self.done.bytes() + self.windex.bytes() + self.bloom.bytes()) as i64;
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
        }
        // ---- grouping (serial). Reads are linked by their content: a read joins every provisional group one of its
        // windows (shifts -1, 0, +1) matches a shift-0 window of; union-find merges such groups. Header readings never
        // link groups (one substitution turns an address into its neighbour); a read without any usable window joins
        // the group of an earlier read with the same header reading.
        let tg = Instant::now();
        let mut ranges: Vec<(usize, usize)> = Vec::new(); // claim c = members[ranges[c].0..ranges[c].1]
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
            // ---- global screen per group (parallel, read-only tables): a group is skipped when a member's window matches
            // a window of a verified strand (Bloom prefilter, then the table), or a member without usable windows reads
            // the header of a verified address
            let ng = count.len();
            let mut gskip = vec![false; ng];
            {
                let gp = SendPtr(gskip.as_mut_ptr());
                let (done, windex, bloom) = (&self.done, &self.windex, &self.bloom);
                let (cs, mbr) = (&cstart, &mb);
                let gch = 1024usize;
                let dbg = self.dbg;
                par_chunks(&self.pool, (ng + gch - 1) / gch, &mut self.slots, |c, _s: &mut Slot| {
                    for g in c * gch..((c + 1) * gch).min(ng) {
                        let hit = dbg & 2 == 0 && mbr[cs[g]..cs[g + 1]].iter().any(|&i| {
                            let sc = &screen[i];
                            if sc.win.iter().any(|&x| x != 0) {
                                sc.win.iter().any(|&x| x != 0 && bloom.maybe(x) && windex.contains(x))
                            } else {
                                sc.hk != 0 && bloom.maybe(sc.hk) && done.contains(sc.hk)
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
            for g in 0..ng {
                if gskip[g] {
                    continue;
                }
                let grp = &mb[cstart[g]..cstart[g + 1]];
                keys.clear();
                if grp.len() >= 4 {
                    for &i in grp {
                        let sc = &screen[i];
                        if sc.hk == 0 {
                            continue;
                        }
                        match keys.iter_mut().find(|k| k.0 == sc.hk) {
                            Some(k) => k.2 += 1,
                            None => keys.push((sc.hk, sc.hdr, 1)),
                        }
                    }
                    keys.retain(|k| k.2 >= 2);
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
                for &i in grp {
                    let sc = &screen[i];
                    let k = keys.iter().position(|k| k.0 == sc.hk).unwrap_or_else(|| {
                        (0..keys.len()).min_by_key(|&k| header_dist(keys[k].1, sc.hdr)).unwrap()
                    });
                    parts[k].push(i);
                }
                for p in parts {
                    let a = members.len();
                    members.extend_from_slice(&p);
                    ranges.push((a, members.len()));
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
        let mut next: Vec<usize> = vec![0; ranges.len()];
        let mut full_next: Vec<usize> = vec![0; ranges.len()];
        let mut open: Vec<usize> = (0..ranges.len()).collect();
        let mut cheap_todo: Vec<usize> = Vec::new();
        let mut full_todo: Vec<usize> = Vec::new();
        let per_round = 1usize;
        loop {
            cheap_todo.clear();
            full_todo.clear();
            for &ci in &open {
                let (a, e) = ranges[ci];
                let c = &members[a..e];
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
                break;
            }
            self.stats[ST_ROUNDS] += 1;
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
                for &i in todo.iter() {
                    if self.b.fate[i] == Fate::Accepted {
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
            // close claims with a verified member; split off members that read another address
            let mut still: Vec<usize> = Vec::with_capacity(open.len());
            for &ci in &open {
                let (a, e) = ranges[ci];
                let tried = next[ci].max(full_next[ci]);
                let acc = members[a..a + tried].iter().copied().find(|&i| self.b.fate[i] == Fate::Accepted);
                match acc {
                    None => {
                        if full_next[ci] < e - a {
                            still.push(ci);
                        }
                    }
                    Some(_) => {}
                }
            }
            open = still;
        }
        self.stats[ST_NS_ATTEMPT] += ns(ta);
        // ---- statistics and spill records, in read order
        let tw = Instant::now();
        let p = cfg.p;
        let f = cfg.f;
        let bk = cfg.buckets;
        let mut rec: Vec<u8> = Vec::with_capacity(9 + 32 + f.max(p));
        for i in 0..n {
            if !self.b.attempted[i] {
                self.stats[ST_SKIPPED] += 1;
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
