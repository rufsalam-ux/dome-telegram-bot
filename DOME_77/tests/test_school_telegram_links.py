import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from app.services.school_ledger import SchoolLedger, SchoolLedgerError
from app.services.school_telegram_links import SchoolTelegramLinks


class SchoolTelegramLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.links = SchoolTelegramLinks(SchoolLedger(Path(self.tmp.name) / 'school.db'))

    def claim(self, token, chat_id=123, now=101, **overrides):
        args = dict(chat_id=chat_id, sender_id=chat_id, chat_type='private', now=now)
        args.update(overrides)
        return self.links.claim(token, **args)

    def test_single_use_and_correct_recipient(self):
        token = self.links.issue('parent-1', now=100)
        self.assertLessEqual(len(token), 64)
        self.assertEqual(self.claim(token), 'parent-1')
        self.assertEqual(self.links.chat_for_member('parent-1'), 123)
        self.assertIsNone(self.links.chat_for_member('parent-2'))
        with self.assertRaises(SchoolLedgerError):
            self.claim(token, chat_id=456)

    def test_group_and_spoofed_sender_cannot_claim(self):
        token = self.links.issue('parent-1', now=100)
        for overrides in ({'chat_type': 'group'}, {'sender_id': 456}, {'chat_id': -123}):
            with self.assertRaisesRegex(SchoolLedgerError, 'PRIVATE_CHAT_REQUIRED'):
                self.claim(token, **overrides)
        self.assertIsNone(self.links.chat_for_member('parent-1'))

    def test_expiry_and_reissue_revoke_old_link(self):
        old = self.links.issue('parent-1', now=100)
        current = self.links.issue('parent-1', now=110)
        with self.assertRaises(SchoolLedgerError):
            self.claim(old)
        with self.assertRaises(SchoolLedgerError):
            self.claim(current, now=710)

    def test_one_chat_cannot_silently_take_another_account(self):
        self.claim(self.links.issue('parent-1', now=100))
        with self.assertRaisesRegex(SchoolLedgerError, 'ACCOUNT_ALREADY_LINKED'):
            self.claim(self.links.issue('parent-2', now=100))
        self.assertEqual(self.links.chat_for_member('parent-1'), 123)

    def test_concurrent_claim_has_one_winner(self):
        token = self.links.issue('parent-1', now=100)
        def take(chat):
            try:
                self.claim(token, chat_id=chat)
                return True
            except SchoolLedgerError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(take, range(100, 108))), 1)

    def test_unlink_revokes_pending_links(self):
        self.claim(self.links.issue('parent-1', now=100))
        pending = self.links.issue('parent-1', now=105)
        self.links.unlink('parent-1')
        self.assertIsNone(self.links.chat_for_member('parent-1'))
        with self.assertRaises(SchoolLedgerError):
            self.claim(pending)


if __name__ == '__main__':
    unittest.main()
