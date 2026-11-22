from datetime import datetime, timezone
from relayforge.domain.models import Delivery, DeliveryStatus, Destination, Event
from relayforge.infra.repository import InMemoryRepository
from relayforge.services.ordering import OrderingService


def setup_repo():
    repo = InMemoryRepository()
    repo.add_destination(Destination("dest", "tenant", "https://example.test/hook", ordering_key="account"))
    event1 = Event("e1", "tenant", "created", {}, "k1", "h1", 1, datetime(2026, 1, 1, tzinfo=timezone.utc))
    event2 = Event("e2", "tenant", "created", {}, "k2", "h2", 2, datetime(2026, 1, 2, tzinfo=timezone.utc))
    repo.insert_event_once(event1)
    repo.insert_event_once(event2)
    first = Delivery("d1", "tenant", "e1", "dest", status=DeliveryStatus.RETRY_WAIT, ordering_key="account", created_at=event1.created_at)
    second = Delivery("d2", "tenant", "e2", "dest", ordering_key="account", created_at=event2.created_at)
    repo.add_delivery(first)
    repo.add_delivery(second)
    return repo, first, second


def test_retry_wait_predecessor_blocks_later_delivery():
    repo, _, later = setup_repo()
    assert OrderingService(repo).is_blocked(later)


def test_successful_predecessor_unblocks_later_delivery():
    repo, first, later = setup_repo()
    repo.save_delivery(first.evolve(status=DeliveryStatus.SUCCEEDED), expected_version=first.version)
    assert not OrderingService(repo).is_blocked(later)
