"""OpenAI Responses API + hosted File Search + Structured Outputs adapter."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from openai import OpenAI

from adapters.common.contracts import (
    ProviderConfigError,
    ProviderResponseError,
    ProviderResult,
    SearchCitation,
    provider_schema,
    store_identifier,
)

DEFAULT_MODEL = "gpt-5.6-sol"
RECORD_ID_PATTERN = re.compile(
    r'(?:record_id|영구 ID)["\s:=]+([A-Za-z0-9_.:/-]+)',
    re.IGNORECASE,
)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _retrieval_metadata(response: Any) -> tuple[bool, list[str], list[SearchCitation]]:
    payload = _as_dict(response)
    file_search_run = False
    retrieved: list[str] = []
    citations: list[SearchCitation] = []

    for item in _walk(payload):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "file_search_call":
            file_search_run = True
            results = item.get("results") or item.get("search_results") or []
            if isinstance(results, list):
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    text = str(result.get("text") or "")
                    record_ids = RECORD_ID_PATTERN.findall(text)
                    retrieved.extend(record_ids)
                    for record_id in record_ids:
                        citations.append(
                            SearchCitation(
                                record_id=record_id,
                                file_name=result.get("filename"),
                                source=result.get("file_id"),
                                page=None,
                            )
                        )
        if item_type == "file_citation":
            record_id = str(
                item.get("record_id")
                or item.get("file_id")
                or item.get("filename")
                or ""
            )
            citations.append(
                SearchCitation(
                    record_id=record_id,
                    file_name=item.get("filename") or item.get("file_name"),
                    source=item.get("file_id") or item.get("source"),
                    page=item.get("page_number"),
                )
            )
        for key in ("text", "content"):
            text = item.get(key)
            if isinstance(text, str):
                retrieved.extend(RECORD_ID_PATTERN.findall(text))

    unique_citations: list[SearchCitation] = []
    seen_citations: set[tuple[str, str | None]] = set()
    for citation in citations:
        key = (citation.record_id, citation.file_name)
        if key not in seen_citations:
            unique_citations.append(citation)
            seen_citations.add(key)
    return file_search_run, list(dict.fromkeys(retrieved)), unique_citations


def run(
    *,
    system_prompt: str,
    payload: dict[str, Any],
    schema_name: str,
    store_alias: str,
    max_results: int = 12,
) -> ProviderResult:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ProviderConfigError("OPENAI_API_KEY is not configured")
    vector_store_id = store_identifier("openai", store_alias)
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "180")),
        max_retries=0,
    )
    args = {
        "model": model,
        "instructions": system_prompt,
        "input": json.dumps(payload, ensure_ascii=False),
        "tools": [
            {
                "type": "file_search",
                "vector_store_ids": [vector_store_id],
                "max_num_results": max_results,
            }
        ],
        "include": ["file_search_call.results"],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name.removesuffix(".schema.json"),
                "schema": provider_schema(schema_name),
                "strict": True,
            }
        },
        "reasoning": {
            "effort": os.getenv("OPENAI_REASONING_EFFORT", "medium")
        },
        "store": False,
    }

    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = client.responses.create(**args)
            output_text = getattr(response, "output_text", "") or ""
            if not output_text.strip():
                raise ProviderResponseError("MODEL_REFUSAL_OR_EMPTY")
            data = json.loads(output_text)
            if not isinstance(data, dict):
                raise ProviderResponseError("provider result is not an object")
            file_search_run, retrieved_ids, citations = _retrieval_metadata(response)
            if not file_search_run:
                raise ProviderResponseError("FILE_SEARCH_NOT_RUN")
            return ProviderResult(
                data=data,
                provider="openai",
                store_alias=store_alias,
                file_search_run=True,
                retrieved_ids=retrieved_ids,
                citations=citations,
                latency_ms=int((time.perf_counter() - started) * 1000),
                raw_response_id=getattr(response, "id", None),
            )
        except (json.JSONDecodeError, ProviderResponseError) as error:
            last_error = error
        except Exception as error:  # SDK maps provider and network errors.
            last_error = error
        if attempt == 0:
            continue
    raise ProviderResponseError(f"OpenAI request failed after one retry: {last_error}")
