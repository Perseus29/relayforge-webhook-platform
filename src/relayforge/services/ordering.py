"""Per-destination ordering checks.

Ordering is scoped to a destination and ordering key, not globally to a tenant. A blocked
head delivery must prevent later siblings from overtaking it, including while it is in a
retry window or held in the dead-letter queue. Operators may explicitly choose to skip a
failed predecessor during a future replay workflow; ordinary polling must never do so.
"""
from __future__ import annotations

from relayforge.domain.models import Delivery, DeliveryStatus
from relayforge.infra.repository import InMemoryRepository

_TERMINAL = {DeliveryStatus.SUCCEEDED, DeliveryStatus.CANCELLED}


class OrderingService:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    def is_blocked(self, candidate: Delivery) -> bool:
        if candidate.ordering_key is None:
            return False
        siblings = self.repository.list_deliveries(candidate.tenant_id)
        for other in siblings:
            if other.id == candidate.id or other.destination_id != candidate.destination_id:
                continue
            if other.ordering_key != candidate.ordering_key:
                continue
            if (other.created_at, other.id) < (candidate.created_at, candidate.id) and other.status not in _TERMINAL:
                return True
        return False

    def predecessor(self, candidate: Delivery) -> Delivery | None:
        if candidate.ordering_key is None:
            return None
        siblings = self.repository.list_deliveries(candidate.tenant_id)
        earlier = [d for d in siblings if d.id != candidate.id
                   and d.destination_id == candidate.destination_id
                   and d.ordering_key == candidate.ordering_key
                   and (d.created_at, d.id) < (candidate.created_at, candidate.id)]
        return max(earlier, key=lambda d: (d.created_at, d.id), default=None)

