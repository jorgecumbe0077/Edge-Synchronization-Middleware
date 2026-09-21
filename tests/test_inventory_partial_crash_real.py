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

EVENT_ID = "MAZ-PARTIAL-CRASH-REAL-001"
PRODUCT_ID = 1
LOCATION_ID = "MAPUTO-STORE"
QUANTITY = 3.0

if not ODOO_API_KEY:
    raise RuntimeError("ODOO_API_KEY não está definido no ambiente")


# =========================================================
# ODOO JSON-2
# =========================================================

def odoo_call(model, method, payload):
    url = f"{ODOO_URL}/json/2/{model}/{method}"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "mAZ-I-PARTIAL-CRASH-REAL/1.0",
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
# EVENTO
# =========================================================

EVENT = {
    "event_id": EVENT_ID,
    "seq_id": 970001,
    "node_id": "POS-MAPUTO-PARTIAL-CRASH",
    "product_id": PRODUCT_ID,
    "location_id": LOCATION_ID,
    "quantity": QUANTITY,
    "operation": "IN",
}


# =========================================================
# AUDITORIA
# =========================================================

def audit(label):
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
                "quantity": m.get("product_uom_qty"),
            },
        )

    return pickings, moves


# =========================================================
# PRÉ-CHECK
# =========================================================

print("=" * 70)
print(" mAZ I-PARTIAL-CRASH-REAL")
print(" CRASH REAL ENTRE PICKING E MOVE")
print("=" * 70)

print()
print("EVENT_ID:", EVENT_ID)
print("ODOO_URL:", ODOO_URL)
print("ODOO_DB :", ODOO_DB)

before_pickings, before_moves = audit("PRE-CHECK")

if before_pickings or before_moves:
    raise RuntimeError(
        "PRE-CHECK FALHOU: já existem objetos Odoo para este event_id"
    )

print()
print("PASS — PRE-CHECK LIMPO")


# =========================================================
# WORKER
#
# O worker executa:
#
#   _create_picking()
#   print("PICKING_CREATED")
#   SIGKILL
#
# Não existe sleep.
# O crash acontece exatamente após o retorno do create.
# =========================================================

worker_code = r'''
import os
import signal
import sys

from bridge.inventory_sync import InventoryOdooSync


EVENT = {
    "event_id": "MAZ-PARTIAL-CRASH-REAL-001",
    "seq_id": 970001,
    "node_id": "POS-MAPUTO-PARTIAL-CRASH",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": 3.0,
    "operation": "IN",
}

endpoint = os.environ["ODOO_URL"]
database = os.environ["ODOO_DB"]
api_key = os.environ["ODOO_API_KEY"]

sync = InventoryOdooSync(
    engine=None,
    endpoint=endpoint,
    timeout=30,
    api_key=api_key,
    database=database,
)

picking_id, locations = sync._create_picking(EVENT)

print(
    f"PICKING_CREATED id={picking_id}",
    flush=True,
)

# =========================================================
# CRASH REAL
# =========================================================

os.kill(os.getpid(), signal.SIGKILL)
'''


print()
print("=" * 70)
print("[1] PROCESSO WORKER")
print("=" * 70)

env = os.environ.copy()
env["PYTHONPATH"] = ROOT

