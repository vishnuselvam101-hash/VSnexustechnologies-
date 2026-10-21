// rs_decode.v -- GF(2^8) Reed-Solomon errors-and-erasures decoder for the
// VNX-DNA inner strand code.
//
// DESIGN STATUS: SIMULATED ONLY.  This file has been elaborated and simulated
// with Icarus Verilog and linted with Verilator; it has NOT been synthesised,
// placed and routed, and no FPGA has been built, powered or measured.  Every
// performance figure derived from it is a THEORETICAL cycle count, not a
// measurement.
//
// ALGORITHM: a phase-by-phase transcription of the software reference
//   src/vnxdna/codec/rs_batch.py  (_decode_block)   -- the reference decoder
//   src/vnxdna/codec/rs_fast.py   (_decode_block)   -- the vectorised twin
// with the same field, the same polynomial conventions and the same acceptance
// test, so a decoded codeword is byte-for-byte identical to the Python
// reference output.
//
// FIELD: GF(2^8) with primitive polynomial 0x11D (x^8+x^4+x^3+x^2+1),
// generator alpha = 2, first consecutive root alpha^0 (fcr = 0).  These are the
// values in src/vnxdna/codec/gf256.py and src/vnxdna/codec/inner_parity.py.
//
// CODE: systematic RS(n, n-nsym) over GF(2^8).  The parity matrix is the one of
// vnxdna.codec.inner_parity.parity_matrix(k, nsym), i.e. the remainder of
// e_i * x^nsym modulo g(x) = prod_{j<nsym} (x - alpha^j).  A codeword is
// c[0..n-1] with c[0] the highest-degree coefficient.
//
// CONVENTIONS (these are what make the output bit-exact):
//   * codeword position j has locator X_j = alpha^(n-1-j)  (rs_batch._alpha_powers)
//   * syndromes S_i = c(alpha^i) for i = 0..nsym-1, by Horner over c starting
//     from zero, i.e. S_i = sum_j c_j * alpha^(i*(n-1-j))
//   * erasure locator Gamma(x) = prod_{flagged j} (1 + X_j x), low degree first
//   * errata locator Lambda(x), low degree first, degree L
//   * Chien search: position j is a root iff Lambda(X_j^-1) == 0
//   * Forney: e_j = X_j * Omega(X_j^-1) / Lambda'(X_j^-1) with
//              Omega = (S * Lambda) mod x^nsym and
//              Lambda' the formal derivative (odd-index coefficients in char 2)
//   * ACCEPTANCE (identical to the reference): the decode succeeds only if
//       (a) L == deg(Lambda),
//       (b) 2*(L - f) + f <= nsym, where f is the number of flagged erasures,
//       (c) L <= n,
//       (d) Lambda has exactly L distinct roots among the codeword positions,
//       (e) every flagged erasure position is one of those roots,
//       (f) no root has Lambda'(X_j^-1) == 0,
//       (g) the CORRECTED codeword has all-zero syndromes.
//     If any test fails, the codeword is returned UNCHANGED with ok = 0 and
//     errata = 0, exactly as rs_batch does.  The decoder never returns a
//     "best effort" codeword: it either reproduces the reference's corrected
//     codeword or it fails.
//
// STRUCTURE: one algorithm phase per clock cycle.  Each phase computes its
// whole result combinationally and registers it at the end of the cycle.  The
// result is a correct decoder with a large (unoptimised) area, chosen so that
// the arithmetic can be checked line by line against the Python reference.  A
// pipelined, area-optimised version is future work; the cycle counts quoted in
// docs/HARDWARE_DESIGN.md are the counts of THIS implementation.
//
// CODING STYLE: the state-carrying registers (nsym_q, n_q, f_q, big_l_q,
// nroots_q, degree_q, dezero_q, feasible_q, use_cand_q, corrected_q, the
// memories and the output registers) are assigned with NON-BLOCKING
// assignments only.  The scratch values that are consumed within a single
// phase (acc, xloc, xinv, delta, omval, deval, big_l, nroots, degree, dezero)
// are declared INSIDE the named clocked block and are plain combinational
// temporaries.  The array memories (cw_mem, cand_mem, era_mem, root_mem, syn,
// gam, lam, bb, tt, bn, omg, drv) are written with blocking assignments inside
// the phase that computes them and are read only in a later phase.
//
// SYNTHESIS: Verilog-2001, no vendor primitives, no SystemVerilog, no
// behavioural arithmetic wider than 9 bits, no dynamic arrays.  A synthesis
// run has NOT been performed.
//
// references:
//   src/vnxdna/codec/gf256.py          (field; EXP/LOG tables)
//   src/vnxdna/codec/rs_batch.py       (reference decoder transcribed here)
//   src/vnxdna/codec/rs_fast.py        (vectorised twin of the same algorithm)
//   src/vnxdna/codec/inner_parity.py   (parity matrix / generator polynomial)
//   src/vnxdna/codec/codecs.py         (InnerRS wrapper, "reed-solomon-gf256")

