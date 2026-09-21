import os
import json
import sqlite3

from bridge.inventory_engine import InventoryEventEngine
from bridge.inventory_sync import InventoryOdooSync


DB = "client/mvp01_remote_warehouse.db"

ODOO_URL = os.environ.get("ODOO_URL", "http://localhost:8069")
ODOO_DB = os.environ.get("ODOO_DB", "maz_lab")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY")

EVENT_ID = "MAZ-MVP01-REMOTE-WH-OUT-001"
NODE_ID = "REMOTE-WAREHOUSE-01"
PRODUCT_ID = 1
LOCAL_LOCATION_ID = "REMOTE-WAREHOUSE-01"

INITIAL_LOCAL_STOCK = 10
QUANTITY = -3


def fresh_db():
    try:
        os.remove(DB)
    except FileNotFoundError:
        pass


def odoo_call(sync, model, method, payload):
    return sync._call(model, method, payload)


def find_picking(sync):
    return odoo_call(
        sync,
        "stock.picking",
        "search_read",
        {
            "domain": [["origin", "=", EVENT_ID]],
            "fields": [
                "id",
                "name",
                "origin",
                "state",
                "move_ids",
                "date_done",
            ],
            "limit": 20,
        },
    )


def find_moves(sync):
    return odoo_call(
        sync,
        "stock.move",
        "search_read",
        {
            "domain": [["origin", "=", EVENT_ID]],
            "fields": [
                "id",
                "origin",
                "state",
                "product_id",
                "product_uom_qty",
                "quantity",
                "location_id",
                "location_dest_id",
                "picking_id",
            ],
            "limit": 20,
        },
    )


def get_odoo_stock(sync):
    rows = odoo_call(
        sync,
        "stock.quant",
        "search_read",
        {
            "domain": [
                ["product_id", "=", PRODUCT_ID],
                ["location_id", "=", 5],
            ],
            "fields": [
                "id",
                "product_id",
                "location_id",
                "quantity",
                "reserved_quantity",
            ],
            "limit": 20,
        },
    )

    return sum(float(r["quantity"]) for r in rows)


