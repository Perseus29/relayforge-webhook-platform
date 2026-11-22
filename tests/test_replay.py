import pytest
from relayforge.domain.errors import ReplayRejectedError
from relayforge.domain.models import Delivery, DeliveryStatus, Destination, Event, ReplayRequest
from relayforge.infra.repository import InMemoryRepository
from relayforge.services.replay import ReplayService


def setup_repo(status=DeliveryStatus.DEAD_LETTERED):
    repo = InMemoryRepository()
    repo.add_destination(Destination("dest", "tenant", "https://example.test/hook"))
    repo.insert_event_once(Event("event", "tenant", "created", {"x": 1}, "key", "hash", 1))
    repo.add_delivery(Delivery("delivery", "tenant", "event", "dest", status=status))
    return repo


def test_replay_creates_new_generation_linked_to_original():
    repo = setup_repo()
    request = ReplayRequest("tenant", ("delivery",), "operator", "Investigate upstream outage", "replay-1")
    replayed = ReplayService(repo).replay(request)
    assert len(replayed) == 1
    assert replayed[0].replay_of == "delivery"
    assert replayed[0].status == DeliveryStatus.PENDING


def test_replay_rejects_pending_delivery():
    repo = setup_repo(DeliveryStatus.PENDING)
    request = ReplayRequest("tenant", ("delivery",), "operator", "Investigate upstream outage", "replay-2")
    with pytest.raises(ReplayRejectedError):
        ReplayService(repo).replay(request)
