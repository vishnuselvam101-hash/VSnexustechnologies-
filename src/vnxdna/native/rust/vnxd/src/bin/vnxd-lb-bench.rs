//! `vnxd-lb-bench`: single- vs multi-worker benchmark of the I/O queue and the load balancer (docs/V14_LOAD_BALANCER.md
//! §5; driver: experiments/v14/bench/p5-balancer/run.py). One run per process, so peak RSS belongs to one
//! configuration. Prints one canonical JSON line.
//!
//!     vnxd-lb-bench --profile electronic|dna-sim --io-workers N --lb-workers N [--clients 16] [--ops 300] [--seed 1]
//!
//! Backend latency and concurrency are MODEL INPUTS of `LatencyMedia` (no physical medium is involved; the
//! `dna-sim` profile is an illustrative slow, barely parallel backend, not a measurement of DNA synthesis or
//! sequencing). What is measured: host request latency (submit -> completion) percentiles, throughput, queue wait,
//! backend slot waits, the concurrent CPU work (a codec stand-in) the balancer completed, and peak RSS. The balancer
//! is kept saturated on purpose (2 x workers tasks queued), so its utilisation is close to 1 by construction; the
//! balancer sweep measures how the CPU work scales with workers and what it costs the host requests sharing the CPU.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use vnxd::addr::Geometry;
use vnxd::balance::{Balancer, BalancerConfig, Class, LimitedMedia};
use vnxd::digest::sha256;
use vnxd::fault::LatencyMedia;
use vnxd::io::{BlockIo, IoQueue, Op, QueueConfig};
use vnxd::json::{obj, Value};
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::volume::{Volume, VolumeConfig};

fn arg(args: &[String], name: &str, default: &str) -> String {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned().unwrap_or_else(|| default.to_string())
}

fn peak_rss_kib() -> i64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| s.lines().find(|l| l.starts_with("VmHWM:")).and_then(|l| l.split_whitespace().nth(1)).and_then(|v| v.parse().ok()))
        .unwrap_or(-1)
}

