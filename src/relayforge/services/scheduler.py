"""Lease-based scheduler for safely claiming due work from concurrent workers."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable

from relayforge.domain.errors import LeaseLostError
from relayforge.domain.models import Delivery, DeliveryStatus, WorkerLease
from relayforge.domain.policies import can_claim
from relayforge.infra.repository import InMemoryRepository
from relayforge.services.ordering import OrderingService


class Scheduler:
    def __init__(self, repository: InMemoryRepository, ordering: OrderingService | None = None,
                 lease_seconds: int = 45) -> None:
        self.repository = repository
        self.ordering = ordering or OrderingService(repository)
        self.lease_seconds = lease_seconds

    def claim_due(self, worker_id: str, tenant_id: str, limit: int = 20,
                  now: datetime | None = None) -> list[tuple[Delivery, WorkerLease]]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("scheduler clock must be timezone-aware")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        claimed: list[tuple[Delivery, WorkerLease]] = []
        for delivery in self.repository.list_deliveries(tenant_id):
            if len(claimed) >= limit:
                break
            destination = self.repository.get_destination(tenant_id, delivery.destination_id)
            if not can_claim(delivery, destination, current,
                             ordering_blocked=self.ordering.is_blocked(delivery)):
                continue
            token = secrets.token_urlsafe(24)
            expires = current + timedelta(seconds=self.lease_seconds)
            updated = delivery.evolve(status=DeliveryStatus.IN_FLIGHT, lease_owner=worker_id,
                                      lease_token=token, lease_expires_at=expires)
            self.repository.save_delivery(updated, expected_version=delivery.version)
            claimed.append((updated, WorkerLease(delivery.id, worker_id, token, expires, current)))
        return claimed

    def assert_lease(self, delivery: Delivery, lease: WorkerLease, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        if (delivery.lease_owner != lease.worker_id or delivery.lease_token != lease.token
                or delivery.lease_expires_at is None or delivery.lease_expires_at <= current):
            raise LeaseLostError(delivery.id)

