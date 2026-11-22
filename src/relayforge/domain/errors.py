"""Typed domain errors used to preserve failure meaning across API and worker layers.

The delivery pipeline deliberately distinguishes permanent rejection, transient failure,
lease loss, stale writes, and operator cancellation. Collapsing these into ``Exception``
creates dangerous retry loops: a malformed destination should not be retried forever,
while a database timeout should not permanently fail a valid event.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RelayForgeError(Exception):
    message: str
    code: str = "relayforge_error"
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class ValidationError(RelayForgeError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, "validation_error", details or None)


class NotFoundError(RelayForgeError):
    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} '{identifier}' was not found", "not_found", {"resource": resource, "id": identifier})


class ConflictError(RelayForgeError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, "conflict", details or None)


class PermanentDeliveryError(RelayForgeError):
    """A response or request condition that must not be retried automatically."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, "permanent_delivery_failure", {"status_code": status_code})
        self.status_code = status_code


class TransientDeliveryError(RelayForgeError):
    """A potentially recoverable delivery failure."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message, "transient_delivery_failure", {"retry_after_seconds": retry_after_seconds})
        self.retry_after_seconds = retry_after_seconds


class LeaseLostError(ConflictError):
    def __init__(self, delivery_id: str) -> None:
        super().__init__(f"Worker no longer owns lease for delivery '{delivery_id}'", delivery_id=delivery_id)
        self.code = "lease_lost"


class StaleVersionError(ConflictError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"Stale version: expected {expected}, found {actual}", expected=expected, actual=actual)
        self.code = "stale_version"


class ReplayRejectedError(ConflictError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, **details)
        self.code = "replay_rejected"


class TenantBoundaryError(RelayForgeError):
    def __init__(self) -> None:
        super().__init__("Resource is outside the authenticated tenant boundary", "tenant_boundary")

