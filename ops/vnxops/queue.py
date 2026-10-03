"""PHASE 9: task queue. One interface, three backends.

* ``LocalQueue``  — SQLite file under /opt/vnx-dna/run. Default; no service needed; durable; visibility timeout.
* ``RedisQueue``  — the local redis-server (127.0.0.1:6379), reliable-queue pattern (LMOVE to a processing list),
                    keys prefixed ``vnxdna:`` in a dedicated DB index. Uses a ~40-line RESP client (no dependency).
* ``SQSQueue``    — AWS SQS adapter, only when explicitly enabled in system.yaml; needs boto3 + credentials, which
                    are NOT installed or configured by default. Nothing in the system requires it.

Messages are JSON objects. ``get`` returns (receipt, message); ``ack`` deletes, ``nack`` requeues (or dead-letters
after ``max_deliveries``).
"""
from __future__ import annotations

import json
import socket
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from . import paths


class QueueBackend(ABC):
    name = "abstract"

    @abstractmethod
    def put(self, message: dict[str, Any]) -> str: ...

    @abstractmethod
    def get(self, timeout: float = 0.0) -> tuple[str, dict[str, Any]] | None: ...

    @abstractmethod
    def ack(self, receipt: str) -> None: ...

    @abstractmethod
    def nack(self, receipt: str, reason: str = "") -> None: ...

    @abstractmethod
    def depth(self) -> dict[str, int]: ...

    def health(self) -> dict[str, Any]:
        try:
            return {"backend": self.name, "ok": True, "depth": self.depth()}
        except Exception as exc:  # health must report, not raise
            return {"backend": self.name, "ok": False, "error": repr(exc)}


