//! The no-space bound of the NBD backend (docs/V14_VALIDATION_PLAN.md rows R15 and B06): a device whose store has
//! a bound refuses a write that needs more space than the bound allows with NO_SPACE, which the host block layer
//! sees as ENOSPC. Nothing already written changes, an overwrite of an extent that carries data needs no new space
//! and still succeeds, and a device without the bound behaves as before. The device is driven over a socket pair: no
//! /dev/nbd device and no privileges are needed.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use vnxd::addr::Geometry;
use vnxd::error::ErrorCode;
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::nbd::{client_negotiate, Request, CMD_DISC, CMD_FLUSH, CMD_READ, CMD_WRITE, REPLY_BYTES, SIMPLE_REPLY_MAGIC};
use vnxd::nbdserve::{ConnEnd, Device, DeviceConfig};
use vnxd::volume::{Volume, VolumeConfig};

/// The bytes of one extent (4 blocks of 4 KiB).
const EXTENT: u64 = 16 * 1024;
/// The bytes of the device (16 extents).
const CAPACITY: u64 = 16 * EXTENT;

struct Dir(std::path::PathBuf);

impl Dir {
    fn new(tag: &str) -> Dir {
        static N: AtomicU64 = AtomicU64::new(0);
        let p = std::env::temp_dir().join(format!("vnxd-nospace-{tag}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::Relaxed)));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        Dir(p)
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A device over a fresh volume in `d` (256 KiB in 16 extents of 16 KiB).
fn device(d: &Dir, cfg: DeviceConfig) -> Device {
    let geo = Geometry::new(4096, 4, 64).unwrap();
    let media: Arc<dyn MediaAdapter> = Arc::new(MemoryMedia::new());
    Volume::create(&d.0.join("vol"), geo, [7; 16], 0, media.clone(), VolumeConfig::default()).unwrap();
    Device::open(&d.0.join("vol"), media, cfg).unwrap()
}

/// A deterministic pattern, different for every seed.
fn pattern(seed: u64, len: usize) -> Vec<u8> {
    (0..len).map(|i| ((i as u64 * 7 + seed * 31) % 251) as u8).collect()
}

struct Client {
    s: UnixStream,
    next: u64,
}

impl Client {
    fn send(&mut self, kind: u16, flags: u16, offset: u64, len: u32, data: &[u8]) -> u64 {
        self.next += 1;
        let r = Request { flags, kind, handle: self.next, offset, len };
        self.s.write_all(&r.encode()).unwrap();
        self.s.write_all(data).unwrap();
        self.next
    }

    fn reply(&mut self, data_len: usize) -> (u64, u32, Vec<u8>) {
        let mut h = [0u8; REPLY_BYTES];
        self.s.read_exact(&mut h).unwrap();
        assert_eq!(u32::from_be_bytes(h[0..4].try_into().unwrap()), SIMPLE_REPLY_MAGIC);
        let e = u32::from_be_bytes(h[4..8].try_into().unwrap());
        let handle = u64::from_be_bytes(h[8..16].try_into().unwrap());
        let mut d = vec![0u8; if e == 0 { data_len } else { 0 }];
        self.s.read_exact(&mut d).unwrap();
        (handle, e, d)
    }

    fn call(&mut self, kind: u16, flags: u16, offset: u64, len: u32, data: &[u8], data_len: usize) -> (u32, Vec<u8>) {
        let h = self.send(kind, flags, offset, len, data);
        let (rh, e, d) = self.reply(data_len);
        assert_eq!(rh, h);
        (e, d)
    }
}

/// Serve one connection and drive it with `f`.
fn with_conn<R>(dev: &Device, f: impl FnOnce(&mut Client) -> R) -> (R, ConnEnd) {
    let (a, b) = UnixStream::pair().unwrap();
    let export = dev.export("vnx");
    std::thread::scope(|sc| {
        let srv = sc.spawn(|| dev.serve(a, &export, true).unwrap());
        let mut b = b;
        let info = client_negotiate(&mut b, "vnx").unwrap();
        assert_eq!(info.size, dev.geometry().capacity_bytes());
        let mut c = Client { s: b, next: 0 };
        let r = f(&mut c);
        drop(c);
        (r, srv.join().unwrap())
    })
}

#[test]
fn a_write_past_the_bound_is_refused_with_enospc_and_nothing_changes() {
    let d = Dir::new("bound");
    let dev = device(&d, DeviceConfig { max_stored_bytes: Some(2 * EXTENT), ..DeviceConfig::default() });
    let ((), end) = with_conn(&dev, |c| {
        // fill the bound: two extents
        assert_eq!(c.call(CMD_WRITE, 0, 0, EXTENT as u32, &pattern(1, EXTENT as usize), 0).0, 0);
        assert_eq!(c.call(CMD_WRITE, 0, EXTENT, EXTENT as u32, &pattern(2, EXTENT as usize), 0).0, 0);
        // the next write needs a third extent: the store is full
        let (e, _) = c.call(CMD_WRITE, 0, 2 * EXTENT, 4096, &pattern(3, 4096), 0);
        assert_eq!(e, ErrorCode::NoSpace.errno(), "a full store is NO_SPACE, which the host sees as ENOSPC");
        assert_eq!(e, 28);
        // what was written before is unchanged
        assert_eq!(c.call(CMD_READ, 0, 0, EXTENT as u32, &[], EXTENT as usize), (0, pattern(1, EXTENT as usize)));
        assert_eq!(c.call(CMD_READ, 0, EXTENT, EXTENT as u32, &[], EXTENT as usize), (0, pattern(2, EXTENT as usize)));
        // the refused write's payload was consumed: the stream stays in step
        assert_eq!(c.call(CMD_FLUSH, 0, 0, 0, &[], 0).0, 0);
        c.send(CMD_DISC, 0, 0, 0, &[]);
    });
    assert_eq!(end, ConnEnd::Disconnected);
    assert!(dev.stats.errors.load(Ordering::Relaxed) >= 1);
    dev.close().unwrap();
}

#[test]
fn an_overwrite_at_the_bound_still_succeeds() {
    let d = Dir::new("overwrite");
    let dev = device(&d, DeviceConfig { max_stored_bytes: Some(2 * EXTENT), ..DeviceConfig::default() });
    let ((), _) = with_conn(&dev, |c| {
        assert_eq!(c.call(CMD_WRITE, 0, 0, EXTENT as u32, &pattern(1, EXTENT as usize), 0).0, 0);
        assert_eq!(c.call(CMD_WRITE, 0, EXTENT, EXTENT as u32, &pattern(2, EXTENT as usize), 0).0, 0);
        // the store is full
        assert_eq!(c.call(CMD_WRITE, 0, 2 * EXTENT, 4096, &pattern(3, 4096), 0).0, ErrorCode::NoSpace.errno());
        // an overwrite of an extent that carries data needs no new space: it succeeds
        assert_eq!(c.call(CMD_WRITE, 0, EXTENT, EXTENT as u32, &pattern(4, EXTENT as usize), 0).0, 0);
        assert_eq!(c.call(CMD_READ, 0, EXTENT, EXTENT as u32, &[], EXTENT as usize), (0, pattern(4, EXTENT as usize)));
        // the extent that carries no data yet is still refused
        assert_eq!(c.call(CMD_WRITE, 0, 2 * EXTENT, 4096, &pattern(3, 4096), 0).0, ErrorCode::NoSpace.errno());
        c.send(CMD_DISC, 0, 0, 0, &[]);
    });
    dev.close().unwrap();
}

#[test]
fn a_device_without_a_bound_has_no_limit() {
    let d = Dir::new("unbound");
    assert_eq!(DeviceConfig::default().max_stored_bytes, None, "the bound is off by default");
    let dev = device(&d, DeviceConfig::default());
    let ((), _) = with_conn(&dev, |c| {
        // four extents: with a bound of two extents this would be refused
        for i in 0..4u64 {
            assert_eq!(c.call(CMD_WRITE, 0, i * EXTENT, EXTENT as u32, &pattern(i + 1, EXTENT as usize), 0).0, 0);
        }
        for i in 0..4u64 {
            assert_eq!(
                c.call(CMD_READ, 0, i * EXTENT, EXTENT as u32, &[], EXTENT as usize),
                (0, pattern(i + 1, EXTENT as usize))
            );
        }
        // a write past the end of the device is refused as before (ENOSPC, the device end)
        assert_eq!(c.call(CMD_WRITE, 0, CAPACITY - 512, 1024, &pattern(9, 1024), 0).0, 28);
        // and a read past the end of the device as before (EINVAL)
        assert_eq!(c.call(CMD_READ, 0, CAPACITY - 512, 1024, &[], 0).0, 22);
        c.send(CMD_DISC, 0, 0, 0, &[]);
    });
    dev.close().unwrap();
}

#[test]
fn a_read_needs_no_space_and_is_not_refused_by_the_bound() {
    let d = Dir::new("reads");
    let dev = device(&d, DeviceConfig { max_stored_bytes: Some(EXTENT), ..DeviceConfig::default() });
    let ((), _) = with_conn(&dev, |c| {
        assert_eq!(c.call(CMD_WRITE, 0, 0, EXTENT as u32, &pattern(1, EXTENT as usize), 0).0, 0);
        // the store is full
        assert_eq!(c.call(CMD_WRITE, 0, EXTENT, 4096, &pattern(2, 4096), 0).0, ErrorCode::NoSpace.errno());
        // a read needs no space: it is answered as before, inside the device and outside the bound
        assert_eq!(c.call(CMD_READ, 0, 0, EXTENT as u32, &[], EXTENT as usize), (0, pattern(1, EXTENT as usize)));
        let (e, d) = c.call(CMD_READ, 0, 2 * EXTENT, 4096, &[], 4096);
        assert_eq!(e, 0);
        assert_eq!(d, vec![0u8; 4096], "an unwritten range reads as zeros");
        c.send(CMD_DISC, 0, 0, 0, &[]);
    });
    dev.close().unwrap();
}
