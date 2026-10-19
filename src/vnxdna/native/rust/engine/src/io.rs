//! IO layer: a read-only memory map of the input (zero copy; the scanner reads the page cache directly) with
//! sequential read-ahead advice, and release of the pages already consumed (`MADV_DONTNEED` on the file mapping drops
//! them from this process's resident set; they stay in the page cache), so the resident memory of a decode does not grow
//! with the input size. Inputs that cannot be mapped (pipes, empty files) use the buffered `read` path of the engine.

use std::fs::File;
use std::os::raw::{c_int, c_void};
use std::os::unix::io::AsRawFd;

extern "C" {
    fn mmap(addr: *mut c_void, len: usize, prot: c_int, flags: c_int, fd: c_int, off: i64) -> *mut c_void;
    fn munmap(addr: *mut c_void, len: usize) -> c_int;
    fn madvise(addr: *mut c_void, len: usize, advice: c_int) -> c_int;
}

const PROT_READ: c_int = 1;
const MAP_PRIVATE: c_int = 2;
const MADV_SEQUENTIAL: c_int = 2;
const MADV_WILLNEED: c_int = 3;
const MADV_DONTNEED: c_int = 4;
const PAGE: usize = 4096;

pub struct Map {
    ptr: *mut u8,
    len: usize,
    released: usize,
}

// SAFETY: the mapping is read-only and owned; it is only read through &[u8] slices.
unsafe impl Send for Map {}
unsafe impl Sync for Map {}

impl Map {
    /// Map a regular, non-empty file; None when the file cannot be mapped (the caller then reads it).
    pub fn open(f: &File) -> Option<Map> {
        let md = f.metadata().ok()?;
        if !md.file_type().is_file() || md.len() == 0 || md.len() > (isize::MAX as u64) {
            return None;
        }
        let len = md.len() as usize;
        // SAFETY: a fresh private read-only mapping of the whole file; checked against MAP_FAILED.
        let p = unsafe { mmap(std::ptr::null_mut(), len, PROT_READ, MAP_PRIVATE, f.as_raw_fd(), 0) };
        if p as isize == -1 || p.is_null() {
            return None;
        }
        // SAFETY: advice on our own mapping; failure is harmless.
        unsafe {
            madvise(p, len, MADV_SEQUENTIAL);
        }
        Some(Map { ptr: p as *mut u8, len, released: 0 })
    }

    pub fn bytes(&self) -> &[u8] {
        // SAFETY: ptr/len describe a live read-only mapping owned by self.
        unsafe { std::slice::from_raw_parts(self.ptr, self.len) }
    }

    pub fn len(&self) -> usize {
        self.len
    }

    /// Ask the kernel to start reading [from, from + len).
    pub fn will_need(&self, from: usize, len: usize) {
        let a = from / PAGE * PAGE;
        let e = (from + len).min(self.len);
        if e > a {
            // SAFETY: range inside the mapping, page-aligned start.
            unsafe {
                madvise(self.ptr.add(a) as *mut c_void, e - a, MADV_WILLNEED);
            }
        }
    }

    /// Drop the pages wholly before `upto` from the resident set (no slice into them may be used afterwards).
    pub fn release(&mut self, upto: usize) {
        let end = upto.min(self.len) / PAGE * PAGE;
        if end > self.released {
            // SAFETY: page-aligned range inside the mapping, not referenced by any live slice (caller contract).
            unsafe {
                madvise(self.ptr.add(self.released) as *mut c_void, end - self.released, MADV_DONTNEED);
            }
            self.released = end;
        }
    }
}

impl Drop for Map {
    fn drop(&mut self) {
        // SAFETY: unmapping our own mapping once.
        unsafe {
            munmap(self.ptr as *mut c_void, self.len);
        }
    }
}

const PROT_WRITE: c_int = 2;
const MAP_ANONYMOUS: c_int = 0x20;
const MADV_HUGEPAGE: c_int = 14;
const HUGE: usize = 2 << 20;

/// Marker for the element types a zero-filled table may hold (every all-zero bit pattern is the value 0).
pub trait Zeroable: Copy {}
impl Zeroable for u8 {}
impl Zeroable for u32 {}
impl Zeroable for u64 {}