def main():
    print("=" * 70)
    print("mAZ MVP-01 — REMOTE WAREHOUSE STOCK DISPATCH")
    print("=" * 70)
    print(f"EVENT_ID : {EVENT_ID}")
    print(f"NODE_ID  : {NODE_ID}")
    print(f"ODOO_URL : {ODOO_URL}")
    print(f"ODOO_DB  : {ODOO_DB}")
    print()

    if not ODOO_API_KEY:
        raise RuntimeError("ODOO_API_KEY não está definido no ambiente.")

    fresh_db()

    # ------------------------------------------------------------
    # 0. ENGINE LOCAL
    # ------------------------------------------------------------
    engine = InventoryEventEngine(DB)

    engine.set_stock(
        PRODUCT_ID,
        LOCAL_LOCATION_ID,
        INITIAL_LOCAL_STOCK,
    )

    print("--- 0. ESTADO INICIAL ---")
    print("Local stock:", engine.get_stock(PRODUCT_ID, LOCAL_LOCATION_ID))

    sync_real = InventoryOdooSync(
        engine,
        ODOO_URL,
        timeout=10,
        api_key=ODOO_API_KEY,
        database=ODOO_DB,
    )

    pre_pickings = find_picking(sync_real)
    pre_moves = find_moves(sync_real)
    odoo_stock_before = get_odoo_stock(sync_real)

    print("Odoo Pickings com EVENT_ID:", len(pre_pickings))
    print("Odoo Moves com EVENT_ID:", len(pre_moves))
    print("Odoo WH/Stock antes:", odoo_stock_before)

    assert len(pre_pickings) == 0
    assert len(pre_moves) == 0

    # ------------------------------------------------------------
    # 1. OPERAÇÃO LOCAL — ARMAZÉM REMOTO
    # ------------------------------------------------------------
    event = {
        "event_id": EVENT_ID,
        "seq_id": 970001,
        "node_id": NODE_ID,
        "product_id": PRODUCT_ID,
        "location_id": LOCAL_LOCATION_ID,
        "quantity": QUANTITY,
        "operation": "OUT",
    }

    print()
    print("--- 1. OPERAÇÃO NO ARMAZÉM REMOTO ---")

    local_result = engine.process_event(event)

    print(json.dumps(local_result, indent=2))

    assert local_result["status"] == "ACCEPTED"
    assert local_result["new_stock"] == 7

    local_stock = engine.get_stock(
        PRODUCT_ID,
        LOCAL_LOCATION_ID,
    )

    print("Local stock após saída:", local_stock)
    print("Outbox:", engine.outbox_count())

    assert local_stock == 7
    assert engine.outbox_count() == 1

    # ------------------------------------------------------------
    # 2. CONECTIVIDADE INDISPONÍVEL
    # ------------------------------------------------------------
    print()
    print("--- 2. CONECTIVIDADE INDISPONÍVEL ---")

    sync_down = InventoryOdooSync(
        engine,
        "http://127.0.0.1:1",
        timeout=2,
        api_key=ODOO_API_KEY,
        database=ODOO_DB,
    )

    failed = sync_down.sync_once()

    print(json.dumps(failed, indent=2))

    pending = engine.get_pending_outbox()

    print("Outbox PENDING:", len(pending))

    assert len(pending) == 1
    assert engine.outbox_count("PENDING") == 1

    outbox_after_failure = engine.get_outbox_event(EVENT_ID)

    print("Outbox após falha:")
    print(json.dumps(outbox_after_failure, indent=2, default=str))

    assert outbox_after_failure["status"] == "PENDING"
    assert outbox_after_failure["attempts"] >= 1
    assert outbox_after_failure["last_error"]

    # ------------------------------------------------------------
    # 3. CONECTIVIDADE RESTAURADA
    # ------------------------------------------------------------
    print()
    print("--- 3. CONECTIVIDADE RESTAURADA ---")

    synced = sync_real.sync_once()

    print(json.dumps(synced, indent=2, default=str))

    assert len(synced) == 1
    assert synced[0]["status"] == "SYNCED"

    assert engine.outbox_count("PENDING") == 0
    assert engine.outbox_count("SYNCED") == 1

    # ------------------------------------------------------------
    # 4. AUDITORIA ODOO
    # ------------------------------------------------------------
    print()
    print("--- 4. AUDITORIA ODOO ---")

    pickings = find_picking(sync_real)
    moves = find_moves(sync_real)

    print("Pickings:", len(pickings))
    print(json.dumps(pickings, indent=2, default=str))

    print("Moves:", len(moves))
    print(json.dumps(moves, indent=2, default=str))

    assert len(pickings) == 1
    assert len(moves) == 1

    picking = pickings[0]
    move = moves[0]

    assert picking["origin"] == EVENT_ID
    assert picking["state"] == "done"

    assert move["origin"] == EVENT_ID
    assert move["state"] == "done"
    assert float(move["product_uom_qty"]) == 3.0
    assert float(move["quantity"]) == 3.0
    assert move["picking_id"][0] == picking["id"]

    # OUT: WH/Stock (5) -> Customers (2)
    assert move["location_id"][0] == 5
    assert move["location_dest_id"][0] == 2

    # ------------------------------------------------------------
    # 5. DELTA REAL DE ESTOQUE NO ODOO
    # ------------------------------------------------------------
    print()
    print("--- 5. DELTA DE ESTOQUE ODOO ---")

    odoo_stock_after = get_odoo_stock(sync_real)
    delta = odoo_stock_after - odoo_stock_before

    print("Odoo WH/Stock antes :", odoo_stock_before)
    print("Odoo WH/Stock depois:", odoo_stock_after)
    print("Delta               :", delta)

    assert delta == -3.0

    # ------------------------------------------------------------
    # 6. REPLAY LOCAL
    # ------------------------------------------------------------
    print()
    print("--- 6. REPLAY LOCAL DO MESMO EVENT_ID ---")

    replay_local = engine.process_event(event)

    print(json.dumps(replay_local, indent=2, default=str))

    assert replay_local["status"] == "DUPLICATE"
    assert engine.get_stock(PRODUCT_ID, LOCAL_LOCATION_ID) == 7

    # ------------------------------------------------------------
    # 7. REPLAY REMOTO
    # ------------------------------------------------------------
    print()
    print("--- 7. REPLAY REMOTO NO ODOO ---")

    replay_remote = sync_real._send(event)

    print(json.dumps(replay_remote, indent=2, default=str))

    assert replay_remote["status"] == "DONE"
    assert replay_remote["recovered"] is True

    # ------------------------------------------------------------
    # 8. PROVA FINAL DE NÃO DUPLICAÇÃO
    # ------------------------------------------------------------
    print()
    print("--- 8. PROVA FINAL DE NÃO DUPLICAÇÃO ---")

    final_pickings = find_picking(sync_real)
    final_moves = find_moves(sync_real)

    print("Pickings finais:", len(final_pickings))
    print("Moves finais   :", len(final_moves))

    assert len(final_pickings) == 1
    assert len(final_moves) == 1

    final_odoo_stock = get_odoo_stock(sync_real)

    print("WH/Stock final:", final_odoo_stock)
    print("Delta final   :", final_odoo_stock - odoo_stock_before)

    assert final_odoo_stock - odoo_stock_before == -3.0

    print()
    print("=" * 70)
    print("RESULTADO MVP-01")
    print("=" * 70)
    print("PASS — REMOTE WAREHOUSE CONTINUITY VALIDATED")
    print()
    print("Local:")
    print("  estoque inicial :", INITIAL_LOCAL_STOCK)
    print("  estoque final   :", engine.get_stock(PRODUCT_ID, LOCAL_LOCATION_ID))
    print("  evento          : ACCEPTED")
    print("  outbox final    : SYNCED")
    print()
    print("Falha de conectividade:")
    print("  PENDING         : SIM")
    print("  retry           : SIM")
    print()
    print("Odoo real:")
    print("  Pickings        :", len(final_pickings))
    print("  Moves           :", len(final_moves))
    print("  Picking state   :", final_pickings[0]["state"])
    print("  Move state      :", final_moves[0]["state"])
    print("  Stock delta     :", final_odoo_stock - odoo_stock_before)
    print()
    print("Replay:")
    print("  local duplicate : SIM")
    print("  remote recovery : SIM")
    print("  duplicated Pickings: 0")
    print("  duplicated Moves   : 0")
    print()
    print("VERDICT:")
    print("mAZ Bridge manteve a operação local durante indisponibilidade,")
    print("persistiu a sincronização, recuperou o Odoo e materializou")
    print("uma única saída de estoque sem duplicação.")
    print("=" * 70)

    engine.close()


if __name__ == "__main__":
    main()
