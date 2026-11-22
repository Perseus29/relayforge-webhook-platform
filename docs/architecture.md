# RelayForge architecture and investigation guide

## Core aggregates

- **Event** is the accepted logical fact. Its identity is tenant-scoped and immutable after acceptance.
- **Delivery** is one attemptable relationship between an event and a destination. Retries belong to the same delivery; replay should create a new generation linked to the original.
- **DeliveryAttempt** is append-only evidence of one outbound execution or explicitly recorded lease expiry.
- **DeadLetter** is an operational projection of a terminally failed delivery. It must not replace attempt history.
- **Replay audit** is an immutable operator decision, distinct from a replayed delivery.

## Invariants to preserve

1. Tenant identity is part of every lookup key and every uniqueness constraint.
2. An idempotency key is reserved only as part of the same transaction that persists the logical event and all fan-out rows.
3. A worker can complete only the lease generation it owns. Lease ownership must be fenced at the persistence boundary, not merely checked before the network request.
4. A destination ordering key is a serialization lane. A non-terminal predecessor blocks later work in that lane.
5. Retry attempts are append-only. A state transition cannot erase prior failures.
6. Replay is a new delivery generation, with an immutable link to the original and an operator audit record.
7. Metrics must define whether they count logical events, delivery generations, or attempts. A single ambiguous `success_count` is not sufficient.

## Suggested durable schema

A relational implementation should use separate tables for tenants, destinations, events, deliveries, attempts, dead_letters, replay_requests, replay_audit, and outbox messages. Recommended uniqueness constraints include `(tenant_id, idempotency_key)` for event ingestion, `(tenant_id, replay_idempotency_key)` for replay submissions, and a stable uniqueness key for delivery fan-out `(event_id, destination_id)` within an event generation. Use foreign keys that include tenant IDs where practical, or enforce tenant consistency in a repository transaction.

Claiming work should use a database primitive equivalent to `SELECT ... FOR UPDATE SKIP LOCKED`, or a queue with visibility timeout plus a fencing generation persisted on the delivery row. Destination-wide concurrency requires a durable semaphore or a count/claim transaction that cannot exceed the configured limit under concurrent workers.

## State lifecycle

```text
pending / scheduled -> in_flight -> succeeded
                              |-> retry_wait -> in_flight
                              |-> dead_lettered
                              |-> cancelled
```

A lease expiry is not itself a successful attempt. The implementation must explicitly decide whether to record it as a timeout attempt and whether it consumes retry budget. Whichever policy is chosen must be idempotent and fenced so that the old worker cannot later append a second result for the same generation.

## Security notes

A production transport must resolve the destination host, validate every resolved address against the egress policy, connect to the validated address, and defend against DNS rebinding between validation and connection. Redirects must be disabled or revalidated hop by hop. The API must derive tenant and operator identity from verified credentials. Secrets should be referenced through a secret manager and never copied into delivery headers persisted in plaintext.

## Starter design compromises

The starter uses a thread-safe in-memory repository to make local behavior inspectable. It cannot demonstrate cross-process transaction isolation. The assignment therefore requires both executable concurrency tests for the adapter and a written explanation of the durable database/queue primitive that would provide the same guarantee in production.
