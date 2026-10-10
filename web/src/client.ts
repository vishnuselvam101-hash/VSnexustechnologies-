/**
 * Node client for the `vnx` command line: runs one command (no shell, explicit argv), parses and validates its
 * vnx.result/1 envelope, and maps failures to a typed `VnxCommandError` that carries the vnx.error/1 object when the
 * command produced one. Output is bounded; a timeout or AbortSignal kills the process. Secrets are never passed on
 * the command line by this client (use the CLI's --key-file / --passphrase-env options).
 */
import { spawn } from "node:child_process";
import { checkNativeStatus, checkResult, type NativeStatus, type VnxError, type VnxResult } from "./contracts.ts";

export interface RunOptions {
  /** executable and leading arguments, default ["vnx"] (e.g. ["python", "-m", "vnxdna"]) */
  command?: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  signal?: AbortSignal;
  maxOutputBytes?: number;
}

export class VnxCommandError extends Error {
  readonly exitCode: number | null;
  readonly vnxError: VnxError | null;
  readonly stderr: string;
  constructor(message: string, exitCode: number | null, vnxError: VnxError | null, stderr: string) {
    super(message);
    this.name = "VnxCommandError";
    this.exitCode = exitCode;
    this.vnxError = vnxError;
    this.stderr = stderr;
  }
}

interface Raw { code: number | null; stdout: string; stderr: string }

function run(args: string[], opts: RunOptions): Promise<Raw> {
  const [cmd, ...lead] = opts.command ?? ["vnx"];
  if (cmd === undefined) return Promise.reject(new VnxCommandError("empty command", null, null, ""));
  const limit = opts.maxOutputBytes ?? 64 << 20;
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, [...lead, ...args], { cwd: opts.cwd, env: opts.env ?? process.env, shell: false,
      stdio: ["ignore", "pipe", "pipe"], ...(opts.timeoutMs ? { timeout: opts.timeoutMs } : {}),
      ...(opts.signal ? { signal: opts.signal } : {}) });
    const out: Buffer[] = [];
    const err: Buffer[] = [];
    let size = 0;
    const take = (sink: Buffer[]) => (b: Buffer) => {
      size += b.length;
      if (size > limit) {
        child.kill("SIGKILL");
        reject(new VnxCommandError(`output exceeded ${limit} bytes`, null, null, ""));
        return;
      }
      sink.push(b);
    };
    child.stdout.on("data", take(out));
    child.stderr.on("data", take(err));
    child.on("error", (e) => reject(new VnxCommandError(`cannot run ${cmd}: ${e.message}`, null, null, "")));
    child.on("close", (code) => resolve({ code, stdout: Buffer.concat(out).toString("utf8"),
      stderr: Buffer.concat(err).toString("utf8") }));
  });
}

function lastJson(stdout: string): unknown {
  const text = stdout.trim();
  try {
    return JSON.parse(text);
  } catch {
    const lines = text.split("\n");
    for (let i = lines.length - 1; i >= 0; i--) {
      try {
        return JSON.parse(lines.slice(i).join("\n"));
      } catch {
        /* keep looking for the start of the JSON document */
      }
    }
    return undefined;
  }
}

/** Run `vnx <args>` and return its validated envelope; a non-zero exit or an invalid envelope throws. */
export async function vnx(args: string[], opts: RunOptions = {}): Promise<VnxResult> {
  const raw = await run(args, opts);
  const doc = lastJson(raw.stdout);
  const checked = checkResult(doc);
  if (!checked.ok) {
    throw new VnxCommandError(`vnx ${args[0] ?? ""}: ${checked.error}`, raw.code, null, raw.stderr);
  }
  if (raw.code !== 0 || checked.value.error !== null) {
    const e = checked.value.error;
    throw new VnxCommandError(e ? `${e.code}: ${e.message}` : `vnx exited with ${raw.code}`, raw.code, e, raw.stderr);
  }
  return checked.value;
}

/** `python -m vnxdna.native` (or `vnx native`) as a validated NativeStatus. */
export async function nativeStatus(opts: RunOptions = {}): Promise<NativeStatus> {
  const raw = await run([], { ...opts, command: opts.command ?? ["python3", "-m", "vnxdna.native"] });
  const checked = checkNativeStatus(lastJson(raw.stdout));
  if (!checked.ok) throw new VnxCommandError(checked.error, raw.code, null, raw.stderr);
  return checked.value;
}
