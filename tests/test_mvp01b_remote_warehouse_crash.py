import json
import os
import signal
import sqlite3
import subprocess
import sys
import urllib.request


# =========================================================
# CONFIGURAÇÃO
# =========================================================

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ODOO_URL = os.environ.get("ODOO_URL", "http://localhost:8069").rstrip("/")
ODOO_DB = os.environ.get("ODOO_DB", "maz_lab")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY")

DB_PATH = os.path.join(
    ROOT,
    "client",
    "mvp01b_remote_warehouse_crash.db",
)

EVENT_ID = "MAZ-MVP01B-REMOTE-WH-CRASH-001"
PRODUCT_ID = 1
LOCAL_LOCATION_ID = "REMOTE-WAREHOUSE-01"
QUANTITY = -3.0
INITIAL_LOCAL_STOCK = 10.0

if not ODOO_API_KEY:
    raise RuntimeError("ODOO_API_KEY não está definido no ambiente")


# =========================================================
# EVENTO
# =========================================================

EVENT = {
    "event_id": EVENT_ID,
    "seq_id": 970002,
    "node_id": "REMOTE-WAREHOUSE-01",
    "product_id": PRODUCT_ID,
    "location_id": LOCAL_LOCATION_ID,
    "quantity": QUANTITY,
    "operation": "OUT",
}


# =========================================================
# ODOO JSON-2
# =========================================================

def odoo_call(model, method, payload):
    url = f"{ODOO_URL}/json/2/{model}/{method}"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "mAZ-MVP01B/1.0",
        "Authorization": f"bearer {ODOO_API_KEY}",
        "X-Odoo-Database": ODOO_DB,
    }

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")

    if not raw:
        return None

    return json.loads(raw)


# =========================================================
# AUDITORIA ODOO
# =========================================================

def audit_event(label):
    pickings = odoo_call(
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
            "limit": 10,
        },
    )

    moves = odoo_call(
        "stock.move",
        "search_read",
        {
            "domain": [["origin", "=", EVENT_ID]],
            "fields": [
                "id",
                "origin",
                "state",
                "picking_id",
                "product_id",
                "product_uom_qty",
                "quantity",
                "location_id",
                "location_dest_id",
            ],
            "limit": 10,
        },
    )

    print()
    print(f"--- {label} ---")
    print(f"Pickings: {len(pickings)}")
    print(f"Moves:    {len(moves)}")

    for p in pickings:
        print(
            "  PICKING:",
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "state": p.get("state"),
                "move_ids": p.get("move_ids"),
            },
        )

    for m in moves:
        print(
            "  MOVE:",
            {
                "id": m.get("id"),
                "state": m.get("state"),
                "picking_id": m.get("picking_id"),
                "product_id": m.get("product_id"),
                "product_uom_qty": m.get("product_uom_qty"),
                "quantity": m.get("quantity"),
                "location_id": m.get("location_id"),
                "location_dest_id": m.get("location_dest_id"),
            },
        )

    return pickings, moves


# =========================================================
# ODOO STOCK
# =========================================================

def get_odoo_stock():
    result = odoo_call(
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
            "limit": 10,
        },
    )

    return sum(float(row.get("quantity", 0.0)) for row in result)


# =========================================================
# LOCAL OUTBOX
# =========================================================

