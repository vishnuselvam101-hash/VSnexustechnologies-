//! Frame verification: the per-row semantics of `vnxdna.dnaenc.frame4.decode_frames` (V13 form, pinned to the V12
//! `decode_frames_reference`). Rows are independent: a row without flagged erasures whose descrambled CRC verifies is
//! clean; every other row is inner-RS decoded (errors + erasures; erasure flags dropped when more than r are set),
//! optionally retried errors-only when it had erasures and failed, then descrambled and CRC-checked again.
//! good = RS ok & CRC ok & version nibble == FRAME_VERSION & kind <= 1.

use crate::config::{Cfg, HEADER_BYTES};
use crate::ffi;

static CRC_TABLE: [u32; 256] = {
    let mut t = [0u32; 256];
    let mut n = 0;
    while n < 256 {
        let mut c = n as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { (c >> 1) ^ 0xEDB8_8320 } else { c >> 1 };
            k += 1;
        }
        t[n] = c;
        n += 1;
    }
    t
};

#[inline]
pub fn crc32(data: &[u8]) -> u32 {
    let mut c = 0xFFFF_FFFFu32;
    for &b in data {
        c = CRC_TABLE[((c ^ b as u32) & 0xFF) as usize] ^ (c >> 8);
    }
    c ^ 0xFFFF_FFFF
}

/// Verified header + payload of one frame.
#[derive(Clone, Copy, Default)]
pub struct Fields {
    pub kind: i64,
    pub tag: i64,
    pub group: i64,
    pub symbol: i64,
}

/// Descramble bytes 1..=span of `frame` into `plain` (span bytes) and return whether its CRC verifies.
#[inline]
fn descramble(cfg: &Cfg, frame: &[u8], plain: &mut [u8]) -> bool {
    let span = cfg.span;
    let ks = &cfg.ks[frame[0] as usize * span..frame[0] as usize * span + span];
    for j in 0..span {
        plain[j] = frame[1 + j] ^ ks[j];
    }
    let body = HEADER_BYTES - 1 + cfg.p;
    let crc = crc32(&plain[..body]);
    plain[body..body + 4] == crc.to_be_bytes()
}

#[inline]
fn fields_of(plain: &[u8]) -> Fields {
    Fields {
        kind: (plain[0] & 15) as i64,
        tag: ((plain[1] as i64) << 8) | plain[2] as i64,
        group: u32::from_be_bytes([plain[3], plain[4], plain[5], plain[6]]) as i64,
        symbol: u16::from_be_bytes([plain[7], plain[8]]) as i64,
    }
}

/// Scratch buffers reused across calls by one thread.
#[derive(Default)]
pub struct FrameScratch {
    plain: Vec<u8>,
    todo: Vec<usize>,
    cw: Vec<u8>,
    er: Vec<u8>,
    out: Vec<u8>,
    ok: Vec<u8>,
    errata: Vec<i64>,
    retry: Vec<usize>,
    cw2: Vec<u8>,
    out2: Vec<u8>,
    ok2: Vec<u8>,
    errata2: Vec<i64>,
}

