import json
import os
import urllib.request


class ReconciliationError(Exception):
    pass


class OdooReadClient:
    def __init__(
        self,
        endpoint,
        api_key,
        database,
        timeout=10
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.database = database
        self.timeout = timeout

    def search_read(
        self,
        model,
        domain,
        fields,
        limit=100
    ):
        url = f"{self.endpoint}/json/2/{model}/search_read"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "mAZ-Reconciliation/1.0",
            "Authorization": f"bearer {self.api_key}",
            "X-Odoo-Database": self.database,
        }

        payload = {
            "domain": domain,
            "fields": fields,
            "limit": limit,
        }

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

                if not body:
                    return []

                result = json.loads(body)

                if isinstance(result, dict) and "error" in result:
                    raise ReconciliationError(str(result["error"]))

                if not isinstance(result, list):
                    raise ReconciliationError(
                        f"Unexpected Odoo response: "
                        f"{type(result).__name__}"
                    )

                return result

        except Exception as exc:
            if isinstance(exc, ReconciliationError):
                raise

            raise ReconciliationError(
                f"Odoo read failed: {exc}"
            ) from exc


class ReconciliationEngine:
    def __init__(
        self,
        engine,
        odoo_client
    ):
        self.engine = engine
        self.odoo = odoo_client
        self._last_evidence = None

    def _get_edge_evidence(self, event_id):
        events = [
            event
            for event in self.engine.get_events()
            if event["event_id"] == event_id
        ]

        if len(events) > 1:
            raise ReconciliationError(
                f"Multiple Edge events found for {event_id}"
            )

        event = events[0] if events else None
        outbox = self.engine.get_outbox_event(event_id)

        return {
            "event": event,
            "outbox": outbox,
        }

    def _get_odoo_evidence(self, event_id):
        events = self.odoo.search_read(
            "maz.inventory.event",
            [["event_id", "=", event_id]],
            [
                "id",
                "event_id",
                "node_id",
                "seq_id",
                "operation",
                "product_id",
                "quantity",
                "status",
                "picking_id",
                "move_id",
            ],
            limit=10,
        )

        pickings = self.odoo.search_read(
            "stock.picking",
            [["origin", "=", event_id]],
            [
                "id",
                "name",
                "origin",
                "state",
                "move_ids",
                "picking_type_id",
                "location_id",
                "location_dest_id",
                "date_done",
            ],
            limit=10,
        )

        moves = self.odoo.search_read(
            "stock.move",
            [["origin", "=", event_id]],
            [
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
            limit=10,
        )

        return {
            "events": events,
            "pickings": pickings,
            "moves": moves,
        }

    @staticmethod
    def _m2o_id(value):
        if isinstance(value, (list, tuple)) and value:
            return value[0]

        if isinstance(value, int):
            return value

        return None

    @staticmethod
    def _float_equal(left, right, tolerance=1e-9):
        if left is None or right is None:
            return False

        try:
            return abs(float(left) - float(right)) <= tolerance
        except (TypeError, ValueError):
            return False

    def _build_evidence(
        self,
        event,
        outbox,
        odoo_events,
        pickings,
        moves
    ):
        evidence = {
            "identity": {
                "event_id_match": None,
                "node_id_match": None,
                "seq_id_match": None,
            },
            "operation": {
                "operation_match": None,
                "quantity_match": None,
            },
            "product": {
                "product_match": None,
            },
            "physical": {
                "picking_count": len(pickings),
                "move_count": len(moves),
                "picking_done": None,
                "move_done": None,
            },
            "routing": {
                "picking_type_match": None,
                "location_match": None,
            },
            "links": {
                "event_picking_match": None,
                "event_move_match": None,
                "picking_move_match": None,
                "picking_move_ids_link": None,
            },
            "mismatches": [],
        }

        if event is None:
            evidence["mismatches"].append(
                "edge_event_missing"
            )
            return evidence

        if outbox is None:
            evidence["mismatches"].append(
                "edge_outbox_missing"
            )
            return evidence

        if len(odoo_events) == 1:
            odoo_event = odoo_events[0]

            evidence["identity"]["event_id_match"] = (
                odoo_event.get("event_id")
                == event.get("event_id")
            )

            evidence["identity"]["node_id_match"] = (
                odoo_event.get("node_id")
                == event.get("node_id")
            )

            evidence["identity"]["seq_id_match"] = (
                odoo_event.get("seq_id")
                == event.get("seq_id")
            )

            evidence["operation"]["operation_match"] = (
                odoo_event.get("operation")
                == event.get("operation")
            )

            edge_quantity = event.get("quantity")
            odoo_quantity = odoo_event.get("quantity")

            evidence["operation"]["quantity_match"] = (
                self._float_equal(
                    abs(float(edge_quantity)),
                    abs(float(odoo_quantity)),
                )
                if edge_quantity is not None
                and odoo_quantity is not None
                else False
            )

            evidence["product"]["product_match"] = (
                self._m2o_id(odoo_event.get("product_id"))
                == event.get("product_id")
            )

        elif len(odoo_events) > 1:
            evidence["mismatches"].append(
                "multiple_odoo_audit_events"
            )

        if len(pickings) == 1:
            picking = pickings[0]

            evidence["physical"]["picking_done"] = (
                picking.get("state") == "done"
            )

            evidence["routing"]["location_match"] = (
                self._routing_matches(
                    event,
                    picking
                )
            )

            expected_picking_type = {
                "IN": 1,
                "OUT": 2,
            }.get(event.get("operation"))

            actual_picking_type = self._m2o_id(
                picking.get("picking_type_id")
            )

            if expected_picking_type is not None:
                evidence["routing"]["picking_type_match"] = (
                    actual_picking_type
                    == expected_picking_type
                )

                if not evidence["routing"]["picking_type_match"]:
                    evidence["mismatches"].append(
                        "picking_type_mismatch"
                    )

            evidence["links"]["picking_move_ids_link"] = (
                len(moves) == 1
                and moves[0].get("id")
                in (picking.get("move_ids") or [])
            )

        if len(moves) == 1:
            move = moves[0]

            evidence["physical"]["move_done"] = (
                move.get("state") == "done"
            )

            evidence["links"]["picking_move_match"] = (
                len(pickings) == 1
                and self._m2o_id(move.get("picking_id"))
                == pickings[0].get("id")
            )

            evidence["product"]["product_match"] = (
                (
                    evidence["product"]["product_match"]
                    if evidence["product"]["product_match"]
                    is not None
                    else self._m2o_id(
                        move.get("product_id")
                    )
                    == event.get("product_id")
                )
            )

            edge_quantity = event.get("quantity")
            move_quantity = move.get("quantity")
            move_planned = move.get("product_uom_qty")

            move_quantity_match = (
                self._float_equal(
                    abs(float(edge_quantity)),
                    abs(float(move_quantity)),
                )
                if edge_quantity is not None
                and move_quantity is not None
                else False
            )

            planned_quantity_match = (
                self._float_equal(
                    abs(float(edge_quantity)),
                    abs(float(move_planned)),
                )
                if edge_quantity is not None
                and move_planned is not None
                else False
            )

            evidence["operation"]["quantity_match"] = (
                evidence["operation"]["quantity_match"]
                if evidence["operation"]["quantity_match"]
                is not None
                else (
                    move_quantity_match
                    and planned_quantity_match
                )
            )

        if len(pickings) == 1:
            picking = pickings[0]

            if (
                picking.get("origin")
                != event.get("event_id")
            ):
                evidence["mismatches"].append(
                    "picking_origin_mismatch"
                )

        if len(moves) == 1:
            move = moves[0]

            if (
                move.get("origin")
                != event.get("event_id")
            ):
                evidence["mismatches"].append(
                    "move_origin_mismatch"
                )

        if len(pickings) == 1:
            if pickings[0].get("state") != "done":
                evidence["mismatches"].append(
                    "picking_not_done"
                )

        if len(moves) == 1:
            if moves[0].get("state") != "done":
                evidence["mismatches"].append(
                    "move_not_done"
                )

        if len(pickings) == 1 and len(moves) == 1:
            picking = pickings[0]
            move = moves[0]

            if move.get("id") not in (
                picking.get("move_ids") or []
            ):
                evidence["mismatches"].append(
                    "picking_move_ids_mismatch"
                )

            if self._m2o_id(
                move.get("picking_id")
            ) != picking.get("id"):
                evidence["mismatches"].append(
                    "move_picking_link_mismatch"
                )

        return evidence

    def _finalize_physical_consistency_checks(
        self,
        event,
        picking,
        move,
        evidence
    ):
        """
        Validate the physical Odoo effect independently of the
        maz.inventory.event audit trail.

        This is Track B reconciliation:
        Edge expectation -> Picking -> Move.
        """

        if event is None or picking is None or move is None:
            return

        event_id = event.get("event_id")

        if picking.get("origin") != event_id:
            if "picking_origin_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "picking_origin_mismatch"
                )

        if move.get("origin") != event_id:
            if "move_origin_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "move_origin_mismatch"
                )

        if picking.get("state") != "done":
            if "picking_not_done" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "picking_not_done"
                )

        if move.get("state") != "done":
            if "move_not_done" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "move_not_done"
                )

        if (
            move.get("id")
            not in (picking.get("move_ids") or [])
        ):
            if "picking_move_ids_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "picking_move_ids_mismatch"
                )

        if (
            self._m2o_id(move.get("picking_id"))
            != picking.get("id")
        ):
            if "move_picking_link_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "move_picking_link_mismatch"
                )

        product_match = (
            self._m2o_id(move.get("product_id"))
            == event.get("product_id")
        )

        evidence["product"]["product_match"] = product_match

        if not product_match:
            if "product_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "product_mismatch"
                )

        edge_quantity = event.get("quantity")
        move_quantity = move.get("quantity")
        move_planned = move.get("product_uom_qty")

        quantity_ok = (
            edge_quantity is not None
            and move_quantity is not None
            and move_planned is not None
            and self._float_equal(
                abs(float(edge_quantity)),
                abs(float(move_quantity)),
            )
            and self._float_equal(
                abs(float(edge_quantity)),
                abs(float(move_planned)),
            )
        )

        evidence["operation"]["quantity_match"] = quantity_ok

        if not quantity_ok:
            if "quantity_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "quantity_mismatch"
                )

        routing_ok = self._routing_matches(
            event,
            picking
        )

        evidence["routing"]["location_match"] = routing_ok

        if not routing_ok:
            if "routing_mismatch" not in evidence["mismatches"]:
                evidence["mismatches"].append(
                    "routing_mismatch"
                )

        expected_picking_type = {
            "IN": 1,
            "OUT": 2,
        }.get(event.get("operation"))

        actual_picking_type = self._m2o_id(
            picking.get("picking_type_id")
        )

        if expected_picking_type is not None:
            picking_type_ok = (
                actual_picking_type
                == expected_picking_type
            )

            evidence["routing"]["picking_type_match"] = (
                picking_type_ok
            )

            if not picking_type_ok:
                if "picking_type_mismatch" not in evidence["mismatches"]:
                    evidence["mismatches"].append(
                        "picking_type_mismatch"
                    )

    def _routing_matches(self, event, picking):
        operation = event.get("operation")

        location_id = picking.get("location_id")
        destination_id = picking.get(
            "location_dest_id"
        )

        location_id = self._m2o_id(location_id)
        destination_id = self._m2o_id(
            destination_id
        )

        if operation == "OUT":
            return (
                location_id == 5
                and destination_id == 2
            )

        if operation == "IN":
            return (
                location_id == 1
                and destination_id == 5
            )

        return False

    def _finalize_consistency_checks(
        self,
        event,
        odoo_event,
        picking,
        move,
        evidence
    ):
        if event is None:
            return

        if picking.get("origin") != event.get("event_id"):
            evidence["mismatches"].append(
                "picking_origin_mismatch"
            )

        if move.get("origin") != event.get("event_id"):
            evidence["mismatches"].append(
                "move_origin_mismatch"
            )

        if odoo_event.get("event_id") != event.get(
            "event_id"
        ):
            evidence["mismatches"].append(
                "event_id_mismatch"
            )

        if odoo_event.get("status") != "done":
            evidence["mismatches"].append(
                "odoo_event_not_done"
            )

        if picking.get("state") != "done":
            evidence["mismatches"].append(
                "picking_not_done"
            )

        if move.get("state") != "done":
            evidence["mismatches"].append(
                "move_not_done"
            )

        if (
            self._m2o_id(
                odoo_event.get("picking_id")
            )
            != picking.get("id")
        ):
            evidence["links"]["event_picking_match"] = False
            evidence["mismatches"].append(
                "event_picking_link_mismatch"
            )
        else:
            evidence["links"]["event_picking_match"] = True

        if (
            self._m2o_id(
                odoo_event.get("move_id")
            )
            != move.get("id")
        ):
            evidence["links"]["event_move_match"] = False
            evidence["mismatches"].append(
                "event_move_link_mismatch"
            )
        else:
            evidence["links"]["event_move_match"] = True

        if (
            self._m2o_id(move.get("product_id"))
            != event.get("product_id")
        ):
            evidence["product"]["product_match"] = False
            evidence["mismatches"].append(
                "product_mismatch"
            )
        else:
            evidence["product"]["product_match"] = True

        edge_quantity = event.get("quantity")
        move_quantity = move.get("quantity")
        move_planned = move.get("product_uom_qty")

        quantity_ok = (
            edge_quantity is not None
            and move_quantity is not None
            and move_planned is not None
            and self._float_equal(
                abs(float(edge_quantity)),
                abs(float(move_quantity)),
            )
            and self._float_equal(
                abs(float(edge_quantity)),
                abs(float(move_planned)),
            )
        )

        evidence["operation"]["quantity_match"] = quantity_ok

        if not quantity_ok:
            evidence["mismatches"].append(
                "quantity_mismatch"
            )

        if (
            odoo_event.get("operation")
            != event.get("operation")
        ):
            evidence["operation"]["operation_match"] = False
            evidence["mismatches"].append(
                "operation_mismatch"
            )
        else:
            evidence["operation"]["operation_match"] = True

        if (
            odoo_event.get("node_id")
            != event.get("node_id")
        ):
            evidence["identity"]["node_id_match"] = False
            evidence["mismatches"].append(
                "node_id_mismatch"
            )
        else:
            evidence["identity"]["node_id_match"] = True

        if (
            odoo_event.get("seq_id")
            != event.get("seq_id")
        ):
            evidence["identity"]["seq_id_match"] = False
            evidence["mismatches"].append(
                "seq_id_mismatch"
            )
        else:
            evidence["identity"]["seq_id_match"] = True

        routing_ok = self._routing_matches(
            event,
            picking
        )

        evidence["routing"]["location_match"] = routing_ok

        if not routing_ok:
            evidence["mismatches"].append(
                "routing_mismatch"
            )

    def classify(
        self,
        edge,
        odoo_events,
        pickings,
        moves
    ):
        event = edge.get("event")
        outbox = edge.get("outbox")

        edge_exists = event is not None
        outbox_exists = outbox is not None

        evidence = self._build_evidence(
            event,
            outbox,
            odoo_events,
            pickings,
            moves
        )

        self._last_evidence = evidence

        if not edge_exists:
            return "EDGE_EVENT_MISSING"

        if not outbox_exists:
            return "EDGE_OUTBOX_MISSING"

        if len(pickings) > 1 or len(moves) > 1:
            evidence["mismatches"].append(
                "duplicate_remote_effect"
            )
            return "DUPLICATE_REMOTE"

        if (
            outbox["status"] == "PENDING"
            and len(odoo_events) == 0
            and len(pickings) == 0
            and len(moves) == 0
        ):
            return "PENDING_EXPECTED"

        if (
            len(pickings) == 1
            and len(moves) == 0
        ):
            return "PARTIAL_REMOTE"

        if (
            len(odoo_events) == 0
            and len(pickings) == 1
            and len(moves) == 1
        ):
            self._finalize_physical_consistency_checks(
                event,
                pickings[0],
                moves[0],
                evidence
            )

            if evidence["mismatches"]:
                return "MISMATCH"

            return "ODOO_AUDIT_MISSING"

        if (
            outbox["status"] == "SYNCED"
            and len(odoo_events) == 1
            and len(pickings) == 1
            and len(moves) == 1
        ):
            odoo_event = odoo_events[0]
            picking = pickings[0]
            move = moves[0]

            self._finalize_consistency_checks(
                event,
                odoo_event,
                picking,
                move,
                evidence
            )

            if not evidence["mismatches"]:
                return "CONSISTENT"

        return "MISMATCH"

    def reconcile_event(self, event_id):
        edge = self._get_edge_evidence(event_id)

        odoo = self._get_odoo_evidence(event_id)

        classification = self.classify(
            edge=edge,
            odoo_events=odoo["events"],
            pickings=odoo["pickings"],
            moves=odoo["moves"],
        )

        return {
            "event_id": event_id,
            "classification": classification,
            "edge": edge,
            "odoo": odoo,
            "evidence": self._last_evidence,
        }


def from_environment(
    engine,
    timeout=10
):
    return ReconciliationEngine(
        engine=engine,
        odoo_client=OdooReadClient(
            endpoint=os.environ["ODOO_URL"],
            api_key=os.environ["ODOO_API_KEY"],
            database=os.environ["ODOO_DB"],
            timeout=timeout,
        ),
    )
