//! The kernel boundary of the block device (docs/V14_DEVICE_INTERFACE.md §4, unsafe-boundary audit §8): the
//! `/dev/nbdN` ioctls and the signal mask. This is the ONLY module of the crate with `unsafe` code, and every
//! `unsafe` block calls one C library function declared below with arguments whose validity is argued in place.
//!
//! The C functions come from the C library that the Rust standard library already links (no new dependency):
//!
//! | function | used for | argument contract |
//! |---|---|---|
//! | `ioctl(int, unsigned long, ...)` | the NBD_* requests | `fd` is the open device file held by [`NbdDevice`] for as long as the call runs; the third argument is an integer passed by value (no pointer is ever passed to the kernel) |
//! | `sigemptyset`, `sigaddset` | build a signal set | pointer to a `SigSet` owned by the caller, sized as glibc's and musl's `sigset_t` (128 bytes) |
//! | `pthread_sigmask(int, const sigset_t*, sigset_t*)` | block INT/TERM/HUP in every thread | pointers to owned `SigSet`s; the old-set pointer may be null |
//! | `sigwait(const sigset_t*, int*)` | the signal thread waits for them | pointers to an owned `SigSet` and an owned `c_int` |
//!
//! errno is read through `std::io::Error::last_os_error()` (no unsafe). No memory is shared with the kernel: NBD
//! moves data through the socket, never through a buffer handed to an ioctl.

#![allow(unsafe_code)] // the documented boundary; the rest of the crate is `deny(unsafe_code)`

use crate::error::{err, ErrorCode, Result, VnxError};
use std::fs::File;
use std::os::raw::{c_int, c_ulong};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};

// include/uapi/linux/nbd.h: #define NBD_SET_SOCK _IO(0xab, 0) ... (_IO(t, n) = (t << 8) | n: no size, no direction)
pub const NBD_SET_SOCK: c_ulong = 0xab00;
pub const NBD_SET_BLKSIZE: c_ulong = 0xab01;
pub const NBD_DO_IT: c_ulong = 0xab03;
pub const NBD_CLEAR_SOCK: c_ulong = 0xab04;
pub const NBD_CLEAR_QUE: c_ulong = 0xab05;
pub const NBD_SET_SIZE_BLOCKS: c_ulong = 0xab07;
pub const NBD_DISCONNECT: c_ulong = 0xab08;
pub const NBD_SET_TIMEOUT: c_ulong = 0xab09;
pub const NBD_SET_FLAGS: c_ulong = 0xab0a;

/// Signal numbers (identical on x86_64 and aarch64 Linux).
pub const SIGHUP: c_int = 1;
pub const SIGINT: c_int = 2;
pub const SIGTERM: c_int = 15;
const SIG_BLOCK: c_int = 0;

/// `sigset_t` of glibc and musl on Linux: 1024 bits.
#[repr(C)]
pub struct SigSet([u64; 16]);

extern "C" {
    fn ioctl(fd: c_int, request: c_ulong, ...) -> c_int;
    fn sigemptyset(set: *mut SigSet) -> c_int;
    fn sigaddset(set: *mut SigSet, signum: c_int) -> c_int;
    fn pthread_sigmask(how: c_int, set: *const SigSet, oldset: *mut SigSet) -> c_int;
    fn sigwait(set: *const SigSet, sig: *mut c_int) -> c_int;
}

fn os_err(what: &str, e: std::io::Error) -> VnxError {
    let code = match e.raw_os_error() {
        Some(16) => ErrorCode::BackendBusy, // EBUSY: another task owns the device
        Some(1) | Some(13) => ErrorCode::Unauthorized, // EPERM, EACCES: needs CAP_SYS_ADMIN
        Some(2) | Some(6) | Some(19) => ErrorCode::NotFound, // ENOENT, ENXIO, ENODEV: no such device / module
        _ => ErrorCode::BackendUnavailable,
    };
    VnxError::new(code, format!("{what}: {e}"))
}

/// The signals the service handles itself: SIGINT and SIGTERM stop it; SIGHUP is blocked and only logged (a dropped
/// terminal must not detach a device a filesystem is using).
pub fn stop_signals() -> SigSet {
    let mut s = SigSet([0; 16]);
    // SAFETY: `s` is an owned, properly sized and aligned sigset; sigemptyset/sigaddset only write inside it and
    // cannot fail for a valid pointer and these valid signal numbers.
    unsafe {
        sigemptyset(&mut s);
        for sig in [SIGHUP, SIGINT, SIGTERM] {
            sigaddset(&mut s, sig);
        }
    }
    s
}

/// Block the stop signals in the calling thread (and so in every thread it spawns afterwards). Called first in
/// `main`, before any thread exists: the signals are then only received by [`wait_signal`], never by the thread
/// that sits in `NBD_DO_IT` (a signal there makes the kernel drop the connection without a clean disconnect).
pub fn block_stop_signals() -> Result<()> {
    let s = stop_signals();
    // SAFETY: `s` is a valid sigset owned by this frame; a null old-set pointer is allowed by POSIX.
    let rc = unsafe { pthread_sigmask(SIG_BLOCK, &s, std::ptr::null_mut()) };
    if rc != 0 {
        return err(ErrorCode::Internal, format!("pthread_sigmask: error {rc}"));
    }
    Ok(())
}

