/**
 * Typed bindings for the JSON contracts VNX-DNA publishes (src/vnxdna/core/schemas/*.schema.json):
 * vnx.event/1 (decode event log, JSON lines), vnx.result/1 (command envelope), vnx.error/1, and the native
 * kernel status (`python -m vnxdna.native`). The schemas are normative; test/contracts.test.ts checks that every enum
 * here equals the schema's, so the two cannot drift silently.
 *
 * Every guard validates the fields the schema requires and returns a typed error instead of throwing on bad input,
 * so a caller can never treat an unvalidated object as a valid record.
 */

export const EVENT_SCHEMA = "vnx.event/1";
export const RESULT_SCHEMAS = ["vnx.result/1", "vnx.decode-report/1"] as const;
export const ERROR_SCHEMA = "vnx.error/1";

export const EVENT_KINDS = ["run_start", "decode_start", "pass1_progress", "pass1_end", "recovery_round", "superblock",
  "pass2_end", "verify", "decode_end", "error", "command_end"] as const;
export type EventKind = (typeof EVENT_KINDS)[number];

export const RESULT_STATUSES = ["SUCCESS", "PARTIAL", "FAILURE", "VERIFIED", "EXTRACTED", "OK", "DONE"] as const;
export type ResultStatus = (typeof RESULT_STATUSES)[number];

export const RESULT_KINDS = ["archive", "encode", "decode", "inspect", "verify", "extract", "list", "locate", "simulate",
  "benchmark", "sweep", "experiment", "conformance", "keygen", "validate", "generate", "profiles", "native",
  "channel-model", "channel-sweep"] as const;
export type ResultKind = (typeof RESULT_KINDS)[number];

export const ERROR_CATEGORIES = ["VERIFICATION_FAILED", "USAGE_ERROR", "INVALID_INPUT", "UNRECOVERABLE_CORRUPTION",
  "AUTHENTICATION_FAILED", "INSUFFICIENT_REDUNDANCY", "UNSUPPORTED_FORMAT", "CONFIGURATION_ERROR", "OUTPUT_ERROR",
  "PROVIDER_ERROR", "INTERNAL_ERROR"] as const;
export type ErrorCategory = (typeof ERROR_CATEGORIES)[number];

export const BACKENDS = ["native", "reference"] as const;

export interface VnxError {
  schema: typeof ERROR_SCHEMA;
  code: string;
  category: ErrorCategory;
  exit_code: number;
  retryable: boolean;
  stage: string;
  message: string;
  hint: string | null;
  details: Record<string, unknown>;
}

export interface VnxEvent {
  schema: typeof EVENT_SCHEMA;
  seq: number;
  run_id: string;
  ts: string;
  elapsed: number;
  task_id: string;
  pid: number;
  rss_bytes: number;
  event: EventKind;
  stage: string;
  archive_id?: string;
  status?: string;
  exit_code?: number;
  error?: VnxError;
  /** stage metrics (reads, groups, rounds, …): additional properties are allowed by the schema */
  [metric: string]: unknown;
}

export interface VnxResult {
  schema: (typeof RESULT_SCHEMAS)[number];
  kind: ResultKind;
  status: ResultStatus;
  software: { name: "vnxdna"; version: string };
  spec: string;
  provenance: Record<string, unknown>;
  inputs: unknown;
  outputs: unknown;
  formats: unknown;
  timings: { seconds: number | null; stage_seconds?: Record<string, number> };
  resources: unknown;
  result: unknown;
  warnings: unknown[];
  error: VnxError | null;
  [extra: string]: unknown;
}

export interface KernelStatus {
  backend: (typeof BACKENDS)[number] | null;
  requested: string | null;
  simd_level: string | null;
  library: string | null;
  library_origin: "env" | "packaged" | "in-place" | null;
  abi_version: number | null;
  reference: string;
  load_error: string | null;
  error: string | null;
  [extra: string]: unknown;
}

