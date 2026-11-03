"""Private school outbox. Enqueue only from authorized server-side events.

An interrupted or timed-out delivery is retained for review, never blindly
resent: Telegram sendMessage has no idempotency key. No network worker is
started by importing this module.
"""
import time
from app.services.school_ledger import SchoolLedgerError


class SchoolNotifications:
    def __init__(self, links):
        self.links = links
        self.ledger = links.ledger
        with self.ledger._transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS school_notifications (
                event_id TEXT NOT NULL, member_id TEXT NOT NULL, body TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', chat_id INTEGER,
                message_id INTEGER, PRIMARY KEY(event_id, member_id))''')

    def enqueue(self, event_id, member_id, body, *, expires_at, now=None):
        now = int(time.time() if now is None else now)
        if any(not isinstance(v, str) or not v.strip() for v in (event_id, member_id, body)):
            raise SchoolLedgerError('INVALID_NOTIFICATION')
        if len(body) > 4096 or type(expires_at) is not int or expires_at <= now:
            raise SchoolLedgerError('INVALID_NOTIFICATION')
        with self.ledger._transaction() as db:
            prior = db.execute('SELECT body, expires_at FROM school_notifications WHERE event_id=? AND member_id=?',
                               (event_id, member_id)).fetchone()
            if prior:
                if prior['body'] != body or prior['expires_at'] != expires_at:
                    raise SchoolLedgerError('NOTIFICATION_CONFLICT')
                return False
            db.execute('INSERT INTO school_notifications (event_id,member_id,body,created_at,expires_at) VALUES (?,?,?,?,?)',
                       (event_id, member_id, body, now, expires_at))
            return True

    async def deliver_one(self, send, *, now=None):
        """send(chat_id, body) returns Telegram message ID; no user-supplied chat ID.

        Resolve the current link when claiming, so queued messages do not retain
        stale chat IDs. Serial delivery workers are supported across processes.
        Unlink does not recall a send already in flight.
        """
        now = int(time.time() if now is None else now)
        with self.ledger._transaction() as db:
            db.execute("UPDATE school_notifications SET state='expired' WHERE state='pending' AND expires_at<=?", (now,))
            row = db.execute('''SELECT n.*, l.chat_id AS recipient FROM school_notifications n
                JOIN school_telegram_links l ON l.member_id=n.member_id
                WHERE n.state='pending' ORDER BY n.created_at, n.event_id LIMIT 1''').fetchone()
            if not row:
                return None
            key = (row['event_id'], row['member_id'])
            db.execute("UPDATE school_notifications SET state='sending',chat_id=? WHERE event_id=? AND member_id=?",
                       (row['recipient'], *key))
        try:
            message_id = await send(row['recipient'], row['body'])
            if type(message_id) is not int or message_id <= 0:
                raise ValueError('Missing delivery receipt')
        except Exception:
            # Do not persist exception strings: transport errors may contain tokens.
            with self.ledger._transaction() as db:
                db.execute("UPDATE school_notifications SET state='review' WHERE event_id=? AND member_id=?", key)
            return {'state': 'review', 'event_id': key[0]}
        with self.ledger._transaction() as db:
            db.execute("UPDATE school_notifications SET state='sent',message_id=? WHERE event_id=? AND member_id=?",
                       (message_id, *key))
        return {'state': 'sent', 'event_id': key[0]}

    def recover_interrupted(self):
        """Run once at startup before any workers. Manual review prevents duplicates."""
        with self.ledger._transaction() as db:
            return db.execute("UPDATE school_notifications SET state='review' WHERE state='sending'").rowcount
