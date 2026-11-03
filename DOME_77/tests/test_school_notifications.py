import asyncio
import tempfile
import unittest
from pathlib import Path
from app.services.school_ledger import SchoolLedger, SchoolLedgerError
from app.services.school_telegram_links import SchoolTelegramLinks
from app.services.school_notifications import SchoolNotifications


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = SchoolTelegramLinks(SchoolLedger(Path(self.tmp.name) / 'school.db'))
        self.queue = SchoolNotifications(self.links)
        self.sent = []

    def link(self, member, chat):
        token = self.links.issue(member, now=100)
        self.links.claim(token, chat_id=chat, sender_id=chat, chat_type='private', now=101)

    async def send(self, chat, body):
        self.sent.append((chat, body))
        await asyncio.sleep(0)
        return len(self.sent)

    def enqueue(self, member='parent-1', body='Your lesson starts soon'):
        return self.queue.enqueue('reminder-1', member, body, expires_at=200, now=100)

    async def test_personal_recipient_and_duplicate_event(self):
        self.link('parent-1', 123)
        self.link('parent-2', 456)
        self.assertTrue(self.enqueue())
        self.assertFalse(self.enqueue())
        await asyncio.gather(*(self.queue.deliver_one(self.send, now=110) for _ in range(4)))
        self.assertEqual(self.sent, [(123, 'Your lesson starts soon')])

    async def test_unlinked_and_expired_never_send(self):
        self.enqueue()
        self.assertIsNone(await self.queue.deliver_one(self.send, now=110))
        self.link('parent-1', 123)
        self.assertIsNone(await self.queue.deliver_one(self.send, now=200))
        self.assertEqual(self.sent, [])

    async def test_unlink_and_relink_resolves_new_chat(self):
        self.link('parent-1', 123)
        self.enqueue()
        self.links.unlink('parent-1')
        self.assertIsNone(await self.queue.deliver_one(self.send, now=110))
        self.link('parent-1', 456)
        await self.queue.deliver_one(self.send, now=110)
        self.assertEqual(self.sent[0][0], 456)

    async def test_timeout_is_not_blindly_retried(self):
        self.link('parent-1', 123)
        self.enqueue()
        async def uncertain(chat, body):
            self.sent.append((chat, body))
            raise TimeoutError('Response lost')
        self.assertEqual((await self.queue.deliver_one(uncertain, now=110))['state'], 'review')
        self.assertIsNone(await self.queue.deliver_one(self.send, now=111))
        self.assertEqual(len(self.sent), 1)

    async def test_cancelled_worker_recovered_without_duplicate(self):
        self.link('parent-1', 123)
        self.enqueue()
        async def cancelled(chat, body):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.queue.deliver_one(cancelled, now=110)
        self.assertEqual(self.queue.recover_interrupted(), 1)
        self.assertIsNone(await self.queue.deliver_one(self.send, now=111))

    async def test_conflicting_replay_and_multiple_recipients(self):
        self.link('parent-1', 123)
        self.link('teacher-1', 456)
        self.enqueue()
        with self.assertRaises(SchoolLedgerError):
            self.enqueue(body='Changed')
        self.enqueue('teacher-1', 'Teacher reminder')
        await self.queue.deliver_one(self.send, now=110)
        await self.queue.deliver_one(self.send, now=110)
        self.assertCountEqual(self.sent, [(123, 'Your lesson starts soon'), (456, 'Teacher reminder')])