export interface NativeStatus {
  vnx_version: string;
  kernels: Record<string, KernelStatus>;
  all_native: boolean;
}

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isInt = (v: unknown): v is number => Number.isInteger(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const oneOf = <T extends readonly unknown[]>(v: unknown, xs: T): v is T[number] => xs.includes(v);

function fail(what: string, field: string): { ok: false; error: string } {
  return { ok: false, error: `${what}: invalid or missing field "${field}"` };
}

export function checkError(v: unknown): Checked<VnxError> {
  if (!isObj(v)) return { ok: false, error: "vnx.error/1: not an object" };
  if (v.schema !== ERROR_SCHEMA) return fail("vnx.error/1", "schema");
  if (!isStr(v.code)) return fail("vnx.error/1", "code");
  if (!oneOf(v.category, ERROR_CATEGORIES)) return fail("vnx.error/1", "category");
  if (!isInt(v.exit_code)) return fail("vnx.error/1", "exit_code");
  if (typeof v.retryable !== "boolean") return fail("vnx.error/1", "retryable");
  if (!isStr(v.stage)) return fail("vnx.error/1", "stage");
  if (!isStr(v.message)) return fail("vnx.error/1", "message");
  if (!(v.hint === null || isStr(v.hint))) return fail("vnx.error/1", "hint");
  if (!isObj(v.details)) return fail("vnx.error/1", "details");
  return { ok: true, value: v as unknown as VnxError };
}

export function checkEvent(v: unknown): Checked<VnxEvent> {
  const what = "vnx.event/1";
  if (!isObj(v)) return { ok: false, error: `${what}: not an object` };
  if (v.schema !== EVENT_SCHEMA) return fail(what, "schema");
  if (!isInt(v.seq) || v.seq < 0) return fail(what, "seq");
  if (!isStr(v.run_id) || !/^[0-9a-f]{16}$/.test(v.run_id)) return fail(what, "run_id");
  if (!isStr(v.ts)) return fail(what, "ts");
  if (typeof v.elapsed !== "number" || !Number.isFinite(v.elapsed)) return fail(what, "elapsed");
  if (!isStr(v.task_id)) return fail(what, "task_id");
  if (!isInt(v.pid)) return fail(what, "pid");
  if (!isInt(v.rss_bytes)) return fail(what, "rss_bytes");
  if (!oneOf(v.event, EVENT_KINDS)) return fail(what, "event");
  if (!isStr(v.stage)) return fail(what, "stage");
  if (v.event === "command_end") {
    if (!isStr(v.status)) return fail(what, "status");
    if (!isInt(v.exit_code)) return fail(what, "exit_code");
    if (!isStr(v.report_sha256)) return fail(what, "report_sha256");
  }
  if (v.event === "error" && v.error !== undefined) {
    const e = checkError(v.error);
    if (!e.ok) return { ok: false, error: `${what}: ${e.error}` };
  }
  return { ok: true, value: v as unknown as VnxEvent };
}

export function checkResult(v: unknown): Checked<VnxResult> {
  const what = "vnx.result/1";
  if (!isObj(v)) return { ok: false, error: `${what}: not an object` };
  if (!oneOf(v.schema, RESULT_SCHEMAS)) return fail(what, "schema");
  if (!oneOf(v.kind, RESULT_KINDS)) return fail(what, "kind");
  if (!oneOf(v.status, RESULT_STATUSES)) return fail(what, "status");
  if (!isObj(v.software) || v.software.name !== "vnxdna" || !isStr(v.software.version)) return fail(what, "software");
  if (!isStr(v.spec)) return fail(what, "spec");
  for (const k of ["provenance", "inputs", "outputs", "formats", "resources", "result"]) {
    if (!(k in v)) return fail(what, k);
  }
  if (!isObj(v.timings) || !(v.timings.seconds === null || typeof v.timings.seconds === "number")) {
    return fail(what, "timings");
  }
  if (!Array.isArray(v.warnings)) return fail(what, "warnings");
  if (v.error !== null) {
    const e = checkError(v.error);
    if (!e.ok) return { ok: false, error: `${what}: ${e.error}` };
  }
  return { ok: true, value: v as unknown as VnxResult };
}

export function checkNativeStatus(v: unknown): Checked<NativeStatus> {
  const what = "native status";
  if (!isObj(v)) return { ok: false, error: `${what}: not an object` };
  if (!isStr(v.vnx_version)) return fail(what, "vnx_version");
  if (typeof v.all_native !== "boolean") return fail(what, "all_native");
  if (!isObj(v.kernels)) return fail(what, "kernels");
  for (const [name, k] of Object.entries(v.kernels)) {
    if (!isObj(k) || !(k.backend === null || oneOf(k.backend, BACKENDS))) return fail(what, `kernels.${name}.backend`);
  }
  return { ok: true, value: v as unknown as NativeStatus };
}

/** A decode is complete only when its status is SUCCESS; PARTIAL is never reported as complete. */
export function isCompleteRecovery(r: Pick<VnxResult, "kind" | "status">): boolean {
  return r.kind === "decode" ? r.status === "SUCCESS" : r.status !== "FAILURE";
}
