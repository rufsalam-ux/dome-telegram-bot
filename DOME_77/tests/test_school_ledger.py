import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.school_ledger import SchoolLedger, SchoolLedgerError


class SchoolLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = SchoolLedger(Path(self.tmp.name) / 'school.sqlite')
        self.start = datetime(2026, 11, 10, 12, 20, tzinfo=timezone.utc)
        self.order('one')

    def order(self, key, student='child-1', lessons=4, **overrides):
        args = dict(order_id=key, student_id=student, course_id='reading',
                    format='individual', lessons=lessons, amount_minor=7200,
                    currency='EUR', allowed_formats=['individual'])
        args.update(overrides)
        self.ledger.create_order(**args)
        self.ledger.bind_provider_order(key, 'paypal-' + key)

    def pay(self, key='one', **overrides):
        args = dict(order_id=key, provider_order_id='paypal-' + key,
                    capture_id='capture-' + key, amount_minor=7200,
                    currency='EUR', status='COMPLETED')
        args.update(overrides)
        return self.ledger.apply_verified_capture(**args)

    def settle(self, lesson, key='one', outcome='no_show', **overrides):
        args = dict(lesson_id=lesson, order_id=key, student_id='child-1',
                    course_id='reading', format='individual', outcome=outcome,
                    starts_at=self.start, now=self.start + timedelta(minutes=60))
        args.update(overrides)
        return self.ledger.settle_lesson(**args)

    def test_payment_is_bound_to_order_and_cannot_be_reused(self):
        self.order('two', student='child-2')
        with self.assertRaisesRegex(SchoolLedgerError, 'ORDER_MISMATCH'):
            self.pay('two', provider_order_id='paypal-one')
        self.pay()
        with self.assertRaisesRegex(SchoolLedgerError, 'ALREADY_USED'):
            self.pay('two', capture_id='capture-one')
        self.assertEqual(self.ledger.balance('two')['remaining'], 0)
        self.assertEqual(self.ledger.balance('one')['remaining'], 4)

    def test_amount_currency_and_pending_capture_rejected(self):
        for mismatch in ({'amount_minor': 7201}, {'currency': 'USD'}, {'status': 'PENDING'}):
            with self.assertRaises(SchoolLedgerError):
                self.pay(**mismatch)
        self.assertEqual(self.ledger.balance('one')['remaining'], 0)

    def test_concurrent_payment_replays_credit_once(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(lambda _: self.pay(), range(12)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(self.ledger.balance('one')['remaining'], 4)

    def test_each_new_package_has_two_free_misses(self):
        self.pay()
        self.assertEqual([self.settle(str(n))['charged'] for n in range(3)], [0, 0, 1])
        self.assertEqual(self.ledger.balance('one')['remaining'], 3)
        self.order('new'); self.pay('new')
        self.assertEqual(self.settle('new-first', 'new')['charged'], 0)
        self.assertEqual(self.ledger.balance('new')['free_misses_remaining'], 1)

    def test_cancellation_boundary_and_no_show_boundary(self):
        self.pay()
        self.assertEqual(self.settle('cancel', outcome='cancelled', now=self.start - timedelta(seconds=60))['charged'], 0)
        with self.assertRaisesRegex(SchoolLedgerError, 'TOO_LATE'):
            self.settle('late', outcome='cancelled', now=self.start - timedelta(seconds=59))
        with self.assertRaisesRegex(SchoolLedgerError, 'TOO_EARLY'):
            self.settle('miss', now=self.start + timedelta(seconds=599))
        self.settle('miss', now=self.start + timedelta(seconds=600))
        self.assertEqual(self.ledger.balance('one')['free_misses_remaining'], 1)

    def test_group_requires_approved_enrollment(self):
        with self.assertRaisesRegex(SchoolLedgerError, 'FORMAT_NOT_ALLOWED'):
            self.order('group', format='group')
        with self.assertRaisesRegex(SchoolLedgerError, 'GROUP_APPROVAL_REQUIRED'):
            self.order('group', format='group', allowed_formats=['individual', 'group'])
        self.order('group', format='group', allowed_formats=['individual', 'group'], group_approved=True)

    def test_no_paid_balance_and_wrong_child_cannot_be_settled(self):
        with self.assertRaisesRegex(SchoolLedgerError, 'NO_PAID_LESSONS'):
            self.settle('unpaid', outcome='attended')
        self.pay()
        for mismatch in ({'student_id': 'child-2'}, {'course_id': 'writing'}, {'format': 'group'}):
            with self.assertRaisesRegex(SchoolLedgerError, 'PACKAGE_MISMATCH'):
                self.settle('wrong', **mismatch)

    def test_concurrent_last_lesson_does_not_overdraw(self):
        self.order('last', lessons=1); self.pay('last')
        def finish(n):
            try:
                self.settle('last-' + str(n), 'last', outcome='attended')
                return True
            except SchoolLedgerError:
                return False
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(finish, range(10)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.ledger.balance('last')['remaining'], 0)

    def test_settlement_replay_does_not_spend_another_free_miss(self):
        self.pay()
        first = self.settle('same')
        self.assertEqual(self.settle('same'), first)
        self.assertEqual(self.ledger.balance('one')['free_misses_remaining'], 1)
        with self.assertRaisesRegex(SchoolLedgerError, 'ALREADY_SETTLED'):
            self.settle('same', outcome='attended')


if __name__ == '__main__':
    unittest.main()
