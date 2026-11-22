from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from relayforge.api.app import Runtime, create_app
from relayforge.domain.canonicalization import canonical_json, payload_digest
from relayforge.domain.errors import ConflictError
from relayforge.domain.models import Destination, RetryPolicy
from relayforge.services.retry import compute_backoff, parse_retry_after


def test_canonical_json_ignores_mapping_insertion_order():
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert payload_digest({"a": 1}) == payload_digest({"a": 1})


def test_backoff_is_bounded_and_deterministic():
    policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=100, jitter_ratio=0.2)
    assert compute_backoff(policy, 1, random_source=lambda: 0.5) == 10
    assert compute_backoff(policy, 20, random_source=lambda: 0.5) == 100


def test_retry_after_supports_seconds_and_http_date():
    assert parse_retry_after("12") == 12
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_retry_after("Thu, 01 Jan 2026 00:00:30 GMT", now) == 30


def test_tenant_cannot_read_another_tenant_destination():
    runtime = Runtime()
    runtime.repository.add_destination(Destination("d1", "tenant-a", "https://example.com/hook"))
    with pytest.raises(Exception):
        runtime.repository.get_destination("tenant-b", "d1")


def test_http_health_and_tenant_required():
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    assert client.get("/v1/deliveries").status_code == 422


def test_ingest_and_idempotent_reingest():
    runtime = Runtime()
    destination = Destination("d1", "tenant-a", "https://example.com/hook")
    runtime.repository.add_destination(destination)
    first = runtime.ingest.ingest("tenant-a", "invoice.created", {"invoice": 7}, "key-1", ["d1"])
    second = runtime.ingest.ingest("tenant-a", "invoice.created", {"invoice": 7}, "key-1", ["d1"])
    assert first.created is True
    assert second.created is False
    assert first.event.id == second.event.id


def test_idempotency_key_payload_mismatch_is_conflict():
    runtime = Runtime()
    runtime.repository.add_destination(Destination("d1", "tenant-a", "https://example.com/hook"))
    runtime.ingest.ingest("tenant-a", "invoice.created", {"invoice": 7}, "key-1", ["d1"])
    with pytest.raises(ConflictError):
        runtime.ingest.ingest("tenant-a", "invoice.created", {"invoice": 8}, "key-1", ["d1"])
