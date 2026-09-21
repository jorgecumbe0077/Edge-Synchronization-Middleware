import os
import sys

sys.path.append(
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
)

from bridge.inventory_sync import InventoryOdooSync


EVENT_ID = "MAZ-IDEMPOTENCY-STRESS-REAL-002"

event = {
    "event_id": EVENT_ID,
    "seq_id": 980001,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": 3,
    "operation": "IN",
}


sync = InventoryOdooSync(
    engine=None,
    endpoint=os.environ["ODOO_URL"],
    api_key=os.environ["ODOO_API_KEY"],
    database=os.environ["ODOO_DB"],
)


print("=" * 70)
print("mAZ — IDEMPOTENCY STRESS / REAL ODOO")
print("=" * 70)


# =========================================================
# ESTADO INICIAL DO ODOO
# =========================================================

before = sync._call(
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
    raise RuntimeError(
        "stock.quant não encontrado"
    )

initial_quantity = float(
    before[0]["quantity"]
)

print()
print("[1] STOCK INICIAL")
print("Quantity:", initial_quantity)


# =========================================================
# PRIMEIRO ENVIO
# =========================================================

print()
print("[2] PRIMEIRO ENVIO")

first = sync._send(event)

print(first)


# =========================================================
# RETRIES
# =========================================================

print()
print("[3] RETRIES")

results = []

for attempt in range(2, 6):

    result = sync._send(event)

    results.append(result)

    print()
    print(f"Retry #{attempt}")
    print(result)


# =========================================================
# PICKINGS
# =========================================================

print()
print("[4] AUDITORIA PICKINGS")

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
            "date_done",
        ],
        "limit": 100,
    }
)

print(pickings)


# =========================================================
# MOVES
# =========================================================

print()
print("[5] AUDITORIA MOVES")

moves = sync._call(
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
print("[6] STOCK FINAL")

after = sync._call(
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

final_quantity = float(
    after[0]["quantity"]
)

delta = final_quantity - initial_quantity

print()
print("Initial:", initial_quantity)
print("Final:", final_quantity)
print("Delta:", delta)


# =========================================================
# ASSERTS
# =========================================================

assert first["status"] == "DONE"
assert first["recovered"] is False

for result in results:

    assert result["status"] == "DONE"
    assert result["recovered"] is True

assert len(pickings) == 1
assert len(moves) == 1

assert pickings[0]["state"] == "done"
assert moves[0]["state"] == "done"

assert float(moves[0]["quantity"]) == 3.0

assert delta == 3.0


# =========================================================
# FINAL
# =========================================================

print()
print("=" * 70)
print("RESULTADO FINAL")
print("=" * 70)

print("Envios totais:", 5)
print("Pickings:", len(pickings))
print("Moves:", len(moves))
print("Delta stock:", delta)
print("Duplicação de Picking:", "NÃO")
print("Duplicação de Move:", "NÃO")
print("Duplicação de Stock:", "NÃO")
print("Idempotência real:", "SIM")
print("=" * 70)
