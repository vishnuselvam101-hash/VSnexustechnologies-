/** Integration with the Python CLI of this repository (VNX_TEST_PYTHON, default python3; PYTHONPATH = ../src). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { nativeStatus, vnx, VnxCommandError } from "../src/client.ts";

const SRC = fileURLToPath(new URL("../../src", import.meta.url));
const PY = process.env.VNX_TEST_PYTHON ?? "python3";
const env = { ...process.env, PYTHONPATH: SRC };
const VNX = [PY, "-c", "import sys; from vnxdna.commands import main; sys.argv[0] = 'vnx'; main()"];

test("vnx native returns a validated envelope", async () => {
  const r = await vnx(["native"], { command: VNX, env, timeoutMs: 120_000 });
  assert.equal(r.kind, "native");
  assert.equal(r.error, null);
});

test("native status lists every kernel with its backend", async () => {
  const st = await nativeStatus({ command: [PY, "-m", "vnxdna.native"], env, timeoutMs: 120_000 });
  for (const k of ["align", "reads", "rs", "cluster", "strand", "integrity"]) assert.ok(k in st.kernels, k);
});

test("a failing command raises VnxCommandError carrying the vnx.error/1 object", async () => {
  await assert.rejects(vnx(["decode", "/nonexistent/reads.fastq", "-o", "/nonexistent/out.vnx"], { command: VNX, env,
    timeoutMs: 120_000 }), (e: unknown) => {
    assert.ok(e instanceof VnxCommandError);
    assert.notEqual(e.exitCode, 0);
    return true;
  });
});

test("the vnx.error/1 object the CLI writes to stderr reaches VnxCommandError.vnxError", async () => {
  await assert.rejects(vnx(["decode", "/nonexistent/reads.fastq", "-o", "/nonexistent/out.vnx"], { command: VNX, env,
    timeoutMs: 120_000 }), (e: unknown) => {
    assert.ok(e instanceof VnxCommandError);
    assert.equal(e.exitCode, 3, e.stderr);
    assert.equal(e.vnxError?.code, "FORMAT_ERROR");
    assert.equal(e.vnxError?.exit_code, 3);
    return true;
  });
});

test("a missing executable is a typed error, not a crash", async () => {
  await assert.rejects(vnx(["native"], { command: ["/nonexistent/vnx"] }), VnxCommandError);
});