class LocalQueue(QueueBackend):
    name = "local-sqlite"

    def __init__(self, path: Path | None = None, visibility_timeout: float = 3600, max_deliveries: int = 3):
        self.path = path or paths.RUN / "queue.db"
        paths.ensure(self.path.parent)
        self.vt, self.max_deliveries = visibility_timeout, max_deliveries
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS q(id TEXT PRIMARY KEY, body TEXT, state TEXT, deliveries INTEGER,
                           visible_at REAL, created REAL, reason TEXT)""")

    def put(self, message: dict[str, Any]) -> str:
        mid = uuid.uuid4().hex
        self.db.execute("INSERT INTO q VALUES(?,?,'ready',0,?,?,NULL)", (mid, json.dumps(message), time.time(), time.time()))
        return mid

    def get(self, timeout: float = 0.0) -> tuple[str, dict[str, Any]] | None:
        deadline = time.time() + timeout
        while True:
            now = time.time()
            self.db.execute("BEGIN IMMEDIATE")
            # inflight messages whose visibility expired (worker died) become ready again
            self.db.execute("UPDATE q SET state='ready' WHERE state='inflight' AND visible_at<=?", (now,))
            row = self.db.execute("SELECT id, body, deliveries FROM q WHERE state='ready' AND visible_at<=? ORDER BY created LIMIT 1",
                                  (now,)).fetchone()
            if row:
                self.db.execute("UPDATE q SET state='inflight', deliveries=deliveries+1, visible_at=? WHERE id=?", (now + self.vt, row[0]))
                self.db.execute("COMMIT")
                return row[0], json.loads(row[1])
            self.db.execute("COMMIT")
            if time.time() >= deadline:
                return None
            time.sleep(min(0.5, max(0.0, deadline - time.time())))

    def ack(self, receipt: str) -> None:
        self.db.execute("DELETE FROM q WHERE id=?", (receipt,))

    def nack(self, receipt: str, reason: str = "") -> None:
        row = self.db.execute("SELECT deliveries FROM q WHERE id=?", (receipt,)).fetchone()
        if not row:
            return
        state = "dead" if row[0] >= self.max_deliveries else "ready"
        self.db.execute("UPDATE q SET state=?, visible_at=?, reason=? WHERE id=?", (state, time.time(), reason, receipt))

    def depth(self) -> dict[str, int]:
        rows = dict(self.db.execute("SELECT state, COUNT(*) FROM q GROUP BY state").fetchall())
        return {k: int(rows.get(k, 0)) for k in ("ready", "inflight", "dead")}


class _Resp:
    """Minimal RESP2 client: enough for LPUSH/LMOVE/LREM/LLEN/HSET/HGET/HDEL/HINCRBY/PING/SELECT."""

    def __init__(self, host: str = "127.0.0.1", port: int = 6379, db: int = 0, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rb")
        if db:
            self.cmd("SELECT", db)

    def cmd(self, *args: Any) -> Any:
        out = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = a if isinstance(a, bytes) else str(a).encode()
            out.append(b"$%d\r\n%s\r\n" % (len(b), b))
        self.sock.sendall(b"".join(out))
        return self._read()

    def _read(self) -> Any:
        line = self.f.readline()
        kind, rest = line[:1], line[1:-2]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RuntimeError(rest.decode())
        if kind == b":":
            return int(rest)
        if kind == b"$":
            n = int(rest)
            if n < 0:
                return None
            data = self.f.read(n + 2)[:-2]
            return data.decode()
        if kind == b"*":
            n = int(rest)
            return None if n < 0 else [self._read() for _ in range(n)]
        raise RuntimeError(f"bad RESP reply {line!r}")


class RedisQueue(QueueBackend):
    name = "redis"

    def __init__(self, host: str = "127.0.0.1", port: int = 6379, db: int = 7, prefix: str = "vnxdna:q",
                 max_deliveries: int = 3):
        self.r = _Resp(host, port, db)
        self.p, self.max_deliveries = prefix, max_deliveries

    def put(self, message: dict[str, Any]) -> str:
        mid = uuid.uuid4().hex
        self.r.cmd("HSET", f"{self.p}:body", mid, json.dumps(message))
        self.r.cmd("LPUSH", f"{self.p}:ready", mid)
        return mid

    def get(self, timeout: float = 0.0) -> tuple[str, dict[str, Any]] | None:
        if timeout > 0:
            mid = self.r.cmd("BLMOVE", f"{self.p}:ready", f"{self.p}:inflight", "RIGHT", "LEFT", max(0.01, timeout))
        else:
            mid = self.r.cmd("LMOVE", f"{self.p}:ready", f"{self.p}:inflight", "RIGHT", "LEFT")
        if mid is None:
            return None
        self.r.cmd("HINCRBY", f"{self.p}:deliveries", mid, 1)
        return mid, json.loads(self.r.cmd("HGET", f"{self.p}:body", mid))

    def ack(self, receipt: str) -> None:
        self.r.cmd("LREM", f"{self.p}:inflight", 1, receipt)
        self.r.cmd("HDEL", f"{self.p}:body", receipt)
        self.r.cmd("HDEL", f"{self.p}:deliveries", receipt)

    def nack(self, receipt: str, reason: str = "") -> None:
        self.r.cmd("LREM", f"{self.p}:inflight", 1, receipt)
        n = int(self.r.cmd("HGET", f"{self.p}:deliveries", receipt) or 0)
        self.r.cmd("LPUSH", f"{self.p}:dead" if n >= self.max_deliveries else f"{self.p}:ready", receipt)

    def depth(self) -> dict[str, int]:
        return {k: int(self.r.cmd("LLEN", f"{self.p}:{k}")) for k in ("ready", "inflight", "dead")}

    def purge(self) -> None:
        for k in ("ready", "inflight", "dead", "body", "deliveries"):
            self.r.cmd("DEL", f"{self.p}:{k}")


class SQSQueue(QueueBackend):
    """AWS SQS adapter. Optional: requires boto3 and AWS credentials from the standard provider chain."""

    name = "aws-sqs"

    def __init__(self, queue_url: str, region: str | None = None, client: Any = None, wait_seconds: int = 10):
        if client is None:
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("SQSQueue needs boto3 (not installed by default; SQS is optional)") from exc
            client = boto3.client("sqs", region_name=region)
        self.c, self.url, self.wait = client, queue_url, wait_seconds

    def put(self, message: dict[str, Any]) -> str:
        return self.c.send_message(QueueUrl=self.url, MessageBody=json.dumps(message))["MessageId"]

    def get(self, timeout: float = 0.0) -> tuple[str, dict[str, Any]] | None:
        r = self.c.receive_message(QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=min(20, int(timeout)))
        msgs = r.get("Messages", [])
        if not msgs:
            return None
        return msgs[0]["ReceiptHandle"], json.loads(msgs[0]["Body"])

    def ack(self, receipt: str) -> None:
        self.c.delete_message(QueueUrl=self.url, ReceiptHandle=receipt)

    def nack(self, receipt: str, reason: str = "") -> None:
        # visibility 0 → redelivered; SQS's own redrive policy handles dead-lettering
        self.c.change_message_visibility(QueueUrl=self.url, ReceiptHandle=receipt, VisibilityTimeout=0)

    def depth(self) -> dict[str, int]:
        a = self.c.get_queue_attributes(QueueUrl=self.url, AttributeNames=["ApproximateNumberOfMessages",
                                                                             "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return {"ready": int(a["ApproximateNumberOfMessages"]), "inflight": int(a["ApproximateNumberOfMessagesNotVisible"]), "dead": 0}


def from_config(cfg: dict[str, Any] | None = None) -> QueueBackend:
    """Backend from system.yaml `queue:`. Falls back to LocalQueue when Redis is configured but unreachable."""
    if cfg is None:
        from . import system
        cfg = system.load().get("queue", {})
    backend = cfg.get("backend", "local")
    if backend == "sqs":
        if not cfg.get("sqs", {}).get("enabled"):
            raise RuntimeError("queue.backend is sqs but queue.sqs.enabled is false")
        return SQSQueue(cfg["sqs"]["queue_url"], cfg["sqs"].get("region"))
    if backend == "redis":
        rc = cfg.get("redis", {})
        try:
            return RedisQueue(rc.get("host", "127.0.0.1"), int(rc.get("port", 6379)), int(rc.get("db", 7)))
        except OSError:
            if not cfg.get("fallback_to_local", True):
                raise
    return LocalQueue()
