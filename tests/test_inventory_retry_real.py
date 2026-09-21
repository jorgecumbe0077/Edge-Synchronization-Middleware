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


DB = "client/retry_real_test.db"

if os.path.exists(DB):
    os.remove(DB)


ODOO_URL = os.environ["ODOO_URL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
ODOO_DB = os.environ["ODOO_DB"]


print("=" * 70)
print("mAZ — REAL ODOO RETRY / NETWORK FAILURE TEST")
print("=" * 70)


# =========================================================
# ENGINE
# =========================================================

engine = InventoryEventEngine(DB)

engine.set_stock(
    product_id=1,
    location_id="MAPUTO-STORE",
    quantity=10
)


# =========================================================
# EVENT
# =========================================================

event = {
    "event_id": "MAZ-RETRY-REAL-001",
    "seq_id": 940001,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": 3,
    "operation": "IN",
}


print()
print("[1] PROCESSANDO EVENTO LOCAL")

result = engine.process_event(event)

print(result)

assert result["status"] == "ACCEPTED"
assert result["new_stock"] == 13

assert engine.outbox_count("PENDING") == 1


# =========================================================
# SIMULATE NETWORK FAILURE
# =========================================================

print()
print("[2] SIMULANDO FALHA DE REDE")

broken_sync = InventoryOdooSync(
    engine=engine,
    endpoint="http://127.0.0.1:1",
    api_key=ODOO_API_KEY,
    database=ODOO_DB,
    timeout=2,
)

result = broken_sync.sync_once()

print("RESULTADO:")
print(result)

assert len(result) == 1
assert result[0]["status"] == "PENDING"

assert engine.outbox_count("PENDING") == 1
assert engine.outbox_count("SYNCED") == 0


# =========================================================
# VERIFY OUTBOX STATE
# =========================================================

print()
print("[3] AUDITORIA APÓS FALHA")

item = engine.get_outbox_event(
    "MAZ-RETRY-REAL-001"
)

print(item)

assert item["status"] == "PENDING"
assert item["attempts"] == 1
assert item["last_error"] is not None


# =========================================================
# RECOVERY
# =========================================================

print()
print("[4] RECUPERANDO CONEXÃO ODOO")

sync = InventoryOdooSync(
    engine=engine,
    endpoint=ODOO_URL,
    api_key=ODOO_API_KEY,
    database=ODOO_DB,
    timeout=10,
)

result = sync.sync_once()

print("RESULTADO:")
print(result)


# =========================================================
# VERIFY RECOVERY
# =========================================================

assert len(result) == 1
assert result[0]["status"] == "SYNCED"

assert engine.outbox_count("PENDING") == 0
assert engine.outbox_count("SYNCED") == 1


# =========================================================
# FINAL AUDIT
# =========================================================

print()
print("=" * 70)
print("AUDITORIA FINAL")
print("=" * 70)

print(
    "Stock local:",
    engine.get_stock(
        1,
        "MAPUTO-STORE"
    )
)

print(
    "PENDING:",
    engine.outbox_count("PENDING")
)

print(
    "SYNCED:",
    engine.outbox_count("SYNCED")
)

item = engine.get_outbox_event(
    "MAZ-RETRY-REAL-001"
)

print("Attempts:", item["attempts"])
print("Last error:", item["last_error"])
print("Synced at:", item["synced_at"])


assert engine.get_stock(
    1,
    "MAPUTO-STORE"
) == 13

assert engine.outbox_count("PENDING") == 0
assert engine.outbox_count("SYNCED") == 1


engine.close()


print()
print("=" * 70)
print("RESULTADO")
print("=" * 70)

print("Evento local:              SIM")
print("Outbox PENDING inicial:    SIM")
print("Falha de rede simulada:    SIM")
print("Evento permaneceu PENDING: SIM")
print("Attempts incrementado:     SIM")
print("Odoo recuperado:           SIM")
print("Retry executado:           SIM")
print("Evento SYNCED:             SIM")
print("PENDING final = 0:         SIM")
print("=" * 70)
