import os
import threading
import json
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

from bridge.inventory_engine import InventoryEventEngine
from bridge.inventory_sync import InventoryOdooSync


DB = "client/test_inventory_sync.db"
PORT = 18765


if os.path.exists(DB):
    os.remove(DB)


received = []


class MockOdooHandler(BaseHTTPRequestHandler):

    def do_POST(self):

        length = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        body = self.rfile.read(length)

        event = json.loads(
            body.decode("utf-8")
        )

        received.append(event)

        response = {
            "status": "OK",
            "event_id": event["event_id"]
        }

        payload = json.dumps(
            response
        ).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json"
        )
        self.send_header(
            "Content-Length",
            str(len(payload))
        )
        self.end_headers()

        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def start_server():

    server = HTTPServer(
        ("127.0.0.1", PORT),
        MockOdooHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()

    return server


print("=" * 70)
print("mAZ — INVENTORY TEST I6-A/B: OUTBOX + ODOO SYNC")
print("=" * 70)


# =========================================================
# LOCAL ENGINE
# =========================================================

engine = InventoryEventEngine(DB)

engine.set_stock(
    product_id=1,
    location_id="MAPUTO-STORE",
    quantity=10
)


event = {
    "event_id": "INV-I6-001",
    "seq_id": 6001,
    "node_id": "POS-MAPUTO-01",
    "product_id": 1,
    "location_id": "MAPUTO-STORE",
    "quantity": -3,
    "operation": "SALE"
}


# =========================================================
# I6-A — LOCAL COMMIT
# =========================================================

print()
print("[1] APLICANDO EVENTO LOCAL")

result = engine.process_event(event)

print(result)

assert result["status"] == "ACCEPTED"
assert engine.get_stock(
    1,
    "MAPUTO-STORE"
) == 7

assert engine.outbox_count(
    "PENDING"
) == 1

print()
print("[2] OUTBOX LOCAL")

print(
    engine.get_outbox_status()
)

assert engine.outbox_count(
    "PENDING"
) == 1


# =========================================================
# ODOO OFFLINE
# =========================================================

print()
print("[3] ODOO OFFLINE")

sync = InventoryOdooSync(
    engine,
    f"http://127.0.0.1:{PORT}/inventory"
)

offline_result = sync.sync_once()

print(offline_result)

assert len(offline_result) == 1
assert offline_result[0]["status"] == "PENDING"

assert engine.outbox_count(
    "PENDING"
) == 1

print(
    "Evento preservado durante blackout: SIM"
)


# =========================================================
# I6-B — ODOO ONLINE
# =========================================================

print()
print("[4] INICIANDO MOCK ODOO")

server = start_server()


print()
print("[5] RETRY")

online_result = sync.sync_once()

print(online_result)

assert len(online_result) == 1
assert online_result[0]["status"] == "SYNCED"


# =========================================================
# AUDITORIA
# =========================================================

print()
print("[6] AUDITORIA")

print(
    "Stock local:",
    engine.get_stock(
        1,
        "MAPUTO-STORE"
    )
)

print(
    "Outbox PENDING:",
    engine.outbox_count("PENDING")
)

print(
    "Outbox SYNCED:",
    engine.outbox_count("SYNCED")
)

print(
    "Eventos recebidos pelo Odoo:",
    len(received)
)


assert engine.get_stock(
    1,
    "MAPUTO-STORE"
) == 7

assert engine.outbox_count(
    "PENDING"
) == 0

assert engine.outbox_count(
    "SYNCED"
) == 1

assert len(received) == 1
assert received[0]["event_id"] == "INV-I6-001"


server.shutdown()
server.server_close()

engine.close()


print()
print("=" * 70)
print("RESULTADO I6-A/B")
print("=" * 70)

print("Evento persistido localmente: SIM")
print("Odoo offline preservou evento: SIM")
print("Retry após recuperação: SIM")
print("Evento sincronizado: SIM")
print("Duplicação local: NÃO")
print("Stock final: 7")
print("Outbox PENDING: 0")
print("Outbox SYNCED: 1")
print()
print("✅ I6-A/B PASSOU")
print("=" * 70)
