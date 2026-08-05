from __future__ import annotations

import time

from batch_queue import BatchWorker


def test_worker_processes_jobs_in_fifo_order() -> None:
    seen: list[str] = []
    ocr_flags: list[bool] = []

    def runner(source: dict[str, str], model: str, use_paddle_ocr: bool):
        seen.append(source["record_id"])
        ocr_flags.append(use_paddle_ocr)
        return {"record_id": source["record_id"], "model": model}, dict(source)

    worker = BatchWorker(runner=runner)
    ids = worker.submit_many(
        [{"record_id": "A"}, {"record_id": "B"}, {"record_id": "C"}],
        "gpt-test",
        use_paddle_ocr=False,
    )

    deadline = time.time() + 3
    while time.time() < deadline:
        statuses = [worker.get(job_id).status for job_id in ids]
        if all(status == "SUCCEEDED" for status in statuses):
            break
        time.sleep(0.01)

    assert seen == ["A", "B", "C"]
    assert ocr_flags == [False, False, False]
    assert all(worker.get(job_id).status == "SUCCEEDED" for job_id in ids)
