import os
import sqlite3
import subprocess
import sys

DB = "client/test_inventory_crash_mid.db"

if os.path.exists(DB):
    os.remove(DB)

print("=" * 70)
print("mAZ — INVENTORY TEST I4-B: CRASH DENTRO DA TRANSAÇÃO")
print("=" * 70)


worker_code = r'''
import os
import sqlite3
import sys

DB = sys.argv[1]

con = sqlite3.connect(
    DB,
    timeout=30
)

con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA synchronous=FULL")

con.execute("""
CREATE TABLE IF NOT EXISTS inventory_stock (
    product_id INTEGER NOT NULL,
    location_id TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(product_id, location_id)
)
""")

con.execute("""
CREATE TABLE IF NOT EXISTS inventory_events (
    event_id TEXT PRIMARY KEY,
    quantity REAL NOT NULL
)
""")

con.execute("""
INSERT OR REPLACE INTO inventory_stock
(product_id, location_id, quantity, version, updated_at)
VALUES (1, 'MAPUTO-STORE', 10, 0, 1)
""")

con.commit()

print("[WORKER] Stock inicial = 10", flush=True)

# ---------------------------------------------------------
# INÍCIO DA TRANSAÇÃO
# ---------------------------------------------------------

con.execute("BEGIN IMMEDIATE")

print("[WORKER] BEGIN IMMEDIATE", flush=True)

# ---------------------------------------------------------
# ALTERAÇÃO DE STOCK
# ---------------------------------------------------------

con.execute("""
UPDATE inventory_stock
SET quantity = 3,
    version = version + 1
WHERE product_id = 1
  AND location_id = 'MAPUTO-STORE'
""")

print("[WORKER] STOCK ALTERADO PARA 3 — SEM COMMIT", flush=True)

# ---------------------------------------------------------
# CRASH
# ---------------------------------------------------------

print("[WORKER] CRASH AGORA", flush=True)

os.kill(os.getpid(), 9)
'''

print()
print("[1] INICIANDO WORKER")

proc = subprocess.Popen(
    [sys.executable, "-c", worker_code, DB],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

while True:
    line = proc.stdout.readline()

    if not line:
        break

    print("   ", line.strip())

    if "CRASH AGORA" in line:
        break

proc.wait()

print()
print("[2] PROCESSO TERMINADO")
print(f"    Exit code: {proc.returncode}")

assert proc.returncode == -9


# =========================================================
# RESTART / RECOVERY
# =========================================================

print()
print("[3] REABRINDO SQLITE APÓS CRASH")

con = sqlite3.connect(
    DB,
    timeout=30
)

con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA synchronous=FULL")

stock = con.execute("""
SELECT quantity
FROM inventory_stock
WHERE product_id = 1
  AND location_id = 'MAPUTO-STORE'
""").fetchone()[0]

events = con.execute("""
SELECT COUNT(*)
FROM inventory_events
""").fetchone()[0]

print(f"    Stock recuperado: {stock}")
print(f"    Eventos registados: {events}")


# =========================================================
# ATOMICIDADE
# =========================================================

print()
print("[4] VERIFICANDO ATOMICIDADE")

# O UPDATE ocorreu dentro de uma transação que nunca recebeu COMMIT.
# Portanto o SQLite deve fazer ROLLBACK durante recovery.

assert stock == 10
assert events == 0

print("    UPDATE não commitado foi revertido: SIM")
print("    Evento fantasma: NÃO")
print("    Stock corrompido: NÃO")

con.close()


# =========================================================
# SEGUNDA ABERTURA
# =========================================================

print()
print("[5] SEGUNDO RESTART / ESTABILIDADE")

con = sqlite3.connect(DB)

stock_again = con.execute("""
SELECT quantity
FROM inventory_stock
WHERE product_id = 1
  AND location_id = 'MAPUTO-STORE'
""").fetchone()[0]

print(f"    Stock após segundo restart: {stock_again}")

assert stock_again == 10

con.close()


print()
print("=" * 70)
print("RESULTADO I4-B")
print("=" * 70)

print("Crash durante transação: SIM")
print("Transação não commitada revertida: SIM")
print("Stock preservado: SIM")
print("Evento fantasma: NÃO")
print("Segundo restart estável: SIM")
print()
print("✅ I4-B PASSOU")
print("=" * 70)
