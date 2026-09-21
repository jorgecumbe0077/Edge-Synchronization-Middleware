import json
import urllib.error
import urllib.request


class InventorySyncError(Exception):
    pass


class InventoryOdooSync:

    def __init__(
        self,
        engine,
        endpoint,
        timeout=10,
        api_key=None,
        database=None
    ):
        self.engine = engine
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self.database = database

    # =========================================================
    # ODOO JSON-2 RPC
    # =========================================================

    def _call(self, model, method, payload):

        url = f"{self.endpoint}/json/2/{model}/{method}"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "mAZ-Inventory-Sync/1.0",
        }

        if self.api_key:
            headers["Authorization"] = (
                f"bearer {self.api_key}"
            )

        if self.database:
            headers["X-Odoo-Database"] = self.database

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:

            with urllib.request.urlopen(
                request,
                timeout=self.timeout
            ) as response:

                body = response.read().decode(
                    "utf-8",
                    errors="replace"
                )

                if response.status < 200 or response.status >= 300:
                    raise InventorySyncError(
                        f"HTTP {response.status}: {body}"
                    )

                if not body:
                    return None

                result = json.loads(body)

                if isinstance(result, dict) and "error" in result:
                    raise InventorySyncError(
                        str(result["error"])
                    )

                return result

        except urllib.error.HTTPError as exc:

            body = exc.read().decode(
                "utf-8",
                errors="replace"
            )

            raise InventorySyncError(
                f"HTTP {exc.code}: {body}"
            ) from exc

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError
        ) as exc:

            raise InventorySyncError(
                f"NETWORK_ERROR: {exc}"
            ) from exc

    # =========================================================
    # LOCATION MAPPING
    # =========================================================

    def _resolve_locations(self, event):

        operation = event["operation"]

        if operation == "IN":

            return {
                "picking_type_id": 1,
                "location_id": 1,
                "location_dest_id": 5,
            }

        if operation == "OUT":

            return {
                "picking_type_id": 2,
                "location_id": 5,
                "location_dest_id": 2,
            }

        raise InventorySyncError(
            f"Unsupported inventory operation: {operation}"
        )

    # =========================================================
    # CREATE PICKING
    # =========================================================

    def _create_picking(self, event):

        locations = self._resolve_locations(event)

        result = self._call(
            "stock.picking",
            "create",
            {
                "vals_list": [
                    {
                        "picking_type_id": locations["picking_type_id"],
                        "location_id": locations["location_id"],
                        "location_dest_id": locations["location_dest_id"],
                        "origin": event["event_id"],
                    }
                ]
            }
        )

        if not isinstance(result, list) or not result:
            raise InventorySyncError(
                f"Invalid stock.picking.create response: {result}"
            )

        picking_id = result[0]

        return picking_id, locations

    # =========================================================
    # CREATE MOVE
    # =========================================================

    def _create_move(
        self,
        event,
        picking_id,
        locations
    ):

        quantity = abs(float(event["quantity"]))

        result = self._call(
            "stock.move",
            "create",
            {
                "vals_list": [
                    {
                        "product_id": int(event["product_id"]),
                        "product_uom_qty": quantity,
                        "product_uom": 1,
                        "location_id": locations["location_id"],
                        "location_dest_id": locations["location_dest_id"],
                        "picking_id": picking_id,
                        "picking_type_id": locations["picking_type_id"],
                        "company_id": 1,
                        "origin": event["event_id"],
                    }
                ]
            }
        )

        if not isinstance(result, list) or not result:
            raise InventorySyncError(
                f"Invalid stock.move.create response: {result}"
            )

        return result[0]

    # =========================================================
    # CONFIRM
    # =========================================================

    def _confirm(self, picking_id):

        return self._call(
            "stock.picking",
            "action_confirm",
            {
                "ids": [picking_id]
            }
        )

    # =========================================================
    # ASSIGN
    # =========================================================

    def _assign(self, picking_id):

        return self._call(
            "stock.picking",
            "action_assign",
            {
                "ids": [picking_id]
            }
        )

    # =========================================================
    # VALIDATE
    # =========================================================

    def _validate(self, picking_id):

        return self._call(
            "stock.picking",
            "button_validate",
            {
                "ids": [picking_id]
            }
        )

    # =========================================================
    # VERIFY
    # =========================================================

    def _verify(self, event, picking_id, move_id):

        picking = self._call(
            "stock.picking",
            "search_read",
            {
                "domain": [
                    ["id", "=", picking_id]
                ],
                "fields": [
                    "id",
                    "name",
                    "origin",
                    "state",
                    "move_ids",
                    "date_done",
                ],
                "limit": 1,
            }
        )

        moves = self._call(
            "stock.move",
            "search_read",
            {
                "domain": [
                    ["id", "=", move_id]
                ],
                "fields": [
                    "id",
                    "state",
                    "product_id",
                    "product_uom_qty",
                    "quantity",
                    "location_id",
                    "location_dest_id",
                    "picking_id",
                ],
                "limit": 1,
            }
        )

        if not picking:
            raise InventorySyncError(
                f"Odoo picking {picking_id} not found"
            )

        if not moves:
            raise InventorySyncError(
                f"Odoo move {move_id} not found"
            )

        picking_data = picking[0]
        move_data = moves[0]

        if picking_data["state"] != "done":
            raise InventorySyncError(
                "Odoo picking not completed: "
                f"{picking_data['state']}"
            )

        if move_data["state"] != "done":
            raise InventorySyncError(
                "Odoo move not completed: "
                f"{move_data['state']}"
            )

        return {
            "picking": picking_data,
            "move": move_data,
        }

    # =========================================================
    # IDEMPOTENCY / RECOVERY
    # =========================================================

    def _find_existing_picking(self, event):

        result = self._call(
            "stock.picking",
            "search_read",
            {
                "domain": [
                    ["origin", "=", event["event_id"]]
                ],
                "fields": [
                    "id",
                    "name",
                    "origin",
                    "state",
                    "move_ids",
                    "date_done",
                ],
                "limit": 10,
            }
        )

        if not result:
            return None

        # O mesmo event_id deve identificar uma única operação.
        if len(result) > 1:
            raise InventorySyncError(
                "IDEMPOTENCY_VIOLATION: "
                f"multiple pickings found for event "
                f"{event['event_id']}"
            )

        return result[0]

    # =========================================================
    # RECOVER PARTIAL PICKING
    # =========================================================

    def _recover_existing_picking(self, event, existing):

        picking_id = existing["id"]
        state = existing.get("state")
        move_ids = existing.get("move_ids") or []

        # ---------------------------------------------------------
        # CASO 1 — PICKING JÁ CONCLUÍDO
        #
        # Não executar confirm/assign/validate novamente.
        # Apenas verificar o objeto existente.
        # ---------------------------------------------------------

        if state == "done":

            if not move_ids:
                raise InventorySyncError(
                    "IDEMPOTENCY_RECOVERY_ERROR: "
                    f"done picking {picking_id} has no move "
                    f"for event {event['event_id']}"
                )

            move_id = move_ids[0]

            verification = self._verify(
                event,
                picking_id,
                move_id
            )

            return {
                "status": "DONE",
                "event_id": event["event_id"],
                "picking_id": picking_id,
                "move_id": move_id,
                "operation": event["operation"],
                "quantity": abs(float(event["quantity"])),
                "recovered": True,
                "verification": verification,
            }

        # ---------------------------------------------------------
        # RESOLVER MOVE
        # ---------------------------------------------------------

        if move_ids:

            if len(move_ids) > 1:
                raise InventorySyncError(
                    "IDEMPOTENCY_VIOLATION: "
                    f"multiple moves found for picking "
                    f"{picking_id}"
                )

            move_id = move_ids[0]

        # ---------------------------------------------------------
        # CASO 2 — PICKING ÓRFÃO / SEM MOVE
        # ---------------------------------------------------------

        else:

            locations = self._resolve_locations(event)

            move_id = self._create_move(
                event,
                picking_id,
                locations
            )

        # ---------------------------------------------------------
        # RECOVERY STATE-AWARE
        # ---------------------------------------------------------

        if state == "draft":

            self._confirm(
                picking_id
            )

            self._assign(
                picking_id
            )

            self._validate(
                picking_id
            )

        elif state == "confirmed":

            self._assign(
                picking_id
            )

            self._validate(
                picking_id
            )

        elif state == "assigned":

            self._validate(
                picking_id
            )

        else:

            raise InventorySyncError(
                "IDEMPOTENCY_RECOVERY_ERROR: "
                f"unsupported picking state '{state}' "
                f"for picking {picking_id}"
            )

        # ---------------------------------------------------------
        # VERIFICAÇÃO FINAL
        # ---------------------------------------------------------

        verification = self._verify(
            event,
            picking_id,
            move_id
        )

        return {
            "status": "DONE",
            "event_id": event["event_id"],
            "picking_id": picking_id,
            "move_id": move_id,
            "operation": event["operation"],
            "quantity": abs(float(event["quantity"])),
            "recovered": True,
            "verification": verification,
        }

    # =========================================================
    # SEND EVENT
    # =========================================================

    def _send(self, event):

        # ---------------------------------------------------------
        # IDEMPOTENCY CHECK
        #
        # Se o evento já chegou ao Odoo numa tentativa anterior,
        # não criar um novo picking.
        # ---------------------------------------------------------

        existing = self._find_existing_picking(event)

        if existing is not None:

            return self._recover_existing_picking(
                event,
                existing
            )

        # ---------------------------------------------------------
        # NORMAL FIRST ATTEMPT
        # ---------------------------------------------------------

        picking_id, locations = self._create_picking(
            event
        )

        move_id = self._create_move(
            event,
            picking_id,
            locations
        )

        self._confirm(
            picking_id
        )

        self._assign(
            picking_id
        )

        self._validate(
            picking_id
        )

        verification = self._verify(
            event,
            picking_id,
            move_id
        )

        return {
            "status": "DONE",
            "event_id": event["event_id"],
            "picking_id": picking_id,
            "move_id": move_id,
            "operation": event["operation"],
            "quantity": abs(float(event["quantity"])),
            "recovered": False,
            "verification": verification,
        }

    # =========================================================
    # OUTBOX SYNC
    # =========================================================

    def sync_once(self, limit=100):

        pending = self.engine.get_pending_outbox(
            limit=limit
        )

        results = []

        for item in pending:

            event_id = item["event_id"]

            try:

                response = self._send(
                    item["payload"]
                )

                self.engine.mark_outbox_synced(
                    event_id
                )

                results.append({
                    "event_id": event_id,
                    "status": "SYNCED",
                    "response": response,
                })

            except Exception as exc:

                self.engine.mark_outbox_attempt(
                    event_id,
                    str(exc)
                )

                results.append({
                    "event_id": event_id,
                    "status": "PENDING",
                    "error": str(exc),
                })

        return results
