import json
import os
import sys
import time
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
from bridge.inventory_sync import InventoryOdooSync


# ============================================================
# CONFIG
# ============================================================

DB = "client/i7_resilience_real.db"

ODOO_URL = os.environ.get("ODOO_URL")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY")
ODOO_DB = os.environ.get("ODOO_DB")

PRODUCT_ID = 1
LOCATION_ID = "MAPUTO-STORE"

EVENT_COUNT = 20
QUANTITY = 1.0

NODE_ID = "I7-NODE"
SEQ_BASE = 970000

ACK_LOSS_EVENT_INDEX = 10

REPORT_FILE = "/tmp/maz_i7_resilience_report.json"


if not ODOO_URL:
    raise RuntimeError("ODOO_URL nao definido")

if not ODOO_API_KEY:
    raise RuntimeError("ODOO_API_KEY nao definido")

if not ODOO_DB:
    raise RuntimeError("ODOO_DB nao definido")


# ============================================================
# HELPERS
# ============================================================

def print_header(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def count_event_picking_move(event_id, sync):
    picking = sync._call(
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
                "picking_id",
            ],
            "limit": 100,
        },
    )

    return picking, moves


class AckLossOnceSync(InventoryOdooSync):
    """
    Executa o evento no Odoo REAL e simula perda do ACK
    depois que o efeito remoto já aconteceu.

    Assim o cliente acredita que houve falha e deixa o
    evento PENDING, permitindo testar o retry/recovery real.
    """

    def __init__(
        self,
        *args,
        ack_loss_event_id=None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)

        self.ack_loss_event_id = ack_loss_event_id
        self.ack_loss_triggered = False

    def _send(self, event):

        response = super()._send(event)

        if (
            event["event_id"]
            == self.ack_loss_event_id
            and not self.ack_loss_triggered
        ):
            self.ack_loss_triggered = True

            raise InventorySyncError(
                "SIMULATED_ACK_LOSS_AFTER_REMOTE_COMMIT"
            )

        return response


# ============================================================
# IMPORT ERROR TYPE AFTER CLASS DEFINITION
# ============================================================

from bridge.inventory_sync import InventorySyncError


# ============================================================
# PREPARE DATABASE
# ============================================================

print_header(
    "mAZ — I7 REAL ODOO RESILIENCE TEST"
)

print("Database:", DB)
print("Odoo URL:", ODOO_URL)
print("Events:", EVENT_COUNT)
print(
    "ACK-loss event:",
    ACK_LOSS_EVENT_INDEX
)

db_path = Path(DB)
db_path.parent.mkdir(
    parents=True,
    exist_ok=True
)

if db_path.exists():
    db_path.unlink()


# ============================================================
# PHASE 1 — LOCAL EVENTS
# ============================================================

print_header(
    "[1] LOCAL EVENT CREATION"
)

engine = InventoryEventEngine(DB)

engine.set_stock(
    product_id=PRODUCT_ID,
    location_id=LOCATION_ID,
    quantity=0
)

events = []

for i in range(
    1,
    EVENT_COUNT + 1
):

    event_id = (
        f"MAZ-I7-{i:03d}"
    )

    event = {
        "event_id": event_id,
        "seq_id": SEQ_BASE + i,
        "node_id": NODE_ID,
        "product_id": PRODUCT_ID,
        "location_id": LOCATION_ID,
        "quantity": QUANTITY,
        "operation": "IN",
    }

    result = engine.process_event(
        event
    )

    print(
        f"{event_id} -> "
        f"{result['status']} "
        f"stock={result['new_stock']}"
    )

    assert result["status"] == "ACCEPTED"

    events.append(event)


local_stock = engine.get_stock(
    PRODUCT_ID,
    LOCATION_ID
)

pending_before = engine.outbox_count(
    "PENDING"
)

print()
print("Local stock:", local_stock)
print("Outbox PENDING:", pending_before)

assert local_stock == EVENT_COUNT
assert pending_before == EVENT_COUNT


# ============================================================
# PHASE 2 — NETWORK BLACKOUT
# ============================================================

print_header(
    "[2] NETWORK BLACKOUT"
)

blackout_sync = InventoryOdooSync(
    engine=engine,
    endpoint="http://127.0.0.1:1",
    timeout=0.5,
)

blackout_results = blackout_sync.sync_once(
    limit=EVENT_COUNT
)

