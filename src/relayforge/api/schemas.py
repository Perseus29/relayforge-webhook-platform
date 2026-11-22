"""HTTP request and response schemas. Tenant identity is injected by middleware/header."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IngestRequest(StrictModel):
    event_type: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any]
    destination_ids: list[str] = Field(min_length=1, max_length=50)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("destination_ids")
    @classmethod
    def unique_destinations(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("destination_ids must be unique")
        return value


class DestinationCreate(StrictModel):
    url: str
    secret_ref: str | None = None
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=120)
    concurrency_limit: int = Field(default=4, ge=1, le=100)
    ordering_key: str | None = None
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)


class ReplayBody(StrictModel):
    delivery_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=12, max_length=1000)
    dry_run: bool = False
    preserve_order: bool = True


class DeliveryView(BaseModel):
    id: str
    event_id: str
    destination_id: str
    status: str
    attempt_count: int
    next_attempt_at: datetime
    last_status_code: int | None
    last_error: str | None
    replay_of: str | None
