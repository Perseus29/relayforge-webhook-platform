"""Delivery execution, attempt recording, and state transitions.

The transport is injected so the service can be tested without network access. A real HTTP
adapter must enforce DNS/IP egress policy at connection time (not only URL validation),
apply timeouts, sign exact transmitted bytes, and cap response-body capture. Worker lease
validation is repeated immediately before persistence to prevent stale workers overwriting
newer state after a long request.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Protocol

from relayforge.domain.errors import LeaseLostError, PermanentDeliveryError, TransientDeliveryError
from relayforge.domain.models import (AttemptOutcome, DeadLetter, Delivery, DeliveryAttempt,
                                      DeliveryResult, DeliveryStatus, WorkerLease)
from relayforge.domain.policies import decide_retry
from relayforge.infra.repository import InMemoryRepository
from relayforge.services.retry import next_attempt_at
from relayforge.services.scheduler import Scheduler


class Transport(Protocol):
    def send(self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float) -> DeliveryResult: ...


class StubTransport:
    """Deterministic transport for local development; response configured per destination."""
    def __init__(self, results: dict[str, DeliveryResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def send(self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float) -> DeliveryResult:
        self.calls.append((url, body, dict(headers), timeout_seconds))
        return self.results.get(url, DeliveryResult(AttemptOutcome.SUCCESS, 204, ""))


class DeliveryService:
    def __init__(self, repository: InMemoryRepository, scheduler: Scheduler,
                 transport: Transport, signing_secrets: dict[str, bytes] | None = None) -> None:
        self.repository = repository
        self.scheduler = scheduler
        self.transport = transport
        self.signing_secrets = signing_secrets or {}

    def _body_and_headers(self, delivery: Delivery) -> tuple[bytes, dict[str, str], str, float]:
        event = self.repository.snapshot().events[delivery.event_id]
        destination = self.repository.get_destination(delivery.tenant_id, delivery.destination_id)
        envelope = {"id": event.id, "type": event.event_type, "tenant_id": event.tenant_id,
                    "sequence": event.sequence, "created_at": event.created_at.isoformat(),
                    "data": event.payload, "metadata": event.metadata}
        body = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "RelayForge/0.1",
                   "X-RelayForge-Event": event.id, **dict(destination.headers)}
        secret = self.signing_secrets.get(destination.secret_ref or "")
        if secret:
            signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
            headers["X-RelayForge-Signature"] = f"sha256={signature}"
        return body, headers, destination.url, destination.timeout_seconds

    def execute(self, delivery_id: str, lease: WorkerLease, now: datetime | None = None) -> Delivery:
        current = now or datetime.now(timezone.utc)
        delivery = self.repository.get_delivery(lease.worker_id.split(":", 1)[0] if False else
                                                self.repository.snapshot().deliveries[delivery_id].tenant_id,
                                                delivery_id)
        self.scheduler.assert_lease(delivery, lease, current)
        destination = self.repository.get_destination(delivery.tenant_id, delivery.destination_id)
        body, headers, url, timeout = self._body_and_headers(delivery)
        started = time.monotonic()
        try:
            result = self.transport.send(url, body, headers, timeout)
        except TimeoutError as exc:
            result = DeliveryResult(AttemptOutcome.TIMEOUT, None, error=str(exc) or "request timed out")
        except PermanentDeliveryError as exc:
            result = DeliveryResult(AttemptOutcome.PERMANENT_FAILURE, exc.status_code, error=str(exc))
        except TransientDeliveryError as exc:
            result = DeliveryResult(AttemptOutcome.TRANSIENT_FAILURE, None, error=str(exc),
                                    retry_after_seconds=exc.retry_after_seconds)
        duration = max(0, int((time.monotonic() - started) * 1000))
        latest = self.repository.get_delivery(delivery.tenant_id, delivery.id)
        self.scheduler.assert_lease(latest, lease, current)
        attempt_number = latest.attempt_count + 1
        attempt = DeliveryAttempt(str(uuid.uuid4()), latest.tenant_id, latest.id, attempt_number,
                                  result.outcome, current, current, duration, result.status_code,
                                  result.error, (result.response_excerpt or "")[:1024], lease.worker_id)
        self.repository.append_attempt(attempt)
        permanent = result.outcome == AttemptOutcome.PERMANENT_FAILURE
        decision = decide_retry(destination.retry_policy, attempt_number, result.status_code, permanent)
        if result.outcome == AttemptOutcome.SUCCESS or decision.terminal_status == DeliveryStatus.SUCCEEDED:
            updated = latest.evolve(status=DeliveryStatus.SUCCEEDED, attempt_count=attempt_number,
                                    last_status_code=result.status_code, last_error=None,
                                    lease_owner=None, lease_token=None, lease_expires_at=None)
        elif decision.retry:
            due = next_attempt_at(destination.retry_policy, attempt_number, current,
                                  result.retry_after_seconds)
            updated = latest.evolve(status=DeliveryStatus.RETRY_WAIT, attempt_count=attempt_number,
                                    next_attempt_at=due, last_status_code=result.status_code,
                                    last_error=result.error or decision.reason, lease_owner=None,
                                    lease_token=None, lease_expires_at=None)
        else:
            updated = latest.evolve(status=DeliveryStatus.DEAD_LETTERED, attempt_count=attempt_number,
                                    last_status_code=result.status_code,
                                    last_error=result.error or decision.reason, lease_owner=None,
                                    lease_token=None, lease_expires_at=None)
            self.repository.upsert_dead_letter(DeadLetter(str(uuid.uuid4()), latest.tenant_id,
                                                          latest.id, updated.last_error or "terminal failure"))
        return self.repository.save_delivery(updated, expected_version=latest.version)

