//! The block-device service: `vnxd nbd create | attach | serve` (docs/V14_DEVICE_INTERFACE.md §2, §6).
//!
//! * `create` makes a new volume (journal directory + backend directory);
//! * `attach` opens the volume, negotiates NBD with itself over a socket pair and hands the client end to the
//!   kernel (`/dev/nbdN`, chosen or the first free one), then runs the device until a stop signal (INT, TERM, HUP),
//!   a management `shutdown`, or the connection ending. Stop order: fsync the block device (the kernel writes its
//!   dirty buffers and sends FLUSH), `NBD_DISCONNECT` (the kernel sends DISC; the server answers every request and
//!   flushes), `NBD_DO_IT` returns, `NBD_CLEAR_SOCK`, the queue drains, the store is flushed again. A panic or an
//!   error after the device was claimed takes the same path through a drop guard; a SIGKILL leaves the kernel to
//!   drop the connection (requests then fail with EIO) and the volume recovers by journal replay on the next open;
//! * `serve` exposes the same device on a Unix socket for userspace NBD clients (no kernel device, no root).
//!
//! Every event is one canonical-JSON line on stderr; readiness (`attached`, `serving`) is also printed on stdout.

use crate::addr::Geometry;
use crate::authz::{MgmtSession, Policy};
use crate::cache::CacheMode;
use crate::error::{err, ErrorCode, Result, VnxError};
use crate::json::Value;
use crate::media::MediaAdapter;
use crate::nbd;
use crate::nbdserve::{ConnEnd, Device, DeviceConfig, DeviceMgmt};
use crate::nbdsys::{self, NbdDevice};
use crate::volume::{Volume, VolumeConfig};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

pub const EXPORT_NAME: &str = "vnx";
const MAX_MGMT_SESSIONS: usize = 8;
const MAX_MGMT_LINE: u64 = 64 * 1024;
const MAX_SERVE_CONNECTIONS: usize = 4;

pub fn log(event: &str, fields: Vec<(&str, Value)>) {
    let mut m: std::collections::BTreeMap<String, Value> = fields.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
    m.insert("event".into(), Value::Str(event.into()));
    m.insert("pid".into(), Value::Int(i64::from(std::process::id())));
    emit_err(&Value::Obj(m).canonical());
}

/// Write one line to stderr; a failure (EPIPE, a full disk) is ignored: logging never panics and never stops the
/// service's control paths (`eprintln!` panics when stderr is gone).
fn emit_err(line: &str) {
    let mut e = std::io::stderr().lock();
    let _ = writeln!(e, "{line}");
}

fn announce(event: &str, fields: Vec<(&str, Value)>) {
    let mut m: std::collections::BTreeMap<String, Value> = fields.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
    m.insert("event".into(), Value::Str(event.into()));
    m.insert("pid".into(), Value::Int(i64::from(std::process::id())));
    let line = Value::Obj(m).canonical();
    emit_err(&line);
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{line}");
    let _ = out.flush();
}

/// Why the service stops; the first reason wins.
#[derive(Default)]
pub struct Stop {
    reason: Mutex<Option<String>>,
    cv: Condvar,
}

impl Stop {
    pub fn request(&self, why: &str) {
        let mut r = self.reason.lock().unwrap_or_else(|p| p.into_inner());
        if r.is_none() {
            *r = Some(why.to_string());
        }
        drop(r);
        self.cv.notify_all();
    }