// LINTING: the scratch variables of a phase are assigned with blocking assignments
// on purpose.  They are combinational temporaries of ONE phase: a value written
// early in a phase is read later in the SAME phase, and the phase's result is
// registered at the end of the cycle.  Turning them into non-blocking assignments
// would read the PREVIOUS cycle's value and silently break the algorithm.
//
// The BLKSEQ check flags every one of them, so the check is switched off for this
// file with the other checks left on: WIDTHTRUNC and WIDTHEXPAND, which caught
// real truncation bugs, stay enabled and must stay clean.  A new file must not
// inherit this exemption.

/* verilator lint_off BLKSEQ */

`default_nettype none

// The RTL is synthesised, not simulated, and carries no delays of its own; the
// timescale is declared so that a testbench's timescale does not change the
// meaning of the (delay-free) RTL.
`timescale 1ns / 1ps

module rs_decode (
    input  wire        clk,
    input  wire        rst_n,

    // ---- input stream: the codeword, most-significant coefficient first
    input  wire        in_valid,
    output wire        in_ready,
    input  wire [7:0]  in_data,
    input  wire        in_erase,
    input  wire        in_last,
    input  wire [7:0]  in_nsym,      // number of parity symbols, 0..64
    input  wire [7:0]  in_n,         // codeword length in bytes, 1..255

    // ---- output stream: the corrected codeword
    output reg         out_valid,
    output reg         out_ok,
    output reg  [7:0]  out_errata,
    output reg  [7:0]  out_data,
    input  wire        out_ready,

    // ---- status
    output wire        busy
);