/// Wait for one of the stop signals (they must be blocked: [`block_stop_signals`]); returns its number.
pub fn wait_signal() -> Result<c_int> {
    let s = stop_signals();
    let mut sig: c_int = 0;
    // SAFETY: both pointers refer to values owned by this frame that outlive the call.
    let rc = unsafe { sigwait(&s, &mut sig) };
    if rc != 0 {
        return err(ErrorCode::Internal, format!("sigwait: error {rc}"));
    }
    Ok(sig)
}

/// `/dev/nbdN` -> N (nothing else is accepted: no partitions, no symlinks resolved, no other block devices).
pub fn nbd_index(path: &Path) -> Option<u32> {
    let s = path.to_str()?;
    let n = s.strip_prefix("/dev/nbd")?;
    if n.is_empty() || n.len() > 4 || !n.bytes().all(|c| c.is_ascii_digit()) || (n.len() > 1 && n.starts_with('0')) {
        return None;
    }
    n.parse().ok()
}

fn sys_dir(index: u32) -> PathBuf {
    PathBuf::from(format!("/sys/block/nbd{index}"))
}

/// The pid of the process serving `/dev/nbdN` (from sysfs), if the device is running.
pub fn device_pid(index: u32) -> Option<u32> {
    std::fs::read_to_string(sys_dir(index).join("pid")).ok()?.trim().parse().ok()
}

/// The device size in 512-byte sectors (0 when unconfigured).
pub fn device_sectors(index: u32) -> Option<u64> {
    std::fs::read_to_string(sys_dir(index).join("size")).ok()?.trim().parse().ok()
}

/// Is the NBD driver loaded (`/sys/module/nbd`)?
pub fn driver_loaded() -> bool {
    Path::new("/sys/module/nbd").exists()
}

/// Devices that look free: present, no serving process, no size. Another task may still claim one first; the
/// claim itself is `NBD_SET_SOCK`, which the kernel refuses (EBUSY) for a device another task set up.
pub fn free_devices() -> Vec<u32> {
    let mut v: Vec<u32> = match std::fs::read_dir("/sys/block") {
        Ok(rd) => rd
            .filter_map(|e| e.ok()?.file_name().to_str()?.strip_prefix("nbd")?.parse().ok())
            .filter(|i| device_pid(*i).is_none() && device_sectors(*i) == Some(0))
            .collect(),
        Err(_) => Vec::new(),
    };
    v.sort_unstable();
    v
}

/// Is `dev` (or one of its partitions) mounted, according to the source field of /proc/self/mounts?
pub fn is_mounted(dev: &Path) -> bool {
    let Some(d) = dev.to_str() else { return false };
    let Ok(text) = std::fs::read_to_string("/proc/self/mounts") else { return false };
    text.lines().filter_map(|l| l.split_whitespace().next()).any(|src| {
        src == d
            || src.strip_prefix(d).map_or(false, |rest| rest.len() > 1 && rest.starts_with('p') && rest[1..].bytes().all(|c| c.is_ascii_digit()))
    })
}

/// An open `/dev/nbdN`. Its file descriptor is the `fd` of every ioctl and lives as long as this value.
pub struct NbdDevice {
    path: PathBuf,
    index: u32,
    file: File,
}

