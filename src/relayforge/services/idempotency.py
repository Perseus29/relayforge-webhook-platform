"""Tenant-scoped idempotency orchestration for event ingestion."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from relayforge.domain.canonicalization import payload_digest
from relayforge.domain.errors import ConflictError, ValidationError
from relayforge.domain.models import Delivery, Destination, Event
from relayforge.infra.repository import InMemoryRepository


@dataclass(frozen=True, slots=True)
class IngestResult:
    event: Event
    deliveries: tuple[Delivery, ...]
    created: bool


class IdempotencyService:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def ingest(self, tenant_id: str, event_type: str, payload: Mapping[str, Any],
               idempotency_key: str, destination_ids: list[str], metadata: Mapping[str, str] | None = None) -> IngestResult:
        if not tenant_id or not event_type.strip():
            raise ValidationError("tenant_id and event_type are required")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValidationError("Idempotency-Key must contain 1 to 200 characters")
        if not destination_ids:
            raise ValidationError("At least one destination is required")
        if len(set(destination_ids)) != len(destination_ids):
            raise ValidationError("Destination list contains duplicates")
        destinations = [self.repository.get_destination(tenant_id, item) for item in destination_ids]
        digest = payload_digest({"type": event_type, "payload": payload})
        event = Event(id=str(uuid.uuid4()), tenant_id=tenant_id, event_type=event_type,
                      payload=dict(payload), idempotency_key=idempotency_key,
                      payload_hash=digest, sequence=self.repository.next_sequence(tenant_id),
                      metadata=dict(metadata or {}))
        stored, created = self.repository.insert_event_once(event)
        if not created:
            if stored.payload_hash != digest:
                raise ConflictError("Idempotency key was already used with a different payload",
                                    idempotency_key=idempotency_key, existing_event_id=stored.id)
            existing = self.repository.list_deliveries(tenant_id, event_id=stored.id)
            return IngestResult(stored, tuple(existing), False)
        deliveries: list[Delivery] = []
        for destination in destinations:
            delivery = Delivery(id=str(uuid.uuid4()), tenant_id=tenant_id, event_id=stored.id,
                                destination_id=destination.id, ordering_key=destination.ordering_key)
            self.repository.add_delivery(delivery)
            deliveries.append(delivery)
        return IngestResult(stored, tuple(deliveries), True)

