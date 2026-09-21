import concurrent.futures
import json
import os
import sys
import time

sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            ".."
        )
    )
)

from bridge.inventory_sync import InventoryOdooSync


ODOO_URL = os.environ["ODOO_URL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
ODOO_DB = os.environ["ODOO_DB"]

EVENT_ID = "MAZ-I9-ATOMIC-002"
CONCURRENCY = 20

PAYLOAD = {
    "event_id": EVENT_ID,
    "node_id": "I9-NODE",
    "seq_id": 1009001,
    "product_id": 1,
    "quantity": 1.0,
    "operation": "IN",
}


def make_sync():
    return InventoryOdooSync(
        engine=None,
        endpoint=ODOO_URL,
        api_key=ODOO_API_KEY,
        database=ODOO_DB,
        timeout=30,
    )


def audit():

    sync = make_sync()

    events = sync._call(
        "maz.inventory.event",
        "search_read",
        {
            "domain": [
                ["event_id", "=", EVENT_ID]
            ],
            "fields": [
                "id",
                "event_id",
                "status",
                "picking_id",
                "move_id",
                "quantity",
                "operation",
            ],
            "limit": 100,
        },
    )

    pickings = sync._call(
        "stock.picking",
        "search_read",
        {
            "domain": [
                ["origin", "=", EVENT_ID]
            ],
            "fields": [
                "id",
                "origin",
                "state",
                "move_ids",
            ],
            "limit": 100,
        },
    )

    moves = sync._call(
        "stock.move",
        "search_read",
        {
            "domain": [
                ["origin", "=", EVENT_ID]
            ],
            "fields": [
                "id",
                "origin",
                "state",
                "picking_id",
            ],
            "limit": 100,
        },
    )

    return events, pickings, moves


def worker(index):

    sync = make_sync()

    start = time.perf_counter()

    try:

        result = sync._call(
            "maz.inventory.event",
            "process_event",
            PAYLOAD,
        )

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        return {
            "request": index,
            "success": True,
            "latency_ms": elapsed,
            "result": result,
        }

    except Exception as exc:

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        return {
            "request": index,
            "success": False,
            "latency_ms": elapsed,
            "error": str(exc),
        }


print("=" * 70)
print("mAZ I9 — ATOMIC ODOO CONVERGENCE")
print("=" * 70)

print("Event ID:", EVENT_ID)
print("Concurrency:", CONCURRENCY)

print()
print("[1] PRE-CHECK")

events, pickings, moves = audit()

print("Existing events:", len(events))
print("Existing pickings:", len(pickings))
print("Existing moves:", len(moves))

if events or pickings or moves:
    raise RuntimeError(
        "EVENT_ID já existe. Use outro EVENT_ID."
    )

print()
print("[2] DISPARANDO CONCORRÊNCIA")

start_all = time.perf_counter()

with concurrent.futures.ThreadPoolExecutor(
    max_workers=CONCURRENCY
) as executor:

    futures = [
        executor.submit(
            worker,
            i
        )
        for i in range(
            1,
            CONCURRENCY + 1
        )
    ]

    results = [
        future.result()
        for future in futures
    ]

wall_time = (
    time.perf_counter() - start_all
) * 1000

results.sort(
    key=lambda item: item["request"]
)

print()
print("[3] RESULTADOS")

successes = 0
errors = 0
fresh = 0
recovered = 0

for item in results:

    prefix = (
        f"{item['request']:02d}/{CONCURRENCY}"
    )

    if item["success"]:

        successes += 1

        result = item["result"] or {}

        if result.get("recovered") is True:
            recovered += 1
        else:
            fresh += 1

        print(
            f"{prefix} SUCCESS "
            f"status={result.get('status')} "
            f"recovered={result.get('recovered')} "
            f"concurrent={result.get('concurrent_recovery')} "
            f"picking={result.get('picking_id')} "
            f"move={result.get('move_id')} "
            f"{item['latency_ms']:.3f} ms"
        )

    else:

        errors += 1

        print(
            f"{prefix} ERROR "
            f"{item['latency_ms']:.3f} ms "
            f"{item['error']}"
        )

print()
print("[4] AUDITORIA ODOO")

events, pickings, moves = audit()

event_ids = [
    item["id"]
    for item in events
]

picking_ids = [
    item["id"]
    for item in pickings
]

move_ids = [
    item["id"]
    for item in moves
]

print("Events:", len(events))
print("Pickings:", len(pickings))
print("Moves:", len(moves))

print("Event IDs:", event_ids)
print("Picking IDs:", picking_ids)
print("Move IDs:", move_ids)

if len(events) == 1:
    event_status = events[0]["status"]
else:
    event_status = None

print("Event status:", event_status)

print()
print("=" * 70)
print("RESULTADO FINAL")
print("=" * 70)

print(
    f"Total requests:        {CONCURRENCY}"
)

print(
    f"Sucessos:              {successes}"
)

print(
    f"Erros:                 {errors}"
)

print(
    f"Fresh:                 {fresh}"
)

print(
    f"Recovered:             {recovered}"
)

print(
    f"Events no Odoo:        {len(events)}"
)

print(
    f"Pickings no Odoo:      {len(pickings)}"
)

print(
    f"Moves no Odoo:         {len(moves)}"
)

print(
    f"Wall time:             {wall_time:.3f} ms"
)

duplicate_events = max(
    len(events) - 1,
    0
)

duplicate_pickings = max(
    len(pickings) - 1,
    0
)

duplicate_moves = max(
    len(moves) - 1,
    0
)

print(
    f"Duplicated events:     {duplicate_events}"
)

print(
    f"Duplicated pickings:   {duplicate_pickings}"
)

print(
    f"Duplicated moves:      {duplicate_moves}"
)

if (
    successes == CONCURRENCY
    and len(events) == 1
    and len(pickings) == 1
    and len(moves) == 1
    and event_status == "done"
    and duplicate_events == 0
    and duplicate_pickings == 0
    and duplicate_moves == 0
):

    verdict = (
        "PASS — ATOMIC CONVERGENCE VALIDATED"
    )

else:

    verdict = (
        "FAIL — CONVERGENCE NOT FULLY VALIDATED"
    )

print()
print(verdict)

output = {
    "event_id": EVENT_ID,
    "concurrency": CONCURRENCY,
    "successes": successes,
    "errors": errors,
    "fresh": fresh,
    "recovered": recovered,
    "events": len(events),
    "pickings": len(pickings),
    "moves": len(moves),
    "duplicate_events": duplicate_events,
    "duplicate_pickings": duplicate_pickings,
    "duplicate_moves": duplicate_moves,
    "event_status": event_status,
    "wall_time_ms": wall_time,
    "verdict": verdict,
    "results": results,
}

output_file = (
    "/tmp/maz_i9_atomic_convergence_results.json"
)

with open(output_file, "w") as f:

    json.dump(
        output,
        f,
        indent=2,
        default=str,
    )

print()
print("Evidence:", output_file)
