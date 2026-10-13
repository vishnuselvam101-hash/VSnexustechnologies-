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