blackout_errors = sum(
    1
    for item in blackout_results
    if item["status"] == "PENDING"
)

pending_after_blackout = (
    engine.outbox_count("PENDING")
)

print(
    "Attempted:",
    len(blackout_results)
)

print(
    "Network failures:",
    blackout_errors
)

print(
    "PENDING after blackout:",
    pending_after_blackout
)

assert len(blackout_results) == EVENT_COUNT
assert blackout_errors == EVENT_COUNT
assert pending_after_blackout == EVENT_COUNT


# ============================================================
# PHASE 3 — CRASH / RESTART
# ============================================================

print_header(
    "[3] CRASH / RESTART SIMULATION"
)

engine.close()

print(
    "Engine fechado."
)

engine = InventoryEventEngine(DB)

pending_after_restart = (
    engine.outbox_count("PENDING")
)

print(
    "PENDING after restart:",
    pending_after_restart
)

assert pending_after_restart == EVENT_COUNT


# ============================================================
# PHASE 4 — REAL ODOO
# ============================================================

print_header(
    "[4] REAL ODOO SYNC"
)

ack_loss_event_id = (
    events[
        ACK_LOSS_EVENT_INDEX - 1
    ]["event_id"]
)

sync = AckLossOnceSync(
    engine=engine,
    endpoint=ODOO_URL,
    api_key=ODOO_API_KEY,
    database=ODOO_DB,
    timeout=20,
    ack_loss_event_id=ack_loss_event_id,
)

first_online_results = sync.sync_once(
    limit=EVENT_COUNT
)

synced_first_pass = sum(
    1
    for item in first_online_results
    if item["status"] == "SYNCED"
)

pending_first_pass = sum(
    1
    for item in first_online_results
    if item["status"] == "PENDING"
)

print(
    "SYNCED first pass:",
    synced_first_pass
)

print(
    "PENDING first pass:",
    pending_first_pass
)

print(
    "Simulated ACK loss:",
    sync.ack_loss_triggered
)

assert len(first_online_results) == EVENT_COUNT
assert sync.ack_loss_triggered is True


# ============================================================
# ACK LOSS EXPECTATION
# ============================================================

ack_pending = engine.get_outbox_event(
    ack_loss_event_id
)

print()
print(
    "ACK-loss event outbox state:",
    ack_pending["status"]
)

print(
    "ACK-loss attempts:",
    ack_pending["attempts"]
)

assert (
    ack_pending["status"]
    == "PENDING"
)


# ============================================================
# PHASE 5 — RETRY / RECOVERY
# ============================================================

print_header(
    "[5] RETRY AFTER ACK LOSS"
)

retry_results = sync.sync_once(
    limit=EVENT_COUNT
)

retry_synced = sum(
    1
    for item in retry_results
    if item["status"] == "SYNCED"
)

retry_pending = sum(
    1
    for item in retry_results
    if item["status"] == "PENDING"
)

print(
    "SYNCED:",
    retry_synced
)

print(
    "PENDING:",
    retry_pending
)

assert retry_pending == 0


# ============================================================
# PHASE 6 — REPLAY ALREADY SYNCED EVENT
# ============================================================

print_header(
    "[6] REPLAY ALREADY COMPLETED EVENT"
)

replay_engine_event = (
    engine.get_outbox_event(
        ack_loss_event_id
    )
)

assert (
    replay_engine_event["status"]
    == "SYNCED"
)

replay_result = sync._send(
    replay_engine_event["payload"]
)

print(
    "Replay response:"
)

print(
    json.dumps(
        replay_result,
        indent=2,
        default=str
    )
)

assert replay_result["status"] == "DONE"
assert replay_result["recovered"] is True


# ============================================================
# PHASE 7 — LOCAL AUDIT
# ============================================================

print_header(
    "[7] LOCAL AUDIT"
)

pending_final = engine.outbox_count(
    "PENDING"
)

synced_final = engine.outbox_count(
    "SYNCED"
)

print(
    "Outbox PENDING:",
    pending_final
)

print(
    "Outbox SYNCED:",
    synced_final
)

assert pending_final == 0
assert synced_final == EVENT_COUNT


# ============================================================
# PHASE 8 — REAL ODOO AUDIT
# ============================================================

print_header(
    "[8] REAL ODOO AUDIT"
)

total_pickings = 0
total_moves = 0

duplicate_pickings = []
duplicate_moves = []

remote_results = []

