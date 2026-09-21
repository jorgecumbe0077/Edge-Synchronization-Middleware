import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append(
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
)

from bridge.inventory_sync import InventoryOdooSync


EVENT_ID = "MAZ-CONCURRENT-REAL-001"
WORKERS = 10
QUANTITY = 3


event = {
    "event_id": EVENT_ID,
    "seq_id": 990001,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": QUANTITY,
    "operation": "IN",
}


def make_sync():
    return InventoryOdooSync(
        engine=None,
        endpoint=os.environ["ODOO_URL"],
        api_key=os.environ["ODOO_API_KEY"],
        database=os.environ["ODOO_DB"],
    )


print("=" * 70)
print("mAZ — CONCURRENT IDEMPOTENCY / REAL ODOO")
print("=" * 70)

# =========================================================
# STOCK INICIAL
# =========================================================

audit = make_sync()

before = audit._call(
    "stock.quant",
    "search_read",
    {
        "domain": [
            ["product_id", "=", 1],
            ["location_id", "=", 5],
        ],
        "fields": [
            "id",
            "product_id",
            "location_id",
            "quantity",
            "reserved_quantity",
        ],
        "limit": 10,
    }
)

if not before:
    raise RuntimeError("stock.quant não encontrado")

initial_quantity = float(before[0]["quantity"])

print()
print("[1] STOCK INICIAL")
print("Quantity:", initial_quantity)


# =========================================================
# WORKER
# =========================================================

def worker(worker_id):

    sync = make_sync()

    try:
        result = sync._send(event)

        return {
            "worker": worker_id,
            "ok": True,
            "result": result,
        }

    except Exception as exc:

        return {
            "worker": worker_id,
            "ok": False,
            "error": repr(exc),
        }


# =========================================================
# CONCORRÊNCIA
# =========================================================

print()
print("[2] LANÇANDO WORKERS SIMULTÂNEOS")
print("Workers:", WORKERS)
print("Event ID:", EVENT_ID)

results = []

with ThreadPoolExecutor(max_workers=WORKERS) as executor:

    futures = [
        executor.submit(worker, i)
        for i in range(1, WORKERS + 1)
    ]

    for future in as_completed(futures):

        result = future.result()
        results.append(result)

        print()
        print(
            f"Worker #{result['worker']}"
        )

        if result["ok"]:
            print(result["result"])
        else:
            print("ERROR:", result["error"])


# =========================================================
# AUDITORIA PICKINGS
# =========================================================

print()
print("[3] AUDITORIA PICKINGS")

pickings = audit._call(
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
            "date_done",
        ],
        "limit": 100,
    }
)

print(pickings)


# =========================================================
# AUDITORIA MOVES
# =========================================================

print()
print("[4] AUDITORIA MOVES")

moves = audit._call(
    "stock.move",
    "search_read",
    {
        "domain": [
            ["origin", "=", EVENT_ID]
        ],
        "fields": [
            "id",
            "state",
            "product_id",
            "product_uom_qty",
            "quantity",
            "location_id",
            "location_dest_id",
            "picking_id",
        ],
        "limit": 100,
    }
)

print(moves)


# =========================================================
# STOCK FINAL
# =========================================================

print()
print("[5] STOCK FINAL")

after = audit._call(
    "stock.quant",
    "search_read",
    {
        "domain": [
            ["product_id", "=", 1],
            ["location_id", "=", 5],
        ],
        "fields": [
            "id",
            "product_id",
            "location_id",
            "quantity",
            "reserved_quantity",
        ],
        "limit": 10,
    }
)

print(after)

if not after:
    raise RuntimeError("stock.quant desapareceu")

final_quantity = float(after[0]["quantity"])
delta = final_quantity - initial_quantity


# =========================================================
# ANÁLISE
# =========================================================

successful = [
    r for r in results
    if r["ok"]
]

failed = [
    r for r in results
    if not r["ok"]
]

recovered = [
    r for r in successful
    if r["result"].get("recovered") is True
]

first_attempt = [
    r for r in successful
    if r["result"].get("recovered") is False
]


print()
print("=" * 70)
print("AUDITORIA DE CONCORRÊNCIA")
print("=" * 70)

print("Workers totais:", WORKERS)
print("Sucessos:", len(successful))
print("Falhas:", len(failed))
print("First attempt:", len(first_attempt))
print("Recovered:", len(recovered))

print("Pickings:", len(pickings))
print("Moves:", len(moves))

print("Initial stock:", initial_quantity)
print("Final stock:", final_quantity)
print("Delta:", delta)


# =========================================================
# ASSERTS
# =========================================================

assert len(successful) == WORKERS, (
    f"Nem todos os workers tiveram sucesso: "
    f"{len(successful)}/{WORKERS}"
)

assert len(pickings) == 1, (
    f"IDEMPOTENCY FAILURE: "
    f"{len(pickings)} pickings encontrados"
)

assert len(moves) == 1, (
    f"IDEMPOTENCY FAILURE: "
    f"{len(moves)} moves encontrados"
)

assert pickings[0]["state"] == "done"

assert moves[0]["state"] == "done"

assert float(moves[0]["quantity"]) == float(QUANTITY)

assert delta == float(QUANTITY), (
    f"STOCK DUPLICATION: delta esperado "
    f"{QUANTITY}, obtido {delta}"
)


# =========================================================
# FINAL
# =========================================================

print()
print("=" * 70)
print("RESULTADO FINAL")
print("=" * 70)

print("Concorrência:", "PASS")
print("Workers:", WORKERS)
print("Pickings:", len(pickings))
print("Moves:", len(moves))
print("Stock delta:", delta)
print("Duplicação de Picking:", "NÃO")
print("Duplicação de Move:", "NÃO")
print("Duplicação de Stock:", "NÃO")
print("Idempotência concorrente:", "SIM")
print("=" * 70)
