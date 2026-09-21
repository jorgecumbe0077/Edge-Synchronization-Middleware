import os
import threading

from bridge.inventory_engine import InventoryEventEngine


DB = "client/test_inventory_concurrency.db"

if os.path.exists(DB):
    os.remove(DB)


print("=" * 70)
print("mAZ — INVENTORY TEST I2: CONCORRÊNCIA DE STOCK")
print("=" * 70)


engine = InventoryEventEngine(DB)

# ---------------------------------------------------------
# STOCK INICIAL
# ---------------------------------------------------------

engine.set_stock(
    product_id=1,
    location_id="MAPUTO-STORE",
    quantity=10
)

print()
print("[1] STOCK INICIAL")
print("    Produto: 1")
print("    Local: MAPUTO-STORE")
print("    Stock: 10")


# ---------------------------------------------------------
# DOIS EVENTOS CONCORRENTES
# ---------------------------------------------------------

events = [
    {
        "event_id": "INV-I2-A",
        "seq_id": 1001,
        "node_id": "POS-A",
        "product_id": 1,
        "location_id": "MAPUTO-STORE",
        "quantity": -7,
        "operation": "SALE"
    },
    {
        "event_id": "INV-I2-B",
        "seq_id": 1002,
        "node_id": "POS-B",
        "product_id": 1,
        "location_id": "MAPUTO-STORE",
        "quantity": -7,
        "operation": "SALE"
    }
]


results = {}
errors = {}

barrier = threading.Barrier(2)


def worker(name, event):

    try:
        print(f"[{name}] preparado")

        barrier.wait()

        print(f"[{name}] processando evento {event['event_id']}")

        result = engine.process_event(event)

        results[name] = result

        print(
            f"[{name}] resultado: "
            f"{result['status']}"
        )

    except Exception as exc:

        errors[name] = repr(exc)

        print(
            f"[{name}] ERRO: {exc}"
        )


# ---------------------------------------------------------
# WORKERS
# ---------------------------------------------------------

threads = [
    threading.Thread(
        target=worker,
        args=("POS-A", events[0])
    ),
    threading.Thread(
        target=worker,
        args=("POS-B", events[1])
    )
]


print()
print("=" * 70)
print("INICIANDO DOIS POS SIMULTANEAMENTE")
print("=" * 70)


for thread in threads:
    thread.start()

for thread in threads:
    thread.join()


# ---------------------------------------------------------
# AUDITORIA
# ---------------------------------------------------------

final_stock = engine.get_stock(
    1,
    "MAPUTO-STORE"
)

accepted = [
    name
    for name, result in results.items()
    if result["status"] == "ACCEPTED"
]

conflicts = [
    name
    for name, result in results.items()
    if result["status"] == "CONFLICT"
]


print()
print("=" * 70)
print("AUDITORIA")
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
    f"{engine.event_count()}"
)

print(
    f"    Conflitos registados: "
    f"{engine.conflict_count()}"
)


# ---------------------------------------------------------
# ASSERTIONS
# ---------------------------------------------------------

assert len(results) == 2, (
    "Os dois workers deveriam terminar."
)

assert len(errors) == 0, (
    f"Não deveriam existir erros: {errors}"
)

assert len(accepted) == 1, (
    "Exatamente uma venda deveria ser aceite."
)

assert len(conflicts) == 1, (
    "Exatamente uma venda deveria entrar em conflito."
)

assert final_stock == 3, (
    f"Stock incorreto: {final_stock}"
)

assert engine.event_count() == 2, (
    "Os dois eventos deveriam estar registados."
)

assert engine.conflict_count() == 1, (
    "Deveria existir exatamente um conflito."
)


# ---------------------------------------------------------
# EVENTOS
# ---------------------------------------------------------

print()
print("=" * 70)
print("EVENT LOG")
print("=" * 70)

for event in engine.get_events():

    print(
        f"    {event['event_id']} | "
        f"{event['node_id']} | "
        f"{event['status']} | "
        f"stock "
        f"{event['previous_stock']} → "
        f"{event['new_stock']}"
    )


print()
print("=" * 70)
print("CONFLICT LOG")
print("=" * 70)

for conflict in engine.get_conflicts():

    print(
        f"    {conflict['event_id']} | "
        f"requested={conflict['requested_qty']} | "
        f"available={conflict['available_qty']} | "
        f"{conflict['reason']}"
    )


engine.close()


print()
print("=" * 70)
print("RESULTADO I2")
print("=" * 70)

print("Concorrência de stock: VALIDADA")
print("Uma venda aceite: SIM")
print("Uma venda rejeitada por conflito: SIM")
print("Stock negativo: NÃO")
print("Stock final: 3")
print("Eventos persistidos: 2")
print("Conflitos persistidos: 1")
print()
print("✅ I2 PASSOU")
print("=" * 70)
