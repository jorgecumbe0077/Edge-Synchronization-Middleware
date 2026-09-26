# mAZ Bridge

## Edge Synchronization Middleware

**mAZ Bridge** is an experimental edge synchronization middleware for maintaining operational continuity across unreliable network boundaries between remote operations and an ERP backend.

It is designed around a simple principle:

> **A locally accepted operation should remain durable and recoverable even when connectivity to the ERP is unavailable or interrupted.**

mAZ Bridge complements ERP offline capabilities by focusing on **operational and event continuity** between an edge environment and the ERP backend.

---

## Architecture

```text
Remote Warehouse
       │
       ▼
    mAZ Edge
 ┌───────────────────────┐
 │ Local State           │
 │ Event Ledger          │
 │ Durable Outbox        │
 │ Retry                 │
 │ Crash Recovery        │
 │ Idempotency           │
 │ Reconciliation        │
 └───────────┬───────────┘
             │
      unreliable network
             │
             ▼
          Odoo 19
             │
             ▼
        PostgreSQL
```
## The Problem

Remote operations may continue while connectivity to the ERP is unavailable, degraded, or interrupted.

In this environment, the main engineering problem is not simply whether an application can remain open offline. The harder problem is preserving the operational event itself, making its state durable, retryable, recoverable, and eventually verifiable against the ERP backend.

mAZ Bridge explores this problem at the edge/ERP boundary.

The design therefore separates local operational acceptance from ERP delivery:

- accept the operation locally
- persist the event durably
- place it in an outbox for delivery
- retry delivery after failures
- recover state after process or machine failure
- tolerate duplicate replay through idempotency
- reconcile the resulting state with the ERP

This repository is an experimental engineering artifact built around those failure modes.

---
## Core Components

### Local Event Engine

`bridge/inventory_engine.py` implements the local event and persistence layer.

Its responsibilities include local state, event recording, durable outbox state, idempotent processing, and recovery-oriented inspection.

### ERP Synchronization

`bridge/inventory_sync.py` implements the synchronization boundary between the local edge state and the ERP backend.

It handles delivery attempts, retry-oriented synchronization, and outbox state transitions.

### Reconciliation

`bridge/reconciliation.py` provides read-oriented reconciliation against Odoo.

The reconciliation layer is intentionally separated from mutation logic so that ERP state can be inspected and compared without turning reconciliation itself into another write path.

---
## Evidence-Driven Development

The project follows a simple investigation loop:

```text
test → real output → interpretation → next test
```

The repository distinguishes between:

- simulated failure scenarios
- local deterministic tests
- real ERP integration tests
- crash and recovery experiments
- read-only reconciliation checks

Results are interpreted from observed outputs rather than from assumptions about distributed-system behavior.

A correlation between an event and a timing or scheduling pattern is not treated as proof of causality.

The public repository contains the cleaned engineering representation of the work. Historical laboratory artifacts and raw experimental data are intentionally not included here.

---
## Investigated Failure Modes

The laboratory investigation exercised the edge/ERP boundary under several failure conditions:

- offline operation while ERP connectivity is unavailable
- network failure followed by retry
- process crash followed by recovery
- lost acknowledgement and duplicate replay
- partial ERP completion and subsequent recovery
- concurrent processing and idempotency
- reconciliation between edge state and ERP state

These scenarios are represented by the test programs under `tests/`.

The tests are retained as engineering evidence and are not presented as a formal verification of the complete system.

---
## MVP — Remote Warehouse Stock Dispatch

The main demonstration scenario models a remote warehouse dispatch performed while ERP connectivity is unavailable or unreliable.

The operation is accepted at the edge, persisted locally, and placed in the durable outbox. Synchronization can then resume when connectivity is restored.

The demonstrated event is:

- Event ID: `MAZ-MVP01-REMOTE-WH-OUT-001`
- Node: `REMOTE-WAREHOUSE-01`
- Product: `1`
- Quantity: `-3`
- Local stock before: `10`
- Local stock after: `7`

After successful synchronization, the outbox reaches `SYNCED` and the corresponding Odoo inventory operation reaches its completed state.

The laboratory evidence for this scenario includes Odoo picking `WH/OUT/00015`, the associated stock move, and read-only reconciliation checks against the resulting inventory state.

The mAZ audit event itself was not established in the Odoo `maz_inventory_event` model for this particular event. Therefore, this repository does not claim complete ERP-side event-audit convergence for the MVP.

---
## Demo Lab

The repository includes a small web demonstration under `demo/web/`.

The demo is intended to make the operational model visible without requiring the reader to inspect the entire implementation first.

The laboratory scenarios used during development included:

- remote warehouse dispatch with local persistence and later synchronization
- crash recovery after an interrupted operation
- concurrent outbox processing
- stale-generation fencing during recovery

The demo is illustrative. The authoritative evidence for the engineering claims is the underlying test and integration work, not the web interface itself.

---
## Repository Structure

