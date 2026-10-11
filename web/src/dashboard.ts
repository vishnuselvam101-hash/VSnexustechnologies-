/**
 * Static VNX-DNA decode dashboard (dashboard/index.html): the user opens a decode report (vnx.result/1 or
 * vnx.decode-report/1, `vnx decode --report`) and/or an event log (`--events`, JSON lines); both are validated with
 * the contract guards and rendered. No network access; everything stays in the browser. File content is written
 * with textContent only (never parsed as HTML).
 */
import { checkResult, isCompleteRecovery, type VnxResult } from "./contracts.ts";
import { parseEvents } from "./parse.ts";
import { emptyProgress, reduce, type Progress } from "./progress.ts";

function el(tag: string, text?: string, cls?: string): HTMLElement {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function row(table: HTMLElement, k: string, v: string): void {
  const tr = el("tr");
  tr.append(el("th", k), el("td", v));
  table.append(tr);
}

function renderReport(target: HTMLElement, r: VnxResult): void {
  target.replaceChildren();
  const complete = isCompleteRecovery(r);
  target.append(el("p", `${r.kind}: ${r.status}${complete ? "" : r.status === "PARTIAL" ? " (partial: not a complete recovery)" : ""}`,
    `status status-${r.status.toLowerCase()}`));
  const t = el("table");
  row(t, "software", `${r.software.name} ${r.software.version} (spec ${r.spec})`);
  row(t, "seconds", r.timings.seconds === null ? "n/a" : r.timings.seconds.toFixed(2));
  for (const [stage, s] of Object.entries(r.timings.stage_seconds ?? {})) row(t, `stage ${stage}`, `${Number(s).toFixed(2)} s`);
  const backends = (r.provenance as { backends?: Record<string, { active?: string | null; level?: string | null }> }).backends;
  for (const [k, b] of Object.entries(backends ?? {})) row(t, `kernel ${k}`, `${b.active ?? "unavailable"}${b.level ? ` (${b.level})` : ""}`);
  if (r.error) row(t, "error", `${r.error.code} [${r.error.category}] at ${r.error.stage}: ${r.error.message}`);
  for (const w of r.warnings) row(t, "warning", typeof w === "string" ? w : JSON.stringify(w));
  target.append(t);
}

function renderProgress(target: HTMLElement, p: Progress): void {
  target.replaceChildren();
  target.append(el("p", `events: ${p.outcome}`, `status status-${p.outcome.toLowerCase()}`));
  const t = el("table");
  row(t, "run", p.runId ?? "n/a");
  row(t, "archive", p.archiveId ?? "n/a");
  row(t, "last event", `${p.lastEvent ?? "none"} (stage ${p.stage ?? "n/a"})`);
  row(t, "elapsed", `${p.elapsed.toFixed(2)} s`);
  row(t, "peak RSS", `${(p.peakRssBytes / 2 ** 20).toFixed(1)} MiB`);
  row(t, "pass-1 reads", p.pass1Reads === null ? "n/a" : String(p.pass1Reads));
  row(t, "recovery rounds", String(p.recoveryRounds));
  if (p.exitCode !== null) row(t, "exit code", String(p.exitCode));
  if (p.error) row(t, "error", `${p.error.code}: ${p.error.message}`);
  for (const bad of p.invalidLines) row(t, "invalid line", bad);
  target.append(t);
}

async function readFile(input: HTMLInputElement): Promise<string | null> {
  const f = input.files?.[0];
  return f ? await f.text() : null;
}

export function mount(root: Document = document): void {
  const report = root.getElementById("report-file") as HTMLInputElement;
  const events = root.getElementById("events-file") as HTMLInputElement;
  const reportOut = root.getElementById("report") as HTMLElement;
  const eventsOut = root.getElementById("events") as HTMLElement;
  report.addEventListener("change", async () => {
    const text = await readFile(report);
    if (text === null) return;
    let doc: unknown;
    try {
      doc = JSON.parse(text);
    } catch (e) {
      reportOut.replaceChildren(el("p", `not JSON: ${(e as Error).message}`, "status status-error"));
      return;
    }
    const c = checkResult(doc);
    if (!c.ok) reportOut.replaceChildren(el("p", c.error, "status status-error"));
    else renderReport(reportOut, c.value);
  });
  events.addEventListener("change", async () => {
    const text = await readFile(events);
    if (text === null) return;
    renderProgress(eventsOut, parseEvents(text).reduce(reduce, emptyProgress()));
  });
}

if (typeof document !== "undefined") mount();
