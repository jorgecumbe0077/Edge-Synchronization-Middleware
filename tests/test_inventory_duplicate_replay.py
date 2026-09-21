import os
import sqlite3
import threading

from bridge.inventory_engine import InventoryEventEngine


DB = "client/test_inventory_duplicate_replay.db"

if os.path.exists(DB):
    os.remove(DB)


print("=" * 70)
print("mAZ — INVENTORY TEST I5: CONCURRENT DUPLICATE REPLAY")
print("=" * 70)


# =========================================================
# PREPARAÇÃO
# =========================================================

engine = InventoryEventEngine(DB)

engine.set_stock(
    product_id=1,
    location_id="MAPUTO-STORE",
    quantity=10
)

engine.close()


event = {
    "event_id": "INV-I5-001",
    "seq_id": 5001,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": -3,
    "operation": "SALE"
}


print()
print("[1] STOCK INICIAL")
print("    Produto: 1")
print("    Local: MAPUTO-STORE")
print("    Stock: 10")


# =========================================================
# DUAS CONEXÕES INDEPENDENTES
# =========================================================

engine_a = InventoryEventEngine(DB)
engine_b = InventoryEventEngine(DB)

print()
print("=" * 70)
print("DUAS INSTÂNCIAS INDEPENDENTES")
print("=" * 70)

print("[POS-A] conexão SQLite independente criada")
print("[POS-B] conexão SQLite independente criada")


# =========================================================
# EXECUÇÃO CONCORRENTE
# =========================================================

barrier = threading.Barrier(2)

results = []
errors = []
lock = threading.Lock()


def worker(name, engine):
    try:
        print(f"[{name}] preparado")

        barrier.wait()

        print(
            f"[{name}] retransmitindo "
            f"{event['event_id']}"
        )

        result = engine.process_event(event)

        with lock:
            results.append((name, result))

        print(
            f"[{name}] resultado: "
            f"{result['status']}"
        )

    except Exception as exc:
        with lock:
            errors.append((name, exc))

        print(
            f"[{name}] ERRO: {exc}"
        )


print()
print("=" * 70)
print("SIMULANDO ACK PERDIDO + RETRANSMISSÃO CONCORRENTE")
print("=" * 70)

t1 = threading.Thread(
    target=worker,
    args=("POS-A", engine_a)
)

t2 = threading.Thread(
    target=worker,
    args=("POS-B", engine_b)
)

t1.start()
t2.start()

t1.join()
t2.join()


# =========================================================
# RESULTADOS
# =========================================================

print()
print("=" * 70)
print("RESULTADOS")
print("=" * 70)

print(f"Workers concluídos: {len(results)}/2")
print(f"Workers com erro: {len(errors)}")

assert len(results) == 2
assert len(errors) == 0


statuses = [
    result["status"]
    for _, result in results
]

accepted = statuses.count("ACCEPTED")
duplicates = statuses.count("DUPLICATE")

print(f"ACCEPTED: {accepted}")
print(f"DUPLICATE: {duplicates}")

assert accepted == 1
assert duplicates == 1


# =========================================================
# AUDITORIA FINAL
# =========================================================

audit_engine = InventoryEventEngine(DB)

final_stock = audit_engine.get_stock(
    1,
    "MAPUTO-STORE"
)

event_count = audit_engine.event_count()
processed_count = audit_engine.processed_count()

print()
print("=" * 70)
print("AUDITORIA FINAL")
print("=" * 70)

print(f"Stock final: {final_stock}")
print(f"Eventos registados: {event_count}")
print(f"Eventos aceites: {processed_count}")

assert final_stock == 7
assert event_count == 1
assert processed_count == 1


# =========================================================
# SQLITE — VERIFICAÇÃO DIRETA
# =========================================================

con = sqlite3.connect(DB)

rows = con.execute("""
SELECT
    event_id,
    seq_id,
    node_id,
    quantity,
    status,
    previous_stock,
    new_stock
FROM inventory_events
ORDER BY rowid
""").fetchall()

print()
print("=" * 70)
print("EVENT LOG SQLITE")
print("=" * 70)

for row in rows:
    print(
        f"    event={row[0]} | "
        f"seq={row[1]} | "
        f"node={row[2]} | "
        f"qty={row[3]} | "
        f"status={row[4]} | "
        f"{row[5]} → {row[6]}"
    )

assert len(rows) == 1
assert rows[0][0] == "INV-I5-001"
assert rows[0][4] == "ACCEPTED"

con.close()

engine_a.close()
engine_b.close()
audit_engine.close()


print()
print("=" * 70)
print("RESULTADO I5")
print("=" * 70)

print("Duas conexões independentes: SIM")
print("Mesmo event_id retransmitido: SIM")
print("Execução concorrente: SIM")
print("Venda aplicada duas vezes: NÃO")
print("Uma operação ACCEPTED: SIM")
print("Uma retransmissão DUPLICATE: SIM")
print("Stock final: 7")
print("Registo único no SQLite: SIM")
print()
print("✅ I5 PASSOU")
print("=" * 70)
