import os
import sqlite3
import tempfile

from bridge.inventory_engine import InventoryEventEngine


def make_event(event_id, seq_id, quantity, node_id="POS-MAPUTO-01"):
    return {
        "event_id": event_id,
        "seq_id": seq_id,
        "node_id": node_id,
        "product_id": 1,
        "location_id": "MAPUTO-STORE",
        "quantity": quantity,
        "operation": "SALE" if quantity < 0 else "RESTOCK",
    }


print("=" * 70)
print("mAZ — INVENTORY TEST I6: OFFLINE RECONCILIATION")
print("=" * 70)

db_path = tempfile.mktemp(prefix="maz_i6_", suffix=".db")

try:
    # ================================================================
    # 1. STOCK INICIAL
    # ================================================================

    engine = InventoryEventEngine(db_path)
    engine.set_stock(1, "MAPUTO-STORE", 20)

    print()
    print("[1] STOCK INICIAL")
    print("    Produto: 1")
    print("    Local: MAPUTO-STORE")
    print("    Stock: 20")

    # ================================================================
    # 2. EVENTOS GERADOS DURANTE OFFLINE
    # ================================================================

    events = [
        make_event("INV-I6-001", 1001, -3),
        make_event("INV-I6-002", 1002, -4),
        make_event("INV-I6-003", 1003, -2),
        make_event("INV-I6-004", 1004, -5),
    ]

    print()
    print("=" * 70)
    print("EVENTOS GERADOS OFFLINE")
    print("=" * 70)

    for event in events:
        print(
            f"    {event['event_id']} | "
            f"seq={event['seq_id']} | "
            f"qty={event['quantity']}"
        )

    # ================================================================
    # 3. RECONEXÃO FORA DE ORDEM
    # ================================================================

    reconciliation_order = [
        events[2],  # 1003
        events[0],  # 1001
        events[3],  # 1004
        events[1],  # 1002
    ]

    print()
    print("=" * 70)
    print("RECONEXÃO — EVENTOS FORA DE ORDEM")
    print("=" * 70)

    results = []

    for event in reconciliation_order:
        result = engine.process_event(event)
        results.append(result)

        print(
            f"    {event['event_id']} | "
            f"seq={event['seq_id']} | "
            f"resultado={result['status']}"
        )

    # ================================================================
    # 4. ESTADO APÓS RECONCILIAÇÃO
    # ================================================================

    stock = engine.get_stock(1, "MAPUTO-STORE")

    print()
    print("=" * 70)
    print("AUDITORIA APÓS RECONCILIAÇÃO")
    print("=" * 70)

    print(f"    ACCEPTED: {sum(r['status'] == 'ACCEPTED' for r in results)}")
    print(f"    DUPLICATE: {sum(r['status'] == 'DUPLICATE' for r in results)}")
    print(f"    CONFLICT: {sum(r['status'] == 'CONFLICT' for r in results)}")
    print(f"    Stock final: {stock}")

    # 20 - 3 - 4 - 2 - 5 = 6
    assert stock == 6.0
    assert sum(r["status"] == "ACCEPTED" for r in results) == 4
    assert sum(r["status"] == "CONFLICT" for r in results) == 0
    assert sum(r["status"] == "DUPLICATE" for r in results) == 0

    # ================================================================
    # 5. REPLAY DOS EVENTOS
    # ================================================================

    print()
    print("=" * 70)
    print("REPLAY DOS MESMOS EVENTOS")
    print("=" * 70)

    replay_results = []

    for event in events:
        result = engine.process_event(event)
        replay_results.append(result)

        print(
            f"    {event['event_id']} | "
            f"resultado={result['status']}"
        )

    replay_stock = engine.get_stock(1, "MAPUTO-STORE")

    print()
    print("=" * 70)
    print("AUDITORIA APÓS REPLAY")
    print("=" * 70)

    print(
        f"    DUPLICATE: "
        f"{sum(r['status'] == 'DUPLICATE' for r in replay_results)}"
    )
    print(f"    Stock final: {replay_stock}")

    assert replay_stock == 6.0
    assert all(
        r["status"] == "DUPLICATE"
        for r in replay_results
    )

    # ================================================================
    # 6. RESTART
    # ================================================================

    engine.close()

    print()
    print("=" * 70)
    print("RESTART DO INVENTORY ENGINE")
    print("=" * 70)

    engine = InventoryEventEngine(db_path)

    restart_stock = engine.get_stock(
        1,
        "MAPUTO-STORE"
    )

    print(f"    Stock recuperado: {restart_stock}")

    assert restart_stock == 6.0

    # ================================================================
    # 7. AUDITORIA FINAL
    # ================================================================

    audit = engine.audit(
        1,
        "MAPUTO-STORE"
    )

    print()
    print("=" * 70)
    print("RESULTADO I6")
    print("=" * 70)

    print("Eventos offline: 4")
    print("Reconciliação fora de ordem: SIM")
    print("Eventos aceites: 4")
    print("Conflitos: 0")
    print("Replay posterior: SIM")
    print("Replay duplicado: NÃO reaplicado")
    print("Restart preservou estado: SIM")
    print(f"Stock final: {restart_stock}")
    print(f"Eventos persistidos: {audit['events']}")

    assert audit["events"] == 4
    assert restart_stock == 6.0

    print()
    print("✅ I6 PASSOU")
    print("=" * 70)

finally:
    try:
        engine.close()
    except Exception:
        pass

    for suffix in ("", "-wal", "-shm"):
        path = db_path + suffix
        if os.path.exists(path):
            os.remove(path)
