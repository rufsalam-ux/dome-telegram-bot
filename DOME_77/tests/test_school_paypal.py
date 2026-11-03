import copy
import tempfile
import unittest
from pathlib import Path
from app.services.school_ledger import SchoolLedger, SchoolLedgerError
from app.services.school_paypal import SchoolPayPal, euro_minor


class SchoolPayPalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = SchoolLedger(Path(self.tmp.name) / 'school.db')
        self.ledger.create_order(order_id='child-package', student_id='child-1',
            course_id='reading', format='individual', lessons=4, amount_minor=7200,
            currency='EUR', allowed_formats=['individual'])
        self.ledger.bind_provider_order('child-package', 'ORDER123')
        self.headers = {key: 'example' for key in ('paypal-auth-algo', 'paypal-cert-url',
            'paypal-transmission-id', 'paypal-transmission-sig', 'paypal-transmission-time')}
        amount = {'value': '72.00', 'currency_code': 'EUR'}
        self.event = {'event_type': 'PAYMENT.CAPTURE.COMPLETED', 'resource': {
            'id': 'CAPTURE123', 'status': 'COMPLETED', 'amount': copy.deepcopy(amount),
            'supplementary_data': {'related_ids': {'order_id': 'ORDER123'}}}}
        self.order = {'id': 'ORDER123', 'status': 'COMPLETED', 'purchase_units': [{
            'custom_id': 'school:child-package', 'payee': {'merchant_id': 'MERCHANT'},
            'amount': copy.deepcopy(amount), 'payments': {'captures': [{
                'id': 'CAPTURE123', 'status': 'COMPLETED', 'amount': copy.deepcopy(amount)}]}}]}
        self.calls = []
        self.signature = 'SUCCESS'
        async def request(method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            if method == 'POST':
                return {'verification_status': self.signature}
            return self.order
        self.processor = SchoolPayPal(request=request, ledger=self.ledger,
                                      webhook_id='WEBHOOK', merchant_id='MERCHANT')

    async def test_verified_capture_and_replay(self):
        self.assertTrue((await self.processor.process(self.event, self.headers))['credited'])
        self.assertFalse((await self.processor.process(self.event, self.headers))['credited'])
        self.assertEqual(self.ledger.balance('child-package')['remaining'], 4)
        self.assertEqual(self.calls[0][2]['body']['webhook_id'], 'WEBHOOK')

    async def test_invalid_signature_does_not_fetch_or_credit(self):
        self.signature = 'FAILURE'
        with self.assertRaisesRegex(SchoolLedgerError, 'INVALID_SIGNATURE'):
            await self.processor.process(self.event, self.headers)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.ledger.balance('child-package')['remaining'], 0)

    async def test_wrong_merchant_not_credited(self):
        self.order['purchase_units'][0]['payee']['merchant_id'] = 'OTHER'
        with self.assertRaisesRegex(SchoolLedgerError, 'MERCHANT_MISMATCH'):
            await self.processor.process(self.event, self.headers)

    async def test_dome_subscription_not_school_payment(self):
        self.order['purchase_units'][0]['custom_id'] = 'dome:child-package'
        with self.assertRaisesRegex(SchoolLedgerError, 'NOT_A_SCHOOL_ORDER'):
            await self.processor.process(self.event, self.headers)

    async def test_partial_capture_not_credited(self):
        self.order['purchase_units'][0]['payments']['captures'][0]['amount']['value'] = '36.00'
        with self.assertRaisesRegex(SchoolLedgerError, 'AMOUNT_MISMATCH'):
            await self.processor.process(self.event, self.headers)

    async def test_pending_order_not_credited(self):
        self.order['status'] = 'APPROVED'
        with self.assertRaisesRegex(SchoolLedgerError, 'ORDER_MISMATCH'):
            await self.processor.process(self.event, self.headers)

    async def test_url_in_provider_id_rejected_without_fetch(self):
        self.event['resource']['supplementary_data']['related_ids']['order_id'] = '../../anything'
        with self.assertRaisesRegex(SchoolLedgerError, 'INVALID_PROVIDER_ID'):
            await self.processor.process(self.event, self.headers)
        self.assertEqual(len(self.calls), 1)

    def test_money_never_rounded_or_parsed_as_float(self):
        self.assertEqual(euro_minor({'currency_code': 'EUR', 'value': '127.20'}), 12720)
        for invalid in ('NaN', 'Infinity', '-1', '0', '1.001', '1e2', 72.0):
            with self.assertRaises(SchoolLedgerError):
                euro_minor({'currency_code': 'EUR', 'value': invalid})


if __name__ == '__main__':
    unittest.main()
