import os
import sys
import json
from pathlib import Path

sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            ".."
        )
    )
)

from bridge.inventory_engine import InventoryEventEngine
from bridge.inventory_sync import (
    InventoryOdooSync,
    InventorySyncError,
)


DB = "client/i8_ack_loss_real.db"

ODOO_URL = os.environ["ODOO_URL"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]
ODOO_DB = os.environ["ODOO_DB"]

EVENT_ID = "MAZ-I8-ACK-LOSS-001"

PRODUCT_ID = 1
LOCATION_ID = "MAPUTO-STORE"


class AckLossOnceSync(InventoryOdooSync):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ack_lost = False

    def _send(self, event):

        result = super()._send(event)

        if not self.ack_lost:
            self.ack_lost = True

            raise InventorySyncError(
                "SIMULATED_ACK_LOSS_AFTER_REMOTE_COMMIT"
            )

        return result


def audit_remote(sync, event_id):

    pickings = sync._call(
        "stock.picking",
        "search_read",
        {
            "domain": [
                ["origin", "=", event_id]
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
                ["origin", "=", event_id]
            ],
            "fields": [
                "id",
                "origin",
                "state",
                "product_id",
                "product_uom_qty",
                "quantity",
                "picking_id",
            ],
            "limit": 100,
        },
    )

    return pickings, moves


print("=" * 70)
print("mAZ — I8 ACK LOSS / IDEMPOTENT REPLAY — REAL ODOO")
print("=" * 70)

# ============================================================
# CLEAN LOCAL DB
# ============================================================

db_path = Path(DB)

if db_path.exists():
    db_path.unlink()

engine = InventoryEventEngine(DB)

# ============================================================
# INITIAL STOCK
# ============================================================

engine.set_stock(
    product_id=PRODUCT_ID,
    location_id=LOCATION_ID,
    quantity=10,
)

print()
print("[1] CRIANDO EVENTO LOCAL")

event = {
    "event_id": EVENT_ID,
    "seq_id": 980001,
    "node_id": "POS-MAPUTO-01",
    "product_id": PRODUCT_ID,
    "location_id": LOCATION_ID,
    "quantity": 3,
    "operation": "IN",
}

local_result = engine.process_event(event)

print(local_result)

assert local_result["status"] == "ACCEPTED"
assert local_result["new_stock"] == 13
assert engine.outbox_count("PENDING") == 1


# ============================================================
# ACK LOSS AFTER REMOTE COMMIT
# ============================================================

print()
print("[2] ENVIANDO AO ODOO COM ACK LOSS SIMULADO")

sync = AckLossOnceSync(
    engine=engine,
    endpoint=ODOO_URL,
    api_key=ODOO_API_KEY,
    database=ODOO_DB,
    timeout=20,
)

first = sync.sync_once(limit=1)

print(first)

assert len(first) == 1
assert first[0]["status"] == "PENDING"
assert sync.ack_lost is True

outbox_after_loss = engine.get_outbox_event(EVENT_ID)

print()
print("OUTBOX APÓS ACK LOSS:")
print(outbox_after_loss)

assert outbox_after_loss["status"] == "PENDING"
assert outbox_after_loss["attempts"] == 1


# ============================================================
# REMOTE AUDIT
# ============================================================

print()
print("[3] AUDITORIA REMOTA APÓS ACK LOSS")

remote_pickings, remote_moves = audit_remote(
    sync,
    EVENT_ID,
)

print("Pickings:", remote_pickings)
print("Moves:", remote_moves)

assert len(remote_pickings) == 1
assert len(remote_moves) == 1
assert remote_pickings[0]["state"] == "done"
assert remote_moves[0]["state"] == "done"


# ============================================================
# RETRY / RECOVERY
# ============================================================

print()
print("[4] RETRY DO MESMO EVENTO")

second = sync.sync_once(limit=1)

print(second)

assert len(second) == 1
assert second[0]["status"] == "SYNCED"

response = second[0]["response"]

print()
print("RECOVERY RESPONSE:")
print(json.dumps(
    response,
    indent=2,
    default=str
))

assert response["status"] == "DONE"
assert response["recovered"] is True


# ============================================================
# FINAL OUTBOX
# ============================================================

final_outbox = engine.get_outbox_event(EVENT_ID)

print()
print("[5] AUDITORIA OUTBOX FINAL")
print(final_outbox)

assert final_outbox["status"] == "SYNCED"
assert final_outbox["synced_at"] is not None


# ============================================================
# FINAL REMOTE AUDIT
# ============================================================

print()
print("[6] AUDITORIA FINAL ODOO")

remote_pickings, remote_moves = audit_remote(
    sync,
    EVENT_ID,
)

print(
    "Pickings:",
    len(remote_pickings)
)

print(
    "Moves:",
    len(remote_moves)
)

assert len(remote_pickings) == 1
assert len(remote_moves) == 1

assert remote_pickings[0]["state"] == "done"
assert remote_moves[0]["state"] == "done"

picking_id = remote_pickings[0]["id"]
move_id = remote_moves[0]["id"]

print("Picking ID:", picking_id)
print("Move ID:", move_id)


# ============================================================
# FINAL LOCAL AUDIT
# ============================================================

final_stock = engine.get_stock(
    PRODUCT_ID,
    LOCATION_ID,
)

pending = engine.outbox_count("PENDING")
synced = engine.outbox_count("SYNCED")

print()
print("[7] AUDITORIA LOCAL")
print("Stock:", final_stock)
print("PENDING:", pending)
print("SYNCED:", synced)

assert final_stock == 13
assert pending == 0
assert synced == 1


engine.close()


# ============================================================
# FINAL RESULT
# ============================================================

print()
print("=" * 70)
print("I8 RESULTADO FINAL")
print("=" * 70)

print("Evento local:                 PASS")
print("Odoo commit remoto:           PASS")
print("ACK perdido simulado:         PASS")
print("Evento permaneceu PENDING:    PASS")
print("Retry executado:              PASS")
print("Recovery idempotente:         PASS")
print("Recovered=True:               PASS")
print("Pickings remotos:             1")
print("Moves remotos:                1")
print("Duplicação de picking:        0")
print("Duplicação de move:           0")
print("PENDING final:                0")
print("SYNCED final:                 1")
print("Stock local final:            13")
print()
print("✅ I8 PASS — ACK LOSS + REPLAY RECOVERY")
print("=" * 70)