    pub fn requested(&self) -> Option<String> {
        self.reason.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    pub fn wait(&self) -> String {
        let mut r = self.reason.lock().unwrap_or_else(|p| p.into_inner());
        loop {
            if let Some(w) = r.as_ref() {
                return w.clone();
            }
            r = self.cv.wait(r).unwrap_or_else(|p| p.into_inner());
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------
// options

#[derive(Clone, Debug)]
pub struct Opts {
    pub volume: PathBuf,
    pub backend: PathBuf,
    /// backend adapter: `image` (P7 ImageMedia, integrity-checked objects; default) or `dir` (DirMedia)
    pub media: String,
    pub device: Option<PathBuf>,
    pub socket: Option<PathBuf>,
    pub mgmt: Option<PathBuf>,
    pub policy: Option<PathBuf>,
    pub dev: DeviceConfig,
    pub kernel_timeout_s: u32,
    // create
    pub size: u64,
    pub block_size: u32,
    pub extent_blocks: u32,
}

fn parse_size(s: &str) -> Option<u64> {
    let (n, mul) = match s.as_bytes().last()? {
        b'K' | b'k' => (&s[..s.len() - 1], 1u64 << 10),
        b'M' | b'm' => (&s[..s.len() - 1], 1 << 20),
        b'G' | b'g' => (&s[..s.len() - 1], 1 << 30),
        _ => (s, 1),
    };
    n.parse::<u64>().ok()?.checked_mul(mul)
}

pub fn parse_opts(args: &[String]) -> Result<Opts> {
    let bad = |m: String| VnxError::new(ErrorCode::InvalidArgument, m);
    let mut o = Opts {
        volume: PathBuf::new(),
        backend: PathBuf::new(),
        media: "image".into(),
        device: None,
        socket: None,
        mgmt: None,
        policy: None,
        dev: DeviceConfig::default(),
        kernel_timeout_s: 120,
        size: 0,
        block_size: 4096,
        extent_blocks: 16,
    };
    let mut it = args.iter();
    while let Some(k) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| bad(format!("{k} needs a value")));
        let num = |v: String| parse_size(&v).ok_or_else(|| bad(format!("{k}: not a size: {v}")));
        match k.as_str() {
            "--volume" => o.volume = PathBuf::from(val()?),
            "--backend" => o.backend = PathBuf::from(val()?),
            "--media" => o.media = val()?,
            "--device" => {
                let v = val()?;
                o.device = if v == "auto" { None } else { Some(PathBuf::from(v)) };
            }
            "--socket" => o.socket = Some(PathBuf::from(val()?)),
            "--mgmt" => o.mgmt = Some(PathBuf::from(val()?)),
            "--policy" => o.policy = Some(PathBuf::from(val()?)),
            "--fault-control" => o.dev.fault_control = Some(PathBuf::from(val()?)),
            "--read-only" => o.dev.read_only = true,
            "--no-readback" => o.dev.volume.verify_readback = false,
            "--cache" => {
                o.dev.cache = match val()?.as_str() {
                    "none" => None,
                    "write-through" => Some(CacheMode::WriteThrough),
                    "write-back" => Some(CacheMode::WriteBack),
                    v => return Err(bad(format!("--cache {v}: none | write-through | write-back"))),
                }
            }
            "--ram" => o.dev.ram_bytes = num(val()?)?,
            "--request-timeout-s" => o.dev.request_timeout = Duration::from_secs(num(val()?)?),
            "--kernel-timeout-s" => o.kernel_timeout_s = u32::try_from(num(val()?)?).map_err(|_| bad("timeout too large".into()))?,
            "--size" => o.size = num(val()?)?,
            "--block-size" => o.block_size = u32::try_from(num(val()?)?).map_err(|_| bad("block size too large".into()))?,
            "--extent-blocks" => o.extent_blocks = u32::try_from(num(val()?)?).map_err(|_| bad("extent blocks too large".into()))?,
            other => return Err(bad(format!("unknown option {other}"))),
        }
    }
    if o.volume.as_os_str().is_empty() || o.backend.as_os_str().is_empty() {
        return Err(bad("--volume DIR and --backend DIR are required".into()));
    }
    // every request is answered within its deadline (start) plus its own run and a FUA flush: the kernel timeout
    // must leave that margin, and 0 (no kernel timeout) is refused (a dead server would hang the host's I/O)
    if o.dev.request_timeout.is_zero() || u64::from(o.kernel_timeout_s) < 2 * o.dev.request_timeout.as_secs() + 10 {
        return Err(bad("need --request-timeout-s > 0 and --kernel-timeout-s >= 2 x request timeout + 10".into()));
    }
    Ok(o)
}

/// A volume's backend is never placed on a block device this service exports (it would deadlock under memory
/// pressure and loop on itself): refuse a volume or backend directory on an NBD-backed filesystem.
fn check_not_on_nbd(p: &Path) -> Result<()> {
    // the nearest existing ancestor (the directory may not exist yet), made absolute
    let abs = if p.is_absolute() { p.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(p) };
    let canon = abs.ancestors().find_map(|a| std::fs::canonicalize(a).ok()).unwrap_or(abs);
    let mounts = std::fs::read_to_string("/proc/self/mounts").unwrap_or_default();
    let mut best: Option<(String, String)> = None;
    for l in mounts.lines() {
        let mut f = l.split_whitespace();
        let (Some(src), Some(mp)) = (f.next(), f.next()) else { continue };
        let (src, mp) = (unescape_mount(src), unescape_mount(mp));
        if canon.starts_with(&mp) && best.as_ref().map_or(true, |(_, b)| mp.len() >= b.len()) {
            best = Some((src, mp));
        }
    }
    if let Some((src, mp)) = best {
        if src.starts_with("/dev/nbd") || src.starts_with("/dev/ublkb") {
            return err(ErrorCode::InvalidArgument, format!("{} is on {src} (mounted at {mp}): never put a backend on an exported device", p.display()));
        }
    }
    Ok(())
}

/// /proc/self/mounts escapes space, tab, newline and backslash as \ooo octal.
fn unescape_mount(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && i + 4 <= b.len() && b[i + 1..i + 4].iter().all(|c| (b'0'..=b'7').contains(c)) {
            out.push((b[i + 1] - b'0') * 64 + (b[i + 2] - b'0') * 8 + (b[i + 3] - b'0'));
            i += 4;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn open_media(o: &Opts) -> Result<Arc<dyn MediaAdapter>> {
    check_not_on_nbd(&o.backend)?;
    check_not_on_nbd(&o.volume)?;
    match o.media.as_str() {
        "image" => Ok(Arc::new(crate::image::ImageMedia::open_or_create(&o.backend)?)),
        "dir" => Ok(Arc::new(crate::dirmedia::DirMedia::open(&o.backend)?)),
        m => err(ErrorCode::InvalidArgument, format!("--media {m}: image | dir")),
    }
}

fn random_id() -> [u8; 16] {
    let mut id = [0u8; 16];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        if f.read_exact(&mut id).is_ok() {
            return id;
        }
    }
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    id.copy_from_slice(&crate::digest::sha256(format!("{t}-{}", std::process::id()).as_bytes())[..16]);
    id
}

pub fn create(o: &Opts) -> Result<()> {
    if o.size == 0 || o.size % u64::from(o.block_size) != 0 {
        return err(ErrorCode::InvalidArgument, format!("--size {} must be a positive multiple of the block size {}", o.size, o.block_size));
    }
    let geo = Geometry::new(o.block_size, o.extent_blocks, o.size / u64::from(o.block_size))?;
    let media = open_media(o)?;
    let id = random_id();
    Volume::create(&o.volume, geo, id, 0, media, VolumeConfig::default())?;
    announce(
        "created",
        vec![
            ("volume", Value::Str(o.volume.display().to_string())),
            ("device_id", Value::Str(crate::digest::hex(&id))),
            ("capacity_bytes", Value::Int(geo.capacity_bytes() as i64)),
            ("block_size", Value::Int(i64::from(geo.block_size()))),
            ("extent_bytes", Value::Int(geo.extent_bytes() as i64)),
        ],
    );
    Ok(())
}

fn open_device(o: &Opts) -> Result<Device> {
    let media = open_media(o)?;
    let dev = Device::open(&o.volume, media, o.dev.clone())?;
    let r = dev.recovery();
    log(
        "volume_opened",
        vec![
            ("records_replayed", Value::Int(r.records_replayed as i64)),
            ("torn_tail", Value::Bool(r.torn_tail.is_some())),
            ("orphans_removed", Value::Int(r.orphans_removed.len() as i64)),
            ("damaged", Value::Int(r.damaged.len() as i64)),
            ("capacity_bytes", Value::Int(dev.geometry().capacity_bytes() as i64)),
        ],
    );
    Ok(dev)
}

// ---------------------------------------------------------------------------------------------------------------
// management socket

fn bind_private(path: &Path, mode: u32) -> Result<UnixListener> {
    use std::os::unix::fs::{FileTypeExt, PermissionsExt};
    if let Ok(m) = std::fs::symlink_metadata(path) {
        // a leftover socket of a dead service is replaced; anything else (or a live socket) is refused
        if !m.file_type().is_socket() || UnixStream::connect(path).is_ok() {
            return err(ErrorCode::BackendBusy, format!("{} exists", path.display()));
        }
        std::fs::remove_file(path).map_err(|e| VnxError::new(ErrorCode::BackendUnavailable, format!("remove {}: {e}", path.display())))?;
    }
    let l = UnixListener::bind(path).map_err(|e| VnxError::new(ErrorCode::BackendUnavailable, format!("bind {}: {e}", path.display())))?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))
        .map_err(|e| VnxError::new(ErrorCode::BackendUnavailable, format!("chmod {}: {e}", path.display())))?;
    l.set_nonblocking(true).map_err(|e| VnxError::new(ErrorCode::BackendUnavailable, format!("socket: {e}")))?;
    Ok(l)
}

fn mgmt_session(dev: &Device, policy: &Policy, stop: &Stop, s: UnixStream) {
    // short read timeouts so that an idle session notices the service stopping (it never delays shutdown)
    let _ = s.set_nonblocking(false);
    let _ = s.set_read_timeout(Some(Duration::from_millis(200)));
    let Ok(w) = s.try_clone() else { return };
    let mut w = w;
    let on_shutdown = || stop.request("management shutdown");
    let backend = DeviceMgmt { dev, on_shutdown: &on_shutdown };
    let mut sess = MgmtSession::new(policy, &backend, None, "unix");
    let mut r = BufReader::new(s);
    let mut line: Vec<u8> = Vec::new();
    let mut idle = Instant::now();
    loop {
        if stop.requested().is_some() || idle.elapsed() > Duration::from_secs(300) {
            return;
        }
        let room = MAX_MGMT_LINE.saturating_sub(line.len() as u64);
        match (&mut r).take(room).read_until(b'\n', &mut line) {
            Ok(0) if room == 0 => return, // over-long line: drop the session
            Ok(0) => return,              // the peer closed
            Ok(_) if !line.ends_with(b"\n") => continue,
            Ok(_) => {}
            Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut | std::io::ErrorKind::Interrupted) => {
                continue // partial bytes stay in `line`
            }
            Err(_) => return,
        }
        idle = Instant::now();
        let text = String::from_utf8_lossy(&line).trim_end().to_string();
        line.clear();
        let resp = sess.handle(&text);
        if writeln!(w, "{resp}").is_err() {
            return;
        }
    }
}

