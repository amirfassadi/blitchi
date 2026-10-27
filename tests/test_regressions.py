import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

from bilitchi import Core, DomainError
from test_core import CoreTests


class RegressionTests(CoreTests):
    # Reuse fixtures/helpers without duplicating the original 25 test methods.
    pass


for name in list(dir(CoreTests)):
    if name.startswith("test_"):
        setattr(RegressionTests, name, None)


def test_cancel_before_maturity_has_zero_eligible_balance(self):
    t = self.purchase()["tickets"][0]
    self.core.cancel_ticket(t["id"], "finance")
    self.assertEqual(self.core.balance("center"), 0)
    self.now += 4 * 86400
    self.assertEqual(self.core.balance("center"), 0)


def test_capacity_error_is_replayed_after_seat_is_freed(self):
    first = self.hold(buyer="a")
    for attempt in range(2):
        with self.assertRaisesRegex(DomainError, "capacity_unavailable"):
            self.hold(key="failed-key", buyer="b")
        if attempt == 0:
            self.core.release("web", "release-a", first["hold_id"], buyer="a")
    self.hold(key="new-intent", buyer="b")


def test_idempotency_key_is_scoped_by_buyer(self):
    a = self.hold(("A1",), key="shared-key", buyer="a")
    b = self.hold(("A2",), key="shared-key", buyer="b")
    self.assertNotEqual(a["hold_id"], b["hold_id"])


def test_other_buyer_cannot_mutate_or_order_hold_in_same_channel(self):
    h = self.hold(buyer="a")
    for method in (self.core.release, self.core.extend, self.core.create_order):
        with self.assertRaisesRegex(DomainError, "not_found"):
            method("web", "malicious", h["hold_id"], buyer="b")
    self.assertEqual(self.core.inventory("show")[0]["state"], "held")
    self.core.create_order("web", "own-order", h["hold_id"], buyer="a")


def test_inventory_is_read_only_even_when_hold_expired(self):
    h = self.hold()
    self.now += 301
    original = self.core._connection

    @contextmanager
    def read_only():
        with original() as db:
            db.execute("PRAGMA query_only=ON")
            yield db

    with patch.object(self.core, "_connection", read_only):
        self.assertEqual(self.core.inventory("show")[0]["state"], "free")
    with original() as db:
        self.assertEqual(db.execute("SELECT state FROM holds WHERE id=?",
                                    (h["hold_id"],)).fetchone()[0], "active")
    self.core.sweep_expired()
    with original() as db:
        self.assertEqual(db.execute("SELECT state FROM holds WHERE id=?",
                                    (h["hold_id"],)).fetchone()[0], "expired")


def test_rounding_residue_goes_to_operator(self):
    with self.core._transaction() as db:
        db.execute("UPDATE shows SET unit_price=1000003 WHERE id='show'")
    order = self.purchase(("A1", "A2", "A3"))
    center = sum(t["center_share"] for t in order["tickets"])
    self.assertEqual(order["expected_amount"], 3_000_009)
    self.assertEqual(center, 2_700_008)
    self.assertEqual(order["expected_amount"] - center, 300_001)


def test_eligibility_uses_end_and_cancellation_deadline(self):
    now = self.now
    with self.core._transaction() as db:
        db.execute("UPDATE shows SET cancellation_deadline_at=? WHERE id='show'",
                   (now + 12_000,))
    self.purchase()
    self.now = now + 3600 + 3 * 86400
    self.assertEqual(self.core.balance("center"), 0)
    self.now = now + 9000 + 3 * 86400
    self.assertEqual(self.core.balance("center"), 0)
    self.now = now + 12_000 + 3 * 86400
    self.assertEqual(self.core.balance("center"), 900_000)


def test_redeem_replays_success_for_same_actor_and_key(self):
    t = self.purchase()["tickets"][0]["id"]
    result = self.core.redeem(t, "gate", "show", key="request")
    self.assertEqual(self.core.redeem(t, "gate", "show", key="request"), result)
    with self.assertRaisesRegex(DomainError, "ticket_not_valid"):
        self.core.redeem(t, "other-gate", "show", key="request")


def test_old_reference_database_is_not_silently_rewritten(self):
    from pathlib import Path
    path = Path(self.temp.name) / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE shows(id TEXT PRIMARY KEY, starts_at INTEGER)")
        db.execute("INSERT INTO shows VALUES('old', 1)")
    db.close()
    legacy = Core(path)
    with self.assertRaisesRegex(DomainError, "reference_schema_upgrade_required"):
        legacy.initialize()
    with legacy._connection() as check:
        self.assertEqual(check.execute("SELECT id FROM shows").fetchone()[0], "old")


for name, value in list(globals().items()):
    if name.startswith("test_") and callable(value):
        setattr(RegressionTests, name, value)
# Avoid unittest discovering the imported base fixture a second time.
del CoreTests
