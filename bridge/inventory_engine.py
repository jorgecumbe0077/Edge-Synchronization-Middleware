# `bridge/inventory_engine.py` — versão completa corrigida para I1–I6

import json
import sqlite3
import threading
import time


class InventoryConflict:
    def __init__(
        self,
        event_id,
        product_id,
        location_id,
        requested_qty,
        available_qty,
        reason
    ):
        self.event_id = event_id
        self.product_id = product_id
        self.location_id = location_id
        self.requested_qty = requested_qty
        self.available_qty = available_qty
        self.reason = reason

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "product_id": self.product_id,
            "location_id": self.location_id,
            "requested_qty": self.requested_qty,
            "available_qty": self.available_qty,
            "reason": self.reason,
        }


class InventoryEventEngine:
    """
    Motor persistente de consistência de inventário na borda mAZ.

    Responsabilidades:
      - persistir stock local;
      - persistir eventos;
      - garantir idempotência por event_id;
      - detectar conflitos de stock;
      - aplicar stock + evento + outbox atomicamente;
      - sobreviver a restart;
      - suportar múltiplas instâncias SQLite;
      - manter eventos pendentes para sincronização externa.

    A mAZ não substitui o inventário do Odoo.

    O motor funciona como camada local de consistência
    antes da sincronização com o ERP.
    """

    def __init__(self, db_path="client/inventory.db"):
        self.db_path = db_path

        self._lock = threading.RLock()

        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30,
            isolation_level=None
        )

        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")

        self._create_tables()

    # =========================================================
    # DATABASE
    # =========================================================

    def _create_tables(self):

        with self._lock:

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory_stock (
                    product_id INTEGER NOT NULL,
                    location_id TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (
                        product_id,
                        location_id
                    )
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory_events (
                    event_id TEXT PRIMARY KEY,
                    seq_id INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    product_id INTEGER NOT NULL,
                    location_id TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    previous_stock REAL,
                    new_stock REAL,
                    created_at REAL NOT NULL
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    product_id INTEGER NOT NULL,
                    location_id TEXT NOT NULL,
                    requested_qty REAL NOT NULL,
                    available_qty REAL NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)

            # -------------------------------------------------
            # OUTBOX
            # -------------------------------------------------

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory_outbox (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    synced_at REAL
                )
            """)

            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_inventory_events_seq
                ON inventory_events(seq_id)
            """)

            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_inventory_events_node
                ON inventory_events(node_id)
            """)

            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS
                idx_inventory_outbox_status
                ON inventory_outbox(status, created_at)
            """)

    # =========================================================
    # TRANSACTION HELPERS
    # =========================================================

    def _begin_write(self):
        """
        Inicia uma transação de escrita exclusiva no SQLite.

        BEGIN IMMEDIATE garante que apenas uma instância
        possa assumir a escrita concorrente naquele momento.
        """
        self.conn.execute("BEGIN IMMEDIATE")

    # =========================================================
    # STOCK
    # =========================================================

    def set_stock(
        self,
        product_id,
        location_id,
        quantity
    ):

        with self._lock:

            now = time.time()

            try:

                self._begin_write()

                self.conn.execute("""
                    INSERT INTO inventory_stock (
                        product_id,
                        location_id,
                        quantity,
                        version,
                        updated_at
                    )
                    VALUES (?, ?, ?, 0, ?)

                    ON CONFLICT(product_id, location_id)
                    DO UPDATE SET
                        quantity = excluded.quantity,
                        version = inventory_stock.version + 1,
                        updated_at = excluded.updated_at
                """, (
                    product_id,
                    location_id,
                    quantity,
                    now
                ))

                self.conn.commit()

            except Exception:
                self.conn.rollback()
                raise

    def get_stock(
        self,
        product_id,
        location_id
    ):

        with self._lock:

            row = self.conn.execute("""
                SELECT quantity
                FROM inventory_stock
                WHERE product_id = ?
                  AND location_id = ?
            """, (
                product_id,
                location_id
            )).fetchone()

            if row is None:
                return 0

            return row[0]

    # =========================================================
    # EVENT
    def process_event(self, event):

        required = (
            "event_id",
            "seq_id",
            "node_id",
            "product_id",
            "location_id",
            "quantity",
            "operation"
        )

        missing = [
            field
            for field in required
            if field not in event
        ]

        if missing:
            raise ValueError(
                "Evento inválido. Campos ausentes: "
                + ", ".join(missing)
            )

        event_id = event["event_id"]

        product_id = event["product_id"]
        location_id = event["location_id"]
        quantity = float(event["quantity"])

        with self._lock:

            try:
                # =================================================
                # TRANSAÇÃO DE ESCRITA
                #
                # IMPORTANTE:
                # O stock só é lido DEPOIS de BEGIN IMMEDIATE.
                # Assim, duas instâncias independentes não podem
                # decidir simultaneamente com base no mesmo stock.
                # =================================================

                self.conn.execute("BEGIN IMMEDIATE")

                # -------------------------------------------------
                # IDEMPOTÊNCIA DENTRO DO LOCK DE ESCRITA
                # -------------------------------------------------

                existing = self.conn.execute("""
                    SELECT status, new_stock
                    FROM inventory_events
                    WHERE event_id = ?
                """, (event_id,)).fetchone()

                if existing is not None:

                    self.conn.rollback()

                    return {
                        "status": "DUPLICATE",
                        "event_id": event_id,
                        "reason": "EVENT_ALREADY_PROCESSED",
                        "original_status": existing[0],
                        "new_stock": existing[1]
                    }

                # -------------------------------------------------
                # STOCK ATUAL
                #
                # Lido somente depois do BEGIN IMMEDIATE.
                # -------------------------------------------------

                row = self.conn.execute("""
                    SELECT quantity
                    FROM inventory_stock
                    WHERE product_id = ?
                      AND location_id = ?
                """, (
                    product_id,
                    location_id
                )).fetchone()

                if row is None:
                    current_stock = 0.0
                else:
                    current_stock = float(row[0])

                now = time.time()

                # =================================================
                # SAÍDA DE INVENTÁRIO
                # =================================================

                if quantity < 0:

                    requested = abs(quantity)

                    if requested > current_stock:

                        # -------------------------------------------------
                        # REGISTAR EVENTO DE CONFLITO
                        # -------------------------------------------------

                        self.conn.execute("""
                            INSERT INTO inventory_events (
                                event_id,
                                seq_id,
                                node_id,
                                product_id,
                                location_id,
                                quantity,
                                operation,
                                status,
                                previous_stock,
                                new_stock,
                                created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            event["event_id"],
                            event["seq_id"],
                            event["node_id"],
                            product_id,
                            location_id,
                            quantity,
                            event["operation"],
                            "CONFLICT",
                            current_stock,
                            current_stock,
                            now
                        ))

                        self.conn.execute("""
                            INSERT INTO inventory_conflicts (
                                event_id,
                                product_id,
                                location_id,
                                requested_qty,
                                available_qty,
                                reason,
                                created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            event_id,
                            product_id,
                            location_id,
                            requested,
                            current_stock,
                            "INSUFFICIENT_STOCK",
                            now
                        ))

                        self.conn.commit()

                        return {
                            "status": "CONFLICT",
                            "event_id": event_id,
                            "reason": "INSUFFICIENT_STOCK",
                            "available_qty": current_stock,
                            "requested_qty": requested
                        }

                # =================================================
                # APLICAR ALTERAÇÃO DE STOCK
                # =================================================

                new_stock = current_stock + quantity

                self.conn.execute("""
                    INSERT INTO inventory_stock (
                        product_id,
                        location_id,
                        quantity,
                        version,
                        updated_at
                    )
                    VALUES (?, ?, ?, 1, ?)

                    ON CONFLICT(product_id, location_id)
                    DO UPDATE SET
                        quantity = excluded.quantity,
                        version = inventory_stock.version + 1,
                        updated_at = excluded.updated_at
                """, (
                    product_id,
                    location_id,
                    new_stock,
                    now
                ))

                # =================================================
                # REGISTAR EVENTO
                #
                # Como BEGIN IMMEDIATE já serializou as escritas,
                # a verificação de event_id acima e este INSERT
                # fazem parte da mesma transação.
                # =================================================

                self.conn.execute("""
                    INSERT INTO inventory_events (
                        event_id,
                        seq_id,
                        node_id,
                        product_id,
                        location_id,
                        quantity,
                        operation,
                        status,
                        previous_stock,
                        new_stock,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event["event_id"],
                    event["seq_id"],
                    event["node_id"],
                    product_id,
                    location_id,
                    quantity,
                    event["operation"],
                    "ACCEPTED",
                    current_stock,
                    new_stock,
                    now
                ))

                # =================================================
                # OUTBOX ATÓMICA
                #
                # O evento aceite e a mensagem para sincronização
                # com o Odoo são persistidos na MESMA transação.
                #
                # Garantia:
                #   stock + event + outbox
                #   são COMMITADOS juntos.
                # =================================================

                outbox_payload = dict(event)

                outbox_payload.update({
                    "previous_stock": current_stock,
                    "new_stock": new_stock,
                })

                self.conn.execute("""
                    INSERT INTO inventory_outbox (
                        event_id,
                        payload,
                        status,
                        attempts,
                        last_error,
                        created_at,
                        updated_at,
                        synced_at
                    )
                    VALUES (?, ?, 'PENDING', 0, NULL, ?, ?, NULL)
                """, (
                    event_id,
                    json.dumps(
                        outbox_payload,
                        separators=(",", ":"),
                        sort_keys=True
                    ),
                    now,
                    now
                ))

                self.conn.commit()

                return {
                    "status": "ACCEPTED",
                    "event_id": event_id,
                    "previous_stock": current_stock,
                    "quantity": quantity,
                    "new_stock": new_stock
                }

            except sqlite3.IntegrityError as exc:

                # =================================================
                # DEFESA FINAL CONTRA RACE DE IDEMPOTÊNCIA
                #
                # Se outra instância inseriu o mesmo event_id,
                # transformar a colisão numa resposta DUPLICATE.
                # =================================================

                self.conn.rollback()

                if "inventory_events.event_id" in str(exc):

                    existing = self.conn.execute("""
                        SELECT status, new_stock
                        FROM inventory_events
                        WHERE event_id = ?
                    """, (event_id,)).fetchone()

                    if existing is not None:

                        return {
                            "status": "DUPLICATE",
                            "event_id": event_id,
                            "reason": "EVENT_ALREADY_PROCESSED",
                            "original_status": existing[0],
                            "new_stock": existing[1]
                        }

                raise

            except Exception:

                self.conn.rollback()
                raise

   
    # =========================================================
    # OUTBOX
    # =========================================================

    def outbox_count(self, status=None):

        with self._lock:

            if status is None:

                row = self.conn.execute("""
                    SELECT COUNT(*)
                    FROM inventory_outbox
                """).fetchone()

            else:

                row = self.conn.execute("""
                    SELECT COUNT(*)
                    FROM inventory_outbox
                    WHERE status = ?
                """, (
                    status,
                )).fetchone()

            return row[0]

    def get_pending_outbox(self, limit=100):

        with self._lock:

            rows = self.conn.execute("""
                SELECT
                    event_id,
                    payload,
                    status,
                    attempts,
                    last_error,
                    created_at,
                    updated_at,
                    synced_at
                FROM inventory_outbox
                WHERE status = 'PENDING'
                ORDER BY created_at ASC
                LIMIT ?
            """, (
                limit,
            )).fetchall()

            return [
                {
                    "event_id": row[0],
                    "payload": json.loads(row[1]),
                    "status": row[2],
                    "attempts": row[3],
                    "last_error": row[4],
                    "created_at": row[5],
                    "updated_at": row[6],
                    "synced_at": row[7],
                }
                for row in rows
            ]

    def mark_outbox_attempt(
        self,
        event_id,
        error=None
    ):

        with self._lock:

            now = time.time()

            try:

                self._begin_write()

                self.conn.execute("""
                    UPDATE inventory_outbox
                    SET
                        attempts = attempts + 1,
                        last_error = ?,
                        updated_at = ?
                    WHERE event_id = ?
                      AND status = 'PENDING'
                """, (
                    error,
                    now,
                    event_id
                ))

                self.conn.commit()

            except Exception:

                self.conn.rollback()
                raise

    def mark_outbox_synced(self, event_id):

        with self._lock:

            now = time.time()

            try:

                self._begin_write()

                self.conn.execute("""
                    UPDATE inventory_outbox
                    SET
                        status = 'SYNCED',
                        updated_at = ?,
                        synced_at = ?,
                        last_error = NULL
                    WHERE event_id = ?
                      AND status = 'PENDING'
                """, (
                    now,
                    now,
                    event_id
                ))

                self.conn.commit()

            except Exception:

                self.conn.rollback()
                raise

    def get_outbox_event(self, event_id):

        with self._lock:

            row = self.conn.execute("""
                SELECT
                    event_id,
                    payload,
                    status,
                    attempts,
                    last_error,
                    created_at,
                    updated_at,
                    synced_at
                FROM inventory_outbox
                WHERE event_id = ?
            """, (
                event_id,
            )).fetchone()

            if row is None:
                return None

            return {
                "event_id": row[0],
                "payload": json.loads(row[1]),
                "status": row[2],
                "attempts": row[3],
                "last_error": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "synced_at": row[7],
            }

    def get_outbox_status(self):

        with self._lock:

            rows = self.conn.execute("""
                SELECT
                    status,
                    COUNT(*)
                FROM inventory_outbox
                GROUP BY status
                ORDER BY status
            """).fetchall()

            return {
                row[0]: row[1]
                for row in rows
            }

    # =========================================================
    # AUDITORIA
    # =========================================================

    def processed_count(self):

        with self._lock:

            row = self.conn.execute("""
                SELECT COUNT(*)
                FROM inventory_events
                WHERE status = 'ACCEPTED'
            """).fetchone()

            return row[0]

    def conflict_count(self):

        with self._lock:

            row = self.conn.execute("""
                SELECT COUNT(*)
                FROM inventory_conflicts
            """).fetchone()

            return row[0]

    def event_count(self):

        with self._lock:

            row = self.conn.execute("""
                SELECT COUNT(*)
                FROM inventory_events
            """).fetchone()

            return row[0]

    def get_events(self):

        with self._lock:

            rows = self.conn.execute("""
                SELECT
                    event_id,
                    seq_id,
                    node_id,
                    product_id,
                    location_id,
                    quantity,
                    operation,
                    status,
                    previous_stock,
                    new_stock,
                    created_at
                FROM inventory_events
                ORDER BY created_at ASC
            """).fetchall()

            return [
                {
                    "event_id": row[0],
                    "seq_id": row[1],
                    "node_id": row[2],
                    "product_id": row[3],
                    "location_id": row[4],
                    "quantity": row[5],
                    "operation": row[6],
                    "status": row[7],
                    "previous_stock": row[8],
                    "new_stock": row[9],
                    "created_at": row[10],
                }
                for row in rows
            ]

    def get_conflicts(self):

        with self._lock:

            rows = self.conn.execute("""
                SELECT
                    event_id,
                    product_id,
                    location_id,
                    requested_qty,
                    available_qty,
                    reason,
                    created_at
                FROM inventory_conflicts
                ORDER BY created_at ASC
            """).fetchall()

            return [
                {
                    "event_id": row[0],
                    "product_id": row[1],
                    "location_id": row[2],
                    "requested_qty": row[3],
                    "available_qty": row[4],
                    "reason": row[5],
                    "created_at": row[6],
                }
                for row in rows
            ]

    def audit(
        self,
        product_id,
        location_id
    ):

        return {
            "product_id": product_id,
            "location_id": location_id,
            "stock": self.get_stock(
                product_id,
                location_id
            ),
            "processed_events":
                self.processed_count(),
            "conflicts":
                self.conflict_count(),
            "events":
                self.event_count(),
            "outbox":
                self.outbox_status()
        }

    def outbox_status(self):

        return self.get_outbox_status()

    # =========================================================
    # CLOSE
    # =========================================================

    def close(self):

        with self._lock:

            self.conn.close()

