import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parseEvents } from "../src/parse.ts";
import { summarize } from "../src/progress.ts";

const events = readFileSync(fileURLToPath(new URL("./fixtures/events.jsonl", import.meta.url)), "utf8");

test("a real event log reduces to its decode outcome", () => {
  const p = summarize(parseEvents(events));
  assert.equal(p.outcome, "SUCCESS");
  assert.equal(p.invalidLines.length, 0);
  assert.ok(p.events >= 3 && p.runId !== null && p.peakRssBytes > 0);
  assert.equal(p.exitCode, 0);
});

test("without a terminal event the run is still running, never successful", () => {
  const lines = events.trim().split("\n");
  const head = lines.filter((l) => !/"event": "(decode_end|command_end)"/.test(l)).join("\n");
  assert.equal(summarize(parseEvents(head)).outcome, "running");
});

test("PARTIAL stays PARTIAL and malformed lines are reported, not dropped", () => {
  const partial = events.replaceAll('"status": "SUCCESS"', '"status": "PARTIAL"');
  const p = summarize(parseEvents(partial + "\n{not json}\n" + '{"schema": "vnx.event/1"}\n'));
  assert.equal(p.outcome, "PARTIAL");
  assert.equal(p.invalidLines.length, 2);
  assert.match(p.invalidLines[0]!, /not JSON/);
});
