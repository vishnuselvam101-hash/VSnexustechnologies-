//! Persistent worker pool for one decode: `threads - 1` workers are created once; every parallel stage broadcasts a
//! job to all of them plus the calling thread (worker index 0) and returns when all have finished. The job borrows the
//! caller's data: `run` does not return before every worker is done with it, which is what makes the lifetime erasure
//! below sound. A panic in a job is caught on its thread and reported as an error of that stage.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;

type Job = dyn Fn(usize) + Sync;

struct State {
    generation: u64,
    job: Option<*const Job>,
    remaining: usize,
    panicked: bool,
    quit: bool,
}

// SAFETY: the job pointer is only dereferenced while `run` keeps the job alive (it waits for `remaining == 0`).
unsafe impl Send for State {}

struct Shared {
    state: Mutex<State>,
    start: Condvar,
    done: Condvar,
}

pub struct Pool {
    shared: Arc<Shared>,
    workers: Vec<JoinHandle<()>>,
}

impl Pool {
    pub fn new(threads: usize) -> Pool {
        let shared = Arc::new(Shared {
            state: Mutex::new(State { generation: 0, job: None, remaining: 0, panicked: false, quit: false }),
            start: Condvar::new(),
            done: Condvar::new(),
        });
        let mut workers = Vec::new();
        for id in 1..threads.max(1) {
            let sh = Arc::clone(&shared);
            workers.push(std::thread::spawn(move || worker(sh, id)));
        }
        Pool { shared, workers }
    }

    pub fn threads(&self) -> usize {
        self.workers.len() + 1
    }

    /// Run `f(worker)` on every worker (1..threads) and on the calling thread (0); returns false when a job panicked.
    pub fn run(&self, f: &(dyn Fn(usize) + Sync)) -> bool {
        if self.workers.is_empty() {
            return catch_unwind(AssertUnwindSafe(|| f(0))).is_ok();
        }
        // SAFETY: lifetime erasure of the job; it is used only until every worker has decremented `remaining`, and
        // this function waits for that before returning (also when the caller's own part panics).
        let ptr: *const Job = unsafe { std::mem::transmute::<&(dyn Fn(usize) + Sync), &'static Job>(f) };
        {
            let mut st = self.shared.state.lock().unwrap();
            st.job = Some(ptr);
            st.remaining = self.workers.len();
            st.panicked = false;
            st.generation += 1;
            self.shared.start.notify_all();
        }
        let mine = catch_unwind(AssertUnwindSafe(|| f(0))).is_ok();
        let mut st = self.shared.state.lock().unwrap();
        while st.remaining > 0 {
            st = self.shared.done.wait(st).unwrap();
        }
        st.job = None;
        mine && !st.panicked
    }
}

fn worker(sh: Arc<Shared>, id: usize) {
    let mut seen = 0u64;
    loop {
        let job = {
            let mut st = sh.state.lock().unwrap();
            while !st.quit && st.generation == seen {
                st = sh.start.wait(st).unwrap();
            }
            if st.quit {
                return;
            }
            seen = st.generation;
            st.job
        };
        let ok = match job {
            // SAFETY: see Pool::run.
            Some(p) => catch_unwind(AssertUnwindSafe(|| unsafe { (*p)(id) })).is_ok(),
            None => true,
        };
        let mut st = sh.state.lock().unwrap();
        if !ok {
            st.panicked = true;
        }
        st.remaining -= 1;
        if st.remaining == 0 {
            sh.done.notify_all();
        }
    }
}

impl Drop for Pool {
    fn drop(&mut self) {
        {
            let mut st = self.shared.state.lock().unwrap();
            st.quit = true;
            self.shared.start.notify_all();
        }
        for w in self.workers.drain(..) {
            let _ = w.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn every_worker_runs_the_job_once() {
        for threads in [1usize, 2, 5] {
            let pool = Pool::new(threads);
            assert_eq!(pool.threads(), threads);
            let hits = AtomicUsize::new(0);
            let seen = Mutex::new(Vec::new());
            for _ in 0..3 {
                assert!(pool.run(&|t| {
                    hits.fetch_add(1, Ordering::Relaxed);
                    seen.lock().unwrap().push(t);
                }));
            }
            assert_eq!(hits.load(Ordering::Relaxed), 3 * threads);
            let mut ids = seen.into_inner().unwrap();
            ids.sort_unstable();
            ids.dedup();
            assert_eq!(ids, (0..threads).collect::<Vec<_>>());
        }
    }

    #[test]
    fn a_panicking_job_is_reported_and_the_pool_survives() {
        let pool = Pool::new(3);
        assert!(!pool.run(&|t| {
            if t == 2 {
                panic!("worker failure");
            }
        }));
        assert!(pool.run(&|_t| {}));
    }
}
