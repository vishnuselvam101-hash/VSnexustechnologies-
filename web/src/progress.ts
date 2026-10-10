/**
 * Pure reduction of a decode event stream into a progress view (used by the dashboard and the Node client).
 * The final outcome comes only from the decode's own status: SUCCESS, PARTIAL and FAILURE stay distinct, and a
 * stream without a terminal event is "running", never "succeeded".
 */
import type { EventItem } from "./parse.ts";
import type { VnxError } from "./contracts.ts";

export type Outcome = "running" | "SUCCESS" | "PARTIAL" | "FAILURE" | "ERROR";

export interface Progress {
  runId: string | null;
  archiveId: string | null;
  stage: string | null;
  lastEvent: string | null;
  elapsed: number;
  peakRssBytes: number;
  pass1Reads: number | null;
  recoveryRounds: number;
  outcome: Outcome;
  exitCode: number | null;
  error: VnxError | null;
  invalidLines: string[];
  events: number;
}

export function emptyProgress(): Progress {
  return { runId: null, archiveId: null, stage: null, lastEvent: null, elapsed: 0, peakRssBytes: 0, pass1Reads: null,
    recoveryRounds: 0, outcome: "running", exitCode: null, error: null, invalidLines: [], events: 0 };
}

const OUTCOMES = new Set(["SUCCESS", "PARTIAL", "FAILURE"]);

export function reduce(p: Progress, item: EventItem): Progress {
  if (item.kind === "bad") return { ...p, invalidLines: [...p.invalidLines, item.error] };
  const e = item.event;
  const next: Progress = { ...p, events: p.events + 1, runId: p.runId ?? e.run_id, stage: e.stage, lastEvent: e.event,
    elapsed: Math.max(p.elapsed, e.elapsed), peakRssBytes: Math.max(p.peakRssBytes, e.rss_bytes) };
  if (e.archive_id !== undefined) next.archiveId = e.archive_id;
  const reads = e["reads"];
  if ((e.event === "pass1_progress" || e.event === "pass1_end") && typeof reads === "number") next.pass1Reads = reads;
  if (e.event === "recovery_round") next.recoveryRounds = p.recoveryRounds + 1;
  if (e.event === "error") {
    next.outcome = "ERROR";
    if (e.error !== undefined) next.error = e.error;
  }
  if ((e.event === "decode_end" || e.event === "command_end") && typeof e.status === "string") {
    next.outcome = OUTCOMES.has(e.status) ? (e.status as Outcome) : next.outcome === "ERROR" ? "ERROR" : "FAILURE";
  }
  if (e.event === "command_end" && typeof e.exit_code === "number") next.exitCode = e.exit_code;
  return next;
}

export function summarize(items: Iterable<EventItem>): Progress {
  let p = emptyProgress();
  for (const it of items) p = reduce(p, it);
  return p;
}