/// Verify `n` received frames (`frames`: n x fb bytes, `erased`: n x fb per-byte flags). Writes `good[i]`, and for good
/// rows `fields[i]` and `payload[i * p..]`. `retry`: the errors-only retry of the exact-length path.
pub fn decode_frames(cfg: &Cfg, frames: &[u8], erased: &[u8], n: usize, retry: bool, s: &mut FrameScratch,
                     good: &mut [bool], fields: &mut [Fields], payload: &mut [u8], corr: &mut [u8])
                     -> Result<(), String> {
    let fb = cfg.fb;
    let span = cfg.span;
    let p = cfg.p;
    let r = cfg.r;
    s.plain.resize(span, 0);
    s.todo.clear();
    for i in 0..n {
        good[i] = false;
        let row = &frames[i * fb..(i + 1) * fb];
        let erow = &erased[i * fb..(i + 1) * fb];
        if erow.iter().all(|&e| e == 0) && descramble(cfg, row, &mut s.plain) {
            if accept(cfg, &s.plain, i, good, fields, payload) {
                corr[i * fb..(i + 1) * fb].copy_from_slice(row);
            }
        } else {
            s.todo.push(i);
        }
    }
    let m = s.todo.len();
    if m == 0 {
        return Ok(());
    }
    s.cw.clear();
    s.er.clear();
    let mut had_erasures = vec![false; m];
    for (k, &i) in s.todo.iter().enumerate() {
        s.cw.extend_from_slice(&frames[i * fb..(i + 1) * fb]);
        let erow = &erased[i * fb..(i + 1) * fb];
        let cnt = erow.iter().filter(|&&e| e != 0).count();
        if cnt > r {
            s.er.extend(std::iter::repeat(0u8).take(fb));
        } else {
            s.er.extend(erow.iter().map(|&e| (e != 0) as u8));
            had_erasures[k] = cnt > 0;
        }
    }
    s.out.resize(m * fb, 0);
    s.ok.resize(m, 0);
    s.errata.resize(m, 0);
    if r == 0 {
        s.out.copy_from_slice(&s.cw);
        s.ok.iter_mut().for_each(|o| *o = 1);
    } else {
        // SAFETY: cw/er/out are m x fb, ok/errata m entries, all owned here.
        let rc = unsafe {
            ffi::vnx_rs_decode_batch(m as i64, fb as i32, r as i32, s.cw.as_ptr(), s.er.as_ptr(), s.out.as_mut_ptr(),
                                     s.ok.as_mut_ptr(), s.errata.as_mut_ptr(), cfg.rs_level)
        };
        if rc != 0 {
            return Err(format!("inner RS kernel error {}", rc));
        }
        if retry {
            s.retry.clear();
            for k in 0..m {
                if s.ok[k] == 0 && had_erasures[k] {
                    s.retry.push(k);
                }
            }
            let q = s.retry.len();
            if q > 0 {
                s.cw2.clear();
                for &k in &s.retry {
                    s.cw2.extend_from_slice(&s.cw[k * fb..(k + 1) * fb]);
                }
                s.out2.resize(q * fb, 0);
                s.ok2.resize(q, 0);
                s.errata2.resize(q, 0);
                // SAFETY: as above, q rows.
                let rc = unsafe {
                    ffi::vnx_rs_decode_batch(q as i64, fb as i32, r as i32, s.cw2.as_ptr(), std::ptr::null(),
                                             s.out2.as_mut_ptr(), s.ok2.as_mut_ptr(), s.errata2.as_mut_ptr(),
                                             cfg.rs_level)
                };
                if rc != 0 {
                    return Err(format!("inner RS kernel error {}", rc));
                }
                for (j, &k) in s.retry.iter().enumerate() {
                    if s.ok2[j] != 0 {
                        s.out[k * fb..(k + 1) * fb].copy_from_slice(&s.out2[j * fb..(j + 1) * fb]);
                        s.ok[k] = 1;
                    }
                }
            }
        }
    }
    for k in 0..m {
        if s.ok[k] == 0 {
            continue;
        }
        let i = s.todo[k];
        let row = &s.out[k * fb..(k + 1) * fb];
        let mut plain = std::mem::take(&mut s.plain);
        if descramble(cfg, row, &mut plain) && accept(cfg, &plain, i, good, fields, payload) {
            corr[i * fb..(i + 1) * fb].copy_from_slice(row);
        }
        s.plain = plain;
    }
    let _ = p;
    Ok(())
}

#[inline]
fn accept(cfg: &Cfg, plain: &[u8], i: usize, good: &mut [bool], fields: &mut [Fields], payload: &mut [u8]) -> bool {
    if (plain[0] >> 4) != cfg.frame_version || (plain[0] & 15) > 1 {
        return false;
    }
    good[i] = true;
    fields[i] = fields_of(plain);
    let p = cfg.p;
    payload[i * p..(i + 1) * p].copy_from_slice(&plain[HEADER_BYTES - 1..HEADER_BYTES - 1 + p]);
    true
}

/// Header fields read without correction from the first 10 frame bytes (`vnxdna.dnaenc.frame4.tentative_address`):
/// None when the version nibble is not FRAME_VERSION.
#[inline]
pub fn tentative(cfg: &Cfg, hdr_bytes: &[u8]) -> Option<Fields> {
    let v = hdr_bytes[0] as usize;
    let ks = &cfg.ks[v * cfg.span..v * cfg.span + HEADER_BYTES - 1];
    let mut h = [0u8; HEADER_BYTES - 1];
    for j in 0..HEADER_BYTES - 1 {
        h[j] = hdr_bytes[1 + j] ^ ks[j];
    }
    if (h[0] >> 4) != cfg.frame_version {
        return None;
    }
    Some(fields_of(&h))
}

/// nt_to_bytes of 4 bases.
#[inline]
pub fn pack4(b: &[u8]) -> u8 {
    (b[0] << 6) | (b[1] << 4) | (b[2] << 2) | b[3]
}