/// A fixed-size, zero-initialised table of the lookup structures (fingerprint sets, Bloom filter, frame store).
///
/// Tables of 2 MiB or more are anonymous mappings aligned to 2 MiB and advised `MADV_HUGEPAGE`: their accesses are
/// random over tens of MiB, and with 4 KiB pages nearly every access also missed the TLB. Only the backing pages
/// change (zero-filled either way), never the contents, so every lookup and insert is unchanged. Smaller tables, and
/// any table whose mapping fails, are an ordinary vector.
pub struct ZeroTable<T: Zeroable> {
    ptr: *mut T,
    len: usize,
    map: Option<(*mut c_void, usize)>,
    vec: Vec<T>,
}

// SAFETY: the table owns its memory exclusively; shared access is read-only through &[T].
unsafe impl<T: Zeroable + Send> Send for ZeroTable<T> {}
unsafe impl<T: Zeroable + Sync> Sync for ZeroTable<T> {}

impl<T: Zeroable> ZeroTable<T> {
    /// (table bytes, mapping bytes) of a mapped table of `len` elements; None for a table below 2 MiB or for a size
    /// that overflows (checked: a saturated size could map less than the table needs; such a size takes the vector
    /// path, whose allocation fails loudly).
    fn map_size(len: usize) -> Option<(usize, usize)> {
        let bytes = len.checked_mul(std::mem::size_of::<T>())?;
        let map_len = bytes.checked_add(HUGE)?;
        (bytes >= HUGE).then_some((bytes, map_len))
    }

    pub fn new(len: usize) -> ZeroTable<T> {
        if let Some((bytes, map_len)) = Self::map_size(len) {
            // SAFETY: a fresh private anonymous read-write mapping, checked against MAP_FAILED; it is zero-filled.
            let p = unsafe { mmap(std::ptr::null_mut(), map_len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0) };
            if p as isize != -1 && !p.is_null() {
                let start = (p as usize + HUGE - 1) / HUGE * HUGE;
                // SAFETY: [start, start + bytes) lies inside the mapping (map_len = bytes + HUGE); advice only.
                unsafe {
                    madvise(start as *mut c_void, bytes, MADV_HUGEPAGE);
                }
                return ZeroTable { ptr: start as *mut T, len, map: Some((p, map_len)), vec: Vec::new() };
            }
        }
        let zero: T = unsafe { std::mem::zeroed() };
        let mut vec = vec![zero; len];
        ZeroTable { ptr: vec.as_mut_ptr(), len, map: None, vec }
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

impl<T: Zeroable> std::ops::Deref for ZeroTable<T> {
    type Target = [T];
    fn deref(&self) -> &[T] {
        // SAFETY: ptr/len describe memory owned by self (the mapping or the vector), initialised (zero-filled).
        unsafe { std::slice::from_raw_parts(self.ptr, self.len) }
    }
}

impl<T: Zeroable> std::ops::DerefMut for ZeroTable<T> {
    fn deref_mut(&mut self) -> &mut [T] {
        // SAFETY: as above, and &mut self gives exclusive access.
        unsafe { std::slice::from_raw_parts_mut(self.ptr, self.len) }
    }
}

impl<T: Zeroable> Drop for ZeroTable<T> {
    fn drop(&mut self) {
        if let Some((p, n)) = self.map {
            // SAFETY: unmapping our own mapping once.
            unsafe {
                munmap(p, n);
            }
        }
        let _ = &self.vec;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zero_table_is_zeroed_writable_and_sized() {
        for len in [0usize, 1, 1000, (HUGE / 8) + 3, 3 * HUGE / 8] {
            let mut t: ZeroTable<u64> = ZeroTable::new(len);
            assert_eq!(t.len(), len);
            assert!(t.iter().all(|&x| x == 0));
            if len > 1 {
                t[len - 1] = 7;
                t[0] = 5;
                assert_eq!((t[0], t[len - 1]), (5, 7));
            }
        }
        assert!(ZeroTable::<u64>::map_size(usize::MAX / 4).is_none());     // len * 8 overflows
        assert!(ZeroTable::<u8>::map_size(usize::MAX - 1).is_none());      // len + HUGE overflows
        assert_eq!(ZeroTable::<u64>::map_size(HUGE / 8), Some((HUGE, 2 * HUGE)));
        assert_eq!(ZeroTable::<u64>::map_size(10), None);                  // small: the vector path
        let b: ZeroTable<u8> = ZeroTable::new(3 * HUGE + 1);
        assert_eq!(b.len(), 3 * HUGE + 1);
        assert_eq!(b.as_ptr() as usize % HUGE, 0);
    }
}
