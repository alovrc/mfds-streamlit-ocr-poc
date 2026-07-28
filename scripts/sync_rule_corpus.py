"""Build and optionally upload homogeneous Rule files for filtered retrieval.

The source JSONL stays outside this public deployment repository. Generated
files default to the ignored .codex_tmp directory and contain only active,
typed Rule records already present in the validated review corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from openai import OpenAI

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from adapters.openai.client import _resolve_vector_store_id

ROUTES = {
    "FS11_FOOD_REVIEW": (
        "FOOD_REVIEW",
        "FS11_FOOD_REVIEW/fs11_food_review_records.jsonl",
    ),
    "FS21_HFF_REVIEW": (
        "HFF_REVIEW",
        "FS21_HFF_REVIEW/fs21_hff_review_records.jsonl",
    ),
}
LEGAL_ITEMS = {
    "DISEASE_PREVENTION_TREATMENT": "ARTICLE8_1_1",
    "MEDICINE_CONFUSION": "ARTICLE8_1_2",
    "HFF_CONFUSION": "ARTICLE8_1_3",
    "FALSE_EXAGGERATED": "ARTICLE8_1_4",
    "CONSUMER_DECEPTION": "ARTICLE8_1_5",
}


def load_typed_rules(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            violation_type = str(record.get("violation_type") or "")
            if (
                record.get("record_class") == "RULE"
                and str(record.get("status") or "").upper() == "ACTIVE"
                and violation_type in LEGAL_ITEMS
            ):
                grouped[violation_type].append(record)
    return dict(grouped)


def render_rule_file(
    route: str,
    violation_type: str,
    records: list[dict[str, Any]],
) -> str:
    sections = [
        "# MFDS filtered Rule corpus",
        "",
        f"- record_class: RULE",
        f"- product_route: {route}",
        f"- violation_type: {violation_type}",
        f"- legal_item: {LEGAL_ITEMS[violation_type]}",
        f"- record_count: {len(records)}",
        "",
    ]
    for record in records:
        sections.extend(
            [
                f"## {record['record_id']}",
                "",
                f"record_id: {record['record_id']}",
                "record_class: RULE",
                f"product_route: {route}",
                f"violation_type: {violation_type}",
                f"legal_item: {LEGAL_ITEMS[violation_type]}",
                f"authority_level: {record.get('authority_level', '')}",
                f"status: {record.get('status', '')}",
                f"source_document: {record.get('source_document', '')}",
                f"article: {record.get('article', '')}",
                f"source_url: {record.get('source_url', '')}",
                f"condition: {record.get('search_text', '')}",
                f"decision: {record.get('quote', '')}",
                "",
            ]
        )
    return "\n".join(sections).strip() + "\n"


def build_files(
    source_root: Path,
    output_dir: Path,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for store_alias, (route, relative_path) in ROUTES.items():
        groups = load_typed_rules(source_root / relative_path)
        for violation_type, records in sorted(groups.items()):
            text = render_rule_file(route, violation_type, records)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            filename = (
                f"{store_alias.lower()}_rule_"
                f"{violation_type.lower()}_{digest[:12]}.md"
            )
            path = output_dir / filename
            path.write_text(text, encoding="utf-8")
            manifest.append(
                {
                    "store_alias": store_alias,
                    "route": route,
                    "violation_type": violation_type,
                    "legal_item": LEGAL_ITEMS[violation_type],
                    "record_count": len(records),
                    "sha256": digest,
                    "path": str(path),
                }
            )
    return manifest


def existing_hashes(
    client: OpenAI,
    vector_store_id: str,
) -> set[str]:
    hashes: set[str] = set()
    page = client.vector_stores.files.list(
        vector_store_id=vector_store_id,
        limit=100,
    )
    for item in page:
        attributes = getattr(item, "attributes", None) or {}
        digest = str(attributes.get("file_sha256") or "")
        if digest:
            hashes.add(digest)
    return hashes


def upload_files(
    client: OpenAI,
    manifest: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    stores: dict[str, str] = {}
    known_hashes: dict[str, set[str]] = {}
    for item in manifest:
        alias = item["store_alias"]
        if alias not in stores:
            stores[alias] = _resolve_vector_store_id(client, alias)
            known_hashes[alias] = existing_hashes(client, stores[alias])
        if item["sha256"] in known_hashes[alias]:
            results.append({**item, "status": "SKIPPED_ALREADY_PRESENT"})
            continue
        path = Path(item["path"])
        with path.open("rb") as handle:
            uploaded = client.files.create(
                file=handle,
                purpose="assistants",
            )
        association = client.vector_stores.files.create_and_poll(
            str(uploaded.id),
            vector_store_id=stores[alias],
            attributes={
                "corpus_type": "review_rule",
                "record_class": "RULE",
                "product_route": item["route"],
                "violation_type": item["violation_type"],
                "legal_item": item["legal_item"],
                "active": True,
                "corpus_version": "2026-07-28-rule-filter-v1",
                "record_count": item["record_count"],
                "file_sha256": item["sha256"],
            },
        )
        status = str(getattr(association, "status", ""))
        if status != "completed":
            raise RuntimeError(
                f"RULE_FILE_INDEX_FAILED: {alias}/{item['violation_type']}"
            )
        known_hashes[alias].add(item["sha256"])
        results.append({**item, "status": "UPLOADED"})
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".codex_tmp/rule_corpus"),
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_files(args.source_root, args.output_dir)
    results = manifest
    if args.apply:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        results = upload_files(OpenAI(api_key=key), manifest)
    report_path = args.output_dir / "rule_sync_report.json"
    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "generated_files": len(manifest),
                "uploaded": sum(
                    item.get("status") == "UPLOADED" for item in results
                ),
                "skipped": sum(
                    item.get("status") == "SKIPPED_ALREADY_PRESENT"
                    for item in results
                ),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
