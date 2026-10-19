//! P5 concurrency stress: host I/O through the queue, the write-back cache, the volume, the fallback layer and a
//! concurrency-limited backend, while the load balancer runs scrubs, compactions and cache flushes. A watchdog fails
//! the test if the whole run does not finish (a deadlock); every client then checks its own region against a byte
//! model (no lost or mixed write under the contention).

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};
use vnxd::addr::Geometry;
use vnxd::balance::{Balancer, BalancerConfig, Class, LimitedMedia};
use vnxd::cache::{CacheMode, ExtentCache};
use vnxd::fallback::{HealthConfig, ResilientMedia, RetryPolicy};
use vnxd::fault::LatencyMedia;
use vnxd::io::{BlockIo, ExtentStore, IoQueue, Op, QueueConfig, Reply};
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::ram::{MemoryManager, RamConfig, Subsystem};
use vnxd::volume::{Volume, VolumeConfig};

struct Rng(u64);
impl Rng {
    fn below(&mut self, n: u64) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0 % n
    }
}

fn run(mode: CacheMode, seed: u64) {
    let dir = std::env::temp_dir().join(format!("vnxd-stress-{}-{mode:?}-{seed}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let latency: Arc<dyn MediaAdapter> =
        Arc::new(LatencyMedia::new(Arc::new(MemoryMedia::new()), Duration::from_micros(200), Duration::from_micros(100), 4));
    let limited: Arc<dyn MediaAdapter> = Arc::new(LimitedMedia::new(latency, None).unwrap());
    let retry = RetryPolicy { max_attempts: 2, base_delay: Duration::ZERO, max_delay: Duration::ZERO };
    let media: Arc<dyn MediaAdapter> = Arc::new(ResilientMedia::new(limited, retry, HealthConfig::default()).unwrap());
    let geo = Geometry::new(512, 8, 8 * 64).unwrap(); // 64 extents of 4 KiB
    let vol = Arc::new(Volume::create(&dir, geo, [seed as u8; 16], 0, media, VolumeConfig::default()).unwrap());
    let mut rc = RamConfig::split(4 << 20);
    rc.quotas.insert(Subsystem::Cache, 64 << 10); // 16 copies for 64 extents: constant eviction
    let ram = MemoryManager::new(rc).unwrap();
    let cache = Arc::new(ExtentCache::new(vol.clone(), ram.clone(), mode).with_readahead(2));
    let io = BlockIo::over(cache.clone(), vol.clone()).unwrap().with_ram(ram.clone());
    let q = Arc::new(IoQueue::start(io.clone(), QueueConfig { workers: 8, depth: 64 }).unwrap());
    let mut bc = BalancerConfig { workers: 4, depth: 64, ..Default::default() };
    bc.resources.insert("volume-maintenance".into(), 1);
    let lb = Arc::new(Balancer::start(bc).unwrap());

    let (done_tx, done_rx) = mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let bg_runs = Arc::new(AtomicU64::new(0));
    let (q2, lb2, vol2, cache2, stop2, runs2) = (q.clone(), lb.clone(), vol.clone(), cache.clone(), stop.clone(), bg_runs.clone());
    std::thread::spawn(move || {
        let cap = geo.capacity_bytes();
        let region = cap / 8;
        // background work through the balancer: 3 threads keep 4 tasks each in flight, so tasks run concurrently,
        // contend for the serialised "volume-maintenance" resource and compete across classes
        let maint_now = Arc::new(AtomicU64::new(0));
        let maint_max = Arc::new(AtomicU64::new(0));
        let bgs: Vec<_> = (0..3u64)
            .map(|k| {
                let (lb, vol, cache, stop, runs) = (lb2.clone(), vol2.clone(), cache2.clone(), stop2.clone(), runs2.clone());
                let (now, max) = (maint_now.clone(), maint_max.clone());
                std::thread::spawn(move || {
                    let mut inflight = std::collections::VecDeque::new();
                    let mut i = k;
                    while !stop.load(Ordering::SeqCst) || !inflight.is_empty() {
                        while !stop.load(Ordering::SeqCst) && inflight.len() < 4 {
                            let t = match i % 3 {
                                0 => {
                                    let v = vol.clone();
                                    lb.submit(Class::Verify, None, None, move || v.scrub().map(|r| r.failed.len()))
                                }
                                1 => {
                                    let (v, now, max) = (vol.clone(), now.clone(), max.clone());
                                    lb.submit(Class::Gc, Some("volume-maintenance"), None, move || {
                                        max.fetch_max(now.fetch_add(1, Ordering::SeqCst) + 1, Ordering::SeqCst);
                                        let r = v.compact().map(|_| 0);
                                        now.fetch_sub(1, Ordering::SeqCst);
                                        r
                                    })
                                }
                                _ => {
                                    let c = cache.clone();
                                    lb.submit(Class::Maintenance, None, None, move || ExtentStore::flush(&*c).map(|_| 0))
                                }
                            };
                            inflight.push_back(t.unwrap());
                            i += 1;
                        }
                        if let Some(t) = inflight.pop_front() {
                            assert_eq!(t.wait().unwrap(), 0, "scrub found damage under contention");
                            runs.fetch_add(1, Ordering::SeqCst);
                        }
                    }
                })
            })
            .collect();
        // 8 clients, each owning one region of the device
        std::thread::scope(|s| {
            for c in 0..8u64 {
                let q = q2.clone();
                s.spawn(move || {
                    let base = c * region;
                    let mut model = vec![0u8; region as usize];
                    let mut rng = Rng(0x9E37_79B9 ^ (seed << 8) ^ c);
                    for step in 0..250u64 {
                        let off = rng.below(region - 1);
                        let len = 1 + rng.below((region - off).min(9000));
                        if rng.below(3) == 0 {
                            match q.submit(Op::Read { offset: base + off, len }, None).unwrap().wait().unwrap() {
                                Reply::Data(d) => assert_eq!(d, &model[off as usize..(off + len) as usize], "client {c} step {step}"),
                                r => panic!("{r:?}"),
                            }
                        } else {
                            let data: Vec<u8> = (0..len).map(|i| (step * 7 + c * 31 + i) as u8).collect();
                            q.submit(Op::Write { offset: base + off, data: data.clone() }, None).unwrap().wait().unwrap();
                            model[off as usize..(off + len) as usize].copy_from_slice(&data);
                        }
                        if step % 50 == 49 {
                            q.submit(Op::Flush, None).unwrap().wait().unwrap();
                        }
                    }
                    let got = match q.submit(Op::Read { offset: base, len: region }, None).unwrap().wait().unwrap() {
                        Reply::Data(d) => d,
                        r => panic!("{r:?}"),
                    };
                    assert_eq!(got, model, "client {c}: final region");
                });
            }
        });
        stop2.store(true, Ordering::SeqCst);
        for bg in bgs {
            bg.join().unwrap();
        }
        assert_eq!(maint_max.load(Ordering::SeqCst), 1, "the serialised resource never ran twice at once");
        q2.submit(Op::Flush, None).unwrap().wait().unwrap();
        let _ = done_tx.send(());
    });
    let t0 = Instant::now();
    match done_rx.recv_timeout(Duration::from_secs(240)) {
        Ok(()) => {}
        Err(mpsc::RecvTimeoutError::Timeout) => panic!("{mode:?}: no progress for 240 s — deadlock"),
        Err(mpsc::RecvTimeoutError::Disconnected) => panic!("{mode:?}: the run panicked (see above)"),
    }
    assert!(bg_runs.load(Ordering::SeqCst) >= 12, "background work ran during the host I/O");
    assert!(lb.metrics(Class::Verify).max_wait_us.load(Ordering::Relaxed) > 0 || lb.metrics(Class::Gc).max_wait_us.load(Ordering::Relaxed) > 0,
        "tasks queued behind each other");
    // after the final flush the volume alone holds every client's data
    assert!(cache.dirty_extents().is_empty());
    let all = BlockIo::new(vol.clone()).read(0, geo.capacity_bytes()).unwrap();
    assert_eq!(all, io.read(0, geo.capacity_bytes()).unwrap(), "{mode:?}: volume and cache agree");
    assert!(vol.scrub().unwrap().failed.is_empty());
    eprintln!("{mode:?} seed {seed}: {:?}, {} background tasks", t0.elapsed(), bg_runs.load(Ordering::SeqCst));
    drop((q, lb));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn no_deadlock_and_no_lost_write_under_contention_write_through() {
    for seed in 1..=3 {
        run(CacheMode::WriteThrough, seed);
    }
}

#[test]
fn no_deadlock_and_no_lost_write_under_contention_write_back() {
    for seed in 1..=3 {
        run(CacheMode::WriteBack, seed);
    }
}