fn mgmt_loop(dev: &Device, policy: &Policy, stop: &Stop, l: &UnixListener) {
    let active = AtomicUsize::new(0);
    std::thread::scope(|sc| {
        while stop.requested().is_none() {
            match l.accept() {
                Ok((s, _)) => {
                    if active.load(Ordering::SeqCst) >= MAX_MGMT_SESSIONS {
                        continue; // dropped: too many sessions
                    }
                    active.fetch_add(1, Ordering::SeqCst);
                    let active = &active;
                    sc.spawn(move || {
                        mgmt_session(dev, policy, stop, s);
                        active.fetch_sub(1, Ordering::SeqCst);
                    });
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(50)),
                Err(_) => std::thread::sleep(Duration::from_millis(50)),
            }
        }
        // every session sees the stop within its read timeout; the scope joins them
    });
}

// ---------------------------------------------------------------------------------------------------------------
// attach (kernel block device)

/// The kernel side of the attachment: the open device and whether it is still configured by us. Every ioctl that
/// acts on the running device (`sync`, `disconnect`) holds this lock and checks `attached`, so none can reach the
/// device after we cleared it (when another process may already have claimed it).
struct Claim {
    nbd: NbdDevice,
    attached: Mutex<bool>,
    /// what went wrong on the device's way down (makes `attach` fail)
    failures: Mutex<Vec<String>>,
}

