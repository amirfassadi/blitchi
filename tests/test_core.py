import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from bilitchi import Core, DomainError

class CoreTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.now = 1000000
        self.core = Core(Path(self.temp.name) / 'test.db', lambda: self.now)
        self.core.initialize()
        self.core.add_center('center', commission_bps=1000)
        self.core.add_channel('web')
        self.core.add_channel('other', financial_owner='external')
        self.core.add_show('show', 'center', ['A1', 'A2', 'A3'], self.now + 3600, self.now + 3500, 1000000, ends_at=self.now + 9000)

    def hold(self, seats=('A1',), key='hold', buyer='buyer', channel='web'):
        return self.core.reserve(channel, key, 'show', buyer, seats, ttl=300)

    def purchase(self, seats=('A1',)):
        hold = self.hold(seats)
        order = self.core.create_order('web', 'order', hold['hold_id'], buyer='buyer')
        self.core.record_payment(order['order_id'], 'sandbox', 'payment', order['amount'], 'succeeded', 'verified-adapter')
        return self.core.order(order['order_id'])

    def test_blank_business_names(self):
        with self.core._connection() as db:
            self.assertEqual(db.execute('SELECT name FROM centers').fetchone()[0], '')

    def test_last_seat_concurrency(self):
        barrier = Barrier(12)

        def worker(i):
            barrier.wait()
            try:
                return self.hold(key=f'k{i}', buyer=f'b{i}')['hold_id']
            except DomainError as e:
                self.assertEqual(str(e), 'capacity_unavailable')
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(worker, range(12)))
        self.assertEqual(sum((r is not None for r in results)), 1)

    def test_group_reservation_is_atomic(self):
        self.hold(('A1',))
        with self.assertRaisesRegex(DomainError, 'capacity_unavailable'):
            self.hold(('A1', 'A2'), key='group', buyer='second')
        self.assertEqual(self.core.inventory('show')[1]['state'], 'free')

    def test_idempotency_and_payload_conflict(self):
        first = self.hold()
        self.assertEqual(first, self.hold())
        with self.assertRaisesRegex(DomainError, 'idempotency_conflict'):
            self.hold(('A2',))

    def test_channel_cannot_release_another_hold(self):
        h = self.hold()
        with self.assertRaisesRegex(DomainError, 'not_found'):
            self.core.release('other', 'release', h['hold_id'], buyer='buyer')

    def test_expiry_and_reacquisition(self):
        old = self.hold()
        self.now += 301
        new = self.hold(key='new', buyer='new')
        self.assertNotEqual(old['hold_id'], new['hold_id'])

    def test_extend_once_and_authoritative_expiry(self):
        h = self.hold()
        r = self.core.extend('web', 'extend', h['hold_id'], buyer='buyer')
        self.assertEqual(r['expires_at'], h['expires_at'] + 300)
        self.assertEqual(r, self.core.extend('web', 'extend', h['hold_id'], buyer='buyer'))
        with self.assertRaisesRegex(DomainError, 'extension_unavailable'):
            self.core.extend('web', 'again', h['hold_id'], buyer='buyer')

    def test_repeat_callback_one_ticket_and_ledger(self):
        o = self.purchase()
        self.core.record_payment(o['id'], 'sandbox', 'payment', o['expected_amount'], 'succeeded', 'adapter')
        self.assertEqual(len(self.core.order(o['id'])['tickets']), 1)
        with self.core._connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0], 1)

    def test_unknown_payment_remains_open(self):
        h = self.hold()
        o = self.core.create_order('web', 'order', h['hold_id'], buyer='buyer')
        self.core.record_payment(o['order_id'], 'sandbox', 'ref', o['amount'], 'unknown', 'adapter')
        self.now += 4 * 86400
        self.assertEqual(self.core.order(o['order_id'])['state'], 'review')
        self.assertEqual(len(self.core.review_queue()), 1)

    def test_unknown_then_success(self):
        h = self.hold()
        o = self.core.create_order('web', 'order', h['hold_id'], buyer='buyer')
        for state in ('unknown', 'succeeded'):
            self.core.record_payment(o['order_id'], 'sandbox', 'ref', o['amount'], state, 'adapter')
        self.assertEqual(self.core.order(o['order_id'])['state'], 'completed')
        self.assertEqual(self.core.review_queue(), [])

    def test_late_payment_new_hold(self):
        h = self.hold()
        o = self.core.create_order('web', 'order', h['hold_id'], buyer='buyer')
        self.now += 301
        self.core.record_payment(o['order_id'], 'sandbox', 'ref', o['amount'], 'succeeded', 'adapter')
        completed = self.core.order(o['order_id'])
        self.assertEqual(completed['state'], 'completed')
        self.assertNotEqual(completed['hold_id'], h['hold_id'])

    def test_late_payment_taken_seat_refund_no_ticket(self):
        h = self.hold()
        o = self.core.create_order('web', 'order', h['hold_id'], buyer='buyer')
        self.now += 301
        self.hold(key='second', buyer='second', channel='other')
        self.core.record_payment(o['order_id'], 'sandbox', 'ref', o['amount'], 'succeeded', 'adapter')
        self.assertEqual(self.core.order(o['order_id'])['tickets'], [])
        with self.core._connection() as db:
            self.assertEqual(db.execute('SELECT amount FROM refunds').fetchone()[0], o['amount'])

    def test_duplicate_successful_attempt_refunds_excess(self):
        o = self.purchase()
        self.core.record_payment(o['id'], 'sandbox', 'second', o['expected_amount'], 'succeeded', 'adapter')
        self.assertEqual(len(self.core.order(o['id'])['tickets']), 1)
        with self.core._connection() as db:
            self.assertEqual(db.execute('SELECT reason FROM refunds').fetchone()[0], 'duplicate_payment')

    def test_reference_cannot_be_reused_for_another_order(self):
        o = self.purchase()
        h2 = self.hold(('A2',), key='h2', buyer='second')
        o2 = self.core.create_order('web', 'o2', h2['hold_id'], buyer='second')
        with self.assertRaisesRegex(DomainError, 'payment_reference_conflict'):
            self.core.record_payment(o2['order_id'], 'sandbox', 'payment', o2['amount'], 'succeeded', 'adapter')

    def test_external_sale_has_no_operator_financial_records(self):
        h = self.hold(channel='other')
        self.core.confirm_external_sale('other', 'sale', h['hold_id'])
        self.assertEqual(self.core.inventory('show')[0]['state'], 'sold')
        with self.core._connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)

    def test_external_channel_cannot_create_operator_order(self):
        h = self.hold(channel='other')
        with self.assertRaisesRegex(DomainError, 'external_sales'):
            self.core.create_order('other', 'order', h['hold_id'], buyer='buyer')

    def test_partial_cancellation_preserves_other_ticket(self):
        o = self.purchase(('A1', 'A2'))
        first, second = o['tickets']
        refund = self.core.cancel_ticket(first['id'], 'finance')
        self.assertEqual(refund['amount'], first['price'])
        after = self.core.order(o['id'])
        self.assertEqual([t['state'] for t in after['tickets']], ['cancelled', 'valid'])
        self.assertEqual(self.core.cancel_ticket(first['id'], 'finance')['id'], refund['id'])
        self.core.redeem(second['id'], 'gate', 'show', key='scan:' + second['id'])

    def test_concurrent_scan_only_once(self):
        ticket = self.purchase()['tickets'][0]['id']
        barrier = Barrier(8)

        def worker(i):
            barrier.wait()
            try:
                self.core.redeem(ticket, f'gate{i}', 'show', key='scan:' + ticket)
                return True
            except DomainError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(worker, range(8))), 1)

    def test_scan_cancel_race(self):
        ticket = self.purchase()['tickets'][0]['id']
        barrier = Barrier(2)

        def scan():
            barrier.wait()
            try:
                self.core.redeem(ticket, 'gate', 'show', key='scan:' + ticket)
                return True
            except DomainError:
                return False

        def cancel():
            barrier.wait()
            try:
                self.core.cancel_ticket(ticket, 'finance')
                return True
            except DomainError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(scan)
            b = pool.submit(cancel)
            self.assertEqual(int(a.result()) + int(b.result()), 1)

    def test_refund_receipt_unique_and_idempotent(self):
        ticket = self.purchase()['tickets'][0]['id']
        r = self.core.cancel_ticket(ticket, 'finance')
        self.core.mark_refund_paid(r['id'], 'receipt', 'finance')
        self.core.mark_refund_paid(r['id'], 'receipt', 'finance')
        with self.assertRaisesRegex(DomainError, 'receipt_conflict'):
            self.core.mark_refund_paid(r['id'], 'different', 'finance')

    def test_settlement_maturity_negative_balance_after_cancellation(self):
        t = self.purchase()['tickets'][0]
        self.assertEqual(self.core.balance('center'), 0)
        self.now += 4 * 86400
        self.assertEqual(self.core.balance('center'), 900000)
        self.core.record_settlement('center', 900000, 'bank', 'finance')
        self.assertEqual(self.core.balance('center'), 0)
        self.core.cancel_ticket(t['id'], 'finance')
        self.assertEqual(self.core.balance('center'), -900000)

    def test_concurrent_settlement_cannot_double_record_balance(self):
        self.purchase()
        self.now += 4 * 86400
        barrier = Barrier(2)

        def worker(i):
            barrier.wait()
            try:
                self.core.record_settlement('center', 900000, f'bank{i}', 'finance')
                return True
            except DomainError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(worker, range(2))), 1)

    def test_success_does_not_close_other_unknown_payment_case(self):
        h = self.hold()
        o = self.core.create_order('web', 'order', h['hold_id'], buyer='buyer')
        self.core.record_payment(o['order_id'], 'sandbox', 'uncertain', o['amount'], 'unknown', 'adapter')
        self.core.record_payment(o['order_id'], 'sandbox', 'confirmed', o['amount'], 'succeeded', 'adapter')
        self.assertEqual(self.core.order(o['order_id'])['state'], 'completed')
        self.assertEqual(len(self.core.review_queue()), 1)
        self.core.record_payment(o['order_id'], 'sandbox', 'uncertain', o['amount'], 'failed', 'adapter')
        self.assertEqual(self.core.review_queue(), [])

    def test_unauthorized_show_scan_and_empty_actor_roll_back(self):
        t = self.purchase()['tickets'][0]
        with self.assertRaisesRegex(DomainError, 'forbidden'):
            self.core.redeem(t['id'], 'gate', 'other-show', key='scan:' + t['id'])
        with self.assertRaisesRegex(DomainError, 'actor_and_key_required'):
            self.core.redeem(t['id'], '', 'show', key='scan:' + t['id'])
        self.assertEqual(self.core.order(t['order_id'])['tickets'][0]['state'], 'valid')

    def test_events_persist_across_restart(self):
        self.purchase()
        second = Core(self.core.database, lambda: self.now)
        events = second.events()
        self.assertGreater(len(events), 0)
        self.assertEqual(len({e['id'] for e in events}), len(events))
        self.assertEqual(second.events(events[-1]['sequence']), [])
if __name__ == '__main__':
    unittest.main()
