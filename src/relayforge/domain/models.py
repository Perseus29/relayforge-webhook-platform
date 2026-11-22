"""Core immutable-ish domain records and state transition vocabulary.

Records are intentionally independent of FastAPI and persistence. A future SQL adapter
must be able to map these objects without importing the HTTP layer. IDs are opaque strings;
callers must not infer chronology or tenant ownership from their shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


class EventStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    PARTIALLY_DELIVERED = "partially_delivered"
    DELIVERED = "delivered"
    FAILED = "failed"


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 8
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 900.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.2
    retryable_status_codes: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least one")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")


@dataclass(frozen=True, slots=True)
class Destination:
    id: str
    tenant_id: str
    url: str
    enabled: bool = True
    secret_ref: str | None = None
    timeout_seconds: float = 10.0
    concurrency_limit: int = 4
    ordering_key: str | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    headers: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    version: int = 1


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    tenant_id: str
    event_type: str
    payload: Mapping[str, Any]
    idempotency_key: str
    payload_hash: str
    sequence: int
    created_at: datetime = field(default_factory=utc_now)
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Delivery:
    id: str
    tenant_id: str
    event_id: str
    destination_id: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempt_count: int = 0
    next_attempt_at: datetime = field(default_factory=utc_now)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    last_status_code: int | None = None
    last_error: str | None = None
    ordering_key: str | None = None
    replay_of: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    version: int = 1

    def evolve(self, **changes: Any) -> "Delivery":
        return replace(self, updated_at=utc_now(), version=self.version + 1, **changes)


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    id: str
    tenant_id: str
    delivery_id: str
    attempt_number: int
    outcome: AttemptOutcome
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    status_code: int | None = None
    error: str | None = None
    response_excerpt: str | None = None
    worker_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeadLetter:
    id: str
    tenant_id: str
    delivery_id: str
    reason: str
    failed_at: datetime = field(default_factory=utc_now)
    resolved_at: datetime | None = None
    resolution: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    tenant_id: str
    delivery_ids: tuple[str, ...]
    requested_by: str
    reason: str
    idempotency_key: str
    requested_at: datetime = field(default_factory=utc_now)
    dry_run: bool = False
    preserve_order: bool = True


@dataclass(frozen=True, slots=True)
class WorkerLease:
    delivery_id: str
    worker_id: str
    token: str
    expires_at: datetime
    acquired_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: AttemptOutcome
    status_code: int | None
    response_excerpt: str | None = None
    error: str | None = None
    retry_after_seconds: float | None = None

