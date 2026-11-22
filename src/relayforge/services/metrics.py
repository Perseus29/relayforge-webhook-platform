"""Low-cardinality operational metrics and tenant-safe aggregate views."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from relayforge.domain.models import DeliveryStatus
from relayforge.infra.repository import InMemoryRepository


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    generated_at: datetime
    total_deliveries: int
    by_status: dict[str, int]
    attempts_last_hour: int
    dead_letter_count: int
    success_rate: float
    average_attempts: float


class MetricsService:
    """Metrics are derived from durable state rather than process-local counters."""
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository
        self._lock = Lock()
        self._counters: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("metrics cannot be decremented")
        with self._lock:
            self._counters[name] += amount

    def snapshot(self, tenant_id: str, now: datetime | None = None) -> MetricSnapshot:
        current = now or datetime.now(timezone.utc)
        deliveries = self.repository.list_deliveries(tenant_id)
        statuses: Counter[str] = Counter(d.status.value for d in deliveries)
        attempts = [a for d in deliveries for a in self.repository.list_attempts(tenant_id, d.id)]
        recent = [a for a in attempts if a.finished_at >= current - timedelta(hours=1)]
        succeeded = statuses[DeliveryStatus.SUCCEEDED.value]
        terminal = succeeded + statuses[DeliveryStatus.DEAD_LETTERED.value]
        success_rate = succeeded / terminal if terminal else 0.0
        avg = sum(d.attempt_count for d in deliveries) / len(deliveries) if deliveries else 0.0
        dead_letters = len(self.repository.list_dead_letters(tenant_id))
        return MetricSnapshot(current, len(deliveries), dict(statuses), len(recent), dead_letters,
                              success_rate, avg)

    def as_dict(self, tenant_id: str, now: datetime | None = None) -> dict[str, Any]:
        snapshot = self.snapshot(tenant_id, now)
        return {"generated_at": snapshot.generated_at.isoformat(),
                "total_deliveries": snapshot.total_deliveries,
                "by_status": snapshot.by_status,
                "attempts_last_hour": snapshot.attempts_last_hour,
                "dead_letter_count": snapshot.dead_letter_count,
                "success_rate": round(snapshot.success_rate, 4),
                "average_attempts": round(snapshot.average_attempts, 2)}