impl Claim {
    fn release(&self) {
        let mut a = self.attached.lock().unwrap_or_else(|p| p.into_inner());
        if *a {
            *a = false;
            if let Err(e) = self.nbd.clear() {
                log("clear_failed", vec![("error", Value::Str(e.to_string()))]);
            }
        }
    }

    /// Flush and disconnect, if still attached.
    fn stop(&self) {
        let a = self.attached.lock().unwrap_or_else(|p| p.into_inner());
        if !*a {
            return;
        }
        if nbdsys::is_mounted(self.nbd.path()) {
            log("detach_while_mounted", vec![("device", Value::Str(self.nbd.path().display().to_string()))]);
        }
        if let Err(e) = self.nbd.sync() {
            self.failures.lock().unwrap_or_else(|p| p.into_inner()).push(format!("device fsync at detach: {e}"));
            log("device_sync_failed", vec![("error", Value::Str(e.to_string()))]);
        }
        if let Err(e) = self.nbd.disconnect() {
            log("disconnect_failed", vec![("error", Value::Str(e.to_string()))]);
        }
    }
}

/// Runs when the attach scope is left in any way (return, error, panic): wakes the controller and ends the server
/// connection so that every scoped thread can be joined.
struct ScopeGuard<'a> {
    stop: &'a Stop,
    server_end: &'a UnixStream,
}

