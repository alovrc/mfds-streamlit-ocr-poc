"""Single-worker background queue for the sequential Streamlit batch app."""

from __future__ import annotations

import os
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Queue
from typing import Any, Callable

JobRunner = Callable[[dict[str, Any], str], tuple[dict[str, Any], dict[str, Any]]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def process_source(source: dict[str, Any], model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve URL/OCR input and run the existing OpenAI pipeline."""

    from ocr_pipeline import collect_and_ocr, merge_capture_text
    from scripts.run_pipeline import run

    resolved = dict(source)
    if resolved.get("source_url") and not resolved.get("body_text"):
        capture = collect_and_ocr(str(resolved["source_url"]))
        resolved["title"] = resolved.get("title") or capture.get("title", "")
        resolved["body_text"] = merge_capture_text(
            resolved["title"],
            capture.get("body_text", ""),
            capture.get("ocr_records", []),
        )
        resolved["ocr_engine"] = "PADDLEOCR_KOREAN_PP-OCRV5"
    os.environ["OPENAI_MODEL"] = model
    return run("openai", resolved), resolved


@dataclass
class BatchJob:
    job_id: str
    sequence: int
    source: dict[str, Any]
    model: str
    status: str = "QUEUED"
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    output: dict[str, Any] | None = None
    resolved_source: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "sequence": self.sequence,
            "record_id": self.source.get("record_id", ""),
            "title": self.source.get("title", ""),
            "source_url": self.source.get("source_url", ""),
            "model": self.model,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": (self.failure or {}).get("error_code", ""),
        }


class BatchWorker:
    """FIFO worker with exactly one active pipeline invocation."""

    def __init__(self, runner: JobRunner = process_source) -> None:
        self._runner = runner
        self._queue: Queue[str] = Queue()
        self._jobs: OrderedDict[str, BatchJob] = OrderedDict()
        self._lock = threading.RLock()
        self._thread = threading.Thread(
            target=self._run,
            name="mfds-batch-worker",
            daemon=True,
        )
        self._thread.start()

    def submit_many(self, sources: list[dict[str, Any]], model: str) -> list[str]:
        job_ids: list[str] = []
        with self._lock:
            start = len(self._jobs) + 1
            for offset, source in enumerate(sources):
                job_id = f"batch-{uuid.uuid4().hex[:10]}"
                job = BatchJob(
                    job_id=job_id,
                    sequence=start + offset,
                    source=dict(source),
                    model=model,
                )
                self._jobs[job_id] = job
                self._queue.put(job_id)
                job_ids.append(job_id)
        return job_ids

    def snapshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return [job.public() for job in self._jobs.values()]

    def get(self, job_id: str) -> BatchJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                self._queue.task_done()
                continue
            with self._lock:
                job.status = "RUNNING"
                job.started_at = _now()
            try:
                output, resolved_source = self._runner(job.source, job.model)
            except Exception as error:
                from scripts.run_pipeline import failure_record

                with self._lock:
                    job.status = "FAILED"
                    job.failure = failure_record(job.source, "openai", "aggregate", error)
                    job.finished_at = _now()
            else:
                with self._lock:
                    job.status = "SUCCEEDED"
                    job.output = output
                    job.resolved_source = resolved_source
                    job.finished_at = _now()
            finally:
                self._queue.task_done()