def local_outbox_status():
    conn = sqlite3.connect(DB_PATH)

    row = conn.execute(
        """
        SELECT event_id, status, attempts, last_error
        FROM inventory_outbox
        WHERE event_id = ?
        """,
        (EVENT_ID,),
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return {
        "event_id": row[0],
        "status": row[1],
        "attempts": row[2],
        "last_error": row[3],
    }


# =========================================================
# HEADER
# =========================================================

print("=" * 70)
print(" mAZ MVP-01B — REMOTE WAREHOUSE REAL CRASH")
print("=" * 70)

print()
print("EVENT_ID :", EVENT_ID)
print("NODE_ID  :", LOCAL_LOCATION_ID)
print("ODOO_URL :", ODOO_URL)
print("ODOO_DB  :", ODOO_DB)
print("DB_PATH  :", DB_PATH)


# =========================================================
# 0. FRESH LOCAL DATABASE
# =========================================================

print()
print("=" * 70)
print("[0] PREPARAÇÃO LOCAL")
print("=" * 70)

try:
    os.remove(DB_PATH)
except FileNotFoundError:
    pass

from bridge.inventory_engine import InventoryEventEngine
from bridge.inventory_sync import InventoryOdooSync

engine = InventoryEventEngine(DB_PATH)

engine.set_stock(
    PRODUCT_ID,
    LOCAL_LOCATION_ID,
    INITIAL_LOCAL_STOCK,
)

local_before = engine.get_stock(
    PRODUCT_ID,
    LOCAL_LOCATION_ID,
)

print("Local stock inicial:", local_before)

if local_before != INITIAL_LOCAL_STOCK:
    raise AssertionError(
        f"Stock local inicial incorreto: {local_before}"
    )


# =========================================================
# 1. PRE-CHECK ODOO
# =========================================================

print()
print("=" * 70)
print("[1] PRE-CHECK ODOO")
print("=" * 70)

before_pickings, before_moves = audit_event(
    "PRE-CHECK — EVENTO"
)

if before_pickings or before_moves:
    raise RuntimeError(
        "PRE-CHECK FALHOU: já existem objetos Odoo para este event_id"
    )

odoo_stock_before = get_odoo_stock()

print()
print("Odoo WH/Stock antes:", odoo_stock_before)

print("PASS — PRE-CHECK LIMPO")


# =========================================================
# 2. OPERAÇÃO LOCAL NO ARMAZÉM REMOTO
# =========================================================

print()
print("=" * 70)
print("[2] OPERAÇÃO REMOTA LOCAL")
print("=" * 70)

result = engine.process_event(EVENT)

print(json.dumps(result, indent=2, sort_keys=True))

if result.get("status") != "ACCEPTED":
    raise AssertionError(
        f"process_event não aceitou operação: {result}"
    )

local_after_operation = engine.get_stock(
    PRODUCT_ID,
    LOCAL_LOCATION_ID,
)

if local_after_operation != 7.0:
    raise AssertionError(
        f"Stock local esperado 7.0; obtido {local_after_operation}"
    )

outbox = local_outbox_status()

print()
print("Local stock após OUT:", local_after_operation)
print("Outbox:", outbox)

if outbox is None:
    raise AssertionError("Evento não apareceu no outbox")

if outbox["status"] != "PENDING":
    raise AssertionError(
        f"Outbox deveria estar PENDING: {outbox}"
    )

print("PASS — OPERAÇÃO LOCAL PERSISTIDA")
print("       stock 10 → 7")
print("       event ACCEPTED")
print("       outbox PENDING")


# =========================================================
# 3. WORKER REAL — CRASH ENTRE PICKING E MOVE
# =========================================================

print()
print("=" * 70)
print("[3] WORKER REAL — CRASH APÓS PICKING")
print("=" * 70)

worker_code = r'''
import os
import signal

from bridge.inventory_sync import InventoryOdooSync

EVENT = {
    "event_id": "MAZ-MVP01B-REMOTE-WH-CRASH-001",
    "seq_id": 970002,
    "node_id": "REMOTE-WAREHOUSE-01",
    "product_id": 1,
    "location_id": "REMOTE-WAREHOUSE-01",
    "quantity": -3.0,
    "operation": "OUT",
}

sync = InventoryOdooSync(
    engine=None,
    endpoint=os.environ["ODOO_URL"],
    timeout=30,
    api_key=os.environ["ODOO_API_KEY"],
    database=os.environ["ODOO_DB"],
)

picking_id, locations = sync._create_picking(EVENT)

print(
    f"PICKING_CREATED id={picking_id}",
    flush=True,
)

os.kill(os.getpid(), signal.SIGKILL)
'''

env = os.environ.copy()
env["PYTHONPATH"] = ROOT

worker = subprocess.Popen(
    [
        sys.executable,
        "-c",
        worker_code,
    ],
    cwd=ROOT,
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

worker_line = worker.stdout.readline().strip()

print("WORKER:", worker_line)

if not worker_line.startswith("PICKING_CREATED"):
    stdout_rest = worker.stdout.read()
    stderr_rest = worker.stderr.read()

    print("STDOUT:", stdout_rest)
    print("STDERR:", stderr_rest)

    worker.kill()
    worker.wait()

    raise RuntimeError(
        "Worker não confirmou criação do picking"
    )

worker.wait()

print("Worker exit code:", worker.returncode)

if worker.returncode != -signal.SIGKILL:
    print("STDERR:")
    print(worker.stderr.read())

    raise RuntimeError(
        f"Crash não foi SIGKILL real: {worker.returncode}"
    )

print("PASS — SIGKILL REAL CONFIRMADO")


# =========================================================
# 4. PROVA DO ESTADO REMOTO PARCIAL
# =========================================================

after_crash_pickings, after_crash_moves = audit_event(
    "POST-CRASH — ODOO PARCIAL"
)

if len(after_crash_pickings) != 1:
    raise AssertionError(
        f"Esperado 1 picking após crash; "
        f"obtido {len(after_crash_pickings)}"
    )

if len(after_crash_moves) != 0:
    raise AssertionError(
        f"Esperado 0 moves após crash; "
        f"obtido {len(after_crash_moves)}"
    )

picking_after_crash = after_crash_pickings[0]

if picking_after_crash.get("origin") != EVENT_ID:
    raise AssertionError(
        "Origin do picking após crash está incorreto"
    )

outbox_after_crash = local_outbox_status()

print()
print("Outbox após crash:", outbox_after_crash)

if outbox_after_crash["status"] != "PENDING":
    raise AssertionError(
        "Outbox deveria continuar PENDING após morte do worker"
    )

print()
print("PASS — CRASH PARCIAL VALIDADO")
print("       Odoo: 1 Picking / 0 Moves")
print("       Local: outbox PENDING")


# =========================================================
# 5. NOVO PROCESSO — RECOVERY NORMAL VIA sync_once()
# =========================================================

print()
print("=" * 70)
print("[4] NOVO PROCESSO — RECOVERY VIA SYNC_ONCE")
print("=" * 70)

recovery_code = r'''
import json
import os

from bridge.inventory_engine import InventoryEventEngine
from bridge.inventory_sync import InventoryOdooSync

DB_PATH = os.environ["MVP01B_DB"]

engine = InventoryEventEngine(DB_PATH)

sync = InventoryOdooSync(
    engine=engine,
    endpoint=os.environ["ODOO_URL"],
    timeout=30,
    api_key=os.environ["ODOO_API_KEY"],
    database=os.environ["ODOO_DB"],
)

results = sync.sync_once()

print("RECOVERY_RESULTS")
print(json.dumps(results, indent=2, sort_keys=True))
'''

env["MVP01B_DB"] = DB_PATH

recovery = subprocess.run(
    [
        sys.executable,
        "-c",
        recovery_code,
    ],
    cwd=ROOT,
    env=env,
    capture_output=True,
    text=True,
)

print(recovery.stdout)

if recovery.returncode != 0:
    print("STDERR:")
    print(recovery.stderr)

    raise RuntimeError(
        f"Recovery falhou: exit={recovery.returncode}"
    )

if "RECOVERY_RESULTS" not in recovery.stdout:
    raise RuntimeError(
        "Recovery não retornou resultados"
    )

if '"status": "SYNCED"' not in recovery.stdout:
    raise AssertionError(
        "sync_once() não terminou em SYNCED"
    )

if '"recovered": true' not in recovery.stdout:
    raise AssertionError(
        "Recovery não reportou recovered=True"
    )

print("PASS — NOVO PROCESSO RECUPEROU VIA OUTBOX")


# =========================================================
# 6. AUDITORIA FINAL ODOO
# =========================================================

final_pickings, final_moves = audit_event(
    "FINAL — APÓS RECOVERY"
)

if len(final_pickings) != 1:
    raise AssertionError(
        f"Esperado 1 picking final; obtido {len(final_pickings)}"
    )

if len(final_moves) != 1:
    raise AssertionError(
        f"Esperado 1 move final; obtido {len(final_moves)}"
    )

final_picking = final_pickings[0]
final_move = final_moves[0]

if final_picking.get("state") != "done":
    raise AssertionError(
        f"Picking final não está done: "
        f"{final_picking.get('state')}"
    )

if final_move.get("state") != "done":
    raise AssertionError(
        f"Move final não está done: "
        f"{final_move.get('state')}"
    )

if float(final_move.get("product_uom_qty", 0)) != 3.0:
    raise AssertionError(
        f"Quantidade do move incorreta: "
        f"{final_move.get('product_uom_qty')}"
    )

final_picking_id = final_picking["id"]
move_picking = final_move.get("picking_id")

if isinstance(move_picking, list):
    move_picking_id = move_picking[0]
else:
    move_picking_id = move_picking

if move_picking_id != final_picking_id:
    raise AssertionError(
        "Move final não pertence ao picking original"
    )

print()
print("PASS — RECOVERY ODOO COMPLETO")
print("       1 Picking")
print("       1 Move")
print("       Picking = done")
print("       Move = done")
print("       quantidade = 3")
print("       mesmo Picking original")


# =========================================================
# 7. OUTBOX FINAL
# =========================================================

engine.close()

check_engine = InventoryEventEngine(DB_PATH)

outbox_final = check_engine.get_outbox_event(EVENT_ID)

print()
print("--- OUTBOX FINAL ---")
print(json.dumps(outbox_final, indent=2, sort_keys=True))

if outbox_final is None:
    raise AssertionError(
        "Evento desapareceu do outbox"
    )

if outbox_final["status"] != "SYNCED":
    raise AssertionError(
        f"Outbox não terminou SYNCED: {outbox_final}"
    )

check_engine.close()

print("PASS — OUTBOX PENDING → SYNCED")


# =========================================================
# 8. DELTA REAL DE STOCK ODOO
# =========================================================

odoo_stock_after = get_odoo_stock()
delta = odoo_stock_after - odoo_stock_before

print()
print("=" * 70)
print("[5] DELTA REAL DE STOCK ODOO")
print("=" * 70)

print("WH/Stock antes :", odoo_stock_before)
print("WH/Stock depois:", odoo_stock_after)
print("Delta          :", delta)

if delta != QUANTITY:
    raise AssertionError(
        f"Delta esperado {QUANTITY}; obtido {delta}"
    )

print("PASS — DELTA ODOO = -3")


# =========================================================
# 9. REPLAY LOCAL
# =========================================================

print()
print("=" * 70)
print("[6] REPLAY LOCAL — MESMO EVENT_ID")
print("=" * 70)

replay_engine = InventoryEventEngine(DB_PATH)

replay_result = replay_engine.process_event(EVENT)

print(json.dumps(replay_result, indent=2, sort_keys=True))

if replay_result.get("status") != "DUPLICATE":
    raise AssertionError(
        f"Replay local não retornou DUPLICATE: {replay_result}"
    )

replay_stock = replay_engine.get_stock(
    PRODUCT_ID,
    LOCAL_LOCATION_ID,
)

if replay_stock != 7.0:
    raise AssertionError(
        f"Replay alterou stock local: {replay_stock}"
    )

replay_engine.close()

print("PASS — REPLAY LOCAL IDEMPOTENTE")


# =========================================================
# 10. REPLAY REMOTO
# =========================================================

print()
print("=" * 70)
print("[7] REPLAY REMOTO — MESMO EVENT_ID")
print("=" * 70)

replay_engine = InventoryEventEngine(DB_PATH)

replay_sync = InventoryOdooSync(
    engine=replay_engine,
    endpoint=ODOO_URL,
    timeout=30,
    api_key=ODOO_API_KEY,
    database=ODOO_DB,
)

remote_replay = replay_sync._send(EVENT)

print(json.dumps(remote_replay, indent=2, sort_keys=True))

if remote_replay.get("status") != "DONE":
    raise AssertionError(
        f"Replay remoto não retornou DONE: {remote_replay}"
    )

if remote_replay.get("recovered") is not True:
    raise AssertionError(
        "Replay remoto não retornou recovered=True"
    )

if remote_replay.get("picking_id") != final_picking_id:
    raise AssertionError(
        "Replay remoto retornou outro picking"
    )

if remote_replay.get("move_id") != final_move["id"]:
    raise AssertionError(
        "Replay remoto retornou outro move"
    )

replay_engine.close()

print("PASS — REPLAY REMOTO RECUPERADO")


# =========================================================
# 11. PROVA FINAL DE NÃO DUPLICAÇÃO
# =========================================================

replay_pickings, replay_moves = audit_event(
    "FINAL — APÓS REPLAY"
)

replay_stock_after = get_odoo_stock()

if len(replay_pickings) != 1:
    raise AssertionError(
        f"Replay criou picking adicional: {len(replay_pickings)}"
    )

if len(replay_moves) != 1:
    raise AssertionError(
        f"Replay criou move adicional: {len(replay_moves)}"
    )

final_delta = replay_stock_after - odoo_stock_before

if final_delta != QUANTITY:
    raise AssertionError(
        f"Replay alterou delta de stock: {final_delta}"
    )

print()
print("PASS — NÃO DUPLICAÇÃO CONFIRMADA")
print("       Pickings = 1")
print("       Moves    = 1")
print("       Delta    = -3")


# =========================================================
# RESULTADO
# =========================================================

print()
print("=" * 70)
print(" RESULTADO MVP-01B")
print("=" * 70)

print()
print("Remote Warehouse:")
print("  operação local       = OUT -3")
print("  stock local          = 10 → 7")

print()
print("Crash real:")
print("  SIGKILL              = confirmado")
print("  Odoo pós-crash       = 1 Picking / 0 Moves")

print()
print("Recovery:")
print("  novo processo        = confirmado")
print("  recovered             = true")
print("  Odoo final            = 1 Picking / 1 Move")
print("  state                 = done")
print("  outbox                = SYNCED")

print()
print("Idempotência:")
print("  replay local          = DUPLICATE")
print("  replay remoto         = recovered=True")
print("  Pickings finais       = 1")
print("  Moves finais          = 1")
print("  Delta Odoo            = -3")

print()
print("PASS — MVP-01B REMOTE WAREHOUSE CRASH RECOVERY VALIDATED")
