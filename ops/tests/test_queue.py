import pytest

from vnxops import queue as q


def test_local_queue_roundtrip_ack(tmp_path):
    lq = q.LocalQueue(tmp_path / "q.db")
    mid = lq.put({"a": 1})
    rec, msg = lq.get()
    assert rec == mid and msg == {"a": 1}
    assert lq.depth() == {"ready": 0, "inflight": 1, "dead": 0}
    lq.ack(rec)
    assert lq.depth() == {"ready": 0, "inflight": 0, "dead": 0}
    assert lq.get() is None


def test_local_queue_nack_then_dead_letter(tmp_path):
    lq = q.LocalQueue(tmp_path / "q.db", max_deliveries=2)
    lq.put({"x": 1})
    r, _ = lq.get()
    lq.nack(r, "first")
    r, _ = lq.get()
    lq.nack(r, "second")
    assert lq.depth()["dead"] == 1 and lq.get() is None


def test_local_queue_visibility_timeout_redelivers(tmp_path):
    lq = q.LocalQueue(tmp_path / "q.db", visibility_timeout=0.05)
    lq.put({"x": 1})
    assert lq.get() is not None
    import time
    time.sleep(0.1)
    assert lq.get() is not None          # worker died without ack → redelivered


def test_local_queue_is_fifo(tmp_path):
    lq = q.LocalQueue(tmp_path / "q.db")
    for i in range(5):
        lq.put({"i": i})
    assert [lq.get()[1]["i"] for _ in range(5)] == list(range(5))


def _redis():
    try:
        return q.RedisQueue(db=7, prefix="vnxdna:test")
    except OSError:
        pytest.skip("redis not reachable")


def test_redis_queue_roundtrip():
    rq = _redis()
    rq.purge()
    try:
        rq.put({"a": 1})
        rq.put({"a": 2})
        r1, m1 = rq.get()
        assert m1 == {"a": 1}
        rq.ack(r1)
        r2, _ = rq.get(timeout=1)
        rq.nack(r2)
        assert rq.depth()["ready"] == 1
    finally:
        rq.purge()


class FakeSQS:
    def __init__(self):
        self.msgs, self.n = {}, 0

    def send_message(self, QueueUrl, MessageBody):
        self.n += 1
        self.msgs[str(self.n)] = MessageBody
        return {"MessageId": str(self.n)}

    def receive_message(self, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
        if not self.msgs:
            return {}
        k = sorted(self.msgs)[0]
        return {"Messages": [{"ReceiptHandle": k, "Body": self.msgs[k]}]}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.msgs.pop(ReceiptHandle)

    def change_message_visibility(self, **kw):
        pass

    def get_queue_attributes(self, QueueUrl, AttributeNames):
        return {"Attributes": {"ApproximateNumberOfMessages": str(len(self.msgs)), "ApproximateNumberOfMessagesNotVisible": "0"}}


def test_sqs_adapter_with_fake_client():
    s = q.SQSQueue("https://sqs.example/q", client=FakeSQS())
    s.put({"t": 1})
    r, m = s.get()
    assert m == {"t": 1}
    s.ack(r)
    assert s.depth()["ready"] == 0


def test_sqs_is_never_selected_unless_enabled():
    with pytest.raises(RuntimeError):
        q.from_config({"backend": "sqs", "sqs": {"enabled": False}})


def test_redis_unreachable_falls_back_to_local():
    b = q.from_config({"backend": "redis", "redis": {"host": "127.0.0.1", "port": 1}, "fallback_to_local": True})
    assert isinstance(b, q.LocalQueue)


def test_backup_verify_is_independent_of_cwd(tmp_path, monkeypatch):
    import subprocess
    from vnxops import backup
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"], check=True)
    dest = backup.backup([repo], "t", dest_root=tmp_path / "bk")
    monkeypatch.chdir(tmp_path)                    # not inside any repository
    assert backup.verify(dest)["ok"]
    (dest / next(e["bundle"]["file"] for e in __import__("json").loads((dest / "MANIFEST.json").read_text())["entries"])).write_bytes(b"x")
    assert not backup.verify(dest)["ok"]
