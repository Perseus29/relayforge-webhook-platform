"""Backoff calculations with deterministic test hooks and Retry-After support."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from relayforge.domain.models import RetryPolicy

RandomSource = Callable[[], float]


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value.strip())
        return max(0.0, seconds)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            return max(0.0, (target - current).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def compute_backoff(policy: RetryPolicy, attempt_number: int, *, random_source: RandomSource | None = None,
                    retry_after_seconds: float | None = None) -> float:
    """Return bounded exponential delay; attempt_number is one-based."""
    if attempt_number < 1:
        raise ValueError("attempt_number must be one-based")
    raw = policy.base_delay_seconds * (policy.multiplier ** (attempt_number - 1))
    bounded = min(policy.max_delay_seconds, raw)
    source = random_source or random.random
    sample = min(1.0, max(0.0, source()))
    jitter = bounded * policy.jitter_ratio * ((sample * 2.0) - 1.0)
    computed = max(0.0, bounded + jitter)
    if retry_after_seconds is not None:
        computed = max(computed, max(0.0, retry_after_seconds))
    return min(max(policy.max_delay_seconds, retry_after_seconds or 0.0), computed)


def next_attempt_at(policy: RetryPolicy, attempt_number: int, now: datetime,
                    retry_after_seconds: float | None = None, random_source: RandomSource | None = None) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    delay = compute_backoff(policy, attempt_number, random_source=random_source,
                            retry_after_seconds=retry_after_seconds)
    return now + timedelta(seconds=delay)

