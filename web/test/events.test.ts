import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFile, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { followEvents, type EventItem } from "../src/events.ts";

const FIX = fileURLToPath(new URL("./fixtures/events.jsonl", import.meta.url));

test("follows a log written in arbitrary chunks, holding partial lines", async () => {
  const text = await readFile(FIX, "utf8");
  const dir = await mkdtemp(join(tmpdir(), "vnxev-"));
  const path = join(dir, "e.jsonl");
  await writeFile(path, "");
  const got: EventItem[] = [];
  const reader = (async () => { for await (const it of followEvents(path, { pollMs: 5 })) got.push(it); })();
  for (let i = 0; i < text.length; i += 37) {
    await appendFile(path, text.slice(i, i + 37));
    await new Promise((r) => setTimeout(r, 2));
  }
  await reader;
  const expected = text.trim().split("\n").length;
  assert.equal(got.filter((g) => g.kind === "event").length, expected);
  assert.equal(got.filter((g) => g.kind === "bad").length, 0);
  const last = got.at(-1);
  assert.ok(last?.kind === "event" && last.event.event === "command_end" && last.event.exit_code === 0);
});

test("stopAt chooses the terminal events", async () => {
  const dir = await mkdtemp(join(tmpdir(), "vnxev-"));
  const path = join(dir, "e.jsonl");
  await writeFile(path, await readFile(FIX, "utf8"));
  const got: EventItem[] = [];
  for await (const it of followEvents(path, { stopAt: ["decode_end"], pollMs: 5 })) got.push(it);
  const last = got.at(-1);
  assert.ok(last?.kind === "event" && last.event.event === "decode_end");
});

test("abort stops a follower waiting on a log that never ends", async () => {
  const dir = await mkdtemp(join(tmpdir(), "vnxev-"));
  const path = join(dir, "e.jsonl");
  await writeFile(path, "{bad line}\n");
  const ac = new AbortController();
  const got: EventItem[] = [];
  setTimeout(() => ac.abort(), 50);
  for await (const it of followEvents(path, { signal: ac.signal, pollMs: 5 })) got.push(it);
  assert.equal(got.length, 1);
  assert.equal(got[0]!.kind, "bad");
});

test("an over-long line stops the follower with an error instead of buffering without bound", async () => {
  const dir = await mkdtemp(join(tmpdir(), "vnxev-"));
  const path = join(dir, "e.jsonl");
  await writeFile(path, "x".repeat(5000));
  const got: EventItem[] = [];
  for await (const it of followEvents(path, { maxLineBytes: 1024, pollMs: 5 })) got.push(it);
  assert.equal(got.length, 1);
  assert.ok(got[0]!.kind === "bad" && /longer than 1024/.test(got[0]!.error));
});
