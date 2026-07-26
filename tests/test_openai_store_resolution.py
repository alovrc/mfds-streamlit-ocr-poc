from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.common.contracts import ProviderConfigError
from adapters.openai import client as openai_client


class FakeVectorStores:
    def __init__(self, *, configured_valid: bool, stores: list[object]):
        self.configured_valid = configured_valid
        self.stores = stores

    def retrieve(self, _store_id: str) -> object:
        if not self.configured_valid:
            raise RuntimeError("not found")
        return object()

    def list(self, *, limit: int) -> list[object]:
        assert limit == 100
        return self.stores


def setup_function() -> None:
    openai_client._STORE_ID_CACHE.clear()


def test_uses_valid_configured_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        openai_client,
        "store_identifier",
        lambda _provider, _alias: "configured",
    )
    client = SimpleNamespace(
        vector_stores=FakeVectorStores(configured_valid=True, stores=[])
    )

    assert (
        openai_client._resolve_vector_store_id(client, "FS01_PRODUCT_GATE")
        == "configured"
    )


def test_falls_back_to_unique_store_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_client,
        "store_identifier",
        lambda _provider, _alias: "stale",
    )
    expected_name = openai_client.STORE_NAMES["FS01_PRODUCT_GATE"]
    client = SimpleNamespace(
        vector_stores=FakeVectorStores(
            configured_valid=False,
            stores=[
                SimpleNamespace(id="resolved", name=expected_name),
                SimpleNamespace(id="other", name="unrelated"),
            ],
        )
    )

    assert (
        openai_client._resolve_vector_store_id(client, "FS01_PRODUCT_GATE")
        == "resolved"
    )


def test_rejects_ambiguous_store_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_client,
        "store_identifier",
        lambda _provider, _alias: "stale",
    )
    expected_name = openai_client.STORE_NAMES["FS01_PRODUCT_GATE"]
    client = SimpleNamespace(
        vector_stores=FakeVectorStores(
            configured_valid=False,
            stores=[
                SimpleNamespace(id="first", name=expected_name),
                SimpleNamespace(id="second", name=expected_name),
            ],
        )
    )

    with pytest.raises(
        ProviderConfigError,
        match="FILE_SEARCH_STORE_NOT_UNIQUELY_CONFIGURED",
    ):
        openai_client._resolve_vector_store_id(client, "FS01_PRODUCT_GATE")