`include "gf256_rom.vh"

    // ------------------------------------------------------------------ sizes
    localparam CW_MAX   = 255;   // the field allows n <= 255
    localparam NSYM_MAX = 64;    // vnxdna.codec.codecs.InnerRS caps parity at 64
    localparam W_MAX    = NSYM_MAX + 1;   // polynomial slots, degree <= nsym

    // ------------------------------------------------------------------ GF(256)
    // gf_mul is bit-identical to vnxdna.codec.gf256.mul:
    //     mul(a,b) = 0 if a==0 or b==0, else EXP[LOG[a]+LOG[b]]
    // The software EXP table has 512 entries precisely so that LOG[a]+LOG[b]
    // (<= 508) needs no modular reduction; the ROM here has the same 512
    // entries, so the expression below is literally the software one.
    function [7:0] gf_mul(input [7:0] a, input [7:0] b);
        reg [8:0] la;
        reg [8:0] lb;
        begin
            if ((a == 8'h00) || (b == 8'h00)) begin
                gf_mul = 8'h00;
            end else begin
                la = {1'b0, gf_log_rom(a)};
                lb = {1'b0, gf_log_rom(b)};
                gf_mul = gf_exp_rom(la + lb);
            end
        end
    endfunction

    // 1/a for a != 0.  gf256.inv(a) = EXP[255 - LOG[a]]; EXP has 512 entries so
    // the index 255-LOG[a] is in range for every LOG[a] in 0..254, and
    // EXP[255] == EXP[0] == 1 covers a == 1.  gf_inv(0) returns 0 and is never
    // used on zero (the reference guards every division).
    function [7:0] gf_inv(input [7:0] a);
        reg [8:0] l;
        begin
            if (a == 8'h00) begin
                gf_inv = 8'h00;
            end else begin
                l = {1'b0, gf_log_rom(a)};
                gf_inv = gf_exp_rom(9'd255 - l);
            end
        end
    endfunction

    // ------------------------------------------------------------------ state
    reg [7:0]  cw_mem  [0:CW_MAX-1];    // the codeword as received
    reg [7:0]  cand_mem[0:CW_MAX-1];    // the corrected codeword (Forney output)
    reg        era_mem  [0:CW_MAX-1];    // the erasure flags as received
    reg        root_mem [0:CW_MAX-1];    // Chien search result per position

    reg [7:0]  syn [0:NSYM_MAX-1];      // syndromes S_0..S_{nsym-1}
    reg [7:0]  gam [0:W_MAX-1];          // erasure locator Gamma
    reg [7:0]  lam [0:W_MAX-1];          // errata locator Lambda
    reg [7:0]  bb  [0:W_MAX-1];          // Berlekamp-Massey auxiliary B
    reg [7:0]  tt  [0:W_MAX-1];          // scratch x*B
    reg [7:0]  bn  [0:W_MAX-1];          // scratch t
    reg [7:0]  omg [0:NSYM_MAX-1];      // Omega = (S * Lambda) mod x^nsym
    reg [7:0]  drv [0:W_MAX-1];          // formal derivative of Lambda

    reg [7:0]  nsym_q;
    reg [7:0]  n_q;
    reg [8:0]  count_q;
    reg [7:0]  f_q;          // number of flagged erasures
    reg [7:0]  big_l_q;      // L
    reg [7:0]  nroots_q;
    reg [7:0]  degree_q;
    reg [8:0]  diff_q;       // L - f
    reg        feasible_q;
    reg        dezero_q;     // a root with Lambda'(X^-1) == 0 was seen
    reg        corrected_q;   // output cand_mem (1) or cw_mem (0)
    reg        use_cand_q;   // the Forney phase ran, so cand_mem is meaningful

    reg [7:0]  out_idx_q;

    // ------------------------------------------------------------------ FSM
    localparam S_IDLE      = 6'd0;
    localparam S_RECV      = 6'd1;
    localparam S_SYND      = 6'd2;
    localparam S_CHECK     = 6'd3;
    localparam S_BM        = 6'd4;
    localparam S_CHIEN     = 6'd5;
    localparam S_OMEGA     = 6'd6;
    localparam S_DERIV     = 6'd7;
    localparam S_FORNEY    = 6'd8;
    localparam S_VERIFY    = 6'd9;
    localparam S_DEGREE    = 6'd10;
    localparam S_FEASIBLE  = 6'd11;
    localparam S_ERASCHK   = 6'd12;
    localparam S_OUTPREP   = 6'd13;
    localparam S_OUT       = 6'd14;

    reg [5:0]  state;

    assign busy     = (state != S_IDLE);
    assign in_ready = (state == S_IDLE) || (state == S_RECV);

    // clean_now: all syndromes zero (the reference's "clean" flag).
    reg clean_now;
    integer z;
    always @* begin
        clean_now = 1'b1;
        for (z = 0; z < NSYM_MAX; z = z + 1) begin
            if ((z < nsym_q) && (syn[z] != 8'h00)) clean_now = 1'b0;
        end
    end

    // ------------------------------------------------------------------ core
    // The named block gives the scratch variables block scope, so they cannot
    // be confused with the state-carrying registers.
    always @(posedge clk) begin : rs_proc

        integer i;
        integer p;
        integer q;
        integer k;

        reg [7:0]  acc;
        reg [7:0]  xloc;
        reg [7:0]  xinv;
        reg [7:0]  delta;
        reg [7:0]  omval;
        reg [7:0]  deval;
        reg [7:0]  big_l;
        reg [7:0]  nroots;
        reg [7:0]  degree;
        reg        dezero;
        reg [8:0]  wide_a;
        reg [8:0]  wide_b;
        reg [8:0]  diff_next;
        reg [8:0]  expidx;

        if (!rst_n) begin
            state       <= S_IDLE;
            out_valid   <= 1'b0;
            out_ok      <= 1'b0;
            out_errata  <= 8'd0;
            out_data    <= 8'h00;
            nsym_q      <= 8'd0;
            n_q         <= 8'd0;
            count_q     <= 9'd0;
            f_q         <= 8'd0;
            big_l_q     <= 8'd0;
            nroots_q    <= 8'd0;
            degree_q    <= 8'd0;
            diff_q      <= 9'd0;
            feasible_q  <= 1'b0;
            dezero_q    <= 1'b0;
            corrected_q <= 1'b0;
            use_cand_q  <= 1'b0;
            out_idx_q   <= 8'd0;
        end else begin
            case (state)

            // ---------------------------------------------------------------
            // IDLE: accept the first byte of a codeword.
            // ---------------------------------------------------------------
            S_IDLE: begin
                out_valid <= 1'b0;
                if (in_valid) begin
                    nsym_q      <= in_nsym;
                    n_q         <= in_n;
                    count_q     <= 9'd1;
                    f_q         <= in_erase ? 8'd1 : 8'd0;
                    cw_mem[0]   <= in_data;
                    era_mem[0]   <= in_erase;
                    feasible_q  <= 1'b0;
                    nroots_q    <= 8'd0;
                    degree_q    <= 8'd0;
                    diff_q      <= 9'd0;
                    dezero_q    <= 1'b0;
                    corrected_q <= 1'b0;
                    use_cand_q  <= 1'b0;
                    big_l_q     <= 8'd0;
                    if (in_last) state <= S_SYND;
                    else         state <= S_RECV;
                end
            end

            // ---------------------------------------------------------------
            // RECV: collect the remaining bytes.
            // ---------------------------------------------------------------
            S_RECV: begin
                if (in_valid) begin
                    cw_mem[count_q[7:0]] <= in_data;
                    era_mem[count_q[7:0]] <= in_erase;
                    if (in_erase) f_q <= f_q + 8'd1;
                    if (in_last) state <= S_SYND;
                    else         count_q <= count_q + 9'd1;
                end
            end

            // ---------------------------------------------------------------
            // SYND: S_i = c(alpha^i) = sum_j c_j * alpha^(i*(n-1-j)).
            // Horner over c, starting from zero:
            //     acc = 0;  for j = 0..n-1:  acc = alpha^i * acc ^ c[j]
            // ---------------------------------------------------------------
            S_SYND: begin
                for (i = 0; i < NSYM_MAX; i = i + 1) begin
                    if (i < nsym_q) begin
                        acc = 8'h00;
                        expidx = i[8:0];
                        for (p = 0; p < CW_MAX; p = p + 1) begin
                            if (p < n_q) acc = gf_mul(gf_exp_rom(expidx), acc) ^ cw_mem[p];
                        end
                        syn[i] = acc;
                    end else begin
                        syn[i] = 8'h00;
                    end
                end
                state <= S_CHECK;
            end

            // ---------------------------------------------------------------
            // CHECK: the reference sets ok = clean AND (f <= nsym) and skips
            // the whole decode when nsym == 0 or when f > nsym.
            // ---------------------------------------------------------------
            S_CHECK: begin
                if (nsym_q == 8'd0) begin
                    // reference: nsym == 0 -> ok, codeword unchanged
                    feasible_q  <= 1'b1;
                    corrected_q <= 1'b0;
                    state       <= S_OUTPREP;
                end else if (f_q > nsym_q) begin
                    feasible_q  <= 1'b0;
                    corrected_q <= 1'b0;
                    state       <= S_OUTPREP;
                end else if (clean_now) begin
                    // clean codeword: nothing to correct
                    feasible_q  <= 1'b1;
                    corrected_q <= 1'b0;
                    state       <= S_OUTPREP;
                end else begin
                    // erasure locator Gamma(x) = prod (1 + X_j x), low degree first
                    for (i = 0; i < W_MAX; i = i + 1) gam[i] = 8'h00;
                    gam[0] = 8'h01;
                    for (p = 0; p < CW_MAX; p = p + 1) begin
                        if ((p < n_q) && era_mem[p]) begin
                            expidx = {1'b0, n_q} - 9'd1 - p[8:0];
                            xloc = gf_exp_rom(expidx);
                            // descending, so gam[i-1] is still the old value
                            for (i = {24'd0, nsym_q}; i > 0; i = i - 1) begin
                                gam[i] = gam[i] ^ gf_mul(xloc, gam[i - 1]);
                            end
                        end
                    end
                    state <= S_BM;
                end
            end

            // ---------------------------------------------------------------
            // BERLEKAMP-MASSEY (errors and erasures, Blahut form), exactly as
            // in rs_batch._decode_block:
            //   lam = b = Gamma, L = f; steps r = 1..nsym, skipping r <= f.
            //   delta = sum_j lam[j] * S_{r-1-j}
            //   t     = lam - delta * x * b
            //   grow  = (delta != 0) and (2L <= r-1+f)
            //   b     = grow ? delta^-1 * lam : x * b
            //   L     = grow ? r+f-L : L
            //   lam   = (delta != 0) ? t : lam
            // ---------------------------------------------------------------
            S_BM: begin
                for (i = 0; i < W_MAX; i = i + 1) begin
                    if (i <= nsym_q) begin
                        lam[i] = gam[i];
                        bb[i]  = gam[i];
                    end
                end
                big_l = f_q;
                for (q = 1; q <= NSYM_MAX; q = q + 1) begin
                    if ((q <= nsym_q) && (q > f_q)) begin
                        // delta = sum_{k=0..nsym} lam[k] * S_{q-1-k}
                        delta = 8'h00;
                        for (k = 0; k <= nsym_q; k = k + 1) begin
                            if (k <= (q - 1)) delta = delta ^ gf_mul(lam[k], syn[(q - 1) - k]);
                        end
                        // xb[k] = b[k-1]   (xb[0] = 0)
                        for (i = 0; i < W_MAX; i = i + 1) tt[i] = 8'h00;
                        for (i = 1; i <= nsym_q; i = i + 1) tt[i] = bb[i - 1];
                        // t[k] = lam[k] ^ delta * xb[k]
                        for (i = 0; i < W_MAX; i = i + 1) bn[i] = lam[i] ^ gf_mul(delta, tt[i]);
                        // grow?
                        wide_a = {1'b0, big_l} << 1;
                        wide_b = q[8:0] - 9'd1 + {1'b0, f_q};
                        if ((delta != 8'h00) && (wide_a <= wide_b)) begin
                            // b = delta^-1 * lam (the OLD lam) ; L = q + f - L
                            for (i = 0; i < W_MAX; i = i + 1) begin
                                if (i <= nsym_q) bb[i] = gf_mul(gf_inv(delta), lam[i]);
                            end
                            big_l = q[7:0] + f_q - big_l;
                        end else begin
                            // b = x * b
                            for (i = 0; i < W_MAX; i = i + 1) begin
                                if (i <= nsym_q) bb[i] = tt[i];
                            end
                        end
                        // lam = (delta != 0) ? t : lam
                        if (delta != 8'h00) begin
                            for (i = 0; i < W_MAX; i = i + 1) begin
                                if (i <= nsym_q) lam[i] = bn[i];
                            end
                        end
                    end
                end
                big_l_q <= big_l;
                state <= S_CHIEN;
            end

            // ---------------------------------------------------------------
            // CHIEN SEARCH: position j is a root iff Lambda(X_j^-1) == 0.
            // Lambda is evaluated over all nsym+1 coefficient slots.
            // ---------------------------------------------------------------
            S_CHIEN: begin
                nroots = 8'd0;
                for (p = 0; p < CW_MAX; p = p + 1) begin
                    if (p < n_q) begin
                        expidx = {1'b0, n_q} - 9'd1 - p[8:0];
                        xloc = gf_exp_rom(expidx);
                        xinv = gf_inv(xloc);
                        acc  = 8'h00;
                        for (k = {24'd0, nsym_q}; k > 0; k = k - 1) begin
                            acc = gf_mul(acc, xinv) ^ lam[k];
                        end
                        acc = gf_mul(acc, xinv) ^ lam[0];
                        root_mem[p] = (acc == 8'h00);
                        if (acc == 8'h00) nroots = nroots + 8'd1;
                    end else begin
                        root_mem[p] = 1'b0;
                    end
                end
                nroots_q <= nroots;
                state <= S_OMEGA;
            end

            // ---------------------------------------------------------------
            // OMEGA = (S * Lambda) mod x^nsym, low degree first.
            //   omega[k+t] ^= lam[k] * S[t]   for k = 0..nsym-1, t < nsym-k
            // ---------------------------------------------------------------
            S_OMEGA: begin
                for (i = 0; i < NSYM_MAX; i = i + 1) omg[i] = 8'h00;
                for (k = 0; k < nsym_q; k = k + 1) begin
                    if (lam[k] != 8'h00) begin
                        for (i = 0; i < NSYM_MAX; i = i + 1) begin
                            if (i < ({24'd0, nsym_q} - k)) omg[k + i] = omg[k + i] ^ gf_mul(lam[k], syn[i]);
                        end
                    end
                end
                state <= S_DERIV;
            end

            // ---------------------------------------------------------------
            // Formal derivative of Lambda in characteristic 2: the odd-index
            // coefficients.  deriv[2m] = lam[2m+1], the rest zero.
            // ---------------------------------------------------------------
            S_DERIV: begin
                for (i = 0; i < W_MAX; i = i + 1) begin
                    drv[i] = 8'h00;
                    if (((i % 2) == 0) && ((i + 1) <= nsym_q)) drv[i] = lam[i + 1];
                end
                state <= S_FORNEY;
            end

            // ---------------------------------------------------------------
            // FORNEY: e_j = X_j * Omega(X_j^-1) / Lambda'(X_j^-1).
            // The divisor is 1 when the derivative vanishes (the reference's
            // safe_de); that case makes the decode fail the (f) test anyway.
            // ---------------------------------------------------------------
            S_FORNEY: begin
                dezero = 1'b0;
                for (p = 0; p < CW_MAX; p = p + 1) begin
                    if (p < n_q) begin
                        expidx = {1'b0, n_q} - 9'd1 - p[8:0];
                        xloc = gf_exp_rom(expidx);
                        xinv = gf_inv(xloc);
                        acc  = 8'h00;
                        for (k = {24'd0, nsym_q} - 32'd1; k > 0; k = k - 1) begin
                            acc = gf_mul(acc, xinv) ^ omg[k];
                        end
                        acc   = gf_mul(acc, xinv) ^ omg[0];
                        omval = acc;
                        acc   = 8'h00;
                        for (k = {24'd0, nsym_q}; k > 0; k = k - 1) begin
                            acc = gf_mul(acc, xinv) ^ drv[k];
                        end
                        acc   = gf_mul(acc, xinv) ^ drv[0];
                        deval = acc;
                        if (root_mem[p]) begin
                            if (deval == 8'h00) dezero = 1'b1;
                            cand_mem[p] = cw_mem[p] ^ gf_mul(gf_mul(xloc, omval),
                                                             gf_inv(deval == 8'h00 ? 8'h01 : deval));
                        end else begin
                            cand_mem[p] = cw_mem[p];
                        end
                    end else begin
                        cand_mem[p] = cw_mem[p];
                    end
                end
                dezero_q   <= dezero;
                use_cand_q <= 1'b1;    // cand_mem has just been written
                state <= S_VERIFY;
            end

            // ---------------------------------------------------------------
            // VERIFY: recompute the syndromes of the corrected codeword; they
            // must all be zero.
            // ---------------------------------------------------------------
            S_VERIFY: begin
                feasible_q <= 1'b1;
                for (i = 0; i < NSYM_MAX; i = i + 1) begin
                    if (i < nsym_q) begin
                        acc = 8'h00;
                        expidx = i[8:0];
                        for (p = 0; p < CW_MAX; p = p + 1) begin
                            if (p < n_q) acc = gf_mul(gf_exp_rom(expidx), acc) ^ cand_mem[p];
                        end
                        if (acc != 8'h00) feasible_q <= 1'b0;
                    end
                end
                state <= S_DEGREE;
            end

            // ---------------------------------------------------------------
            // DEGREE of Lambda: the highest non-zero slot, or 0 when Lambda is
            // identically zero (the reference's np.where(lam.any(), ..., 0)).
            // ---------------------------------------------------------------
            S_DEGREE: begin
                degree = 8'd0;
                for (i = {24'd0, nsym_q}; i >= 0; i = i - 1) begin
                    if (lam[i] != 8'h00) begin
                        degree = i[7:0];
                        i = -1;      // stop the loop
                    end
                end
                degree_q <= degree;
                state <= S_FEASIBLE;
            end

            // ---------------------------------------------------------------
            // FEASIBILITY: the reference's acceptance tests (a), (c), (d), (f).
            // ---------------------------------------------------------------
            S_FEASIBLE: begin
                if (degree_q != big_l_q) feasible_q <= 1'b0;
                if (big_l_q > n_q)       feasible_q <= 1'b0;
                if (nroots_q != big_l_q) feasible_q <= 1'b0;
                if (dezero_q)            feasible_q <= 1'b0;
                diff_next = {1'b0, big_l_q} - {1'b0, f_q};
                diff_q <= diff_next;
                state <= S_ERASCHK;
            end

            // (e) every flagged erasure must be a root; then (b).
            S_ERASCHK: begin
                for (p = 0; p < CW_MAX; p = p + 1) begin
                    if ((p < n_q) && era_mem[p] && !root_mem[p]) feasible_q <= 1'b0;
                end
                // (b) 2*(L - f) + f <= nsym
                if (((diff_q << 1) + {1'b0, f_q}) > {1'b0, nsym_q}) feasible_q <= 1'b0;
                state <= S_OUTPREP;
            end

            // ---------------------------------------------------------------
            // OUT: stream the result.  On failure the original codeword is
            // returned unchanged, exactly as the reference does.
            // ---------------------------------------------------------------
            S_OUTPREP: begin
                out_ok     <= feasible_q;
                out_errata <= feasible_q ? nroots_q : 8'd0;
                // The corrected codeword is used only when the Forney phase
                // actually ran AND the decode was accepted.  On the clean
                // path (no byte errors) the reference returns the codeword
                // UNCHANGED, and cand_mem still holds the PREVIOUS case's
                // contents, so it must not be selected here.
                corrected_q <= feasible_q && use_cand_q;
                out_idx_q  <= 8'd0;
                out_data   <= (feasible_q && use_cand_q) ? cand_mem[0] : cw_mem[0];
                out_valid  <= 1'b1;
                state      <= S_OUT;
            end

            S_OUT: begin
                if (out_valid && out_ready) begin
                    if (out_idx_q + 8'd1 >= n_q) begin
                        out_valid <= 1'b0;
                        state     <= S_IDLE;
                    end else begin
                        out_idx_q <= out_idx_q + 8'd1;
                        out_data  <= corrected_q ? cand_mem[out_idx_q + 8'd1] : cw_mem[out_idx_q + 8'd1];
                    end
                end
            end

            default: state <= S_IDLE;
            endcase
        end
    end

endmodule

/* verilator lint_on BLKSEQ */

`default_nettype wire
