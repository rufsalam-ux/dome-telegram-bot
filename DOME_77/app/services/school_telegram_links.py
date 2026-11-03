"""Single-use links from authenticated Wix accounts to private Telegram chats.

The HTTP layer must resolve member_id from the Wix session. The bot layer must
take chat_id and sender_id from an authenticated Telegram update, never a form.
Only token hashes are persisted; raw link tokens expire after ten minutes.
"""
import hashlib
import secrets
import time
from app.services.school_ledger import SchoolLedgerError


class SchoolTelegramLinks:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger._transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS school_telegram_tokens (
                token_hash TEXT PRIMARY KEY, member_id TEXT NOT NULL,
                expires_at INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS school_telegram_links (
                member_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL UNIQUE,
                linked_at INTEGER NOT NULL)''')

    def issue(self, member_id, *, now=None):
        if not isinstance(member_id, str) or not member_id.strip():
            raise SchoolLedgerError('MEMBER_REQUIRED')
        now = int(time.time() if now is None else now)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.ledger._transaction() as db:
            db.execute('DELETE FROM school_telegram_tokens WHERE member_id=? OR expires_at<=?', (member_id, now))
            db.execute('INSERT INTO school_telegram_tokens VALUES (?,?,?,0)', (digest, member_id, now + 600))
        return 'school_' + token

    def claim(self, start_payload, *, chat_id, sender_id, chat_type, now=None):
        if chat_type != 'private' or type(chat_id) is not int or chat_id <= 0 or chat_id != sender_id:
            raise SchoolLedgerError('PRIVATE_CHAT_REQUIRED')
        if not isinstance(start_payload, str) or not start_payload.startswith('school_'):
            raise SchoolLedgerError('INVALID_LINK')
        digest = hashlib.sha256(start_payload[7:].encode()).hexdigest()
        now = int(time.time() if now is None else now)
        with self.ledger._transaction() as db:
            token = db.execute('SELECT * FROM school_telegram_tokens WHERE token_hash=?', (digest,)).fetchone()
            if not token or token['used'] or token['expires_at'] <= now:
                raise SchoolLedgerError('LINK_EXPIRED_OR_USED')
            member_id = token['member_id']
            existing = db.execute('SELECT * FROM school_telegram_links WHERE member_id=? OR chat_id=?', (member_id, chat_id)).fetchall()
            if any(row['member_id'] != member_id or row['chat_id'] != chat_id for row in existing):
                raise SchoolLedgerError('ACCOUNT_ALREADY_LINKED')
            if not existing:
                db.execute('INSERT INTO school_telegram_links VALUES (?,?,?)', (member_id, chat_id, now))
            db.execute('UPDATE school_telegram_tokens SET used=1 WHERE token_hash=?', (digest,))
            return member_id

    def chat_for_member(self, member_id):
        with self.ledger._transaction() as db:
            row = db.execute('SELECT chat_id FROM school_telegram_links WHERE member_id=?', (member_id,)).fetchone()
            return row['chat_id'] if row else None

    def unlink(self, member_id):
        """Caller must authorize this exact member; no admin role is granted."""
        with self.ledger._transaction() as db:
            db.execute('DELETE FROM school_telegram_links WHERE member_id=?', (member_id,))
            db.execute('UPDATE school_telegram_tokens SET used=1 WHERE member_id=?', (member_id,))