impl Drop for ScopeGuard<'_> {
    fn drop(&mut self) {
        self.stop.request("attach ended");
        let _ = self.server_end.shutdown(std::net::Shutdown::Both);
    }
}

/// Claim a device: the requested one, or the first free one. `NBD_SET_SOCK` is the claim (the kernel refuses a
/// device another task set up), so it is the first ioctl.
fn claim(o: &Opts, client: &UnixStream) -> Result<NbdDevice> {
    let candidates: Vec<PathBuf> = match &o.device {
        Some(p) => vec![p.clone()],
        None => nbdsys::free_devices().into_iter().map(|i| PathBuf::from(format!("/dev/nbd{i}"))).collect(),
    };
    if candidates.is_empty() {
        return err(
            ErrorCode::NotFound,
            if nbdsys::driver_loaded() { "no free /dev/nbdN device" } else { "the nbd kernel module is not loaded (run: modprobe nbd)" },
        );
    }
    let mut last = None;
    for p in candidates {
        match NbdDevice::open(&p).and_then(|d| d.set_sock(client).map(|_| d)) {
            Ok(d) => return Ok(d),
            Err(e) if e.code == ErrorCode::BackendBusy && o.device.is_none() => last = Some(e),
            Err(e) => return Err(e),
        }
    }
    Err(last.unwrap_or_else(|| VnxError::new(ErrorCode::NotFound, "no free /dev/nbdN device")))
}

