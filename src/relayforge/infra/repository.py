"""Thread-safe in-memory repository used by the starter application and tests.

The interface is deliberately explicit about tenant IDs and expected versions. Production
adapters should implement the same atomic semantics with transactions, unique constraints,
conditional updates, and row-level locking. The in-memory adapter is not a distributed lock.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from relayforge.domain.errors import ConflictError, NotFoundError, StaleVersionError, TenantBoundaryError
from relayforge.domain.models import Delivery, DeliveryAttempt, DeadLetter, Destination, Event, ReplayRequest


@dataclass
class RepositoryState:
    destinations: dict[str, Destination] = field(default_factory=dict)
    events: dict[str, Event] = field(default_factory=dict)
    deliveries: dict[str, Delivery] = field(default_factory=dict)
    attempts: dict[str, list[DeliveryAttempt]] = field(default_factory=lambda: defaultdict(list))
    dead_letters: dict[str, DeadLetter] = field(default_factory=dict)
    event_keys: dict[tuple[str, str], str] = field(default_factory=dict)
    replay_keys: dict[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)
    sequence_by_tenant: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class InMemoryRepository:
    def __init__(self) -> None:
        self._state = RepositoryState()
        self._lock = threading.RLock()

    def add_destination(self, destination: Destination) -> None:
        with self._lock:
            if destination.id in self._state.destinations:
                raise ConflictError("Destination already exists", destination_id=destination.id)
            self._state.destinations[destination.id] = destination

    def get_destination(self, tenant_id: str, destination_id: str) -> Destination:
        with self._lock:
            item = self._state.destinations.get(destination_id)
            if item is None:
                raise NotFoundError("destination", destination_id)
            if item.tenant_id != tenant_id:
                raise TenantBoundaryError()
            return item

    def list_destinations(self, tenant_id: str) -> list[Destination]:
        with self._lock:
            return sorted((d for d in self._state.destinations.values() if d.tenant_id == tenant_id), key=lambda d: d.id)

    def next_sequence(self, tenant_id: str) -> int:
        with self._lock:
            self._state.sequence_by_tenant[tenant_id] += 1
            return self._state.sequence_by_tenant[tenant_id]

    def insert_event_once(self, event: Event) -> tuple[Event, bool]:
        with self._lock:
            key = (event.tenant_id, event.idempotency_key)
            existing_id = self._state.event_keys.get(key)
            if existing_id:
                return self._state.events[existing_id], False
            self._state.events[event.id] = event
            self._state.event_keys[key] = event.id
            return event, True

    def add_delivery(self, delivery: Delivery) -> None:
        with self._lock:
            if delivery.event_id not in self._state.events:
                raise NotFoundError("event", delivery.event_id)
            if delivery.destination_id not in self._state.destinations:
                raise NotFoundError("destination", delivery.destination_id)
            self._state.deliveries[delivery.id] = delivery

    def get_delivery(self, tenant_id: str, delivery_id: str) -> Delivery:
        with self._lock:
            item = self._state.deliveries.get(delivery_id)
            if item is None:
                raise NotFoundError("delivery", delivery_id)
            if item.tenant_id != tenant_id:
                raise TenantBoundaryError()
            return item

    def list_deliveries(self, tenant_id: str, *, event_id: str | None = None) -> list[Delivery]:
        with self._lock:
            items = [d for d in self._state.deliveries.values() if d.tenant_id == tenant_id]
            if event_id is not None:
                items = [d for d in items if d.event_id == event_id]
            return sorted(items, key=lambda d: (d.created_at, d.id))

    def save_delivery(self, delivery: Delivery, expected_version: int) -> Delivery:
        with self._lock:
            current = self._state.deliveries.get(delivery.id)
            if current is None:
                raise NotFoundError("delivery", delivery.id)
            if current.tenant_id != delivery.tenant_id:
                raise TenantBoundaryError()
            if current.version != expected_version:
                raise StaleVersionError(expected_version, current.version)
            self._state.deliveries[delivery.id] = delivery
            return delivery

    def append_attempt(self, attempt: DeliveryAttempt) -> None:
        with self._lock:
            delivery = self._state.deliveries.get(attempt.delivery_id)
            if delivery is None or delivery.tenant_id != attempt.tenant_id:
                raise TenantBoundaryError()
            self._state.attempts[attempt.delivery_id].append(attempt)

    def list_attempts(self, tenant_id: str, delivery_id: str) -> list[DeliveryAttempt]:
        self.get_delivery(tenant_id, delivery_id)
        with self._lock:
            return list(self._state.attempts.get(delivery_id, ()))

    def upsert_dead_letter(self, letter: DeadLetter) -> None:
        with self._lock:
            delivery = self._state.deliveries.get(letter.delivery_id)
            if delivery is None or delivery.tenant_id != letter.tenant_id:
                raise TenantBoundaryError()
            self._state.dead_letters[letter.delivery_id] = letter

    def list_dead_letters(self, tenant_id: str) -> list[DeadLetter]:
        with self._lock:
            return sorted((x for x in self._state.dead_letters.values() if x.tenant_id == tenant_id), key=lambda x: x.failed_at)

    def record_replay_key(self, tenant_id: str, key: str, result_ids: tuple[str, ...]) -> tuple[tuple[str, ...], bool]:
        with self._lock:
            index = (tenant_id, key)
            if index in self._state.replay_keys:
                return self._state.replay_keys[index], False
            self._state.replay_keys[index] = result_ids
            return result_ids, True

    def snapshot(self) -> RepositoryState:
        """Return a defensive snapshot for diagnostics; never mutate it as a write path."""
        with self._lock:
            import copy
            return copy.deepcopy(self._state)

