//! VNX-DNA integrity kernel (hybrid architecture, Rust, ABI 1).
//!
//! Batched CRC-32 (zlib / IEEE 802.3, reflected polynomial 0xEDB88320) over the rows of a strided byte matrix: the
//! frame CRC of encoding (`frame4.plain_rows`) and of decoding (`frame4.decode_frames`). The NumPy reference
//! `vnxdna.core.crc.crc32_rows` is the specification; the Python tests check bit-exact equality and `zlib.crc32`.
//!
//! Why Rust: an input-validation and integrity component on untrusted read data, where checked arithmetic, bounds-
//! checked slices and scoped threads (no shared mutable state, no data races by construction) are the requirement.
//! The C kernels keep the DP/alignment/RS hot paths.
//!
//! C ABI contract (all functions):
//! * the caller owns every buffer; nothing is retained after a call returns; no allocation crosses the boundary;
//! * every length/offset/capacity is validated with checked arithmetic before a slice is formed;
//! * return codes: >= 0 success, `VNX_RI_EARG` (-1) invalid argument, `VNX_RI_EPANIC` (-3) an internal panic was
//!   caught (`catch_unwind`); a panic never unwinds into the caller;
//! * no global mutable state; calls are thread-safe; results do not depend on the thread count.

use std::panic::{catch_unwind, AssertUnwindSafe};

pub const ABI_VERSION: i32 = 1;
pub const VNX_RI_EARG: i32 = -1;
pub const VNX_RI_EPANIC: i32 = -3;
pub const MAX_THREADS: i64 = 64;

/// Slicing-by-8 tables, computed at compile time.
const TABLES: [[u32; 256]; 8] = make_tables();

const fn make_tables() -> [[u32; 256]; 8] {
    let mut t = [[0u32; 256]; 8];
    let mut n = 0;
    while n < 256 {
        let mut c = n as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 { (c >> 1) ^ 0xEDB8_8320 } else { c >> 1 };
            k += 1;
        }
        t[0][n] = c;
        n += 1;
    }
    let mut s = 1;
    while s < 8 {
        let mut n = 0;
        while n < 256 {
            let prev = t[s - 1][n];
            t[s][n] = (prev >> 8) ^ t[0][(prev & 0xFF) as usize];
            n += 1;
        }
        s += 1;
    }
    t
}

/// CRC-32 of one buffer (safe Rust; the same value as zlib.crc32).
pub fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    let mut chunks = data.chunks_exact(8);
    for c in &mut chunks {
        let lo = crc ^ u32::from_le_bytes([c[0], c[1], c[2], c[3]]);
        crc = TABLES[7][(lo & 0xFF) as usize]
            ^ TABLES[6][((lo >> 8) & 0xFF) as usize]
            ^ TABLES[5][((lo >> 16) & 0xFF) as usize]
            ^ TABLES[4][(lo >> 24) as usize]
            ^ TABLES[3][c[4] as usize]
            ^ TABLES[2][c[5] as usize]
            ^ TABLES[1][c[6] as usize]
            ^ TABLES[0][c[7] as usize];
    }
    for &b in chunks.remainder() {
        crc = TABLES[0][((crc ^ b as u32) & 0xFF) as usize] ^ (crc >> 8);
    }
    crc ^ 0xFFFF_FFFF
}

/// Validated geometry of a strided row matrix.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rows {
    pub n: usize,
    pub width: usize,
    pub stride: usize,
}

impl Rows {
    /// Checks n, width, stride >= width and that the last row ends inside `data_len` bytes (checked arithmetic).
    pub fn new(n: i64, width: i64, stride: i64, data_len: i64) -> Option<Rows> {
        if n < 0 || width < 0 || stride < width || data_len < 0 {
            return None;
        }
        let (n, width, stride, len) = (
            usize::try_from(n).ok()?,
            usize::try_from(width).ok()?,
            usize::try_from(stride).ok()?,
            usize::try_from(data_len).ok()?,
        );
        if n > 0 {
            let end = (n - 1).checked_mul(stride)?.checked_add(width)?;
            if end > len {
                return None;
            }
        }
        Some(Rows { n, width, stride })
    }
}

/// CRC-32 of every row into `out` (safe Rust). Rows are split into at most `threads` contiguous ranges, each run on a
/// scoped thread that writes only its own disjoint part of `out`.
pub fn crc32_rows(rows: Rows, data: &[u8], out: &mut [u32], threads: usize) {
    let one = |first: usize, part: &mut [u32]| {
        for (k, o) in part.iter_mut().enumerate() {
            let s = (first + k) * rows.stride;
            *o = crc32(&data[s..s + rows.width]);
        }
    };
    let out = &mut out[..rows.n];
    let parts = threads.clamp(1, rows.n.max(1));
    if parts == 1 {
        one(0, out);
        return;
    }
    let size = (rows.n + parts - 1) / parts;
    std::thread::scope(|scope| {
        for (i, part) in out.chunks_mut(size).enumerate() {
            let one = &one;
            scope.spawn(move || one(i * size, part));
        }
    });
}

