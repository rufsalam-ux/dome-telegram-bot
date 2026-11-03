import tempfile
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from app.services.school_ledger import SchoolLedger
from app.services.school_telegram_links import SchoolTelegramLinks
from app.webapp.school_api import register_school_routes

KEY = 'test-only-bridge-key-' + 'x' * 43


class SchoolAPITests(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = SchoolTelegramLinks(SchoolLedger(Path(self.tmp.name) / 'school.db'))
        self.ready = True
        app = web.Application()
        register_school_routes(app, self.links, KEY, lambda: self.ready)
        return app

    async def call(self, action, data=None, key=KEY):
        return await self.client.post('/school/telegram/' + action,
            json={'memberId': 'member-1'} if data is None else data,
            headers={'Authorization': 'Bearer ' + key})

    async def test_no_access_without_server_credential(self):
        self.assertEqual((await self.call('link', key='wrong')).status, 401)
        with self.links.ledger._transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM school_telegram_tokens').fetchone()[0], 0)

    async def test_full_link_status_and_unlink(self):
        response = await self.call('link')
        data = await response.json()
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertTrue(data['url'].startswith('https://t.me/BilingvaDom_bot?start=school_'))
        self.links.claim(data['url'].split('start=')[1], chat_id=123, sender_id=123, chat_type='private')
        status = await (await self.call('status')).json()
        self.assertTrue(status['linked'])
        self.assertNotIn('chatId', status)
        other = await (await self.call('status', {'memberId': 'member-2'})).json()
        self.assertFalse(other['linked'])
        await self.call('unlink')
        self.assertIsNone(self.links.chat_for_member('member-1'))

    async def test_extra_identity_fields_and_large_body_rejected(self):
        self.assertEqual((await self.call('link', {'memberId': 'member-1', 'chatId': 123})).status, 400)
        self.assertEqual((await self.call('link', {'memberId': 'x' * 3000})).status, 413)
        self.assertEqual((await self.call('link', [])).status, 400)

    async def test_offline_bot_cannot_issue_links(self):
        self.ready = False
        self.assertEqual((await self.call('link')).status, 503)

    async def test_bridge_does_not_expose_payment_or_message_endpoints(self):
        self.assertEqual((await self.call('send')).status, 404)
        self.assertEqual((await self.call('credit')).status, 404)
