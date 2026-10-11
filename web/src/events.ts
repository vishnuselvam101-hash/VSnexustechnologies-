/**
 * Decode event logs (`vnx decode --events <file>`, JSON lines of vnx.event/1).
 *
 * `parseEvents` validates a complete log; `followEvents` tails a log that a running decode is still writing and
 * yields each record as soon as its line is complete. A malformed line is yielded as a typed `bad` item with its line
 * number (never dropped), a partial trailing line is held until it is complete, the pending buffer is bounded
 * (`maxLineBytes`), and an AbortSignal stops the follower and closes the file.
 */
import { open } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";
import { parseEventLine, type EventItem } from "./parse.ts";

export { parseEventLine, parseEvents, type EventItem } from "./parse.ts";

export interface FollowOptions {
  signal?: AbortSignal;
  pollMs?: number;
  maxLineBytes?: number;
  /** events after which the follower returns (default ["command_end"], the CLI's last event; an SDK-only decode log
   *  ends at "decode_end"; [] follows until aborted) */
  stopAt?: readonly string[];
}

export async function* followEvents(path: string, opts: FollowOptions = {}): AsyncGenerator<EventItem> {
  const pollMs = opts.pollMs ?? 200;
  const maxLine = opts.maxLineBytes ?? 1 << 20;
  const stopAt = new Set(opts.stopAt ?? ["command_end"]);
  const fh = await open(path, "r");
  const buf = Buffer.alloc(64 * 1024);
  let pending = Buffer.alloc(0);
  let pos = 0;
  let line = 0;
  try {
    while (!opts.signal?.aborted) {
      const { bytesRead } = await fh.read(buf, 0, buf.length, pos);
      if (bytesRead === 0) {
        try {
          await sleep(pollMs, undefined, opts.signal ? { signal: opts.signal } : {});
        } catch {
          return; // aborted while waiting
        }
        continue;
      }
      pos += bytesRead;
      pending = Buffer.concat([pending, buf.subarray(0, bytesRead)]);
      let nl: number;
      while ((nl = pending.indexOf(0x0a)) >= 0) {
        const text = pending.subarray(0, nl).toString("utf8");
        pending = pending.subarray(nl + 1);
        line += 1;
        if (text.trim() === "") continue;
        const item = parseEventLine(text, line);
        yield item;
        if (item.kind === "event" && stopAt.has(item.event.event)) {
          return;
        }
      }
      if (pending.length > maxLine) {
        line += 1;
        yield { kind: "bad", line, error: `line ${line}: longer than ${maxLine} bytes; follower stopped` };
        return;
      }
    }
  } finally {
    await fh.close();
  }
}
