# RelayForge

RelayForge is a deliberately realistic starter codebase for a webhook delivery control plane. It accepts tenant-scoped events, fans them out to destinations, tracks delivery attempts, schedules retries, preserves per-destination ordering, and exposes operator replay and delivery metrics.

This is a local exercise application, **not production-ready infrastructure**. The repository is in-memory, the tenant header is a development shim rather than authentication, and the transport is a stub. The project intentionally contains correctness gaps that a capable coding agent must discover and resolve across the domain, API, scheduler, worker, and persistence boundaries.

## Stack

- Python 3.11+
- FastAPI + Pydantic v2
- Thread-safe in-memory repository with explicit version checks
- pytest / httpx
- No external services required for the starter test suite

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\activate
pip install -e '.[test]'
uvicorn relayforge.api.app:app --reload
```

Open `http://127.0.0.1:8000/docs`. For local-only testing, create a destination with an HTTPS URL (or localhost HTTP URL), then submit events with `X-Tenant-ID` and `Idempotency-Key` headers.

Run tests:

```bash
pytest -q
```

## Architecture map

| Area | Files | Responsibility |
|---|---|---|
| Domain | `domain/models.py`, `errors.py`, `policies.py`, `canonicalization.py` | Records, status vocabulary, validation, deterministic identity |
| Ingestion | `services/idempotency.py` | Tenant-scoped idempotency and fan-out |
| Scheduling | `services/scheduler.py`, `ordering.py`, `retry.py` | Due-work selection, leases, ordering, backoff |
| Execution | `services/delivery.py`, `worker.py` | HTTP transport boundary, attempts, state transitions |
| Operations | `services/replay.py`, `metrics.py` | Dead-letter replay, audit semantics, aggregate metrics |
| HTTP | `api/app.py`, `api/schemas.py` | Control-plane endpoints and serialization |
| Persistence | `infra/repository.py` | Atomicity boundary and in-memory adapter |

## Important starter limitations

These are intentional investigation points, not a complete bug list:

1. Event creation, sequence allocation, idempotency reservation, and delivery fan-out are not one atomic operation.
2. A replay idempotency reservation can be left in an incomplete state if validation or creation fails part-way through.
3. Scheduler claim is a read-then-write sequence; two independent processes can claim the same delivery.
4. Destination `concurrency_limit` is modeled but not enforced by the scheduler.
5. An expired lease can be reclaimed, but attempt completion and state persistence need a stronger fencing-token contract.
6. Ordering behavior around dead-lettered predecessors and operator replay needs explicit semantics.
7. Replay creates new deliveries but does not fully define dead-letter resolution, audit history, or replay-of-replay policy.
8. URL validation checks the hostname string but does not protect against DNS rebinding or private-address resolution at connection time.
9. The API trusts `X-Tenant-ID` and `X-Operator-ID`; this is explicitly unsuitable for production.
10. Metrics need a consistent snapshot and careful treatment of retries, terminal states, and replay generations.

## Repository exercise

See [`TASK.md`](TASK.md). Do not implement the assignment by changing only one service. The expected solution requires a coordinated design across at least twelve substantial files, tests, API contracts, and operational documentation.
