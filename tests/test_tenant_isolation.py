import pytest
from relayforge.domain.errors import TenantBoundaryError
from relayforge.domain.models import Delivery, Destination, Event
from relayforge.infra.repository import InMemoryRepository


def test_attempt_and_delivery_reads_are_tenant_scoped():
    repo = InMemoryRepository()
    repo.add_destination(Destination("dest", "tenant-a", "https://example.test/hook"))
    repo.insert_event_once(Event("event", "tenant-a", "created", {}, "key", "hash", 1))
    repo.add_delivery(Delivery("delivery", "tenant-a", "event", "dest"))
    with pytest.raises(TenantBoundaryError):
        repo.get_delivery("tenant-b", "delivery")
    with pytest.raises(TenantBoundaryError):
        repo.list_attempts("tenant-b", "delivery")
