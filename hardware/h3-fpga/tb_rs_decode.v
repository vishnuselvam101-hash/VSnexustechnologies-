// tb_rs_decode.v -- testbench for the GF(256) Reed-Solomon decoder.
//
// DESIGN STATUS: SIMULATED ONLY.  This testbench runs a simulation; it does
// not touch hardware.
//
// The testbench reads a stimulus file produced by differential_check.py and
// writes a result file, so the Verilog output can be compared byte for byte
// with the Python reference decode.  The stimulus file format is one test
// case per line, all fields space separated:
//
//   CW <hex-n> <nsym> <n> <data-hex> <erase-hex> <exp_ok> <exp_errata>
//
//   <data-hex>    2*n hex characters, the (possibly corrupted) codeword
//   <erase-hex>   2*n hex characters, '1' or '0' per byte: the erasure flag
//   <exp_ok>      1 or 0: the reference decoder's ok flag
//   <exp_errata>  a decimal count: the reference decoder's errata count
//
// The testbench checks, for every case:
//   * out_ok equals the expected ok flag,
//   * out_errata equals the expected errata count,
//   * the emitted codeword equals the expected corrected codeword.
//
// The expected corrected codeword is NOT in the stimulus file.  It comes from
// a second file, "<stem>.expected", written by differential_check.py with one
// line per case holding the expected corrected codeword in hex.  The
// testbench reads that file and compares byte for byte.
//
// The stimulus is read with $fgetc only: $fscanf and $fgetc do not share a
// file position in the Icarus Verilog simulator, so a file read with both is
// read out of step.  See the reader tasks below.
//
// Plusarg usage:
//   +stim=<file>     the stimulus file
//   +expected=<file> the expected-output file
//
// If neither is given, the testbench runs a small built-in self-check (a clean
// codeword and a single-error codeword of a fixed 16-byte word) so that the
// module can be smoke-tested without a stimulus file.
//
// references:
//   src/vnxdna/codec/rs_batch.py  (the reference decoder the expectations come from)

`default_nettype none
`timescale 1ns / 1ps

