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
STORE_NAMES = {
    "FS01_PRODUCT_GATE": "MFDS_FS01_PRODUCT_GATE_20260727_V09",
    "FS11_FOOD_REVIEW": "MFDS_FS11_FOOD_REVIEW_20260727_V09",
    "FS21_HFF_REVIEW": "MFDS_FS21_HFF_REVIEW_20260727_V09",
}
_STORE_ID_CACHE: dict[str, str] = {}
RECORD_ID_PATTERN = re.compile(
    r'(?:record_id|영구 ID)["\s:=]+([A-Za-z0-9_.:/-]+)',
    re.IGNORECASE,
)


def _resolve_vector_store_id(client: OpenAI, store_alias: str) -> str:
    """Resolve a usable store without exposing project-specific IDs.

    A saved ID can become stale when the API key is moved to another project.
    Verify the configured ID first, then fall back to the unique deployment
    store name in the key's current project.
    """

    cached = _STORE_ID_CACHE.get(store_alias)
    if cached:
        return cached

    configured = ""
    try:
        configured = store_identifier("openai", store_alias)
    except ProviderConfigError:
        pass

    if configured:
        try:
            client.vector_stores.retrieve(configured)
        except Exception:
            # Do not include the provider exception because it can contain IDs.
            pass
        else:
            _STORE_ID_CACHE[store_alias] = configured
            return configured

    expected_name = STORE_NAMES.get(store_alias)
    if not expected_name:
        raise ProviderConfigError("UNKNOWN_FILE_SEARCH_STORE_ALIAS")

    try:
        stores = client.vector_stores.list(limit=100)
        matches = [
            item
            for item in stores
            if getattr(item, "name", None) == expected_name
        ]
    except Exception as error:
        raise ProviderConfigError(
            "FILE_SEARCH_STORE_DISCOVERY_FAILED"
        ) from error

    if len(matches) != 1:
        raise ProviderConfigError(
            "FILE_SEARCH_STORE_NOT_UNIQUELY_CONFIGURED"
        )

    resolved = str(getattr(matches[0], "id", "")).strip()
    if not resolved:
        raise ProviderConfigError("FILE_SEARCH_STORE_ID_MISSING")
    _STORE_ID_CACHE[store_alias] = resolved
    return resolved


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _provider_error_code(error: Exception) -> str | None:
    """Map provider failures to safe public codes without leaking details."""

    text = str(error).lower()
    if "insufficient_quota" in text or "current quota" in text:
        return "PROVIDER_QUOTA_EXCEEDED"
    if "invalid_api_key" in text or "incorrect api key" in text:
        return "PROVIDER_AUTH_FAILED"
    if "rate_limit_exceeded" in text:
        return "PROVIDER_RATE_LIMITED"
    if "permission" in text or "error code: 403" in text:
        return "PROVIDER_PROJECT_ACCESS_DENIED"
    if "timeout" in text or "timed out" in text:
        return "PROVIDER_TIMEOUT"
    return None


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _search_excerpt(
    text: str,
    record_id: str,
    *,
    max_chars: int = 1200,
) -> str | None:
    """Return a bounded search-result excerpt centred on the record ID."""

    normalized = " ".join(text.split())
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized
    position = normalized.find(record_id)
    if position < 0:
        return normalized[:max_chars].rstrip() + "…"
    start = max(0, position - max_chars // 3)
    end = min(len(normalized), start + max_chars)
    start = max(0, end - max_chars)
    excerpt = normalized[start:end].strip()
    rendered = (
        ("…" if start else "")
        + excerpt
        + ("…" if end < len(normalized) else "")
    )
    return rendered[:max_chars]


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
                    file_id = str(result.get("file_id") or "").strip()
                    file_name = result.get("filename")
                    citation_ids = record_ids or [
                        value for value in (file_id, file_name) if value
                    ][:1]
                    retrieved.extend(record_ids or ([file_id] if file_id else []))
                    for record_id in citation_ids:
                        citations.append(
                            SearchCitation(
                                record_id=record_id,
                                file_name=file_name,
                                source=file_id or None,
                                page=None,
                                excerpt=_search_excerpt(text, record_id),
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
                    excerpt=None,
                )
            )
        for key in ("text", "content"):
            text = item.get(key)
            if isinstance(text, str):
                retrieved.extend(RECORD_ID_PATTERN.findall(text))

    unique_citations: list[SearchCitation] = []
    citation_positions: dict[tuple[str, str | None], int] = {}
    for citation in citations:
        key = (citation.record_id, citation.file_name)
        if key not in citation_positions:
            citation_positions[key] = len(unique_citations)
            unique_citations.append(citation)
        elif (
            not unique_citations[citation_positions[key]].excerpt
            and citation.excerpt
        ):
            unique_citations[citation_positions[key]] = citation
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
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "180")),
        max_retries=0,
    )
    vector_store_id = _resolve_vector_store_id(client, store_alias)
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
            safe_code = _provider_error_code(error)
            if safe_code and safe_code != "PROVIDER_RATE_LIMITED":
                raise ProviderResponseError(safe_code) from error
            last_error = (
                ProviderResponseError(safe_code)
                if safe_code
                else error
            )
        if attempt == 0:
            if isinstance(last_error, ProviderResponseError) and str(
                last_error
            ) == "PROVIDER_RATE_LIMITED":
                time.sleep(1)
            continue
    raise ProviderResponseError(f"OpenAI request failed after one retry: {last_error}")
