"""Operator-controlled replay with audit metadata and idempotent batch submission."""
from __future__ import annotations

import uuid
from dataclasses import replace

from relayforge.domain.errors import NotFoundError, ReplayRejectedError
from relayforge.domain.models import Delivery, DeliveryStatus, ReplayRequest
from relayforge.domain.policies import validate_replay_request
from relayforge.infra.repository import InMemoryRepository


class ReplayService:
    def __init__(self, repository: InMemoryRepository, max_batch: int = 100) -> None:
        self.repository = repository
        self.max_batch = max_batch

    def replay(self, request: ReplayRequest) -> tuple[Delivery, ...]:
        validate_replay_request(request.delivery_ids, request.reason, self.max_batch)
        previous, created = self.repository.record_replay_key(request.tenant_id,
                                                               request.idempotency_key, ())
        if not created:
            return tuple(self.repository.get_delivery(request.tenant_id, item) for item in previous)
        originals = [self.repository.get_delivery(request.tenant_id, item) for item in request.delivery_ids]
        invalid = [d.id for d in originals if d.status not in {DeliveryStatus.DEAD_LETTERED,
                                                                DeliveryStatus.CANCELLED,
                                                                DeliveryStatus.RETRY_WAIT}]
        if invalid:
            raise ReplayRejectedError("Only failed, cancelled, or waiting deliveries can be replayed",
                                      delivery_ids=invalid)
        if request.dry_run:
            return tuple(originals)
        if request.preserve_order:
            originals.sort(key=lambda d: (d.destination_id, d.ordering_key or "", d.created_at, d.id))
        created_deliveries: list[Delivery] = []
        for original in originals:
            clone = Delivery(id=str(uuid.uuid4()), tenant_id=original.tenant_id,
                             event_id=original.event_id, destination_id=original.destination_id,
                             status=DeliveryStatus.PENDING, next_attempt_at=request.requested_at,
                             ordering_key=original.ordering_key, replay_of=original.id)
            self.repository.add_delivery(clone)
            created_deliveries.append(clone)
        self.repository.record_replay_key(request.tenant_id, request.idempotency_key,
                                          tuple(item.id for item in created_deliveries))
        return tuple(created_deliveries)

