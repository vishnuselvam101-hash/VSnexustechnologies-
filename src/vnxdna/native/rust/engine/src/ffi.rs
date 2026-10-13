//! The reused C kernels, linked statically (build.rs). Signatures as in the C sources; every call site passes buffers
//! whose sizes it derived itself, and checks the return code.

extern "C" {
    pub fn vnx_or_scan(n: i64, codes: *const u8, ncodes: i64, lengths: *const i64, offs: *const i64, nm: i64,
                       mpos: *const i64, want: *const u8, block: *const i64, nblocks: i64, margin: i64,
                       max_shift: i64, flip: *mut u8) -> i32;
    pub fn vnx_or_rc_bound(n: i64, codes: *const u8, ncodes: i64, lengths: *const i64, offs: *const i64, nm: i64,
                           mpos: *const i64, want: *const u8, block: *const i64, nblocks: i64, band: i64, c_mm: i64,
                           c_ins: i64, c_del: i64, out: *mut i64) -> i32;
    pub fn vnx_or_spec_project(n: i64, codes: *const u8, ncodes: i64, lengths: *const i64, offs: *const i64,
                               quals: *const u8, min_q: i64, t: i64, nm: i64, mpos: *const i64, want: *const u8,
                               block: *const i64, nblocks: i64, frame_nt: i64, frame_pos: *const i64, band: i64,
                               out_ok: *mut u8, out_bases: *mut u8, out_erased: *mut u8, out_mm: *mut i64,
                               out_indels: *mut i64) -> i32;
    pub fn vnx_or_pack_projection(n: i64, bases: *const u8, erased: *const u8, f: i64, out_hard: *mut u8,
                                  out_proj: *mut u8, out_frames: *mut u8, out_erased_bytes: *mut u8) -> i32;
    pub fn vnx_or_pack_exact(n: i64, codes: *const u8, ncodes: i64, offs: *const i64, quals: *const u8, min_q: i64,
                             t: i64, nm: i64, mpos: *const i64, want: *const u8, f: i64, frame_pos: *const i64,
                             out_hard: *mut u8, out_proj: *mut u8, out_frames: *mut u8, out_erased_bytes: *mut u8,
                             out_mm: *mut i64) -> i32;
    pub fn vnx_align_batch_sel_lv(n: i64, codes: *const u8, starts: *const i64, lens: *const i64, total_len: i64,
                                  quals: *const u8, has_qual: *const u8, t: i32, tpl: *const i16, seg_of: *const i32,
                                  prev_seg: *const i32, next_seg: *const i32, n_segments: i32, frame_nt: i32,
                                  frame_pos: *const i32, seg_frame: *const i32, band: i32, c_mm: i32, c_ins: i32,
                                  c_del: i32, c_x: i32, guard: i32, min_q: i32, out_bases: *mut u8,
                                  out_erased: *mut u8, out_ok: *mut u8, out_ins: *mut i64, out_del: *mut i64,
                                  out_mm: *mut i64, out_cost: *mut i64, lanes: i32) -> i32;
    pub fn vnx_rs_decode_batch(n_words: i64, n: i32, nsym: i32, cw: *const u8, erase: *const u8, out: *mut u8,
                               ok: *mut u8, errata: *mut i64, level: i32) -> i32;
}
