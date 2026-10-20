"""SQLite persistence, idempotency, audit events and a leased outbox.

A production system would use shared durable infrastructure, monitoring and
external-recipient idempotency. This implementation is a single-instance demo.
"""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .models import Classification, LeadRequest
from .policy import PolicyResult


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class WorkflowStore:
    def __init__(self, db_path: str | Path, clock: Callable[[], float] = time.time):
        self.db_path = str(db_path)
        self.clock = clock
        self.initialize()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=10.0)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def initialize(self):
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_digest TEXT NOT NULL,
                    lead_ref TEXT NOT NULL,
                    source TEXT NOT NULL,
                    department TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    classifier TEXT NOT NULL,
                    status TEXT NOT NULL,
                    review_reason TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    workflow_id TEXT PRIMARY KEY REFERENCES workflows(workflow_id),
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL,
                    lease_until REAL,
                    last_error_code TEXT
                );
            """)
            db.commit()

    def _audit(self, db, workflow_id: str, actor: str, action: str, detail: str):
        db.execute(
            "INSERT INTO audit (workflow_id, actor, action, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (workflow_id, actor, action, detail, self.clock()),
        )

    @staticmethod
    def _view(row):
        return {
            "workflow_id": row["workflow_id"],
            "lead_ref": row["lead_ref"],
            "source": row["source"],
            "department": row["department"],
            "urgency": row["urgency"],
            "confidence": row["confidence"],
            "classifier": row["classifier"],
            "status": row["status"],
            "review_reason": row["review_reason"],
        }

    def find_by_key(self, idempotency_key: str, digest: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM workflows WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if row is None:
                return None
            if row["payload_digest"] != digest:
                raise Conflict("idempotency key reused with different payload")
            return self._view(row)

    def create_or_replay(
        self, lead: LeadRequest, idempotency_key: str, digest: str,
        classification: Classification, classifier: str, policy: PolicyResult,
    ) -> tuple[dict, bool]:
        status = "awaiting_approval" if policy.requires_approval else "queued"
        workflow_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM workflows WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing["payload_digest"] != digest:
                    raise Conflict("idempotency key reused with different payload")
                return self._view(existing), True
            db.execute(
                """INSERT INTO workflows
                (workflow_id, idempotency_key, payload_digest, lead_ref, source,
                 department, urgency, confidence, classifier, status, review_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (workflow_id, idempotency_key, digest, lead.lead_ref, lead.source,
                 classification.department, classification.urgency, classification.confidence,
                 classifier, status, policy.reason, self.clock()),
            )
            self._audit(db, workflow_id, "system", "intake_accepted", "payload_not_retained")
            self._audit(db, workflow_id, "policy", "route_selected", policy.reason)
            if not policy.requires_approval:
                db.execute(
                    "INSERT INTO outbox (workflow_id, status, available_at) VALUES (?, 'ready', ?)",
                    (workflow_id, self.clock()),
                )
            db.commit()
        return self.get(workflow_id), False

    def get(self, workflow_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
            if not row:
                raise NotFound("workflow not found")
            return self._view(row)

    def events(self, workflow_id: str) -> list[dict]:
        self.get(workflow_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT event_id, actor, action, detail, created_at FROM audit WHERE workflow_id = ? ORDER BY event_id",
                (workflow_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def decide(self, workflow_id: str, approved: bool, actor: str = "operator") -> dict:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
            if not row:
                raise NotFound("workflow not found")
            if row["status"] != "awaiting_approval":
                raise Conflict("workflow is not awaiting approval")
            status = "queued" if approved else "rejected"
            db.execute("UPDATE workflows SET status = ? WHERE workflow_id = ?", (status, workflow_id))
            self._audit(db, workflow_id, actor, "approved" if approved else "rejected", "operator_decision")
            if approved:
                db.execute(
                    "INSERT INTO outbox (workflow_id, status, available_at) VALUES (?, 'ready', ?)",
                    (workflow_id, self.clock()),
                )
            db.commit()
        return self.get(workflow_id)

    def claim_next(self, lease_seconds: float = 60.0) -> dict | None:
        now = self.clock()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT workflow_id, attempts FROM outbox
                   WHERE (status = 'ready' AND available_at <= ?)
                      OR (status = 'inflight' AND lease_until <= ?)
                   ORDER BY available_at, workflow_id LIMIT 1""",
                (now, now),
            ).fetchone()
            if not row:
                return None
            db.execute(
                """UPDATE outbox SET status='inflight', attempts=attempts+1,
                   lease_until=? WHERE workflow_id=?""",
                (now + lease_seconds, row["workflow_id"]),
            )
            self._audit(db, row["workflow_id"], "dispatcher", "dispatch_attempt", "mock_crm")
            db.commit()
            return {"workflow_id": row["workflow_id"], "attempt": row["attempts"] + 1}

    def finish_dispatch(self, workflow_id: str, succeeded: bool, max_attempts: int = 3):
        now = self.clock()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT attempts, status FROM outbox WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
            if not row or row["status"] != "inflight":
                raise Conflict("dispatch is not currently claimed")
            if succeeded:
                db.execute(
                    "UPDATE outbox SET status='sent', lease_until=NULL, last_error_code=NULL WHERE workflow_id=?",
                    (workflow_id,),
                )
                db.execute("UPDATE workflows SET status='dispatched_mock' WHERE workflow_id=?", (workflow_id,))
                self._audit(db, workflow_id, "dispatcher", "dispatched_mock", "no_external_side_effect")
            elif row["attempts"] >= max_attempts:
                db.execute(
                    "UPDATE outbox SET status='failed', lease_until=NULL, last_error_code='dispatch_error' WHERE workflow_id=?",
                    (workflow_id,),
                )
                db.execute("UPDATE workflows SET status='dispatch_failed' WHERE workflow_id=?", (workflow_id,))
                self._audit(db, workflow_id, "dispatcher", "terminal_failure", "manual_recovery_required")
            else:
                delay = min(2 ** row["attempts"], 60)
                db.execute(
                    """UPDATE outbox SET status='ready', lease_until=NULL,
                       available_at=?, last_error_code='dispatch_error' WHERE workflow_id=?""",
                    (now + delay, workflow_id),
                )
                self._audit(db, workflow_id, "dispatcher", "retry_scheduled", f"backoff_seconds={delay}")
            db.commit()


class MockCRM:
    """Non-networking downstream adapter: intentionally never calls a real CRM."""

    def __init__(self, fail_once_for: set[str] | None = None):
        self.fail_once_for = fail_once_for or set()
        self.sent: list[str] = []
        self._attempts: dict[str, int] = {}

    def send(self, workflow_id: str) -> None:
        count = self._attempts.get(workflow_id, 0) + 1
        self._attempts[workflow_id] = count
        if workflow_id in self.fail_once_for and count == 1:
            raise RuntimeError("simulated downstream failure")
        if workflow_id not in self.sent:
            self.sent.append(workflow_id)


def drain_outbox(store: WorkflowStore, dispatcher: MockCRM, limit: int = 5) -> dict:
    """Manually invoked by authenticated operator. No external calls performed."""
    processed = 0
    succeeded = 0
    failed = 0
    for _ in range(limit):
        claimed = store.claim_next()
        if not claimed:
            break
        processed += 1
        try:
            dispatcher.send(claimed["workflow_id"])
        except Exception:
            # Do not expose downstream exception strings, which could contain secrets.
            store.finish_dispatch(claimed["workflow_id"], succeeded=False)
            failed += 1
        else:
            store.finish_dispatch(claimed["workflow_id"], succeeded=True)
            succeeded += 1
    return {"processed": processed, "succeeded": succeeded, "failed": failed, "mode": "mock_only"}