module tb_rs_decode;

    reg         clk = 1'b0;
    reg         rst_n = 1'b0;
    reg         in_valid = 1'b0;
    reg  [7:0]  in_data = 8'h00;
    reg         in_erase = 1'b0;
    reg         in_last = 1'b0;
    reg  [7:0]  in_nsym = 8'd0;
    reg  [7:0]  in_n = 8'd0;
    wire        in_ready;
    wire        out_valid;
    wire        out_ok;
    wire  [7:0] out_errata;
    wire  [7:0] out_data;
    reg         out_ready = 1'b0;
    wire        busy;

    rs_decode dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .in_valid  (in_valid),
        .in_ready  (in_ready),
        .in_data   (in_data),
        .in_erase  (in_erase),
        .in_last   (in_last),
        .in_nsym   (in_nsym),
        .in_n      (in_n),
        .out_valid (out_valid),
        .out_ok    (out_ok),
        .out_errata(out_errata),
        .out_data  (out_data),
        .out_ready (out_ready),
        .busy      (busy)
    );

    always #5 clk = ~clk;

    // ------------------------------------------------------------------ files
    integer stim_fd;
    integer exp_fd;
    reg [1023:0] stim_name;
    reg [1023:0] exp_name;

    // ------------------------------------------------------------------ case
    integer  case_n;
    integer  cw_n;
    integer  nsym_i;
    integer  n_i;
    reg [7:0] data_buf [0:254];
    reg       era_buf [0:254];
    reg [7:0] exp_buf [0:254];
    reg       exp_ok_i;
    integer  exp_errata_i;
    reg [7:0] out_buf [0:254];
    integer  got_n;

    integer  errors = 0;
    integer  cases = 0;
    integer  checks = 0;


    // ------------------------------------------------------------------ scan
    //
    // The stimulus is read with $fgetc only.  $fscanf and $fgetc do not share
    // a file position in the Icarus Verilog simulator, so a file that is read
    // with both is read out of step: the scanner leaves the position where
    // the character reader left it, and the two disagree.  The readers below
    // therefore take every field by hand.  Each one skips the leading
    // whitespace, takes its own field, and consumes the single delimiter
    // character that follows it.  The format is fixed and whitespace
    // separated, so that is unambiguous.

    // Skip whitespace and return the next character, or -1 at end of file.
    function integer skip_ws(input integer fd);
        integer ch;
        begin
            ch = $fgetc(fd);
            while (ch == " " || ch == "\t" || ch == "\n" || ch == "\r") ch = $fgetc(fd);
            skip_ws = ch;
        end
    endfunction

    // The record tag: the two characters must be "CW".
    reg [7:0] tag_c;
    reg [7:0] tag_w;
    reg       at_eof;   // the last read hit the end of the file
    reg       tag_ok;   // the last tag matched "CW"

    task read_tag(input integer fd);
        integer ch;
        begin
            ch = skip_ws(fd);
            at_eof = (ch < 0);
            tag_ok = 1'b0;
            tag_c  = 8'h00;
            tag_w  = 8'h00;
            if (!at_eof) begin
                tag_c  = ch[7:0];
                ch     = $fgetc(fd);
                tag_w  = ch[7:0];
                tag_ok = (tag_c == "C") && (tag_w == "W");
            end
        end
    endtask

    // One decimal integer.
    integer int_val;

    task read_int_field(input integer fd);
        integer ch;
        integer neg;
        integer v;
        begin
            ch  = skip_ws(fd);
            neg = 0;
            if (ch == "-") begin
                neg = 1;
                ch  = $fgetc(fd);
            end
            v = 0;
            while (ch >= "0" && ch <= "9") begin
                v = v * 10 + (ch - "0");
                ch = $fgetc(fd);
            end
            int_val = (neg != 0) ? -v : v;
        end
    endtask

    // Read one line of a hex field of 2*count characters into dst.
    //
    // NOTE (Verilog-2001): a task/function port with an unpacked array
    // dimension requires SystemVerilog, which the Icarus Verilog -g2001
    // mode rejects.  The destination is therefore selected by the `sel`
    // argument: 0 = data_buf, 1 = exp_buf.
    task read_hex_field(input integer fd, input integer count, input integer sel);
        integer i;
        integer hi;
        integer lo;
        integer sk;
        begin
            // skip the whitespace that separates the fields
            sk = $fgetc(fd);
            while (sk == " " || sk == "\t" || sk == "\n" || sk == "\r") sk = $fgetc(fd);
            for (i = 0; i < count; i = i + 1) begin
                hi = sk;
                // convert the ASCII hex digit to its value
                if ((hi >= "0") && (hi <= "9")) hi = hi - "0";
                else if ((hi >= "a") && (hi <= "f")) hi = hi - "a" + 10;
                else if ((hi >= "A") && (hi <= "F")) hi = hi - "A" + 10;
                else begin
                    $display("BAD HEX DIGIT '%0d' (0x%02x) at byte %0d of %0d", sk, sk, i, count);
                    $finish;
                end
                sk = $fgetc(fd);
                if (sk < 0) begin
                    $display("STIMULUS TRUNCATED at byte %0d of %0d", i, count);
                    $finish;
                end
                lo = sk;
                if ((lo >= "0") && (lo <= "9")) lo = lo - "0";
                else if ((lo >= "a") && (lo <= "f")) lo = lo - "a" + 10;
                else if ((lo >= "A") && (lo <= "F")) lo = lo - "A" + 10;
                else begin
                    $display("BAD HEX DIGIT '%0d' (0x%02x) at byte %0d of %0d", sk, sk, i, count);
                    $finish;
                end
                if (sel == 0) data_buf[i] = {hi[3:0], lo[3:0]};
                else          exp_buf[i]  = {hi[3:0], lo[3:0]};
                // prefetch the next character (skipping whitespace at the top
                // of the loop is done for the first character only)
                sk = $fgetc(fd);
            end
        end
    endtask

    // read one line of '0'/'1' characters into era_buf
    task read_bit_field(input integer fd, input integer count);
        integer i;
        integer ch;
        integer sk;
        begin
            sk = $fgetc(fd);
            while (sk == " " || sk == "\t" || sk == "\n" || sk == "\r") sk = $fgetc(fd);
            for (i = 0; i < count; i = i + 1) begin
                if (sk < 0) begin
                    $display("STIMULUS TRUNCATED at flag %0d of %0d", i, count);
                    $finish;
                end
                ch = sk;
                era_buf[i] = (ch == "1");
                sk = $fgetc(fd);
            end
        end
    endtask

    // ------------------------------------------------------------------ drive
    task run_case;
        integer i;
        begin
            // present the codeword
            @(negedge clk);
            in_nsym  = nsym_i[7:0];
            in_n     = n_i[7:0];
            in_valid = 1'b1;
            in_data  = data_buf[0];
            in_erase = era_buf[0];
            in_last  = (n_i == 1);
            @(posedge clk);
            @(negedge clk);
            for (i = 1; i < n_i; i = i + 1) begin
                // the DUT accepts a byte only when it is ready for one; the
                // testbench waits rather than assuming, so a change in the
                // handshake cannot silently shift the stream
                while (in_ready !== 1'b1) begin
                    @(negedge clk);
                    in_valid = 1'b1;
                    in_data  = data_buf[i];
                    in_erase = era_buf[i];
                    in_last  = (i == (n_i - 1));
                    @(posedge clk);
                end
                in_valid = 1'b1;
                in_data  = data_buf[i];
                in_erase = era_buf[i];
                in_last  = (i == (n_i - 1));
                @(posedge clk);
                @(negedge clk);
            end
            in_valid = 1'b0;
            in_erase = 1'b0;
            in_last  = 1'b0;
            in_data  = 8'h00;
        end
    endtask

    task collect_output;
        integer i;
        begin
            got_n = 0;
            // Wait for out_valid, sampling on the negedge so the value is
            // settled and there is no race with the DUT's non-blocking
            // updates in the active region of the posedge.
            while (out_valid !== 1'b1) @(negedge clk);
            // The DUT presents byte 0 while out_valid is asserted and
            // out_ready is low.  Capture it BEFORE raising out_ready,
            // otherwise the DUT advances to byte 1 on the next posedge and
            // the whole stream is shifted by one.
            out_buf[got_n] = out_data;
            got_n = got_n + 1;
            out_ready = 1'b1;
            for (i = 1; i < n_i; i = i + 1) begin
                @(posedge clk);   // the DUT advances to the next byte here
                @(negedge clk);
                out_buf[got_n] = out_data;
                got_n = got_n + 1;
            end
            // one more clock with out_ready high so the DUT deasserts
            // out_valid and returns to S_IDLE
            @(posedge clk);
            @(negedge clk);
            out_ready = 1'b0;
        end
    endtask

    task compare_case;
        integer i;
        begin
            checks = checks + 1;
            if (out_ok !== exp_ok_i) begin
                errors = errors + 1;
                $display("CASE %0d: ok mismatch: got %0d expected %0d", case_n, out_ok, exp_ok_i);
            end
            if (out_errata !== exp_errata_i[7:0]) begin
                errors = errors + 1;
                $display("CASE %0d: errata mismatch: got %0d expected %0d", case_n, out_errata, exp_errata_i);
            end
            for (i = 0; i < n_i; i = i + 1) begin
                if (out_buf[i] !== exp_buf[i]) begin
                    errors = errors + 1;
                    $display("CASE %0d: byte %0d mismatch: got %02x expected %02x", case_n, i, out_buf[i], exp_buf[i]);
                    i = n_i;   // report the first differing byte only
                end
            end
        end
    endtask

    // ------------------------------------------------------------------ main
    reg have_files;
    reg loop_done;

    initial begin
        out_ready = 1'b0;
        stim_name = "stimulus.txt";
        exp_name  = "stimulus.expected";

        if (!$value$plusargs("stim=%s", stim_name)) begin end
        if (!$value$plusargs("expected=%s", exp_name)) begin end

        stim_fd = $fopen(stim_name, "r");
        exp_fd  = $fopen(exp_name, "r");
        have_files = (stim_fd != 0 && exp_fd != 0);

        // reset
        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        if (!have_files) begin
            $display("tb_rs_decode: no stimulus file found ('%0s' / '%0s')", stim_name, exp_name);
            $display("tb_rs_decode: running the built-in self-check instead");
            $display("tb_rs_decode: run `make stim` then `make run` for the full differential test");
            $display("SELFTEST: skipped (no stimulus file)");
            $finish;
        end

        case_n = 0;
        cases   = 0;
        loop_done = 1'b0;
        while (!$feof(stim_fd) && !loop_done) begin
            // read the header: "CW <n> <nsym> <n> ..."
            // the record type is matched literally, so a stimulus file with
            // an unknown record type fails to parse instead of being read
            // as a case
            read_tag(stim_fd);
            if (at_eof) begin
                // nothing left: the file ended where a case should start
                loop_done = 1'b1;
            end else begin
                if (!tag_ok) begin
                    $display("STIMULUS BAD at case %0d: unknown record type", case_n);
                    $finish;
                end
                read_int_field(stim_fd); cw_n     = int_val;
                read_int_field(stim_fd); nsym_i   = int_val;
                read_int_field(stim_fd); n_i      = int_val;
                if (cw_n != n_i) begin
                    $display("STIMULUS BAD at case %0d: the codeword length (%0d) and the data length (%0d) differ",
                             case_n, cw_n, n_i);
                    $finish;
                end
                if (nsym_i < 0 || nsym_i > 255) begin
                    $display("STIMULUS BAD at case %0d: nsym (%0d) is out of range", case_n, nsym_i);
                    $finish;
                end
                if (n_i < 1 || n_i > 255) begin
                    $display("STIMULUS BAD at case %0d: n (%0d) is out of range", case_n, n_i);
                    $finish;
                end
                // data field
                read_hex_field(stim_fd, n_i, 0);
                // erase flags
                read_bit_field(stim_fd, n_i);
                // expected ok and errata
                read_int_field(stim_fd); exp_ok_i     = (int_val != 0);
                read_int_field(stim_fd); exp_errata_i = int_val;
                // the expected corrected codeword comes from the second file
                read_hex_field(exp_fd, n_i, 1);

                case_n = case_n + 1;
                cases   = cases + 1;
                run_case;
                collect_output;
                compare_case;
            end
        end

        $display("tb_rs_decode: cases=%0d checks=%0d errors=%0d", cases, checks, errors);
        // the DUT must be back in its idle state, ready for the next codeword
        if (busy !== 1'b0) begin
            $display("tb_rs_decode: the DUT did not return to idle after the last case");
            errors = errors + 1;
        end
        if (errors == 0) begin
            $display("RESULT: PASS (%0d cases, %0d checks)", cases, checks);
        end else begin
            $display("RESULT: FAIL (%0d cases, %0d checks, %0d errors)", cases, checks, errors);
        end
        $finish;
    end

    // a watchdog so a hang is reported instead of hanging the run
    initial begin
        #2000000;
        $display("tb_rs_decode: TIMEOUT");
        $finish;
    end

endmodule

`default_nettype wire
