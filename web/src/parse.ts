/** Line parser for vnx.event/1 logs, without Node APIs (shared by the Node follower and the browser dashboard). */
import { checkEvent, type VnxEvent } from "./contracts.ts";

export type EventItem = { kind: "event"; line: number; event: VnxEvent } | { kind: "bad"; line: number; error: string };

export function parseEventLine(text: string, line: number): EventItem {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (e) {
    return { kind: "bad", line, error: `line ${line}: not JSON (${(e as Error).message})` };
  }
  const c = checkEvent(value);
  return c.ok ? { kind: "event", line, event: c.value } : { kind: "bad", line, error: `line ${line}: ${c.error}` };
}

export function parseEvents(text: string): EventItem[] {
  const out: EventItem[] = [];
  const lines = text.split("\n");
  lines.forEach((l, i) => {
    if (l.trim() !== "") out.push(parseEventLine(l, i + 1));
  });
  return out;
}
