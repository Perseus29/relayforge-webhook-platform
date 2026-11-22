"""Policy evaluation for retry, destination safety, quotas, and replay admission."""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from relayforge.domain.errors import ValidationError
from relayforge.domain.models import Delivery, DeliveryStatus, Destination, RetryPolicy


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    terminal_status: DeliveryStatus | None
    reason: str


def validate_destination_url(url: str, *, allow_localhost: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (allow_localhost and parsed.scheme == "http"):
        raise ValidationError("Destination URL must use HTTPS", url=url)
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("Destination URL must have a host and no embedded credentials")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} and not allow_localhost:
        raise ValidationError("Loopback destinations are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        if not allow_localhost:
            raise ValidationError("Private or reserved destination addresses are not allowed", host=host)


def decide_retry(policy: RetryPolicy, attempt_count: int, status_code: int | None, permanent: bool = False) -> RetryDecision:
    if permanent:
        return RetryDecision(False, DeliveryStatus.DEAD_LETTERED, "permanent_failure")
    if attempt_count >= policy.max_attempts:
        return RetryDecision(False, DeliveryStatus.DEAD_LETTERED, "attempt_limit_reached")
    if status_code is None or status_code in policy.retryable_status_codes:
        return RetryDecision(True, DeliveryStatus.RETRY_WAIT, "transient_failure")
    if 200 <= status_code < 300:
        return RetryDecision(False, DeliveryStatus.SUCCEEDED, "success")
    if 400 <= status_code < 500:
        return RetryDecision(False, DeliveryStatus.DEAD_LETTERED, "non_retryable_client_error")
    return RetryDecision(True, DeliveryStatus.RETRY_WAIT, "retryable_server_error")


def can_claim(delivery: Delivery, destination: Destination, now, *, ordering_blocked: bool) -> bool:
    if not destination.enabled or ordering_blocked:
        return False
    if delivery.status not in {DeliveryStatus.PENDING, DeliveryStatus.SCHEDULED, DeliveryStatus.RETRY_WAIT}:
        return False
    if delivery.next_attempt_at > now:
        return False
    if delivery.lease_expires_at and delivery.lease_expires_at > now:
        return False
    return True


def validate_replay_request(ids: tuple[str, ...], reason: str, max_batch: int = 100) -> None:
    if not ids:
        raise ValidationError("At least one delivery must be selected")
    if len(ids) > max_batch:
        raise ValidationError("Replay batch exceeds configured limit", limit=max_batch)
    if len(set(ids)) != len(ids):
        raise ValidationError("Replay request contains duplicate delivery IDs")
    if len(reason.strip()) < 12:
        raise ValidationError("Replay reason must contain at least 12 non-whitespace characters")

