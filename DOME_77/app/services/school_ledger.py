"""Transactional live-school ledger, separate from DOME subscriptions.

Internal service only: callers must authorize Wix members and verify provider
signatures before applying captures. No browser-facing endpoint is installed.
Use a dedicated persistent SQLite file on a single service/volume.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3


class SchoolLedgerError(ValueError):
    pass


class SchoolLedger:
    def __init__(self, path):
        self.path = str(path)
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS school_orders (
                    id TEXT PRIMARY KEY, student_id TEXT NOT NULL,
                    course_id TEXT NOT NULL, format TEXT NOT NULL
                        CHECK(format IN ('individual','group')),
                    lessons INTEGER NOT NULL CHECK(lessons > 0),
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL, provider_order_id TEXT UNIQUE,
                    capture_id TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'pending',
                    consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed >= 0),
                    misses INTEGER NOT NULL DEFAULT 0 CHECK(misses >= 0)
                );
                CREATE TABLE IF NOT EXISTS school_lesson_results (
                    lesson_id TEXT PRIMARY KEY, order_id TEXT NOT NULL
                        REFERENCES school_orders(id), outcome TEXT NOT NULL,
                    charged INTEGER NOT NULL, message TEXT NOT NULL
                );
            """)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_order(self, *, order_id, student_id, course_id, format,
                     lessons, amount_minor, currency, allowed_formats,
                     group_approved=False):
        """Inputs come from server catalog/enrollment, never client prices."""
        if format not in ('individual', 'group') or format not in allowed_formats:
            raise SchoolLedgerError('FORMAT_NOT_ALLOWED')
        if format == 'group' and not group_approved:
            raise SchoolLedgerError('GROUP_APPROVAL_REQUIRED')
        if any(not isinstance(v, str) or not v.strip() for v in
               (order_id, student_id, course_id, currency)):
            raise SchoolLedgerError('INVALID_ORDER')
        if type(lessons) is not int or lessons <= 0 or type(amount_minor) is not int or amount_minor <= 0:
            raise SchoolLedgerError('INVALID_ORDER')
        values = (order_id, student_id, course_id, format, lessons, amount_minor, currency)
        with self._transaction() as db:
            prior = db.execute('SELECT * FROM school_orders WHERE id=?', (order_id,)).fetchone()
            if prior:
                keys = ('id', 'student_id', 'course_id', 'format', 'lessons', 'amount_minor', 'currency')
                if tuple(prior[k] for k in keys) != values:
                    raise SchoolLedgerError('ORDER_CONFLICT')
                return
            db.execute('INSERT INTO school_orders (id,student_id,course_id,format,lessons,amount_minor,currency) VALUES (?,?,?,?,?,?,?)', values)

    def bind_provider_order(self, order_id, provider_order_id):
        if not provider_order_id:
            raise SchoolLedgerError('INVALID_PROVIDER_ORDER')
        with self._transaction() as db:
            row = self._order(db, order_id)
            if row['provider_order_id'] not in (None, provider_order_id):
                raise SchoolLedgerError('ORDER_CONFLICT')
            db.execute('UPDATE school_orders SET provider_order_id=? WHERE id=?', (provider_order_id, order_id))

    @staticmethod
    def _order(db, order_id):
        row = db.execute('SELECT * FROM school_orders WHERE id=?', (order_id,)).fetchone()
        if row is None:
            raise SchoolLedgerError('ORDER_NOT_FOUND')
        return row

    def apply_verified_capture(self, *, order_id, provider_order_id, capture_id,
                               amount_minor, currency, status):
        """Only after authenticating PayPal and matching merchant + full capture.

        A client redirect or an AI message must NEVER call this method.
        Returns False for an exact replay; conflicting receipts are rejected.
        """
        if status != 'COMPLETED' or not capture_id or type(amount_minor) is not int:
            raise SchoolLedgerError('CAPTURE_NOT_COMPLETED')
        with self._transaction() as db:
            row = self._order(db, order_id)
            if not row['provider_order_id'] or row['provider_order_id'] != provider_order_id:
                raise SchoolLedgerError('PAYMENT_ORDER_MISMATCH')
            if row['amount_minor'] != amount_minor or row['currency'] != currency:
                raise SchoolLedgerError('PAYMENT_AMOUNT_MISMATCH')
            if row['capture_id']:
                if row['capture_id'] == capture_id and row['status'] == 'paid':
                    return False
                raise SchoolLedgerError('PAYMENT_CONFLICT')
            used = db.execute('SELECT id FROM school_orders WHERE capture_id=?', (capture_id,)).fetchone()
            if used:
                raise SchoolLedgerError('PAYMENT_ALREADY_USED')
            db.execute("UPDATE school_orders SET capture_id=?,status='paid' WHERE id=?", (capture_id, order_id))
            return True

    def balance(self, order_id):
        with self._transaction() as db:
            row = self._order(db, order_id)
            return {'student_id': row['student_id'], 'course_id': row['course_id'],
                    'format': row['format'], 'remaining': row['lessons'] - row['consumed'] if row['status'] == 'paid' else 0,
                    'free_misses_remaining': max(0, 2 - row['misses'])}

    def settle_lesson(self, *, lesson_id, order_id, student_id, course_id,
                      format, outcome, starts_at, now):
        """Authorized scheduler supplies persisted booking identity and times.

        Booking/reservation and attendance verification remain caller concerns.
        Missing attendance must not be inferred from missing Telegram activity.
        """
        if not lesson_id or outcome not in ('attended', 'cancelled', 'no_show'):
            raise SchoolLedgerError('INVALID_OUTCOME')
        if any(not isinstance(t, datetime) or t.utcoffset() is None for t in (starts_at, now)):
            raise SchoolLedgerError('TIMEZONE_REQUIRED')
        delta = (now.astimezone(timezone.utc) - starts_at.astimezone(timezone.utc)).total_seconds()
        with self._transaction() as db:
            row = self._order(db, order_id)
            if (student_id, course_id, format) != (row['student_id'], row['course_id'], row['format']):
                raise SchoolLedgerError('LESSON_PACKAGE_MISMATCH')
            prior = db.execute('SELECT * FROM school_lesson_results WHERE lesson_id=?', (lesson_id,)).fetchone()
            if prior:
                if prior['order_id'] != order_id or prior['outcome'] != outcome:
                    raise SchoolLedgerError('LESSON_ALREADY_SETTLED')
                return dict(prior)
            if outcome == 'cancelled':
                if delta > -60:
                    raise SchoolLedgerError('CANCELLATION_TOO_LATE')
                charged, message = 0, 'Урок отменён. Оплаченное занятие сохранено.'
            else:
                if row['status'] != 'paid' or row['consumed'] >= row['lessons']:
                    raise SchoolLedgerError('NO_PAID_LESSONS')
                minimum = 600 if outcome == 'no_show' else (1800 if format == 'individual' else 3300)
                if delta < minimum:
                    raise SchoolLedgerError('TOO_EARLY')
                charged = int(outcome == 'attended' or row['misses'] >= 2)
                message = 'Урок проведён и списан из оплаченного пакета.'
                if outcome == 'no_show':
                    db.execute('UPDATE school_orders SET misses=misses+1 WHERE id=?', (order_id,))
                    message = ('Нам очень жаль, что сегодня не удалось посетить занятие. Педагог ждал вас. '
                               'Урок списан из оплаченного пакета.' if charged else
                               'Нам жаль, что сегодня не удалось посетить занятие. В этот раз урок не списан. '
                               'Первые два пропуска в каждом новом пакете бесплатны; начиная с третьего, урок списывается. '
                               'Вы можете отменить занятие не позднее чем за минуту до начала без списания.')
            db.execute('UPDATE school_orders SET consumed=consumed+? WHERE id=?', (charged, order_id))
            db.execute('INSERT INTO school_lesson_results VALUES (?,?,?,?,?)', (lesson_id, order_id, outcome, charged, message))
            return {'lesson_id': lesson_id, 'order_id': order_id, 'outcome': outcome,
                    'charged': charged, 'message': message}