fn pct(sorted: &[u64], p: f64) -> i64 {
    if sorted.is_empty() {
        return 0;
    }
    let i = ((sorted.len() as f64 - 1.0) * p).round() as usize;
    sorted[i] as i64
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let profile = arg(&args, "--profile", "electronic");
    let io_workers: usize = arg(&args, "--io-workers", "4").parse().expect("--io-workers");
    let lb_workers: usize = arg(&args, "--lb-workers", "4").parse().expect("--lb-workers");
    let clients: u64 = arg(&args, "--clients", "16").parse().expect("--clients");
    let ops: u64 = arg(&args, "--ops", "300").parse().expect("--ops");
    let seed: u64 = arg(&args, "--seed", "1").parse().expect("--seed");
    let (put, get, inflight) = match profile.as_str() {
        "electronic" => (Duration::from_micros(300), Duration::from_micros(150), 32),
        "dna-sim" => (Duration::from_millis(20), Duration::from_millis(10), 2),
        p => panic!("unknown profile {p}"),
    };
    let dir = std::env::temp_dir().join(format!("vnxd-lb-bench-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    let modelled: Arc<dyn MediaAdapter> = Arc::new(LatencyMedia::new(Arc::new(MemoryMedia::new()), put, get, inflight));
    let limited = Arc::new(LimitedMedia::new(modelled, None).unwrap());
    let geo = Geometry::new(4096, 16, 16 * 256).unwrap(); // 256 extents of 64 KiB = 16 MiB
    let vol = Arc::new(Volume::create(&dir, geo, [7; 16], 0, limited.clone(), VolumeConfig::default()).unwrap());
    let q = Arc::new(IoQueue::start(BlockIo::new(vol.clone()), QueueConfig { workers: io_workers, depth: 256 }).unwrap());
    let lb = Arc::new(Balancer::start(BalancerConfig { workers: lb_workers, depth: 1024, ..Default::default() }).unwrap());

    // concurrent CPU work (a codec stand-in: SHA-256 over 1 MiB per task) kept queued in the balancer
    let stop = Arc::new(AtomicBool::new(false));
    let cpu_done = Arc::new(AtomicU64::new(0));
    let feeder = {
        let (lb, stop, done) = (lb.clone(), stop.clone(), cpu_done.clone());
        std::thread::spawn(move || {
            let buf = Arc::new(vec![0x5au8; 1 << 20]);
            let mut pending = std::collections::VecDeque::new();
            while !stop.load(Ordering::SeqCst) {
                while pending.len() < 2 * lb_workers {
                    let b = buf.clone();
                    pending.push_back(lb.submit(Class::Codec, None, None, move || Ok(sha256(&b)[0])).unwrap());
                }
                if let Some(t) = pending.pop_front() {
                    t.wait().unwrap();
                    done.fetch_add(1, Ordering::Relaxed);
                }
            }
            for t in pending {
                let _ = t.wait();
            }
        })
    };

    let cap = geo.capacity_bytes();
    let lat = Arc::new(std::sync::Mutex::new(Vec::with_capacity((clients * ops) as usize)));
    let bytes = Arc::new(AtomicU64::new(0));
    let t0 = Instant::now();
    std::thread::scope(|s| {
        for c in 0..clients {
            let (q, lat, bytes) = (q.clone(), lat.clone(), bytes.clone());
            s.spawn(move || {
                let mut x = 0x9E37_79B9_7F4A_7C15u64 ^ (seed << 16) ^ c;
                let mut next = || {
                    x ^= x << 13;
                    x ^= x >> 7;
                    x ^= x << 17;
                    x
                };
                let mut mine = Vec::with_capacity(ops as usize);
                for _ in 0..ops {
                    // 4 KiB .. 256 KiB, 2/3 reads
                    let len = 4096u64 << (next() % 7);
                    let off = (next() % (cap - len)) & !4095;
                    let op = if next() % 3 == 0 { Op::Write { offset: off, data: vec![c as u8; len as usize] } } else { Op::Read { offset: off, len } };
                    let t = Instant::now();
                    q.submit(op, None).unwrap().wait().unwrap();
                    mine.push(t.elapsed().as_micros() as u64);
                    bytes.fetch_add(len, Ordering::Relaxed);
                }
                lat.lock().unwrap().extend(mine);
            });
        }
    });
    let wall = t0.elapsed();
    stop.store(true, Ordering::SeqCst);
    feeder.join().unwrap();
    let mut l = lat.lock().unwrap().clone();
    l.sort_unstable();
    let m = q.metrics();
    let started = (m.completed.load(Ordering::Relaxed) + m.failed.load(Ordering::Relaxed)).max(1);
    let secs = wall.as_secs_f64();
    let out = obj([
        ("profile", Value::Str(profile.clone())),
        ("io_workers", Value::Int(io_workers as i64)),
        ("lb_workers", Value::Int(lb_workers as i64)),
        ("clients", Value::Int(clients as i64)),
        ("ops_per_client", Value::Int(ops as i64)),
        ("seed", Value::Int(seed as i64)),
        ("model_put_us", Value::Int(put.as_micros() as i64)),
        ("model_get_us", Value::Int(get.as_micros() as i64)),
        ("model_max_in_flight", Value::Int(i64::from(inflight))),
        ("wall_ms", Value::Int(wall.as_millis() as i64)),
        ("requests", Value::Int(l.len() as i64)),
        ("throughput_req_per_s_milli", Value::Int((l.len() as f64 / secs * 1000.0) as i64)),
        ("throughput_kib_per_s", Value::Int((bytes.load(Ordering::Relaxed) as f64 / 1024.0 / secs) as i64)),
        ("latency_us_p50", Value::Int(pct(&l, 0.50))),
        ("latency_us_p95", Value::Int(pct(&l, 0.95))),
        ("latency_us_p99", Value::Int(pct(&l, 0.99))),
        ("latency_us_max", Value::Int(l.last().copied().unwrap_or(0) as i64)),
        ("queue_wait_us_mean", Value::Int((m.queue_wait_us.load(Ordering::Relaxed) / started) as i64)),
        ("queue_depth_max", Value::Int(m.max_queue_depth.load(Ordering::Relaxed) as i64)),
        ("backend_slot_wait_us_total", Value::Int(limited.wait_us.load(Ordering::Relaxed) as i64)),
        ("backend_in_flight_max", Value::Int(limited.max_seen.load(Ordering::Relaxed) as i64)),
        ("cpu_tasks_done", Value::Int(cpu_done.load(Ordering::Relaxed) as i64)),
        ("cpu_tasks_per_s_milli", Value::Int((cpu_done.load(Ordering::Relaxed) as f64 / secs * 1000.0) as i64)),
        ("lb_utilisation_milli", Value::Int((lb.utilisation() * 1000.0) as i64)),
        ("peak_rss_kib", Value::Int(peak_rss_kib())),
        ("cpus", Value::Int(std::thread::available_parallelism().map_or(0, |n| n.get()) as i64)),
    ]);
    println!("{}", out.canonical());
    drop((q, lb));
    let _ = std::fs::remove_dir_all(&dir);
}
