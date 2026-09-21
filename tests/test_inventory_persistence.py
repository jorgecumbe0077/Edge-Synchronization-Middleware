import os

from bridge.inventory_engine import InventoryEventEngine


DB = "client/test_inventory_persistence.db"


if os.path.exists(DB):
    os.remove(DB)


print("=" * 70)
print("mAZ — INVENTORY TEST I1: PERSISTÊNCIA SQLITE")
print("=" * 70)


# =========================================================
# ENGINE 1
# =========================================================

engine = InventoryEventEngine(DB)

engine.set_stock(
    product_id=1,
    location_id="MAPUTO-STORE",
    quantity=100
)

event = {
    "event_id": "INV-I1-001",
    "seq_id": 1,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": -7,
    "operation": "SALE"
}

result = engine.process_event(event)

print()
print("[1] EVENTO APLICADO")
print(result)

assert result["status"] == "ACCEPTED"
assert result["new_stock"] == 93

print()
print("[2] STOCK ANTES DO RESTART")
print(
    engine.get_stock(
        1,
        "MAPUTO-STORE"
    )
)

assert engine.get_stock(
    1,
    "MAPUTO-STORE"
) == 93

engine.close()


# =========================================================
# RESTART
# =========================================================

print()
print("[3] SIMULANDO RESTART DO PROCESSO")

engine = InventoryEventEngine(DB)

stock_after_restart = engine.get_stock(
    1,
    "MAPUTO-STORE"
)

print(
    f"    Stock recuperado: {stock_after_restart}"
)

assert stock_after_restart == 93


# =========================================================
# DUPLICATE
# =========================================================

print()
print("[4] REENVIANDO MESMO EVENTO")

duplicate = engine.process_event(event)

print(duplicate)

assert duplicate["status"] == "DUPLICATE"

assert engine.get_stock(
    1,
    "MAPUTO-STORE"
) == 93


# =========================================================
# AUDITORIA
# =========================================================

print()
print("[5] AUDITORIA")

print(
    engine.audit(
        1,
        "MAPUTO-STORE"
    )
)

assert engine.event_count() == 1
assert engine.processed_count() == 1
assert engine.conflict_count() == 0

engine.close()


print()
print("=" * 70)
print("RESULTADO I1")
print("=" * 70)

print("Eventos aceites: 1")
print("Duplicações: 0")
print("Stock final: 93")
print("Restart preservou estado: SIM")
print()
print("✅ I1 PASSOU")
print("=" * 70)