for event in events:

    event_id = event["event_id"]

    pickings, moves = (
        count_event_picking_move(
            event_id,
            sync
        )
    )

    picking_count = len(
        pickings
    )

    move_count = len(
        moves
    )

    total_pickings += picking_count
    total_moves += move_count

    if picking_count > 1:
        duplicate_pickings.append(
            event_id
        )

    if move_count > 1:
        duplicate_moves.append(
            event_id
        )

    remote_results.append(
        {
            "event_id": event_id,
            "pickings": pickings,
            "moves": moves,
        }
    )

    print(
        f"{event_id}: "
        f"pickings={picking_count} "
        f"moves={move_count}"
    )

assert total_pickings == EVENT_COUNT
assert total_moves == EVENT_COUNT
assert not duplicate_pickings
assert not duplicate_moves


# ============================================================
# PHASE 9 — GLOBAL ODOO DUPLICATION CHECK
# ============================================================

print_header(
    "[9] GLOBAL DUPLICATION CHECK"
)

picking_duplicates = sync._call(
    "stock.picking",
    "search_read",
    {
        "domain": [
            [
                "origin",
                "ilike",
                "MAZ-I7-"
            ]
        ],
        "fields": [
            "id",
            "origin",
            "state",
        ],
        "limit": 1000,
    },
)

move_duplicates = sync._call(
    "stock.move",
    "search_read",
    {
        "domain": [
            [
                "origin",
                "ilike",
                "MAZ-I7-"
            ]
        ],
        "fields": [
            "id",
            "origin",
            "state",
        ],
        "limit": 1000,
    },
)

print(
    "Remote pickings found:",
    len(picking_duplicates)
)

print(
    "Remote moves found:",
    len(move_duplicates)
)

assert len(picking_duplicates) == EVENT_COUNT
assert len(move_duplicates) == EVENT_COUNT


# ============================================================
# PHASE 10 — FINAL LOCAL STATE
# ============================================================

final_local_stock = engine.get_stock(
    PRODUCT_ID,
    LOCATION_ID
)

print()
print(
    "Final local stock:",
    final_local_stock
)

assert final_local_stock == EVENT_COUNT


# ============================================================
# REPORT
# ============================================================

report = {
    "test": "I7 REAL ODOO RESILIENCE",
    "event_count": EVENT_COUNT,
    "local_stock": {
        "initial": 0,
        "final": final_local_stock,
    },
    "blackout": {
        "attempts": len(blackout_results),
        "failures": blackout_errors,
        "pending_after": pending_after_blackout,
    },
    "restart": {
        "pending_after_restart":
            pending_after_restart,
    },
    "ack_loss": {
        "event_id": ack_loss_event_id,
        "simulated": sync.ack_loss_triggered,
        "status_after_loss":
            ack_pending["status"],
        "attempts":
            ack_pending["attempts"],
    },
    "retry": {
        "synced": retry_synced,
        "pending": retry_pending,
    },
    "local_outbox": {
        "pending_final": pending_final,
        "synced_final": synced_final,
    },
    "odoo": {
        "pickings": total_pickings,
        "moves": total_moves,
        "duplicate_pickings":
            duplicate_pickings,
        "duplicate_moves":
            duplicate_moves,
    },
    "replay": {
        "status":
            replay_result["status"],
        "recovered":
            replay_result["recovered"],
    },
    "remote_results":
        remote_results,
}


with open(
    REPORT_FILE,
    "w"
) as f:

    json.dump(
        report,
        f,
        indent=2,
        default=str,
    )


engine.close()


# ============================================================
# FINAL RESULT
# ============================================================

print_header(
    "I7 RESULTADO FINAL"
)

print(
    "Eventos locais:             ",
    EVENT_COUNT
)

print(
    "Eventos no Outbox:          ",
    synced_final
)

print(
    "PENDING final:              ",
    pending_final
)

print(
    "Pickings Odoo:              ",
    total_pickings
)

print(
    "Moves Odoo:                 ",
    total_moves
)

print(
    "Duplicações de picking:     ",
    len(duplicate_pickings)
)

print(
    "Duplicações de move:        ",
    len(duplicate_moves)
)

print(
    "ACK-loss recuperado:        ",
    replay_result["recovered"]
)

print(
    "Stock local final:          ",
    final_local_stock
)

print()
print(
    "I7 PASS — RESILIÊNCIA VALIDADA"
)

print()
print(
    "Relatório:",
    REPORT_FILE
)