pub fn attach(o: &Opts, stop: Arc<Stop>) -> Result<()> {
    let dev = open_device(o)?;
    let g = dev.geometry();
    if !(512..=4096).contains(&g.block_size()) {
        return err(ErrorCode::InvalidArgument, format!("the kernel NBD driver needs a block size of 512..=4096, the volume has {}", g.block_size()));
    }
    let policy = match (&o.mgmt, &o.policy) {
        (Some(_), Some(p)) => Some(Policy::load(p)?),
        (Some(_), None) => return err(ErrorCode::InvalidArgument, "--mgmt needs --policy"),
        _ => None,
    };
    let mgmt = match &o.mgmt {
        Some(p) => Some(bind_private(p, 0o660)?),
        None => None,
    };
    let export = dev.export(EXPORT_NAME);
    let (server_end, mut client_end) = UnixStream::pair().map_err(|e| nbd::io_err("socketpair", e))?;
    let server_shut = server_end.try_clone().map_err(|e| nbd::io_err("clone socket", e))?;
    let pid = std::process::id();
    // values the scoped threads borrow live outside the scope
    let claim_cell: std::sync::OnceLock<Claim> = std::sync::OnceLock::new();
    let server_trouble: Mutex<Option<String>> = Mutex::new(None);
    let running = AtomicBool::new(true);
    let outcome: Result<Vec<String>> = std::thread::scope(|sc| {
        let _guard = ScopeGuard { stop: &stop, server_end: &server_shut };
        let dev = &dev;
        let export = &export;
        let stop_ref = &*stop;
        let (shut_ref, trouble_ref) = (&server_shut, &server_trouble);
        let server = sc.spawn(move || {
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| dev.serve(server_end, export, true)));
            // whatever happened, the kernel must see the connection end (never a FLUSH waiting for a dead server)
            let _ = shut_ref.shutdown(std::net::Shutdown::Both);
            let (why, bad) = match r {
                Ok(Ok(ConnEnd::Disconnected)) => ("connection ended: Disconnected".to_string(), false),
                Ok(Ok(end)) => (format!("connection ended: {end:?}"), true),
                Ok(Err(e)) => (format!("server error: {e}"), true),
                Err(_) => {
                    dev.mark_failed("the NBD server thread panicked");
                    ("server panicked".to_string(), true)
                }
            };
            stop_ref.request(&why);
            if bad {
                *trouble_ref.lock().unwrap_or_else(|p| p.into_inner()) = Some(why.clone());
            }
            log("server_ended", vec![("why", Value::Str(why))]);
        });
        if let (Some(l), Some(p)) = (&mgmt, &policy) {
            sc.spawn(move || mgmt_loop(dev, p, stop_ref, l));
        }
        let info = nbd::client_negotiate(&mut client_end, EXPORT_NAME)?;
        if info.size != export.size || info.flags != export.flags || info.block_size != export.block_size {
            return err(ErrorCode::Protocol, format!("negotiated {info:?}, expected {export:?}"));
        }
        let nbd_dev = claim(o, &client_end)?;
        if claim_cell.set(Claim { nbd: nbd_dev, attached: Mutex::new(true), failures: Mutex::new(Vec::new()) }).is_err() {
            return err(ErrorCode::Internal, "device claimed twice");
        }
        let claim: &Claim = claim_cell.get().expect("just set");
        // from here on every exit path releases the device (drop order: `release` runs before the scope joins)
        struct Release<'c>(&'c Claim);
        impl Drop for Release<'_> {
            fn drop(&mut self) {
                self.0.release();
            }
        }
        let _release = Release(claim);
        claim.nbd.configure(info.block_size, info.size, info.flags, o.kernel_timeout_s)?;
        let index = claim.nbd.index();
        let (claim_ref, running_ref) = (claim, &running);
        // readiness: the kernel publishes the serving pid once NBD_DO_IT runs
        sc.spawn(move || {
            let t0 = Instant::now();
            while running_ref.load(Ordering::SeqCst) && t0.elapsed() < Duration::from_secs(30) {
                if nbdsys::device_pid(index) == Some(pid) {
                    announce(
                        "attached",
                        vec![
                            ("device", Value::Str(claim_ref.nbd.path().display().to_string())),
                            ("size", Value::Int(info.size as i64)),
                            ("block_size", Value::Int(i64::from(info.block_size))),
                            ("flags", Value::Int(i64::from(info.flags))),
                        ],
                    );
                    return;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
        });
        // the controller: waits for a stop reason, then detaches cleanly
        sc.spawn(move || {
            let why = stop_ref.wait();
            if running_ref.load(Ordering::SeqCst) {
                claim_ref.stop(); // before logging: a failed log write must never skip the detach
                log("detaching", vec![("why", Value::Str(why)), ("device", Value::Str(claim_ref.nbd.path().display().to_string()))]);
            }
        });
        let ran = claim.nbd.do_it();
        running.store(false, Ordering::SeqCst);
        // a detach nobody asked for (kernel timeout, server failure) is a failure of the device
        let requested = stop.requested().map_or(false, |w| w.starts_with("signal") || w.starts_with("management"));
        stop.request("device stopped");
        claim.release();
        log("device_stopped", vec![("result", Value::Str(ran.as_ref().map_or_else(|e| e.to_string(), |_| "ok".into())))]);
        drop(server); // joined by the scope
        let mut failures = std::mem::take(&mut *claim.failures.lock().unwrap_or_else(|p| p.into_inner()));
        if let Err(e) = ran {
            failures.push(format!("NBD_DO_IT: {e}"));
        } else if !requested {
            failures.push(format!("the device stopped without a requested detach ({})", stop.requested().unwrap_or_default()));
        }
        Ok(failures)
    });
    let outcome: Result<()> = outcome.and_then(|mut failures| {
        if let Some(t) = server_trouble.lock().unwrap_or_else(|p| p.into_inner()).take() {
            failures.push(t);
        }
        if dev.health_state() == "failed" {
            failures.push("the device health is failed".into());
        }
        if failures.is_empty() {
            Ok(())
        } else {
            err(ErrorCode::BackendUnavailable, failures.join("; "))
        }
    });
    if let Some(p) = &o.mgmt {
        let _ = std::fs::remove_file(p);
    }
    let health = dev.health_json();
    let stats = dev.stats_json();
    let closed = dev.close();
    log(
        "detached",
        vec![("closed", Value::Str(closed.as_ref().map_or_else(|e| e.to_string(), |_| "ok".into()))), ("health", health), ("stats", stats)],
    );
    outcome.and(closed)
}

// ---------------------------------------------------------------------------------------------------------------
// serve (userspace NBD clients on a Unix socket)

