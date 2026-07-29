#!/usr/bin/env python3
"""Run fixed Stage 1 regression inputs from a JSONL file.

Each JSONL line is analyzed as a fixed title/body_text input.
source_url is retained as metadata only; this script never captures URLs or runs OCR.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_pipeline import failure_record, sanitize_unicode_surrogates, stage1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        choices=["offline", "openai"],
        required=True,
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results: list[dict] = []
    failed_count = 0

    for line_number, raw_line in enumerate(
        args.input.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue

        source: dict = {}
        try:
            source = sanitize_unicode_surrogates(json.loads(raw_line))
            result = stage1(args.provider, source)
            results.append(
                {
                    "record_id": source["record_id"],
                    "status": "SUCCESS",
                    "input": source,
                    "stage1": result,
                }
            )
        except Exception as error:
            failed_count += 1
            results.append(
                {
                    "record_id": str(
                        source.get("record_id") or f"LINE-{line_number}"
                    ),
                    "status": "FAILED",
                    "failure": failure_record(
                        source,
                        args.provider,
                        "stage1",
                        error,
                    ),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in results
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"stage1 regression complete: "
        f"{len(results)} records, {failed_count} failed"
    )
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
