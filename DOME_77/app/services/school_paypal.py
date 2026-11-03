"""PayPal verification boundary for live-school payments.

Transport is injected; it must authenticate to the configured PayPal API.
This module does not enable a route or alter DOME subscription processing.
API contracts: developer.paypal.com/api/webhooks/v1/verify-webhook-signature-post
and developer.paypal.com/api/orders/v2/orders-get.
"""
from decimal import Decimal, InvalidOperation
import re

from app.services.school_ledger import SchoolLedgerError


def euro_minor(amount):
    if not isinstance(amount, dict) or amount.get('currency_code') != 'EUR':
        raise SchoolLedgerError('PAYMENT_CURRENCY_MISMATCH')
    value = amount.get('value')
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
        raise SchoolLedgerError('INVALID_PAYMENT_AMOUNT')
    try:
        result = Decimal(value) * 100
        if result <= 0 or result != result.to_integral_value():
            raise SchoolLedgerError('INVALID_PAYMENT_AMOUNT')
        return int(result)
    except InvalidOperation as exc:
        raise SchoolLedgerError('INVALID_PAYMENT_AMOUNT') from exc


class SchoolPayPal:
    def __init__(self, *, request, ledger, webhook_id, merchant_id):
        if not webhook_id or not merchant_id:
            raise SchoolLedgerError('SCHOOL_PAYPAL_NOT_CONFIGURED')
        self.request = request
        self.ledger = ledger
        self.webhook_id = webhook_id
        self.merchant_id = merchant_id

    async def process(self, event, headers):
        """Only a verified completed capture can credit the ledger.

        Unknown events are ignored; refund/reversal handling must be connected
        before launch. HTTP callers must retry transient API/database failures.
        """
        if not isinstance(event, dict):
            raise SchoolLedgerError('INVALID_EVENT')
        headers = {str(k).lower(): v for k, v in headers.items()}
        mapping = {'auth_algo': 'paypal-auth-algo', 'cert_url': 'paypal-cert-url',
                   'transmission_id': 'paypal-transmission-id',
                   'transmission_sig': 'paypal-transmission-sig',
                   'transmission_time': 'paypal-transmission-time'}
        body = {key: headers.get(header) for key, header in mapping.items()}
        if any(not isinstance(v, str) or not v for v in body.values()):
            raise SchoolLedgerError('INVALID_SIGNATURE_HEADERS')
        body.update(webhook_id=self.webhook_id, webhook_event=event)
        verified = await self.request('POST', '/v1/notifications/verify-webhook-signature', body=body)
        if verified.get('verification_status') != 'SUCCESS':
            raise SchoolLedgerError('INVALID_SIGNATURE')
        if event.get('event_type') != 'PAYMENT.CAPTURE.COMPLETED':
            return {'credited': False, 'ignored': True}
        resource = event.get('resource') or {}
        if resource.get('status') != 'COMPLETED':
            raise SchoolLedgerError('CAPTURE_NOT_COMPLETED')
        capture_id = resource.get('id')
        related = (resource.get('supplementary_data') or {}).get('related_ids') or {}
        provider_order_id = related.get('order_id')
        if any(not isinstance(v, str) or not re.fullmatch(r'[A-Z0-9]{1,36}', v)
               for v in (capture_id, provider_order_id)):
            raise SchoolLedgerError('INVALID_PROVIDER_ID')
        # Re-read the order from the provider, never follow event-supplied URLs.
        order = await self.request('GET', '/v2/checkout/orders/' + provider_order_id)
        if order.get('id') != provider_order_id or order.get('status') != 'COMPLETED':
            raise SchoolLedgerError('PAYMENT_ORDER_MISMATCH')
        units = order.get('purchase_units') or []
        if len(units) != 1:
            raise SchoolLedgerError('UNSUPPORTED_PURCHASE_UNITS')
        unit = units[0]
        if (unit.get('payee') or {}).get('merchant_id') != self.merchant_id:
            raise SchoolLedgerError('PAYMENT_MERCHANT_MISMATCH')
        custom_id = unit.get('custom_id')
        if not isinstance(custom_id, str) or not custom_id.startswith('school:') or not custom_id[7:]:
            raise SchoolLedgerError('NOT_A_SCHOOL_ORDER')
        captures = (unit.get('payments') or {}).get('captures') or []
        if len(captures) != 1 or captures[0].get('id') != capture_id:
            raise SchoolLedgerError('CAPTURE_MISMATCH')
        capture = captures[0]
        if capture.get('status') != 'COMPLETED':
            raise SchoolLedgerError('CAPTURE_NOT_COMPLETED')
        total = euro_minor(unit.get('amount'))
        if total != euro_minor(capture.get('amount')) or total != euro_minor(resource.get('amount')):
            raise SchoolLedgerError('PAYMENT_AMOUNT_MISMATCH')
        credited = self.ledger.apply_verified_capture(
            order_id=custom_id[7:], provider_order_id=provider_order_id,
            capture_id=capture_id, amount_minor=total, currency='EUR', status='COMPLETED')
        return {'credited': credited, 'ignored': False}