pub fn serve(o: &Opts, stop: Arc<Stop>) -> Result<()> {
    let sock = o.socket.clone().ok_or_else(|| VnxError::new(ErrorCode::InvalidArgument, "serve needs --socket PATH"))?;
    let dev = open_device(o)?;
    let policy = match (&o.mgmt, &o.policy) {
        (Some(_), Some(p)) => Some(Policy::load(p)?),
        (Some(_), None) => return err(ErrorCode::InvalidArgument, "--mgmt needs --policy"),
        _ => None,
    };
    let mgmt = match &o.mgmt {
        Some(p) => Some(bind_private(p, 0o660)?),
        None => None,
    };
    let listener = bind_private(&sock, 0o600)?;
    let export = dev.export(EXPORT_NAME);
    let conns: Mutex<std::collections::BTreeMap<u64, UnixStream>> = Mutex::new(std::collections::BTreeMap::new());
    let mut next_id = 0u64;
    let active = AtomicUsize::new(0);
    std::thread::scope(|sc| {
        let (dev, export, stop_ref, conns, active) = (&dev, &export, &*stop, &conns, &active);
        if let (Some(l), Some(p)) = (&mgmt, &policy) {
            sc.spawn(move || mgmt_loop(dev, p, stop_ref, l));
        }
        announce("serving", vec![("socket", Value::Str(sock.display().to_string()))]);
        while stop_ref.requested().is_none() {
            match listener.accept() {
                Ok((s, _)) => {
                    let _ = s.set_nonblocking(false);
                    if active.load(Ordering::SeqCst) >= MAX_SERVE_CONNECTIONS {
                        continue;
                    }
                    next_id += 1;
                    let id = next_id;
                    if let Ok(c) = s.try_clone() {
                        conns.lock().unwrap_or_else(|p| p.into_inner()).insert(id, c);
                    }
                    active.fetch_add(1, Ordering::SeqCst);
                    sc.spawn(move || {
                        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| dev.serve(s, export, true)));
                        match r {
                            Ok(Ok(ConnEnd::ProtocolError(m))) => log("connection_protocol_error", vec![("error", Value::Str(m))]),
                            Ok(Ok(_)) => {}
                            Ok(Err(e)) => log("connection_error", vec![("error", Value::Str(e.to_string()))]),
                            Err(_) => dev.mark_failed("an NBD connection thread panicked"),
                        }
                        conns.lock().unwrap_or_else(|p| p.into_inner()).remove(&id); // its descriptor is closed now
                        active.fetch_sub(1, Ordering::SeqCst);
                    });
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(20)),
                Err(_) => std::thread::sleep(Duration::from_millis(20)),
            }
        }
        // stop: end every connection (requests already started complete; the store is flushed by close)
        for c in conns.lock().unwrap_or_else(|p| p.into_inner()).values() {
            let _ = c.shutdown(std::net::Shutdown::Both);
        }
    });
    let _ = std::fs::remove_file(&sock);
    if let Some(p) = &o.mgmt {
        let _ = std::fs::remove_file(p);
    }
    let health = dev.health_json();
    let stats = dev.stats_json();
    let closed = dev.close();
    log(
        "stopped",
        vec![("closed", Value::Str(closed.as_ref().map_or_else(|e| e.to_string(), |_| "ok".into()))), ("health", health), ("stats", stats)],
    );
    closed
}

