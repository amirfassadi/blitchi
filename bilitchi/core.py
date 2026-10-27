"""Durable single-installation reference core; not a public/authenticated API."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


class DomainError(Exception):
    pass


def uid() -> str:
    return uuid.uuid4().hex


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Core:
    def __init__(self, database: str | Path, clock: Callable[[], int] | None = None):
        self.database = str(database)
        self.clock = clock or (lambda: int(time.time()))

    def initialize(self):
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(Path(__file__).with_name("schema.sql").read_text("utf-8-sig"))

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _one(db, sql, args=()):
        row = db.execute(sql, args).fetchone()
        if row is None:
            raise DomainError("not_found")
        return row

    def _event(self, db, kind, payload):
        db.execute("INSERT INTO events(id,kind,payload,created_at) VALUES(?,?,?,?)",
                   (uid(), kind, encoded(payload), self.clock()))

    def _audit(self, db, actor, action, target):
        if not actor:
            raise DomainError("actor_required")
        db.execute("INSERT INTO audit(actor,action,target,created_at) VALUES(?,?,?,?)",
                   (actor, action, target, self.clock()))

    def _operation(self, db, channel, key, action, payload, execute):
        if not key or not channel:
            raise DomainError("idempotency_key_and_channel_required")
        self._one(db, "SELECT id FROM channels WHERE id=?", (channel,))
        fingerprint = hashlib.sha256(encoded([action, payload]).encode()).hexdigest()
        old = db.execute("SELECT * FROM operations WHERE channel_id=? AND op_key=?",
                         (channel, key)).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise DomainError("idempotency_conflict")
            return json.loads(old["response"])
        result = execute()
        db.execute("INSERT INTO operations VALUES(?,?,?,?)",
                   (channel, key, fingerprint, encoded(result)))
        return result

    def _expire(self, db):
        rows = db.execute("SELECT id FROM holds WHERE state='active' AND expires_at<=?",
                          (self.clock(),)).fetchall()
        for row in rows:
            db.execute("DELETE FROM claims WHERE hold_id=?", (row["id"],))
            db.execute("UPDATE holds SET state='expired' WHERE id=?", (row["id"],))
            self._event(db, "hold.expired", {"hold_id": row["id"]})

    def add_center(self, center_id, name="", commission_bps=1000):
        if not isinstance(commission_bps, int) or not 0 <= commission_bps <= 10000:
            raise DomainError("invalid_commission")
        with self._transaction() as db:
            db.execute("INSERT INTO centers VALUES(?,?,?)",
                       (center_id, name, commission_bps))

    def add_channel(self, channel_id, name="", financial_owner="operator"):
        if financial_owner not in ("operator", "external"):
            raise DomainError("invalid_financial_owner")
        with self._transaction() as db:
            db.execute("INSERT INTO channels VALUES(?,?,?)",
                       (channel_id, name, financial_owner))

    def add_show(self, show_id, center_id, seats, starts_at, sales_close_at,
                 unit_price, title=""):
        seats = list(seats)
        if (not seats or len(set(seats)) != len(seats)
                or any(not isinstance(s, str) or not s.strip() for s in seats)
                or not isinstance(unit_price, int) or unit_price <= 0
                or sales_close_at > starts_at or sales_close_at <= self.clock()):
            raise DomainError("invalid_show")
        with self._transaction() as db:
            db.execute("INSERT INTO shows VALUES(?,?,?,?,?,?)",
                       (show_id, center_id, title, starts_at, sales_close_at, unit_price))
            db.executemany("INSERT INTO seats VALUES(?,?)",
                           [(show_id, seat) for seat in seats])

    def inventory(self, show_id):
        with self._transaction() as db:
            self._expire(db)
            self._one(db, "SELECT id FROM shows WHERE id=?", (show_id,))
            rows = db.execute("""
                SELECT s.label, h.state, h.expires_at FROM seats s
                LEFT JOIN claims c ON c.show_id=s.show_id AND c.label=s.label
                LEFT JOIN holds h ON h.id=c.hold_id
                WHERE s.show_id=? ORDER BY s.label""", (show_id,)).fetchall()
            return [{"seat": r["label"],
                     "state": "free" if r["state"] is None else
                              ("sold" if r["state"] == "sold" else "held"),
                     "expires_at": r["expires_at"] if r["state"] == "active" else None}
                    for r in rows]

    def _create_hold(self, db, show_id, channel, buyer, seats, ttl):
        show = self._one(db, "SELECT * FROM shows WHERE id=?", (show_id,))
        if self.clock() >= show["sales_close_at"]:
            raise DomainError("sales_closed")
        for seat in seats:
            self._one(db, "SELECT label FROM seats WHERE show_id=? AND label=?",
                      (show_id, seat))
            if db.execute("SELECT 1 FROM claims WHERE show_id=? AND label=?",
                          (show_id, seat)).fetchone():
                raise DomainError("capacity_unavailable")
        hold = uid()
        expires = min(self.clock() + ttl, show["sales_close_at"])
        db.execute("INSERT INTO holds VALUES(?,?,?,?,?,?,0)",
                   (hold, show_id, channel, buyer, "active", expires))
        db.executemany("INSERT INTO hold_seats VALUES(?,?)",
                       [(hold, seat) for seat in seats])
        db.executemany("INSERT INTO claims VALUES(?,?,?)",
                       [(show_id, seat, hold) for seat in seats])
        return hold, expires

    def reserve(self, channel, key, show_id, buyer, seats, ttl=600):
        seats = sorted(seats)
        if (not buyer or not seats or len(seats) != len(set(seats))
                or not isinstance(ttl, int) or not 300 <= ttl <= 900):
            raise DomainError("invalid_reservation")
        payload = [show_id, buyer, seats, ttl]
        with self._transaction() as db:
            self._expire(db)

            def execute():
                if db.execute("""SELECT 1 FROM holds WHERE show_id=? AND buyer_id=?
                              AND state='active'""", (show_id, buyer)).fetchone():
                    raise DomainError("active_hold_limit")
                hold, expires = self._create_hold(db, show_id, channel, buyer, seats, ttl)
                self._event(db, "hold.created",
                            {"hold_id": hold, "show_id": show_id, "seats": seats})
                return {"hold_id": hold, "expires_at": expires}
            return self._operation(db, channel, key, "reserve", payload, execute)

    def extend(self, channel, key, hold_id, seconds=300):
        if not isinstance(seconds, int) or not 1 <= seconds <= 300:
            raise DomainError("invalid_extension")
        with self._transaction() as db:
            self._expire(db)

            def execute():
                h = self._one(db, "SELECT * FROM holds WHERE id=? AND channel_id=?",
                              (hold_id, channel))
                if h["state"] != "active" or h["extended"]:
                    raise DomainError("extension_unavailable")
                show = self._one(db, "SELECT * FROM shows WHERE id=?", (h["show_id"],))
                expiry = min(h["expires_at"] + seconds, show["sales_close_at"])
                db.execute("UPDATE holds SET expires_at=?,extended=1 WHERE id=?",
                           (expiry, hold_id))
                self._event(db, "hold.extended", {"hold_id": hold_id, "expires_at": expiry})
                return {"hold_id": hold_id, "expires_at": expiry}
            return self._operation(db, channel, key, "extend", [hold_id, seconds], execute)

    def release(self, channel, key, hold_id):
        with self._transaction() as db:
            self._expire(db)

            def execute():
                h = self._one(db, "SELECT * FROM holds WHERE id=? AND channel_id=?",
                              (hold_id, channel))
                if h["state"] == "sold":
                    raise DomainError("sale_requires_cancellation")
                if h["state"] == "active":
                    db.execute("DELETE FROM claims WHERE hold_id=?", (hold_id,))
                    db.execute("UPDATE holds SET state='released' WHERE id=?", (hold_id,))
                    self._event(db, "hold.released", {"hold_id": hold_id})
                return {"hold_id": hold_id}
            return self._operation(db, channel, key, "release", [hold_id], execute)

    def create_order(self, channel, key, hold_id):
        with self._transaction() as db:
            self._expire(db)

            def execute():
                h = self._one(db, """SELECT h.*,c.financial_owner FROM holds h
                        JOIN channels c ON c.id=h.channel_id
                        WHERE h.id=? AND h.channel_id=?""", (hold_id, channel))
                if h["state"] != "active":
                    raise DomainError("hold_not_active")
                if h["financial_owner"] != "operator":
                    raise DomainError("external_sales_not_operator_revenue")
                if db.execute("SELECT 1 FROM orders WHERE hold_id=?", (hold_id,)).fetchone():
                    raise DomainError("order_already_exists")
                show = self._one(db, """SELECT s.*,c.commission_bps FROM shows s
                        JOIN centers c ON c.id=s.center_id WHERE s.id=?""", (h["show_id"],))
                count = db.execute("SELECT COUNT(*) FROM hold_seats WHERE hold_id=?",
                                   (hold_id,)).fetchone()[0]
                order = uid()
                total = count * show["unit_price"]
                db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?)",
                           (order, hold_id, show["center_id"], "awaiting_payment",
                            total, show["commission_bps"], self.clock()))
                self._event(db, "order.created", {"order_id": order})
                return {"order_id": order, "amount": total, "currency": "IRR"}
            return self._operation(db, channel, key, "create_order", [hold_id], execute)

    def _review(self, db, order, reason):
        show = self._one(db, """SELECT s.starts_at FROM orders o JOIN holds h
                    ON h.id=o.hold_id JOIN shows s ON s.id=h.show_id WHERE o.id=?""",
                         (order,))
        due = self.clock() + (300 if show["starts_at"] - self.clock() <= 3600 else 1800)
        db.execute("""INSERT OR IGNORE INTO review_cases
                   VALUES(?,?,?,?,?,?,NULL)""",
                   (uid(), order, reason, "operator", self.clock(), due))
        db.execute("UPDATE orders SET state='review' WHERE id=? AND state!='completed'",
                   (order,))
        self._event(db, "order.review", {"order_id": order, "reason": reason})

    def _refund(self, db, payment, amount, reason, ticket_id=None):
        used = db.execute("SELECT COALESCE(SUM(amount),0) FROM refunds WHERE payment_id=?",
                          (payment["id"],)).fetchone()[0]
        if used + amount > payment["amount"]:
            raise DomainError("refund_exceeds_payment")
        refund_id = uid()
        db.execute("INSERT INTO refunds VALUES(?,?,?,?,?,?)",
                   (refund_id, payment["id"], ticket_id, amount, "pending", reason))
        self._event(db, "refund.requested", {"refund_id": refund_id, "amount": amount})
        return refund_id

    def record_payment(self, order_id, provider, provider_ref, amount, state, actor):
        """Trusted verified-provider adapter entry. Never expose directly to buyers."""
        if state not in ("unknown", "failed", "succeeded") or not provider or not provider_ref:
            raise DomainError("invalid_payment")
        if not isinstance(amount, int) or amount <= 0:
            raise DomainError("invalid_amount")
        with self._transaction() as db:
            self._expire(db)
            order = self._one(db, "SELECT * FROM orders WHERE id=?", (order_id,))
            p = db.execute("SELECT * FROM payments WHERE provider=? AND provider_ref=?",
                           (provider, provider_ref)).fetchone()
            if p:
                if p["order_id"] != order_id or p["amount"] != amount:
                    raise DomainError("payment_reference_conflict")
                if p["state"] == "succeeded":
                    return {"payment_id": p["id"], "state": "succeeded"}
                if p["state"] == "failed" and state != "failed":
                    raise DomainError("terminal_payment_requires_reconciliation")
                db.execute("UPDATE payments SET state=? WHERE id=?", (state, p["id"]))
                payment_id = p["id"]
            else:
                payment_id = uid()
                db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,0)",
                           (payment_id, order_id, provider, provider_ref, amount, state))
            self._audit(db, actor, "payment." + state, payment_id)
            payment = self._one(db, "SELECT * FROM payments WHERE id=?", (payment_id,))
            if state == "unknown":
                self._review(db, order_id, "payment_unknown:" + payment_id)
            elif state == "succeeded":
                if order["state"] == "completed":
                    self._refund(db, payment, amount, "duplicate_payment")
                elif amount != order["expected_amount"]:
                    self._review(db, order_id, "amount_mismatch")
                    self._refund(db, payment, amount, "amount_mismatch")
                else:
                    self._complete(db, order, payment)
            if state in ("succeeded", "failed"):
                db.execute("""UPDATE review_cases SET resolved_at=?
                    WHERE order_id=? AND reason=?""",
                    (self.clock(), order_id, "payment_unknown:" + payment_id))
            self._event(db, "payment.recorded",
                        {"order_id": order_id, "payment_id": payment_id, "state": state})
            return {"payment_id": payment_id, "state": state}

    def _complete(self, db, order, payment):
        h = self._one(db, "SELECT * FROM holds WHERE id=?", (order["hold_id"],))
        show = self._one(db, "SELECT * FROM shows WHERE id=?", (h["show_id"],))
        seats = [r[0] for r in db.execute(
            "SELECT label FROM hold_seats WHERE hold_id=? ORDER BY label", (h["id"],))]
        if self.clock() >= show["sales_close_at"]:
            self._refund(db, payment, payment["amount"], "sales_closed")
            db.execute("UPDATE orders SET state='closed' WHERE id=?", (order["id"],))
            return
        if h["state"] != "active":
            unavailable = any(db.execute("SELECT 1 FROM claims WHERE show_id=? AND label=?",
                                         (h["show_id"], seat)).fetchone() for seat in seats)
            if unavailable:
                self._refund(db, payment, payment["amount"], "capacity_unavailable")
                db.execute("UPDATE orders SET state='closed' WHERE id=?", (order["id"],))
                return
            new_hold, _ = self._create_hold(
                db, h["show_id"], h["channel_id"], h["buyer_id"], seats, 300)
            self._event(db, "hold.reacquired",
                        {"old_hold_id": h["id"], "hold_id": new_hold,
                         "order_id": order["id"]})
            db.execute("UPDATE orders SET hold_id=? WHERE id=?", (new_hold, order["id"]))
            h = self._one(db, "SELECT * FROM holds WHERE id=?", (new_hold,))
        count = len(seats)
        price = order["expected_amount"] // count
        commission = order["expected_amount"] * order["commission_bps"] // 10000
        center_total = order["expected_amount"] - commission
        per_ticket, remainder = divmod(center_total, count)
        for index, seat in enumerate(seats):
            ticket = uid()
            share = per_ticket + int(index < remainder)
            db.execute("INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?,?)",
                       (ticket, order["id"], h["id"], seat, "valid", price, share, None, None))
            db.execute("INSERT INTO ledger VALUES(?,?,?,?,?)",
                       (uid(), order["center_id"], "sale:" + ticket, share,
                        show["starts_at"] + 3 * 86400))
        db.execute("UPDATE holds SET state='sold' WHERE id=?", (h["id"],))
        db.execute("UPDATE orders SET state='completed' WHERE id=?", (order["id"],))
        db.execute("UPDATE payments SET allocated=1 WHERE id=?", (payment["id"],))

        self._event(db, "sale.confirmed", {"order_id": order["id"], "hold_id": h["id"]})

    def confirm_external_sale(self, channel, key, hold_id):
        """Inventory-only confirmation; no operator revenue or operator tickets."""
        with self._transaction() as db:
            self._expire(db)

            def execute():
                h = self._one(db, """SELECT h.*,c.financial_owner FROM holds h
                    JOIN channels c ON c.id=h.channel_id WHERE h.id=? AND h.channel_id=?""",
                              (hold_id, channel))
                if h["financial_owner"] != "external" or h["state"] != "active":
                    raise DomainError("external_confirmation_unavailable")
                db.execute("UPDATE holds SET state='sold' WHERE id=?", (hold_id,))
                self._event(db, "external.sale_confirmed", {"hold_id": hold_id})
                return {"hold_id": hold_id, "state": "sold"}
            return self._operation(db, channel, key, "external_sale", [hold_id], execute)

    def cancel_ticket(self, ticket_id, actor):
        """Privileged full-price cancellation only; caller must authorize policy."""
        with self._transaction() as db:
            t = self._one(db, """SELECT t.*,o.center_id FROM tickets t
                    JOIN orders o ON o.id=t.order_id WHERE t.id=?""", (ticket_id,))
            if t["state"] == "used":
                raise DomainError("ticket_already_used")
            if t["state"] == "cancelled":
                return dict(self._one(db, "SELECT * FROM refunds WHERE ticket_id=?",
                                      (ticket_id,)))
            p = self._one(db, "SELECT * FROM payments WHERE order_id=? AND allocated=1",
                          (t["order_id"],))
            h = self._one(db, "SELECT * FROM holds WHERE id=?", (t["hold_id"],))
            refund = self._refund(db, p, t["price"], "ticket_cancelled", ticket_id)
            db.execute("UPDATE tickets SET state='cancelled' WHERE id=?", (ticket_id,))
            db.execute("DELETE FROM claims WHERE show_id=? AND label=? AND hold_id=?",
                       (h["show_id"], t["label"], h["id"]))
            db.execute("INSERT INTO ledger VALUES(?,?,?,?,?)",
                       (uid(), t["center_id"], "cancel:" + ticket_id,
                        -t["center_share"], self.clock()))
            self._audit(db, actor, "ticket.cancel", ticket_id)
            self._event(db, "ticket.cancelled", {"ticket_id": ticket_id})
            return dict(self._one(db, "SELECT * FROM refunds WHERE id=?", (refund,)))

    def redeem(self, ticket_id, actor, allowed_show_id):
        """Caller must derive allowed_show_id from authenticated staff privileges."""
        with self._transaction() as db:
            t = self._one(db, """SELECT t.*,h.show_id FROM tickets t
                JOIN holds h ON h.id=t.hold_id WHERE t.id=?""", (ticket_id,))
            if t["show_id"] != allowed_show_id:
                raise DomainError("forbidden")
            if t["state"] != "valid":
                raise DomainError("ticket_not_valid")
            db.execute("UPDATE tickets SET state='used',used_at=?,used_by=? WHERE id=?",
                       (self.clock(), actor, ticket_id))
            self._audit(db, actor, "ticket.redeem", ticket_id)
            self._event(db, "ticket.used", {"ticket_id": ticket_id})
            return {"ticket_id": ticket_id, "state": "used"}

    def mark_refund_paid(self, refund_id, bank_ref, actor):
        if not bank_ref:
            raise DomainError("bank_reference_required")
        with self._transaction() as db:
            r = self._one(db, "SELECT * FROM refunds WHERE id=?", (refund_id,))
            if r["state"] == "paid":
                old = self._one(db, "SELECT * FROM refund_receipts WHERE refund_id=?",
                                (refund_id,))
                if old["bank_ref"] != bank_ref:
                    raise DomainError("receipt_conflict")
                return
            db.execute("INSERT INTO refund_receipts VALUES(?,?,?)",
                       (refund_id, bank_ref, self.clock()))
            db.execute("UPDATE refunds SET state='paid' WHERE id=?", (refund_id,))
            self._audit(db, actor, "refund.paid", refund_id)

    def _balance(self, db, center_id):
        self._one(db, "SELECT id FROM centers WHERE id=?", (center_id,))
        due = db.execute("""SELECT COALESCE(SUM(amount),0) FROM ledger
                         WHERE center_id=? AND eligible_at<=?""",
                         (center_id, self.clock())).fetchone()[0]
        paid = db.execute("SELECT COALESCE(SUM(amount),0) FROM settlements WHERE center_id=?",
                          (center_id,)).fetchone()[0]
        return due - paid

    def balance(self, center_id):
        with self._connection() as db:
            return self._balance(db, center_id)

    def record_settlement(self, center_id, amount, bank_ref, actor):
        """Record an already verified manual transfer; does not send money."""
        if not isinstance(amount, int) or amount <= 0 or not bank_ref:
            raise DomainError("invalid_settlement")
        with self._transaction() as db:
            old = db.execute("SELECT * FROM settlements WHERE bank_ref=?",
                             (bank_ref,)).fetchone()
            if old:
                if old["center_id"] != center_id or old["amount"] != amount:
                    raise DomainError("receipt_conflict")
                return dict(old)
            if amount > self._balance(db, center_id):
                raise DomainError("insufficient_eligible_balance")
            settlement = uid()
            db.execute("INSERT INTO settlements VALUES(?,?,?,?,?)",
                       (settlement, center_id, amount, bank_ref, self.clock()))
            self._audit(db, actor, "settlement.record", settlement)
            return dict(self._one(db, "SELECT * FROM settlements WHERE id=?", (settlement,)))

    def order(self, order_id):
        with self._connection() as db:
            result = dict(self._one(db, "SELECT * FROM orders WHERE id=?", (order_id,)))
            result["tickets"] = [dict(r) for r in db.execute(
                "SELECT * FROM tickets WHERE order_id=? ORDER BY label", (order_id,))]
            result["payments"] = [dict(r) for r in db.execute(
                "SELECT * FROM payments WHERE order_id=?", (order_id,))]
            return result

    def events(self, after=0, limit=100):
        """Trusted internal outbox reader. Public integrations need scoped filtering."""
        if not 1 <= limit <= 1000:
            raise DomainError("invalid_page_size")
        with self._connection() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM events WHERE sequence>? ORDER BY sequence LIMIT ?",
                (after, limit))]

    def review_queue(self):
        with self._connection() as db:
            return [dict(r) for r in db.execute("""SELECT * FROM review_cases
                WHERE resolved_at IS NULL ORDER BY due_at""")]
