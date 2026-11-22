"""FastAPI application exposing the starter control plane.

For this exercise, X-Tenant-ID is a development identity shim, not authentication. The
assignment intentionally requires replacing it with verified auth claims and making all
resource access tenant-scoped. Do not expose this starter app to the public internet.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from relayforge.api.schemas import DestinationCreate, IngestRequest, ReplayBody
from relayforge.domain.errors import RelayForgeError
from relayforge.domain.models import Destination, ReplayRequest
from relayforge.domain.policies import validate_destination_url
from relayforge.infra.repository import InMemoryRepository
from relayforge.services.idempotency import IdempotencyService
from relayforge.services.metrics import MetricsService
from relayforge.services.ordering import OrderingService
from relayforge.services.replay import ReplayService
from relayforge.services.scheduler import Scheduler
from relayforge.services.delivery import DeliveryService, StubTransport


class Runtime:
    def __init__(self) -> None:
        self.repository = InMemoryRepository()
        self.ordering = OrderingService(self.repository)
        self.scheduler = Scheduler(self.repository, self.ordering)
        self.transport = StubTransport()
        self.delivery = DeliveryService(self.repository, self.scheduler, self.transport)
        self.ingest = IdempotencyService(self.repository)
        self.replay = ReplayService(self.repository)
        self.metrics = MetricsService(self.repository)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    state = runtime or Runtime()
    app = FastAPI(title="RelayForge", version="0.1.0", description="Webhook delivery control plane")
    app.state.runtime = state

    @app.exception_handler(RelayForgeError)
    async def domain_error_handler(_: Request, exc: RelayForgeError) -> JSONResponse:
        status = 404 if exc.code == "not_found" else 409 if exc.code in {"conflict", "stale_version", "tenant_boundary", "lease_lost", "replay_rejected"} else 422
        return JSONResponse(status_code=status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    def tenant(x_tenant_id: str = Header(..., alias="X-Tenant-ID")) -> str:
        if not x_tenant_id.strip() or len(x_tenant_id) > 128:
            raise HTTPException(401, "Invalid tenant identity")
        return x_tenant_id

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "relayforge"}

    @app.post("/v1/destinations", status_code=201)
    def create_destination(body: DestinationCreate, tenant_id: str = Header(..., alias="X-Tenant-ID")):
        validate_destination_url(body.url, allow_localhost=True)
        item = Destination(id=str(uuid.uuid4()), tenant_id=tenant_id, url=body.url,
                           enabled=body.enabled, secret_ref=body.secret_ref,
                           timeout_seconds=body.timeout_seconds, concurrency_limit=body.concurrency_limit,
                           ordering_key=body.ordering_key, headers=body.headers)
        state.repository.add_destination(item)
        return {"id": item.id, "url": item.url, "enabled": item.enabled}

    @app.get("/v1/destinations")
    def list_destinations(tenant_id: str = Header(..., alias="X-Tenant-ID")):
        return [{"id": d.id, "url": d.url, "enabled": d.enabled,
                 "ordering_key": d.ordering_key, "version": d.version}
                for d in state.repository.list_destinations(tenant_id)]

    @app.post("/v1/events", status_code=202)
    def ingest_event(body: IngestRequest, idempotency_key: str = Header(..., alias="Idempotency-Key"),
                     tenant_id: str = Header(..., alias="X-Tenant-ID")):
        result = state.ingest.ingest(tenant_id, body.event_type, body.payload,
                                     idempotency_key, body.destination_ids, body.metadata)
        return {"event_id": result.event.id, "sequence": result.event.sequence,
                "created": result.created,
                "deliveries": [{"id": d.id, "status": d.status.value} for d in result.deliveries]}

    @app.get("/v1/deliveries")
    def list_deliveries(tenant_id: str = Header(..., alias="X-Tenant-ID")):
        return [{"id": d.id, "event_id": d.event_id, "destination_id": d.destination_id,
                 "status": d.status.value, "attempt_count": d.attempt_count,
                 "next_attempt_at": d.next_attempt_at.isoformat(), "last_error": d.last_error}
                for d in state.repository.list_deliveries(tenant_id)]

    @app.get("/v1/deliveries/{delivery_id}/attempts")
    def attempts(delivery_id: str, tenant_id: str = Header(..., alias="X-Tenant-ID")):
        return [{"id": a.id, "attempt_number": a.attempt_number, "outcome": a.outcome.value,
                 "status_code": a.status_code, "error": a.error, "duration_ms": a.duration_ms}
                for a in state.repository.list_attempts(tenant_id, delivery_id)]

    @app.get("/v1/dead-letters")
    def dead_letters(tenant_id: str = Header(..., alias="X-Tenant-ID")):
        return [{"id": x.id, "delivery_id": x.delivery_id, "reason": x.reason,
                 "failed_at": x.failed_at.isoformat()} for x in state.repository.list_dead_letters(tenant_id)]

    @app.post("/v1/replays", status_code=202)
    def replay(body: ReplayBody, idempotency_key: str = Header(..., alias="Idempotency-Key"),
               x_operator_id: str = Header("local-operator", alias="X-Operator-ID"),
               tenant_id: str = Header(..., alias="X-Tenant-ID")):
        request = ReplayRequest(tenant_id, tuple(body.delivery_ids), x_operator_id, body.reason,
                                idempotency_key, datetime.now(timezone.utc), body.dry_run, body.preserve_order)
        items = state.replay.replay(request)
        return {"dry_run": body.dry_run, "deliveries": [d.id for d in items]}

    @app.get("/v1/metrics")
    def metrics(tenant_id: str = Header(..., alias="X-Tenant-ID")):
        return state.metrics.as_dict(tenant_id)

    return app


app = create_app()