```text
bridge/
├── inventory_engine.py       Local event ledger and persistence
├── inventory_sync.py         ERP synchronization and outbox delivery
└── reconciliation.py        Read-oriented ERP reconciliation

tests/
├── MVP and remote-warehouse scenarios
├── crash and recovery experiments
├── retry and acknowledgement-loss experiments
├── concurrency and idempotency experiments
└── reconciliation and integration checks

demo/
├── README.md                 Demo documentation
└── web/                      Small browser-based demonstration

docker-compose.yml           Local Odoo/PostgreSQL lab definition
```

---
## Running the Code

The core Python components can be inspected and exercised independently of the ERP environment.

### Python environment

```bash
python3 -m compileall -q bridge tests
```

### Local components

The local event engine is implemented in `bridge/inventory_engine.py` and uses a durable local database for event and outbox state.

### Test suite

The programs under `tests/` document the failure scenarios investigated during development. Some tests are self-contained; integration tests require the corresponding Odoo environment.

Run an individual test explicitly when its environment is available:

```bash
python3 tests/<test_file>.py
```

The repository intentionally does not hide environment requirements behind a single command that could give a misleading impression of full integration coverage.

---
## Odoo Integration

The laboratory integration targets Odoo 19 with PostgreSQL through the Odoo JSON-2 API.

The project investigation identified two distinct integration paths:

### Direct synchronization path

The initial synchronization path delivers inventory operations through standard Odoo inventory methods exposed through JSON-2:

- `stock.picking.create`
- `stock.move.create`
- `action_confirm`
- `action_assign`
- `button_validate`
- `search_read` for verification

This path was useful for establishing the initial integration and for exposing a concurrency failure mode: when multiple requests for the same logical event independently followed a search/create sequence, concurrent delivery could produce duplicate physical inventory operations.

### Atomic event-processing path

For the concurrency-sensitive workflow, the laboratory also uses a custom Odoo model method:

`maz.inventory.event.process_event`

This method is invoked through the native Odoo JSON-2 endpoint:

`POST /json/2/maz.inventory.event/process_event`

The method centralizes event ownership and the resulting inventory operation on the Odoo side. The implementation uses the event identifier as the logical identity of the operation and relies on PostgreSQL-backed concurrency control and uniqueness constraints.

The I9 experiment exercised this atomic path directly with 20 simultaneous requests for the same `event_id`. In the tested scenario:

20 concurrent requests
→ 20 successful responses
→ 1 fresh execution + 19 recovered executions
→ 1 event
→ 1 picking
→ 1 move
→ 0 duplicate physical operations

The final Odoo state was independently inspected through the JSON-2 API and PostgreSQL.

This result is experimental evidence of convergence for the tested workflow. It is not a universal exactly-once guarantee and does not constitute a production-readiness claim.

### Architectural boundary

The resulting experimental architecture is:

Remote / Edge Operation
        |
        v
   mAZ Bridge
   |-- local event persistence
   |-- durable outbox
   |-- retry / replay
   `-- reconciliation
        |
        v
     Odoo JSON-2
        |
        |-- Direct synchronization path
        |
        `-- Atomic event-processing path
                 |
                 v
       maz.inventory.event
          process_event()
                 |
          +------+------+
          |             |
          v             v
   stock.picking   stock.move
          |             |
          +------+------+
                 |
                 v
             PostgreSQL

The distinction between these paths is intentional. The direct path represents the initial integration approach; the atomic path represents the subsequent design response to the concurrency race identified during the laboratory investigation.

The included `docker-compose.yml` describes the local Odoo/PostgreSQL laboratory environment used by the project.

The integration remains experimental and may require environment-specific configuration or adaptation.

---

## What This Repository Demonstrates

Based on the implemented components and the laboratory investigation, this repository demonstrates an engineering approach to edge/ERP operational continuity built around:

- durable local event persistence
- outbox-based delivery
- retry after delivery failure
- recovery after process failure
- idempotent handling of duplicate replay
- concurrent processing experiments
- lost-acknowledgement experiments
- read-oriented reconciliation with ERP state
- integration with Odoo 19 and PostgreSQL in the laboratory environment

The evidence supports the existence and behavior of these mechanisms in the investigated scenarios. It should not be interpreted as proof that every possible distributed-system failure has been solved.

---
## Limitations

This repository does not claim:

- formal distributed-systems correctness
- elimination of all failure modes
- exactly-once delivery over an unreliable network
- production readiness for all deployment environments
- complete ERP-side event-audit convergence in every scenario
- arbitrary compatibility with every Odoo deployment without adaptation
- generalizable performance or benchmark results

In particular, idempotent recovery should not be confused with exactly-once network delivery.

The experiments were conducted in a specific laboratory environment and are evidence for the investigated scenarios, not a universal guarantee.

---
## Status

**Experimental / Research Prototype**

mAZ Bridge is an engineering and research prototype. The repository is published to make the architecture, implementation, experiments, and documented limitations inspectable and reproducible.

The public repository is a sanitized representation of the laboratory work. Historical laboratory artifacts are intentionally kept outside this public tree.

---

## License

This project is licensed under the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for the full license text.
