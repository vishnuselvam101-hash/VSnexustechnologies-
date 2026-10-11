import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { checkError, checkEvent, checkResult, ERROR_CATEGORIES, EVENT_KINDS, isCompleteRecovery, RESULT_KINDS,
  RESULT_STATUSES } from "../src/contracts.ts";

const SCHEMAS = fileURLToPath(new URL("../../src/vnxdna/core/schemas/", import.meta.url));
const FIX = fileURLToPath(new URL("./fixtures/", import.meta.url));
const schema = (n: string) => JSON.parse(readFileSync(`${SCHEMAS}${n}.schema.json`, "utf8"));
const fixture = (n: string) => readFileSync(`${FIX}${n}`, "utf8");

test("enums equal the normative JSON schemas (no drift)", () => {
  assert.deepEqual([...EVENT_KINDS], schema("event").properties.event.enum);
  assert.deepEqual([...RESULT_STATUSES], schema("result").properties.status.enum);
  assert.deepEqual([...RESULT_KINDS], schema("result").properties.kind.enum);
  assert.deepEqual([...ERROR_CATEGORIES], schema("error").properties.category.enum);
  for (const req of schema("event").required) assert.ok(typeof req === "string");
});

test("a real decode report, CLI envelope and event log validate", () => {
  const report = checkResult(JSON.parse(fixture("report.json")));
  assert.ok(report.ok, report.ok ? "" : report.error);
  assert.equal(report.value.kind, "decode");
  assert.equal(report.value.status, "SUCCESS");
  const stdout = checkResult(JSON.parse(fixture("decode-stdout.json")));
  assert.ok(stdout.ok, stdout.ok ? "" : stdout.error);
  const lines = fixture("events.jsonl").trim().split("\n");
  assert.ok(lines.length >= 3);
  for (const l of lines) {
    const c = checkEvent(JSON.parse(l));
    assert.ok(c.ok, c.ok ? "" : c.error);
  }
});

test("guards reject malformed records with a named field", () => {
  const ev = JSON.parse(fixture("events.jsonl").split("\n")[0]!);
  for (const [k, v] of [["schema", "vnx.event/2"], ["seq", -1], ["run_id", "XYZ"], ["event", "made_up"], ["pid", 1.5]] as const) {
    const c = checkEvent({ ...ev, [k]: v });
    assert.equal(c.ok, false);
    if (!c.ok) assert.match(c.error, new RegExp(`"${k}"`));
  }
  assert.equal(checkEvent(null).ok, false);
  assert.equal(checkEvent({ ...ev, event: "command_end" }).ok, false); // command_end requires status/exit_code/report_sha256
  const rep = JSON.parse(fixture("report.json"));
  assert.equal(checkResult({ ...rep, status: "MOSTLY" }).ok, false);
  assert.equal(checkResult({ ...rep, error: { schema: "vnx.error/1" } }).ok, false);
  assert.equal(checkError({}).ok, false);
});

test("partial recovery is never complete", () => {
  assert.equal(isCompleteRecovery({ kind: "decode", status: "SUCCESS" }), true);
  assert.equal(isCompleteRecovery({ kind: "decode", status: "PARTIAL" }), false);
  assert.equal(isCompleteRecovery({ kind: "decode", status: "FAILURE" }), false);
  assert.equal(isCompleteRecovery({ kind: "verify", status: "VERIFIED" }), true);
});
