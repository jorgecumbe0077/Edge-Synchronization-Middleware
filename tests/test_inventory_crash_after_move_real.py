import os
import sys

sys.path.append(
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
)

from bridge.inventory_sync import InventoryOdooSync


EVENT_ID = "MAZ-CRASH-MOVE-REAL-001"

event = {
    "event_id": EVENT_ID,
    "seq_id": 970001,
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
print("mAZ — CRASH AFTER MOVE / REAL RECOVERY TEST")
print("=" * 70)


# =========================================================
# GARANTIR EVENTO NOVO
# =========================================================

existing = sync._find_existing_picking(event)

if existing is not None:
    raise RuntimeError(
        f"EVENTO JÁ EXISTE NO ODOO: {existing}"
    )


# =========================================================
# CREATE PICKING
# =========================================================

print()
print("[1] CREATE PICKING")

picking_id, locations = sync._create_picking(event)

print("Picking:", picking_id)
print("Locations:", locations)


# =========================================================
# CREATE MOVE
# =========================================================

print()
print("[2] CREATE MOVE")

move_id = sync._create_move(
    event,
    picking_id,
    locations
)

print("Move:", move_id)


# =========================================================
# CONFIRM
# =========================================================

print()
print("[3] CONFIRM PICKING")

sync._confirm(picking_id)

print("Confirm: OK")


# =========================================================
# ASSIGN
# =========================================================

print()
print("[4] ASSIGN PICKING")

sync._assign(picking_id)

print("Assign: OK")


# =========================================================
# SIMULATE CRASH
# =========================================================

print()
print("[5] SIMULANDO CRASH ANTES DO VALIDATE")

partial = sync._find_existing_picking(event)

print("Estado parcial:")
print(partial)


# =========================================================
# RETRY
# =========================================================

print()
print("[6] RETRY DO MESMO EVENTO")

result = sync._send(event)

print("RESULTADO:")
print(result)


# =========================================================
# FINAL STATE
# =========================================================

print()
print("[7] ESTADO FINAL")

final = sync._find_existing_picking(event)

print(final)


# =========================================================
# ASSERTS
# =========================================================

assert result["status"] == "DONE"
assert result["recovered"] is True

assert result["picking_id"] == picking_id
assert result["move_id"] == move_id

assert final["id"] == picking_id
assert final["move_ids"] == [move_id]
assert final["state"] == "done"


print()
print("=" * 70)
print("RESULTADO")
print("=" * 70)

print("Mesmo Picking:", "SIM")
print("Mesmo Move:", "SIM")
print("Novo Picking criado no retry:", "NÃO")
print("Novo Move criado no retry:", "NÃO")
print("Recovered:", result["recovered"])
print("Estado final:", final["state"])
print("=" * 70)