proc = subprocess.Popen(
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

line = proc.stdout.readline().strip()

print("WORKER:", line)

if not line.startswith("PICKING_CREATED"):
    stdout_rest = proc.stdout.read()
    stderr_rest = proc.stderr.read()

    print("STDOUT:", stdout_rest)
    print("STDERR:", stderr_rest)

    proc.kill()
    proc.wait()

    raise RuntimeError(
        "Worker não confirmou criação do picking"
    )

print()
print("Picking criado.")
print("Agora o processo será terminado por SIGKILL.")

proc.wait()

print()
print("Worker exit code:", proc.returncode)

if proc.returncode != -signal.SIGKILL:
    raise RuntimeError(
        f"Crash não foi SIGKILL real. Exit={proc.returncode}"
    )

print("PASS — SIGKILL REAL CONFIRMADO")


# =========================================================
# AUDITORIA IMEDIATAMENTE APÓS O CRASH
# =========================================================

after_crash_pickings, after_crash_moves = audit(
    "POST-CRASH — ODOO PARCIAL"
)

if len(after_crash_pickings) != 1:
    raise AssertionError(
        f"Esperado exatamente 1 picking após crash; "
        f"obtido {len(after_crash_pickings)}"
    )

if len(after_crash_moves) != 0:
    raise AssertionError(
        f"Esperado 0 moves após crash; "
        f"obtido {len(after_crash_moves)}"
    )

picking = after_crash_pickings[0]

if picking.get("origin") != EVENT_ID:
    raise AssertionError("Origin do picking incorreto")

print()
print("PASS — ESTADO REMOTO PARCIAL CONFIRMADO")
print("       1 picking")
print("       0 moves")
print("       processo morreu antes de _create_move()")


# =========================================================
# RECOVERY — NOVO PROCESSO
# =========================================================

print()
print("=" * 70)
print("[2] NOVO PROCESSO — RECOVERY")
print("=" * 70)

recovery_code = r'''
import os
import json

from bridge.inventory_sync import InventoryOdooSync


EVENT = {
    "event_id": "MAZ-PARTIAL-CRASH-REAL-001",
    "seq_id": 970001,
    "node_id": "POS-MAPUTO-PARTIAL-CRASH",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": 3.0,
    "operation": "IN",
}

sync = InventoryOdooSync(
    engine=None,
    endpoint=os.environ["ODOO_URL"],
    timeout=30,
    api_key=os.environ["ODOO_API_KEY"],
    database=os.environ["ODOO_DB"],
)

result = sync._send(EVENT)

print("RECOVERY_RESULT")
print(json.dumps(result, indent=2, sort_keys=True))
'''

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
        f"Recovery falhou. Exit={recovery.returncode}"
    )

if "RECOVERY_RESULT" not in recovery.stdout:
    raise RuntimeError(
        "Recovery não retornou resultado"
    )

print("PASS — NOVO PROCESSO EXECUTOU RECOVERY")


# =========================================================
# AUDITORIA FINAL
# =========================================================

final_pickings, final_moves = audit(
    "FINAL — APÓS RECOVERY"
)

if len(final_pickings) != 1:
    raise AssertionError(
        f"Esperado 1 picking final; "
        f"obtido {len(final_pickings)}"
    )

if len(final_moves) != 1:
    raise AssertionError(
        f"Esperado 1 move final; "
        f"obtido {len(final_moves)}"
    )

final_picking = final_pickings[0]
final_move = final_moves[0]

if final_picking.get("state") != "done":
    raise AssertionError(
        f"Picking não terminou em done: "
        f"{final_picking.get('state')}"
    )

if float(final_move.get("product_uom_qty", 0)) != QUANTITY:
    raise AssertionError(
        f"Quantidade incorreta: "
        f"{final_move.get('product_uom_qty')}"
    )

picking_id = final_picking["id"]

move_picking = final_move.get("picking_id")

if isinstance(move_picking, list):
    move_picking_id = move_picking[0]
else:
    move_picking_id = move_picking

if move_picking_id != picking_id:
    raise AssertionError(
        "Move final não pertence ao picking original"
    )

print()
print("PASS — RECOVERY COMPLETO")
print("       1 picking")
print("       1 move")
print("       state = done")
print("       quantity =", QUANTITY)
print("       mesmo picking original")


# =========================================================
# REPLAY — TERCEIRA EXECUÇÃO
# =========================================================

print()
print("=" * 70)
print("[3] REPLAY — MESMO EVENT_ID")
print("=" * 70)

replay = subprocess.run(
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

print(replay.stdout)

if replay.returncode != 0:
    print("STDERR:")
    print(replay.stderr)

    raise RuntimeError(
        f"Replay falhou. Exit={replay.returncode}"
    )

replay_pickings, replay_moves = audit(
    "FINAL — APÓS REPLAY"
)

if len(replay_pickings) != 1:
    raise AssertionError(
        "Replay criou ou perdeu picking"
    )

if len(replay_moves) != 1:
    raise AssertionError(
        "Replay criou ou perdeu move"
    )

print()
print("PASS — REPLAY IDEMPOTENTE")
print("       nenhum picking adicional")
print("       nenhum move adicional")


# =========================================================
# RESULTADO
# =========================================================

print()
print("=" * 70)
print(" RESULTADO I-PARTIAL-CRASH-REAL")
print("=" * 70)

print()
print("Pré-crash:")
print("  Pickings = 0")
print("  Moves    = 0")

print()
print("Após SIGKILL:")
print("  Pickings = 1")
print("  Moves    = 0")

print()
print("Após recovery:")
print("  Pickings = 1")
print("  Moves    = 1")
print("  State    = done")
print("  Quantity =", QUANTITY)

print()
print("Após replay:")
print("  Pickings = 1")
print("  Moves    = 1")

print()
print("PASS — PARTIAL REMOTE STATE RECOVERY VALIDATED")
