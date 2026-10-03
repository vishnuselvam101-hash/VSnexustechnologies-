"""Queue worker: pulls LAYA requests from the configured queue and runs them through LAYA (one at a time).

Message format: {"request": "...", "task_type": optional, "requested_by": "...", "execute": true}. A request that LAYA
defers (resources) is nacked and retried later; everything else is acked once its decision record is written.
The worker never runs a message twice concurrently and stops after `max_messages` or on SIGTERM.
"""
from __future__ import annotations

import signal
import time
from typing import Any

from . import governor, laya, queue

_stop = False


def _sigterm(*_: Any) -> None:
    global _stop
    _stop = True


def run(max_messages: int | None = None, poll: float = 5.0, backend: queue.QueueBackend | None = None) -> dict[str, Any]:
    signal.signal(signal.SIGTERM, _sigterm)
    q = backend or queue.from_config()
    done: dict[str, Any] = {"processed": 0, "deferred": 0, "errors": 0, "decisions": []}
    while not _stop and (max_messages is None or done["processed"] + done["deferred"] + done["errors"] < max_messages):
        item = q.get(timeout=poll)
        if item is None:
            if max_messages is not None:
                break
            continue
        receipt, msg = item
        try:
            rec = laya.decide(msg["request"], task_type=msg.get("task_type"), requested_by=msg.get("requested_by", "queue"),
                              execute=msg.get("execute", True))
            done["decisions"].append({"decision_id": rec["decision_id"], "status": rec["status"]})
            if rec["status"] == "deferred":
                q.nack(receipt, rec.get("reason", "deferred"))
                done["deferred"] += 1
                time.sleep(poll)
            else:
                q.ack(receipt)
                done["processed"] += 1
        except Exception as exc:
            governor.log_event("worker_error", error=repr(exc), message=msg)
            q.nack(receipt, repr(exc))
            done["errors"] += 1
    return done
