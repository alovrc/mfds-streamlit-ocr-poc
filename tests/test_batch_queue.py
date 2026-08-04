from __future__ import annotations

import time

from batch_queue import BatchWorker


def test_worker_processes_jobs_in_fifo_order() -> None:
    seen: list[str] = []

    def runner(source: dict[str, str], model: str):
        seen.append(source["record_id"])
        return {"record_id": source["record_id"], "model": model}, dict(source)

    worker = BatchWorker(runner=runner)
    ids = worker.submit_many(
        [{"record_id": "A"}, {"record_id": "B"}, {"record_id": "C"}],
        "gpt-test",
    )

    deadline = time.time() + 3
    while time.time() < deadline:
        statuses = [worker.get(job_id).status for job_id in ids]
        if all(status == "SUCCEEDED" for status in statuses):
            break
        time.sleep(0.01)

    assert seen == ["A", "B", "C"]
    assert all(worker.get(job_id).status == "SUCCEEDED" for job_id in ids)
