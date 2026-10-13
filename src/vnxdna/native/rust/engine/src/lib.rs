//! VNX-DNA V13 streaming decode engine (crate `vnx_engine`): pass 1 of a decode in one native call.
//!
//! C ABI (loaded with ctypes by `vnxdna.native.engine`):
//!   int vnx_eng_abi_version(void)
//!   int vnx_eng_pass1(const RawCfg *cfg, const char *reads_path, const char *spill_dir,
//!                     int64_t *stats, int64_t n_stats, char *err, int64_t err_cap)
//! Return codes: 0 ok; 1 input outside the engine's exact domain (the caller falls back); 2 cancelled; -1 bad
//! argument; -2 IO error; -3 kernel error; -4 panic (caught here, never unwinds into C). `err` receives a
//! NUL-terminated message (truncated to err_cap).
//!
//! Every per-read result equals `vnxdna.recovery.pass1._process` (default recovery configuration) for that read; the
//! address scheduler decides which reads are attempted (see docs/V13_ENGINE_ARCHITECTURE.md).

pub mod config;
pub mod engine;
mod ffi;
pub mod frame;
pub mod io;
pub mod pass1;
pub mod scan;
pub mod sched;

use std::ffi::CStr;
use std::os::raw::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;

pub const ABI_VERSION: i32 = 1;

pub const RC_OK: i32 = 0;
pub const RC_UNSUPPORTED: i32 = 1;
pub const RC_CANCELLED: i32 = 2;
pub const RC_EARG: i32 = -1;
pub const RC_EIO: i32 = -2;
pub const RC_EKERNEL: i32 = -3;
pub const RC_EPANIC: i32 = -4;

#[no_mangle]
pub extern "C" fn vnx_eng_abi_version() -> i32 {
    ABI_VERSION
}

/// Number of statistics slots `vnx_eng_pass1` fills.
#[no_mangle]
pub extern "C" fn vnx_eng_n_stats() -> i64 {
    engine::N_STATS as i64
}

fn write_err(err: *mut c_char, cap: i64, msg: &str) {
    if err.is_null() || cap <= 0 {
        return;
    }
    let bytes = msg.as_bytes();
    let n = bytes.len().min(cap as usize - 1);
    // SAFETY: the caller owns err with cap bytes; n + 1 <= cap.
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), err as *mut u8, n);
        *err.add(n) = 0;
    }
}

unsafe fn path_arg<'a>(p: *const c_char) -> Result<&'a Path, String> {
    if p.is_null() {
        return Err("null path".into());
    }
    CStr::from_ptr(p).to_str().map(Path::new).map_err(|_| "path is not UTF-8".to_string())
}

/// # Safety
/// `cfg` must point to a valid RawCfg (see config.rs), the paths to NUL-terminated strings, `stats` to `n_stats`
/// int64 and `err` to `err_cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn vnx_eng_pass1(cfg: *const config::RawCfg, reads_path: *const c_char, spill_dir: *const c_char,
                                       stats: *mut i64, n_stats: i64, err: *mut c_char, err_cap: i64) -> i32 {
    let res = catch_unwind(AssertUnwindSafe(|| -> (i32, String) {
        if stats.is_null() || n_stats < engine::N_STATS as i64 {
            return (RC_EARG, "stats buffer too small".into());
        }
        let cfg = match config::Cfg::from_raw(cfg) {
            Ok(c) => c,
            Err(e) => return (RC_EARG, e),
        };
        let (rp, sd) = match (path_arg(reads_path), path_arg(spill_dir)) {
            (Ok(a), Ok(b)) => (a, b),
            (Err(e), _) | (_, Err(e)) => return (RC_EARG, e),
        };
        let mut eng = match engine::Engine::new(&cfg, sd) {
            Ok(e) => e,
            Err(e) => return map_err(e),
        };
        let r = eng.run_file(rp);
        let out = std::slice::from_raw_parts_mut(stats, engine::N_STATS);
        out.copy_from_slice(&eng.stats);
        match r {
            Ok(()) => (RC_OK, String::new()),
            Err(e) => map_err(e),
        }
    }));
    match res {
        Ok((rc, msg)) => {
            write_err(err, err_cap, &msg);
            rc
        }
        Err(_) => {
            write_err(err, err_cap, "engine panic (caught at the C ABI)");
            RC_EPANIC
        }
    }
}

fn map_err(e: engine::EngErr) -> (i32, String) {
    match e {
        engine::EngErr::Unsupported(m) => (RC_UNSUPPORTED, m),
        engine::EngErr::Io(m) => (RC_EIO, m),
        engine::EngErr::Kernel(m) => (RC_EKERNEL, m),
        engine::EngErr::Cancelled => (RC_CANCELLED, "cancelled".into()),
    }
}

// keeps the ABI struct's field order checked at compile time against the binding's expectation (45 x 8 bytes)
const _: () = assert!(std::mem::size_of::<config::RawCfg>() == 45 * 8);

