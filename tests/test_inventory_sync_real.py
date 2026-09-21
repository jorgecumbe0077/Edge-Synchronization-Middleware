import os
import sys

sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            ".."
        )
    )
)

from bridge.inventory_engine import InventoryEventEngine
from bridge.inventory_sync import InventoryOdooSync


DB = "client/real_odo_sync_test.db"

ODOO_URL = os.environ["ODOO_URL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
ODOO_DB = os.environ["ODOO_DB"]


print("=" * 70)
print("mAZ — REAL BIDIRECTIONAL INVENTORY E2E")
print("=" * 70)


# =========================================================
# CLEAN DATABASE
# =========================================================

if os.path.exists(DB):
    os.remove(DB)


engine = InventoryEventEngine(DB)


# =========================================================
# INITIAL LOCAL STOCK
# =========================================================

engine.set_stock(
    product_id=1,
    location_id="MAPUTO-STORE",
    quantity=10
)

print()
print("[1] STOCK LOCAL INICIAL")

initial_stock = engine.get_stock(
    1,
    "MAPUTO-STORE"
)

print("Stock:", initial_stock)

assert initial_stock == 10


# =========================================================
# IN EVENT
# =========================================================

event_in = {
    "event_id": "MAZ-E2E-IN-001",
    "seq_id": 940001,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": 3,
    "operation": "IN",
}


print()
print("[2] PROCESSANDO IN LOCAL")

result_in = engine.process_event(event_in)

print(result_in)

assert result_in["status"] == "ACCEPTED"
assert result_in["previous_stock"] == 10
assert result_in["new_stock"] == 13


stock_after_in = engine.get_stock(
    1,
    "MAPUTO-STORE"
)

print("Stock após IN:", stock_after_in)

assert stock_after_in == 13


# =========================================================
# OUT EVENT
# =========================================================

event_out = {
    "event_id": "MAZ-E2E-OUT-001",
    "seq_id": 940002,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": -2,
    "operation": "OUT",
}


print()
print("[3] PROCESSANDO OUT LOCAL")

result_out = engine.process_event(event_out)

print(result_out)

assert result_out["status"] == "ACCEPTED"
assert result_out["previous_stock"] == 13
assert result_out["new_stock"] == 11


stock_after_out = engine.get_stock(
    1,
    "MAPUTO-STORE"
)

print("Stock após OUT:", stock_after_out)

assert stock_after_out == 11


# =========================================================
# OUTBOX
# =========================================================

print()
print("[4] OUTBOX")

outbox_status = engine.get_outbox_status()

print(outbox_status)

assert engine.outbox_count("PENDING") == 2


# =========================================================
# REAL ODOO SYNC
# =========================================================

print()
print("[5] SINCRONIZANDO COM ODOO REAL")

sync = InventoryOdooSync(
    engine=engine,
    endpoint=ODOO_URL,
    api_key=ODOO_API_KEY,
    database=ODOO_DB,
)


results = sync.sync_once()

print()
print("SYNC RESULTS:")

for item in results:
    print(item)


# =========================================================
# SYNC ASSERTIONS
# =========================================================

assert len(results) == 2

for item in results:
    assert item["status"] == "SYNCED"

assert engine.outbox_count("PENDING") == 0
assert engine.outbox_count("SYNCED") == 2


# =========================================================
# VERIFY OPERATIONS
# =========================================================

print()
print("[6] VERIFICAÇÃO DAS OPERAÇÕES")

operations = {}

for item in results:

    response = item["response"]

    print()
    print("Event:", item["event_id"])
    print("Status:", response["status"])
    print("Operation:", response["operation"])
    print("Quantity:", response["quantity"])
    print("Picking:", response["picking_id"])
    print("Move:", response["move_id"])

    assert response["status"] == "DONE"

    operations[
        response["operation"]
    ] = response


assert "IN" in operations
assert "OUT" in operations


# =========================================================
# VERIFY IN
# =========================================================

in_response = operations["IN"]

assert in_response["quantity"] == 3.0

assert (
    in_response["verification"]["picking"]["state"]
    == "done"
)

assert (
    in_response["verification"]["move"]["state"]
    == "done"
)

assert (
    in_response["verification"]["move"]["location_id"][0]
    == 1
)

assert (
    in_response["verification"]["move"]["location_dest_id"][0]
    == 5
)


# =========================================================
# VERIFY OUT
# =========================================================

out_response = operations["OUT"]

assert out_response["quantity"] == 2.0

assert (
    out_response["verification"]["picking"]["state"]
    == "done"
)

assert (
    out_response["verification"]["move"]["state"]
    == "done"
)

assert (
    out_response["verification"]["move"]["location_id"][0]
    == 5
)

assert (
    out_response["verification"]["move"]["location_dest_id"][0]
    == 2
)


# =========================================================
# FINAL AUDIT
# =========================================================

print()
print("=" * 70)
print("AUDITORIA FINAL")
print("=" * 70)

print(
    "Stock local inicial:",
    initial_stock
)

print(
    "Stock local após IN:",
    stock_after_in
)

print(
    "Stock local após OUT:",
    stock_after_out
)

print(
    "Outbox PENDING:",
    engine.outbox_count("PENDING")
)

print(
    "Outbox SYNCED:",
    engine.outbox_count("SYNCED")
)

print()
print("IN Odoo:")
print(
    "  Picking:",
    in_response["picking_id"]
)
print(
    "  Move:",
    in_response["move_id"]
)
print("  State: done")
print("  Vendors -> WH/Stock")

print()
print("OUT Odoo:")
print(
    "  Picking:",
    out_response["picking_id"]
)
print(
    "  Move:",
    out_response["move_id"]
)
print("  State: done")
print("  WH/Stock -> Customers")


engine.close()


print()
print("=" * 70)
print("RESULTADO FINAL")
print("=" * 70)

print("IN local:                    SIM")
print("OUT local:                   SIM")
print("IN colocado no Outbox:      SIM")
print("OUT colocado no Outbox:     SIM")
print("IN Odoo JSON-2 real:         SIM")
print("OUT Odoo JSON-2 real:        SIM")
print("IN Picking:                  DONE")
print("IN Move:                     DONE")
print("OUT Picking:                 DONE")
print("OUT Move:                    DONE")
print("Outbox IN:                   SYNCED")
print("Outbox OUT:                  SYNCED")
print("PENDING final:               0")
print("=" * 70)