/// `vnxd nbd ...` entry point: returns the process exit code.
pub fn main(args: &[String]) -> i32 {
    let usage = "usage: vnxd nbd create --volume DIR --backend DIR --size N [--block-size 4096] [--extent-blocks 16]\n\
                 \x20      vnxd nbd attach --volume DIR --backend DIR [--device /dev/nbdN|auto] [options]\n\
                 \x20      vnxd nbd serve  --volume DIR --backend DIR --socket PATH [options]\n\
                 options: --media image|dir  --cache none|write-through|write-back  --ram N  --read-only  --request-timeout-s N\n\
                 \x20        --kernel-timeout-s N  --mgmt SOCKET --policy FILE  --fault-control FILE  --no-readback";
    let (cmd, rest) = match args.split_first() {
        Some((c, r)) => (c.as_str(), r),
        None => {
            eprintln!("{usage}");
            return 2;
        }
    };
    let o = match parse_opts(rest) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("vnxd nbd: {e}\n{usage}");
            return 2;
        }
    };
    let r = match cmd {
        "create" => create(&o),
        "attach" | "serve" => {
            // stop signals go to one thread only (never to the thread inside NBD_DO_IT); blocked before any thread
            if let Err(e) = nbdsys::block_stop_signals() {
                log("error", vec![("error", Value::Str(e.to_string()))]);
                return 1;
            }
            let stop = Arc::new(Stop::default());
            let s2 = stop.clone();
            let _ = std::thread::Builder::new().name("vnxd-signals".into()).spawn(move || {
                // SIGHUP (a dropped terminal) never detaches a device a filesystem may be using: it is logged only
                while let Ok(sig) = nbdsys::wait_signal() {
                    if sig != nbdsys::SIGHUP {
                        s2.request(&format!("signal {sig}")); // first: a failed log write must not lose the stop
                    }
                    log("signal", vec![("signal", Value::Int(i64::from(sig))), ("stops", Value::Bool(sig != nbdsys::SIGHUP))]);
                }
            });
            if cmd == "attach" {
                attach(&o, stop)
            } else {
                serve(&o, stop)
            }
        }
        _ => {
            eprintln!("{usage}");
            return 2;
        }
    };
    match r {
        Ok(()) => 0,
        Err(e) => {
            log("error", vec![("code", Value::Str(e.code.as_str().into())), ("error", Value::Str(e.message.clone()))]);
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(s: &str) -> Vec<String> {
        s.split_whitespace().map(String::from).collect()
    }

    #[test]
    fn options_parse_and_validate() {
        let o = parse_opts(&args("--volume v --backend b --size 128M --cache write-back --device /dev/nbd3 --ram 512M")).unwrap();
        assert_eq!(o.size, 128 << 20);
        assert_eq!(o.dev.cache, Some(CacheMode::WriteBack));
        assert_eq!(o.device, Some(PathBuf::from("/dev/nbd3")));
        assert_eq!(o.dev.ram_bytes, 512 << 20);
        assert!(parse_opts(&args("--volume v")).is_err());
        assert!(parse_opts(&args("--volume v --backend b --cache maybe")).is_err());
        assert!(parse_opts(&args("--volume v --backend b --bogus")).is_err());
        assert!(parse_opts(&args("--volume v --backend b --request-timeout-s 200 --kernel-timeout-s 100")).is_err());
        assert!(parse_opts(&args("--volume v --backend b --kernel-timeout-s 0")).is_err(), "no kernel timeout is refused");
        assert!(parse_opts(&args("--volume v --backend b --request-timeout-s 30 --kernel-timeout-s 60")).is_err(), "margin");
        assert_eq!(parse_opts(&args("--volume v --backend b")).unwrap().media, "image");
        assert_eq!(parse_opts(&args("--volume v --backend b --device auto")).unwrap().device, None);
    }

    #[test]
    fn stop_keeps_the_first_reason() {
        let s = Stop::default();
        assert_eq!(s.requested(), None);
        s.request("a");
        s.request("b");
        assert_eq!(s.wait(), "a");
    }

    #[test]
    fn create_and_serve_over_a_socket() {
        let t = crate::journal::tests::TempDir::new("nbdctl-serve");
        let o = parse_opts(&[
            "--volume".into(), t.0.join("vol").display().to_string(),
            "--backend".into(), t.0.join("be").display().to_string(),
            "--size".into(), "1M".into(),
            "--socket".into(), t.0.join("nbd.sock").display().to_string(),
        ])
        .unwrap();
        create(&o).unwrap();
        assert!(create(&o).is_err(), "a volume is never created twice");
        let stop = Arc::new(Stop::default());
        let s2 = stop.clone();
        let o2 = o.clone();
        let srv = std::thread::spawn(move || serve(&o2, s2));
        let sock = t.0.join("nbd.sock");
        let t0 = Instant::now();
        let mut c = loop {
            if let Ok(c) = UnixStream::connect(&sock) {
                break c;
            }
            assert!(t0.elapsed() < Duration::from_secs(10));
            std::thread::sleep(Duration::from_millis(20));
        };
        let info = nbd::client_negotiate(&mut c, "").unwrap();
        assert_eq!(info.size, 1 << 20);
        assert_eq!(info.block_size, 4096);
        let r = nbd::Request { flags: 0, kind: nbd::CMD_WRITE, handle: 1, offset: 0, len: 4096 };
        c.write_all(&r.encode()).unwrap();
        c.write_all(&[3u8; 4096]).unwrap();
        let mut h = [0u8; nbd::REPLY_BYTES];
        c.read_exact(&mut h).unwrap();
        assert_eq!(&h[4..8], &[0, 0, 0, 0]);
        stop.request("test");
        srv.join().unwrap().unwrap();
        assert!(!sock.exists(), "the socket is removed at stop");
    }

    #[test]
    fn backend_on_an_exported_device_is_refused_and_others_pass() {
        assert!(check_not_on_nbd(Path::new("/tmp")).is_ok());
        assert!(check_not_on_nbd(Path::new("relative/not/yet/created")).is_ok());
        assert_eq!(unescape_mount("/mnt/a\\040b"), "/mnt/a b");
        assert_eq!(unescape_mount("/plain\\0"), "/plain\\0");
    }
}