impl NbdDevice {
    /// Open `/dev/nbdN` for configuration. Refuses any other path and a device that is running or configured.
    pub fn open(path: &Path) -> Result<NbdDevice> {
        let index = match nbd_index(path) {
            Some(i) => i,
            None => return err(ErrorCode::InvalidArgument, format!("{} is not an /dev/nbdN device path", path.display())),
        };
        if !driver_loaded() {
            return err(ErrorCode::NotFound, "the nbd kernel module is not loaded (run: modprobe nbd)");
        }
        if let Some(pid) = device_pid(index) {
            return err(ErrorCode::BackendBusy, format!("{} is served by process {pid}", path.display()));
        }
        match device_sectors(index) {
            Some(0) => {}
            Some(n) => return err(ErrorCode::BackendBusy, format!("{} is configured ({n} sectors)", path.display())),
            None => return err(ErrorCode::NotFound, format!("{} has no sysfs entry", path.display())),
        }
        let file = std::fs::OpenOptions::new().read(true).write(true).open(path).map_err(|e| os_err(&format!("open {}", path.display()), e))?;
        Ok(NbdDevice { path: path.to_path_buf(), index, file })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn index(&self) -> u32 {
        self.index
    }

    fn ioctl(&self, what: &str, request: c_ulong, arg: c_ulong) -> Result<()> {
        // SAFETY: `self.file` is open for the whole call (borrowed by `&self`); every NBD request used here takes
        // an integer argument by value (a socket fd, a size, a flag word, seconds or nothing), so the kernel never
        // dereferences a user pointer from this call.
        let rc = unsafe { ioctl(self.file.as_raw_fd(), request, arg) };
        if rc < 0 {
            return Err(os_err(&format!("{what} on {}", self.path.display()), std::io::Error::last_os_error()));
        }
        Ok(())
    }

    /// Claim the device for this thread with the connected socket (`NBD_SET_SOCK`; EBUSY when another task set it
    /// up). Must be the first ioctl, and every later configuration ioctl and `do_it` run on the same thread.
    pub fn set_sock(&self, sock: &impl AsRawFd) -> Result<()> {
        let fd = sock.as_raw_fd();
        if fd < 0 {
            return err(ErrorCode::InvalidArgument, "invalid socket");
        }
        self.ioctl("NBD_SET_SOCK", NBD_SET_SOCK, fd as c_ulong)
    }

    /// Block size, size in blocks, transmission flags and the request timeout (seconds; 0 = none).
    pub fn configure(&self, block_size: u32, size_bytes: u64, flags: u16, timeout_s: u32) -> Result<()> {
        if !block_size.is_power_of_two() || !(512..=4096).contains(&block_size) {
            return err(ErrorCode::InvalidArgument, format!("the nbd driver needs a block size in 512..=4096, got {block_size}"));
        }
        if size_bytes == 0 || size_bytes % u64::from(block_size) != 0 {
            return err(ErrorCode::InvalidArgument, format!("size {size_bytes} is not a positive multiple of {block_size}"));
        }
        self.ioctl("NBD_SET_BLKSIZE", NBD_SET_BLKSIZE, c_ulong::from(block_size))?;
        let blocks = size_bytes / u64::from(block_size);
        let blocks = c_ulong::try_from(blocks).or_else(|_| err(ErrorCode::Overflow, "size does not fit the ioctl argument"))?;
        self.ioctl("NBD_SET_SIZE_BLOCKS", NBD_SET_SIZE_BLOCKS, blocks)?;
        self.ioctl("NBD_SET_FLAGS", NBD_SET_FLAGS, c_ulong::from(flags))?;
        self.ioctl("NBD_SET_TIMEOUT", NBD_SET_TIMEOUT, c_ulong::from(timeout_s))
    }

    /// Run the device: blocks until the connection ends (a disconnect, the server closing the socket, a fatal
    /// signal or a kernel timeout). The kernel creates `/sys/block/nbdN/pid` while this runs.
    pub fn do_it(&self) -> Result<()> {
        self.ioctl("NBD_DO_IT", NBD_DO_IT, 0)
    }

    /// Ask the kernel to send NBD_CMD_DISC and end `do_it` once outstanding requests are answered.
    pub fn disconnect(&self) -> Result<()> {
        self.ioctl("NBD_DISCONNECT", NBD_DISCONNECT, 0)
    }

    /// Release the socket and the configuration after `do_it` returned (also used on the error paths of attach).
    pub fn clear(&self) -> Result<()> {
        let q = self.ioctl("NBD_CLEAR_QUE", NBD_CLEAR_QUE, 0); // a no-op on current kernels, kept for older ones
        let s = self.ioctl("NBD_CLEAR_SOCK", NBD_CLEAR_SOCK, 0);
        q.and(s)
    }

    /// Write back the kernel's dirty buffers of the device and send a FLUSH (fsync on the block device).
    pub fn sync(&self) -> Result<()> {
        self.file.sync_all().map_err(|e| os_err(&format!("fsync {}", self.path.display()), e))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_whole_nbd_devices_are_accepted() {
        assert_eq!(nbd_index(Path::new("/dev/nbd0")), Some(0));
        assert_eq!(nbd_index(Path::new("/dev/nbd15")), Some(15));
        for bad in ["/dev/nbd", "/dev/nbd0p1", "/dev/nbd01", "/dev/vda", "/dev/loop0", "nbd0", "/dev/nbd-1", "/dev/../dev/nbd0",
            "/dev/nbd99999", "/dev/ublkb0"]
        {
            assert_eq!(nbd_index(Path::new(bad)), None, "{bad}");
        }
    }

    #[test]
    fn open_refuses_other_devices() {
        for p in ["/dev/vda", "/dev/null", "/dev/loop0", "/tmp/x"] {
            let e = NbdDevice::open(Path::new(p)).err().unwrap();
            assert_eq!(e.code, ErrorCode::InvalidArgument, "{p}");
        }
    }

    #[test]
    fn signal_set_is_buildable() {
        let s = stop_signals();
        // glibc stores signal n at bit n-1 of the first word
        assert_eq!(s.0[0] & ((1 << 0) | (1 << 1) | (1 << 14)), (1 << 0) | (1 << 1) | (1 << 14));
    }

    #[test]
    fn mount_check_matches_partitions_only() {
        // a path that cannot be mounted is never reported
        assert!(!is_mounted(Path::new("/dev/nbd4242")));
    }
}
