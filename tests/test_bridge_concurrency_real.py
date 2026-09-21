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

from bridge.inventory_sync import (
    InventoryOdooSync,
    InventorySyncError,
)


ODOO_URL = os.environ["ODOO_URL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
ODOO_DB = os.environ["ODOO_DB"]

EVENT_ID = "MAZ-BRIDGE-CONC-001"
CONCURRENCY = 20

EVENT = {
    "event_id": EVENT_ID,
    "seq_id": 990001,
    "node_id": "BRIDGE-CONC-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": 1,
    "operation": "IN",
}


def make_sync():
    return InventoryOdooSync(
        engine=None,
        endpoint=ODOO_URL,
        api_key=ODOO_API_KEY,
        database=ODOO_DB,
        timeout=20,
    )


def audit_remote():
    sync = make_sync()

    pickings = sync._call(
        "stock.picking",
        "search_read",
        {
            "domain": [
                ["origin", "=", EVENT_ID]
            ],
            "fields": [
                "id",
                "name",
                "origin",
                "state",
                "move_ids",
            ],
            "limit": 1000,
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
                "product_uom_qty",
                "quantity",
                "picking_id",
            ],
            "limit": 1000,
        },
    )

    return pickings, moves


def worker(index):
    sync = make_sync()

    start = time.perf_counter()

    try:
        result = sync._send(EVENT)

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        return {
            "request": index,
            "success": True,
            "elapsed_ms": elapsed,
            "result": result,
        }

    except Exception as exc:

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        return {
            "request": index,
            "success": False,
            "elapsed_ms": elapsed,
            "error": str(exc),
        }


print("=" * 70)
print("mAZ BRIDGE — REAL ODOO CONCURRENCY TEST")
print("=" * 70)

print(f"Event ID:      {EVENT_ID}")
print(f"Concorrencia:  {CONCURRENCY}")

# ============================================================
# PRE-CHECK
# ============================================================

print()
print("[1] PRE-CHECK")

existing_pickings, existing_moves = audit_remote()

print(
    "Existing pickings:",
    len(existing_pickings)
)

print(
    "Existing moves:",
    len(existing_moves)
)

if existing_pickings or existing_moves:
    raise RuntimeError(
        "EVENT_ID já existe no Odoo. "
        "Use outro EVENT_ID antes do teste."
    )

# ============================================================
# CONCURRENT EXECUTION
# ============================================================

print()
print("[2] DISPARANDO REQUISIÇÕES SIMULTÂNEAS")

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
    key=lambda x: x["request"]
)

# ============================================================
# RESULTS
# ============================================================

print()
print("[3] RESULTADOS")

successes = 0
errors = 0
fresh = 0
recovered = 0

picking_ids = []
move_ids = []

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

        picking_id = result.get(
            "picking_id"
        )

        move_id = result.get(
            "move_id"
        )

        if picking_id is not None:
            picking_ids.append(
                picking_id
            )

        if move_id is not None:
            move_ids.append(
                move_id
            )

        print(
            f"{prefix} SUCCESS "
            f"status={result.get('status')} "
            f"recovered={result.get('recovered')} "
            f"picking={picking_id} "
            f"move={move_id} "
            f"{item['elapsed_ms']:.3f} ms"
        )

    else:

        errors += 1

        print(
            f"{prefix} ERROR "
            f"{item['elapsed_ms']:.3f} ms "
            f"{item['error']}"
        )

# ============================================================
# REMOTE AUDIT
# ============================================================

print()
print("[4] AUDITORIA ODOO")

final_pickings, final_moves = audit_remote()

unique_picking_ids = sorted(
    {
        item["id"]
        for item in final_pickings
    }
)

unique_move_ids = sorted(
    {
        item["id"]
        for item in final_moves
    }
)

print(
    "Pickings encontrados:",
    len(final_pickings)
)

print(
    "Moves encontrados:",
    len(final_moves)
)

print(
    "Picking IDs:",
    unique_picking_ids
)

print(
    "Move IDs:",
    unique_move_ids
)

# ============================================================
# DETAILED REMOTE OBJECTS
# ============================================================

print()
print("PICKINGS REMOTOS:")

for picking in final_pickings:
    print(
        json.dumps(
            picking,
            default=str
        )
    )

print()
print("MOVES REMOTOS:")

for move in final_moves:
    print(
        json.dumps(
            move,
            default=str
        )
    )

# ============================================================
# DUPLICATION ANALYSIS
# ============================================================

duplicate_pickings = max(
    len(final_pickings) - 1,
    0
)

duplicate_moves = max(
    len(final_moves) - 1,
    0
)

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
    f"Fresh executions:      {fresh}"
)

print(
    f"Recovered:             {recovered}"
)

print(
    f"Pickings no Odoo:      {len(final_pickings)}"
)

print(
    f"Moves no Odoo:         {len(final_moves)}"
)

print(
    f"Duplicated pickings:   {duplicate_pickings}"
)

print(
    f"Duplicated moves:      {duplicate_moves}"
)

print(
    f"Wall time:             {wall_time:.3f} ms"
)

# ============================================================
# VERDICT
# ============================================================

if (
    len(final_pickings) == 1
    and len(final_moves) == 1
    and successes == CONCURRENCY
):
    verdict = (
        "PASS — CONCURRENT IDEMPOTENCY "
        "FULLY VALIDATED"
    )

elif (
    len(final_pickings) == 1
    and len(final_moves) == 1
):
    verdict = (
        "PARTIAL — ZERO DUPLICATION, "
        "BUT NOT ALL REQUESTS SUCCEEDED"
    )

else:
    verdict = (
        "FAIL — REMOTE DUPLICATION DETECTED"
    )

print()
print(verdict)

# ============================================================
# SAVE EVIDENCE
# ============================================================

output = {
    "test": "mAZ Bridge Real Odoo Concurrency",
    "event_id": EVENT_ID,
    "concurrency": CONCURRENCY,
    "successes": successes,
    "errors": errors,
    "fresh": fresh,
    "recovered": recovered,
    "picking_ids": unique_picking_ids,
    "move_ids": unique_move_ids,
    "pickings_count": len(final_pickings),
    "moves_count": len(final_moves),
    "duplicate_pickings": duplicate_pickings,
    "duplicate_moves": duplicate_moves,
    "wall_time_ms": wall_time,
    "verdict": verdict,
    "results": results,
    "pickings": final_pickings,
    "moves": final_moves,
}

output_file = (
    "/tmp/maz_bridge_concurrency_real_results.json"
)

with open(output_file, "w") as f:
    json.dump(
        output,
        f,
        indent=2,
        default=str,
    )

print()
print(
    "Evidence:",
    output_file
)
