from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.db.models import Base

engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def _add_columns(conn, table: str, additions: dict[str, str]):
    def existing_columns(sync_conn):
        return {column["name"] for column in inspect(sync_conn).get_columns(table)}

    names = await conn.run_sync(existing_columns)
    for name, ddl in additions.items():
        if name not in names:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def _dedupe_lesson_entitlements(conn) -> None:
    """Merge legacy duplicate rows before installing the uniqueness guard.

    Old versions did not have a composite uniqueness constraint, so two runtime
    callers could theoretically unlock the same lesson twice. Preserve the most
    permissive/advanced state, then keep one canonical row.
    """
    groups = (await conn.execute(text(
        """
        SELECT child_id, lesson_id, course_id, COUNT(*) AS n
        FROM lesson_entitlements
        GROUP BY child_id, lesson_id, course_id
        HAVING COUNT(*) > 1
        """
    ))).mappings().all()
    for g in groups:
        rows = (await conn.execute(text(
            """
            SELECT id, unlocked_at, expires_at, max_completed_runs, completed_runs,
                   cartoon_generated, source, status
            FROM lesson_entitlements
            WHERE child_id=:child_id AND lesson_id=:lesson_id AND course_id=:course_id
            ORDER BY id ASC
            """
        ), dict(g))).mappings().all()
        if not rows:
            continue
        keep_id = int(rows[-1]["id"])
        unlocked = min((r["unlocked_at"] for r in rows if r["unlocked_at"] is not None), default=None)
        expires = max((r["expires_at"] for r in rows if r["expires_at"] is not None), default=None)
        max_runs = max(int(r["max_completed_runs"] or 2) for r in rows)
        completed = min(max_runs, max(int(r["completed_runs"] or 0) for r in rows))
        cartoon = 1 if any(bool(r["cartoon_generated"]) for r in rows) else 0
        status = "COMPLETED" if completed >= max_runs else "ACTIVE"
        await conn.execute(text(
            """
            UPDATE lesson_entitlements
            SET unlocked_at=COALESCE(:unlocked_at, unlocked_at),
                expires_at=:expires_at,
                max_completed_runs=:max_runs,
                completed_runs=:completed,
                cartoon_generated=:cartoon,
                status=:status
            WHERE id=:keep_id
            """
        ), {
            "unlocked_at": unlocked,
            "expires_at": expires,
            "max_runs": max_runs,
            "completed": completed,
            "cartoon": cartoon,
            "status": status,
            "keep_id": keep_id,
        })
        await conn.execute(text(
            "DELETE FROM lesson_entitlements WHERE child_id=:child_id AND lesson_id=:lesson_id AND course_id=:course_id AND id<>:keep_id"
        ), {**dict(g), "keep_id": keep_id})


async def init_db() -> None:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to an existing table. Keep the mobile
        # identity fields portable across the bundled SQLite DB and Railway Postgres.
        await _add_columns(conn, "parents", {
            "email": "VARCHAR(255)",
            "password_hash": "VARCHAR(255)",
            "email_verified": "BOOLEAN NOT NULL DEFAULT TRUE",
            "email_verification_code_hash": "VARCHAR(255)",
            "email_verification_expires_at": "TIMESTAMP",
            "email_reports_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
            "phone": "VARCHAR(40)",
            "active_child_id": "INTEGER",
        })
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_parents_active_child_id ON parents(active_child_id)"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_parents_email ON parents(email) WHERE email IS NOT NULL AND email <> ''"))
        # Existing accounts predate standalone verification. New registrations
        # explicitly set email_verified=False until the emailed code is entered.
        await conn.execute(text(
            "UPDATE parents SET email_verified=TRUE "
            "WHERE COALESCE(email_verified,FALSE)=FALSE "
            "AND email IS NOT NULL AND email <> '' AND password_hash IS NOT NULL "
            "AND email_verification_code_hash IS NULL AND created_at < '2026-08-22 00:00:00'"
        ))
        if settings.database_url.startswith("sqlite"):
            await _add_columns(conn, "characters", {
                "source": "VARCHAR(40) NOT NULL DEFAULT 'CHILD_DRAWING'", "catalog_id": "VARCHAR(80)"})
            await _add_columns(conn, "children", {
                "country": "VARCHAR(120)", "language_level": "VARCHAR(20) NOT NULL DEFAULT 'PRE_A1'",
                "working_difficulty": "FLOAT NOT NULL DEFAULT 0.15", "comprehension_score": "FLOAT NOT NULL DEFAULT 0",
                "grammar_score": "FLOAT NOT NULL DEFAULT 0", "vocabulary_score": "FLOAT NOT NULL DEFAULT 0",
                "pronunciation_score": "FLOAT NOT NULL DEFAULT 0", "fluency_score": "FLOAT NOT NULL DEFAULT 0",
                "independence_score": "FLOAT NOT NULL DEFAULT 0", "answers_count": "INTEGER NOT NULL DEFAULT 0", "age_years": "INTEGER",
                "birth_day": "INTEGER", "birth_month": "INTEGER", "birth_year": "INTEGER", "gender": "VARCHAR(16)",
                "birthday_greeted_year": "INTEGER", "can_read_target": "BOOLEAN"})
            await _add_columns(conn, "lesson_sessions", {
                "level_at_start": "VARCHAR(20) NOT NULL DEFAULT 'PRE_A1'", "level_at_end": "VARCHAR(20)", "completed_at": "DATETIME",
                "lesson_revision": "INTEGER NOT NULL DEFAULT 11", "runtime_state_json": "TEXT NOT NULL DEFAULT '{}'"})
            await _add_columns(conn, "voice_attempts", {
                "transcript": "TEXT", "detected_language": "VARCHAR(30)", "confidence": "FLOAT",
                "grammar_errors": "TEXT", "pronunciation_errors": "TEXT", "semantic_match": "FLOAT",
                "comprehension_score": "FLOAT", "grammar_score": "FLOAT", "vocabulary_score": "FLOAT",
                "pronunciation_score": "FLOAT", "fluency_score": "FLOAT", "independence_score": "FLOAT",
                "recommended_difficulty": "FLOAT"})
            await _add_columns(conn, "homework_assignments", {
                "current_step": "INTEGER NOT NULL DEFAULT 0"})
            await _add_columns(conn, "subscriptions", {
                "payment_provider": "VARCHAR(30) NOT NULL DEFAULT 'manual'",
                "provider_subscription_id": "VARCHAR(255)",
                "release_baseline_count": "INTEGER NOT NULL DEFAULT 0"})
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_subscriptions_provider_subscription_id ON subscriptions(provider_subscription_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_subscriptions_payment_provider ON subscriptions(payment_provider)"))
            await conn.execute(text("UPDATE subscriptions SET payment_provider='stripe' WHERE (payment_provider IS NULL OR payment_provider='manual') AND provider_subscription_id LIKE 'sub_%'"))
            await _dedupe_lesson_entitlements(conn)
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_lesson_entitlement_child_lesson_course "
                "ON lesson_entitlements(child_id, lesson_id, course_id)"
            ))
            await conn.execute(text("UPDATE characters SET source='BOT_CATALOG' WHERE original_path LIKE '%preset-characters%'"))
            await conn.execute(text("UPDATE children SET native_language='ru' WHERE native_language IS NULL OR native_language=''"))
            await conn.execute(text("UPDATE children SET target_language='en' WHERE target_language IS NULL OR target_language=''"))