#[no_mangle]
pub extern "C" fn vnx_ri_abi_version() -> i32 {
    ABI_VERSION
}

/// CRC-32 of `n` rows of `width` bytes, row i starting at `data + i * stride`; `data_len` bytes are readable at
/// `data`, `out_len` u32 are writable at `out`. `threads` in 1..=64. Returns 0, VNX_RI_EARG or VNX_RI_EPANIC.
///
/// # Safety
/// `data` must point to `data_len` readable bytes and `out` to `out_len` writable u32 (either may be null when its
/// length is 0); the buffers must not overlap and must stay valid for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn vnx_ri_crc32_rows(n: i64, width: i64, stride: i64, data: *const u8, data_len: i64,
                                           out: *mut u32, out_len: i64, threads: i64) -> i32 {
    let rows = match Rows::new(n, width, stride, data_len) {
        Some(r) => r,
        None => return VNX_RI_EARG,
    };
    if !(1..=MAX_THREADS).contains(&threads) || out_len < n || (data_len > 0 && data.is_null())
        || (n > 0 && out.is_null()) {
        return VNX_RI_EARG;
    }
    if rows.n == 0 {
        return 0;
    }
    // SAFETY: lengths validated above; the caller guarantees validity and non-overlap (see # Safety).
    let data = std::slice::from_raw_parts(data, data_len as usize);
    let out = std::slice::from_raw_parts_mut(out, out_len as usize);
    match catch_unwind(AssertUnwindSafe(|| crc32_rows(rows, data, out, threads as usize))) {
        Ok(()) => 0,
        Err(_) => VNX_RI_EPANIC,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bitwise(data: &[u8]) -> u32 {
        let mut c = 0xFFFF_FFFFu32;
        for &b in data {
            c ^= b as u32;
            for _ in 0..8 {
                c = if c & 1 != 0 { (c >> 1) ^ 0xEDB8_8320 } else { c >> 1 };
            }
        }
        !c
    }

    #[test]
    fn known_vectors() {
        assert_eq!(crc32(b""), 0);
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(crc32(b"The quick brown fox jumps over the lazy dog"), 0x414F_A339);
    }

    #[test]
    fn slicing_equals_bitwise_for_every_length() {
        let data: Vec<u8> = (0..300u32).map(|i| (i.wrapping_mul(2_654_435_761) >> 13) as u8).collect();
        for len in 0..data.len() {
            assert_eq!(crc32(&data[..len]), bitwise(&data[..len]), "len {len}");
        }
    }

    #[test]
    fn rows_geometry_is_checked() {
        assert_eq!(Rows::new(3, 4, 5, 14), Some(Rows { n: 3, width: 4, stride: 5 }));
        assert_eq!(Rows::new(3, 4, 5, 13), None);
        assert_eq!(Rows::new(1, 5, 4, 100), None);
        assert_eq!(Rows::new(-1, 4, 4, 100), None);
        assert_eq!(Rows::new(i64::MAX, 1, i64::MAX, i64::MAX), None);
        assert_eq!(Rows::new(0, 0, 0, 0), Some(Rows { n: 0, width: 0, stride: 0 }));
    }

    #[test]
    fn threads_do_not_change_results() {
        let data: Vec<u8> = (0..(97 * 41)).map(|i: u32| (i * 7 + 3) as u8).collect();
        let rows = Rows::new(97, 37, 41, data.len() as i64).unwrap();
        let mut one = vec![0u32; 97];
        crc32_rows(rows, &data, &mut one, 1);
        for t in [2, 3, 8, 64, 200] {
            let mut many = vec![0u32; 97];
            crc32_rows(rows, &data, &mut many, t);
            assert_eq!(one, many);
        }
        for (i, c) in one.iter().enumerate() {
            assert_eq!(*c, crc32(&data[i * 41..i * 41 + 37]));
        }
    }

    #[test]
    fn ffi_rejects_bad_arguments() {
        let data = [0u8; 16];
        let mut out = [0u32; 4];
        unsafe {
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, data.as_ptr(), 16, out.as_mut_ptr(), 4, 1), 0);
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, data.as_ptr(), 15, out.as_mut_ptr(), 4, 1), VNX_RI_EARG);
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, data.as_ptr(), 16, out.as_mut_ptr(), 3, 1), VNX_RI_EARG);
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, data.as_ptr(), 16, out.as_mut_ptr(), 4, 0), VNX_RI_EARG);
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, data.as_ptr(), 16, out.as_mut_ptr(), 4, 65), VNX_RI_EARG);
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, std::ptr::null(), 16, out.as_mut_ptr(), 4, 1), VNX_RI_EARG);
            assert_eq!(vnx_ri_crc32_rows(4, 4, 4, data.as_ptr(), 16, std::ptr::null_mut(), 4, 1), VNX_RI_EARG);
            assert_eq!(vnx_ri_crc32_rows(0, 0, 0, std::ptr::null(), 0, std::ptr::null_mut(), 0, 1), 0);
        }
    }
}
