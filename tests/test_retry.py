from datetime import datetime, timezone
from relayforge.domain.models import RetryPolicy
from relayforge.services.retry import compute_backoff, next_attempt_at, parse_retry_after


def test_backoff_jitter_lower_and_upper_bounds():
    policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=60, jitter_ratio=0.2)
    assert compute_backoff(policy, 1, random_source=lambda: 0.0) == 8
    assert compute_backoff(policy, 1, random_source=lambda: 1.0) == 12


def test_retry_after_is_a_floor_even_with_jitter():
    policy = RetryPolicy(base_delay_seconds=2, max_delay_seconds=100, jitter_ratio=0.2)
    assert compute_backoff(policy, 1, random_source=lambda: 0.0, retry_after_seconds=30) == 30


def test_next_attempt_preserves_timezone_aware_clock():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert next_attempt_at(RetryPolicy(jitter_ratio=0), 1, now).isoformat() == "2026-01-01T00:00:02+00:00"


def test_invalid_retry_after_is_ignored():
    assert parse_retry_after("not-a-date") is None
