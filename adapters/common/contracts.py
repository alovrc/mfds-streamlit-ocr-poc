"""Shared provider request/result types and safe local configuration loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class ProviderConfigError(RuntimeError):
    """Provider configuration is absent or incompatible."""


class ProviderResponseError(RuntimeError):
    """Provider returned an unusable response."""


@dataclass(frozen=True)
class SearchCitation:
    record_id: str
    file_name: str | None = None
    source: str | None = None
    page: int | None = None
    excerpt: str | None = None


@dataclass
class ProviderResult:
    data: dict[str, Any]
    provider: str
    store_alias: str
    file_search_run: bool
    retrieved_ids: list[str] = field(default_factory=list)
    citations: list[SearchCitation] = field(default_factory=list)
    supplemental_queries: list[str] = field(default_factory=list)
    latency_ms: int = 0
    raw_response_id: str | None = None

    def tracking(self, query: str) -> dict[str, Any]:
        search_query = " | ".join(
            dict.fromkeys([query, *self.supplemental_queries])
        )
        return {
            "provider": self.provider,
            "store_alias": self.store_alias,
            "file_search_run": self.file_search_run,
            "search_query": search_query,
            "retrieved_ids": self.retrieved_ids,
            "citations": [
                {
                    "record_id": item.record_id,
                    "file_name": item.file_name,
                    "source": item.source,
                    "page": item.page,
                    "excerpt": item.excerpt,
                }
                for item in self.citations
            ],
            "latency_ms": self.latency_ms,
        }


def load_store_registry() -> dict[str, Any]:
    candidates = [
        os.getenv("MFDS_STORE_REGISTRY"),
        str(PACKAGE_ROOT / "config" / "store_registry.json"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return json.loads(Path(candidate).read_text(encoding="utf-8"))
    return {}


def store_identifier(provider: str, alias: str) -> str:
    env_name = f"{provider.upper()}_{alias}_STORE_ID"
    direct = os.getenv(env_name, "").strip()
    if direct:
        return direct
    registry = load_store_registry()
    value: Any = registry.get(provider, {}).get(alias, "")
    if not value and registry.get("provider") == provider:
        value = registry.get("stores", {}).get(alias, {}).get("id", "")
    if isinstance(value, dict):
        value = value.get("id", "")
    if not value:
        raise ProviderConfigError(
            f"{provider} store is not configured for {alias}; set {env_name}"
        )
    return str(value)


def load_schema(schema_name: str) -> dict[str, Any]:
    return json.loads(
        (PACKAGE_ROOT / "schemas" / schema_name).read_text(encoding="utf-8")
    )


def provider_schema(schema_name: str) -> dict[str, Any]:
    """Return the common provider subset without document-only schema keywords."""

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key not in {"$schema", "$id", "format"}
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(load_schema(schema_name))
