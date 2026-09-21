import os
import threading

from bridge.inventory_engine import InventoryEventEngine


DB = "client/test_inventory_multi_instance.db"
PRODUCT = 1
LOCATION = "MAPUTO-STORE"


if os.path.exists(DB):
    os.remove(DB)


print("=" * 70)
print("mAZ — INVENTORY TEST I3: MULTI-INSTANCE SQLITE")
print("=" * 70)


# =========================================================
# SETUP — INSTÂNCIA INICIAL
# =========================================================

engine_seed = InventoryEventEngine(DB)

engine_seed.set_stock(
    product_id=PRODUCT,
    location_id=LOCATION,
    quantity=10
)

print()
print("[1] STOCK INICIAL")
print(f"    Produto: {PRODUCT}")
print(f"    Local: {LOCATION}")
print("    Stock: 10")

engine_seed.close()


# =========================================================
# DUAS INSTÂNCIAS INDEPENDENTES
# =========================================================

engine_a = InventoryEventEngine(DB)
engine_b = InventoryEventEngine(DB)

print()
print("=" * 70)
print("DUAS INSTÂNCIAS INDEPENDENTES")
print("=" * 70)

print("[POS-A] conexão SQLite independente criada")
print("[POS-B] conexão SQLite independente criada")


events = [
    (
        "POS-A",
        engine_a,
        {
            "event_id": "INV-I3-A",
            "seq_id": 1,
            "node_id": "POS-A",
            "product_id": PRODUCT,
            "location_id": LOCATION,
            "quantity": -7,
            "operation": "SALE",
        },
    ),
    (
        "POS-B",
        engine_b,
        {
            "event_id": "INV-I3-B",
            "seq_id": 2,
            "node_id": "POS-B",
            "product_id": PRODUCT,
            "location_id": LOCATION,
            "quantity": -7,
            "operation": "SALE",
        },
    ),
]


results = []
errors = []

barrier = threading.Barrier(2)


def worker(name, engine, event):

    try:
        print(f"[{name}] preparado")

        barrier.wait()

        print(
            f"[{name}] processando "
            f"{event['event_id']}"
        )

        result = engine.process_event(event)

        print(
            f"[{name}] resultado: "
            f"{result['status']}"
        )

        results.append(
            (name, result)
        )

    except Exception as exc:

        errors.append(
            (name, repr(exc))
        )

        print(
            f"[{name}] ERRO: "
            f"{repr(exc)}"
        )


threads = [
    threading.Thread(
        target=worker,
        args=item
    )
    for item in events
]


print()
print("======================================================================")
print("INICIANDO DOIS POS SIMULTANEAMENTE")
print("======================================================================")


for thread in threads:
    thread.start()


for thread in threads:
    thread.join()


# =========================================================
# AUDITORIA
# =========================================================

audit_engine = InventoryEventEngine(DB)

final_stock = audit_engine.get_stock(
    PRODUCT,
    LOCATION
)

event_count = audit_engine.event_count()
processed_count = audit_engine.processed_count()
conflict_count = audit_engine.conflict_count()

accepted = [
    result
    for _, result in results
    if result["status"] == "ACCEPTED"
]

conflicts = [
    result
    for _, result in results
    if result["status"] == "CONFLICT"
]


print()
print("=" * 70)
print("AUDITORIA FINAL")
print("=" * 70)

print(
    f"    Workers concluídos: "
    f"{len(results)}/2"
)

print(
    f"    Workers com erro: "
    f"{len(errors)}"
)

print(
    f"    ACCEPTED: "
    f"{len(accepted)}"
)

print(
    f"    CONFLICT: "
    f"{len(conflicts)}"
)

print(
    f"    Stock final: "
    f"{final_stock}"
)

print(
    f"    Eventos registados: "
    f"{event_count}"
)

print(
    f"    Eventos aceites: "
    f"{processed_count}"
)

print(
    f"    Conflitos registados: "
    f"{conflict_count}"
)


# =========================================================
# EVENT LOG
# =========================================================

print()
print("=" * 70)
print("EVENT LOG")
print("=" * 70)

for event in audit_engine.get_events():

    print(
        f"    {event['event_id']} | "
        f"{event['node_id']} | "
        f"{event['status']} | "
        f"stock "
        f"{event['previous_stock']} → "
        f"{event['new_stock']}"
    )


# =========================================================
# CONFLICT LOG
# =========================================================

print()
print("=" * 70)
print("CONFLICT LOG")
print("=" * 70)

for conflict in audit_engine.get_conflicts():

    print(
        f"    {conflict['event_id']} | "
        f"requested={conflict['requested_qty']} | "
        f"available={conflict['available_qty']} | "
        f"{conflict['reason']}"
    )


# =========================================================
# ASSERTIONS
# =========================================================

assert len(errors) == 0

assert len(results) == 2

assert len(accepted) == 1

assert len(conflicts) == 1

assert final_stock == 3

assert event_count == 2

assert processed_count == 1

assert conflict_count == 1

assert final_stock >= 0


# =========================================================
# FECHO
# =========================================================

engine_a.close()
engine_b.close()
audit_engine.close()


print()
print("=" * 70)
print("RESULTADO I3")
print("=" * 70)

print("Multi-instância SQLite: VALIDADA")
print("Duas conexões independentes: SIM")
print("Uma venda aceite: SIM")
print("Uma venda rejeitada por conflito: SIM")
print("Stock negativo: NÃO")
print("Stock final: 3")
print("Eventos persistidos: 2")
print("Conflitos persistidos: 1")
print()
print("✅ I3 PASSOU")
print("=" * 70)
