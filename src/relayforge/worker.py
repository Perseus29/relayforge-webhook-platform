"""Small worker loop for local development and integration tests."""
from __future__ import annotations

from datetime import datetime, timezone
from relayforge.api.app import Runtime


class Worker:
    def __init__(self, runtime: Runtime, worker_id: str) -> None:
        self.runtime = runtime
        self.worker_id = worker_id

    def run_once(self, tenant_id: str, limit: int = 20, now: datetime | None = None) -> int:
        claimed = self.runtime.scheduler.claim_due(self.worker_id, tenant_id, limit, now)
        completed = 0
        for delivery, lease in claimed:
            self.runtime.delivery.execute(delivery.id, lease, now or datetime.now(timezone.utc))
            completed += 1
        return completed
