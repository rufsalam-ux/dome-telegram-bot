from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.db.models import Base

engine = create_async_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def _add_columns(conn, table: str, additions: dict[str,str]):
    rows=(await conn.execute(text(f"PRAGMA table_info({table})"))).mappings().all()
    names={r['name'] for r in rows}
    for name,ddl in additions.items():
        if name not in names:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

async def init_db() -> None:
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.database_url.startswith("sqlite"):
            await _add_columns(conn,"parents",{
                "email":"VARCHAR(255)","email_reports_enabled":"BOOLEAN NOT NULL DEFAULT 0","phone":"VARCHAR(40)"})
            await _add_columns(conn,"characters",{
                "source":"VARCHAR(40) NOT NULL DEFAULT 'CHILD_DRAWING'","catalog_id":"VARCHAR(80)"})
            await _add_columns(conn,"children",{
                "country":"VARCHAR(120)","language_level":"VARCHAR(20) NOT NULL DEFAULT 'PRE_A1'",
                "working_difficulty":"FLOAT NOT NULL DEFAULT 0.15","comprehension_score":"FLOAT NOT NULL DEFAULT 0",
                "grammar_score":"FLOAT NOT NULL DEFAULT 0","vocabulary_score":"FLOAT NOT NULL DEFAULT 0",
                "pronunciation_score":"FLOAT NOT NULL DEFAULT 0","fluency_score":"FLOAT NOT NULL DEFAULT 0",
                "independence_score":"FLOAT NOT NULL DEFAULT 0","answers_count":"INTEGER NOT NULL DEFAULT 0","age_years":"INTEGER","birth_day":"INTEGER","birth_month":"INTEGER","birth_year":"INTEGER","gender":"VARCHAR(16)","birthday_greeted_year":"INTEGER"})
            await _add_columns(conn,"lesson_sessions",{
                "level_at_start":"VARCHAR(20) NOT NULL DEFAULT 'PRE_A1'","level_at_end":"VARCHAR(20)","completed_at":"DATETIME",
                "lesson_revision":"INTEGER NOT NULL DEFAULT 11"})
            await _add_columns(conn,"voice_attempts",{
                "transcript":"TEXT","detected_language":"VARCHAR(30)","confidence":"FLOAT",
                "grammar_errors":"TEXT","pronunciation_errors":"TEXT","semantic_match":"FLOAT",
                "comprehension_score":"FLOAT","grammar_score":"FLOAT","vocabulary_score":"FLOAT",
                "pronunciation_score":"FLOAT","fluency_score":"FLOAT","independence_score":"FLOAT",
                "recommended_difficulty":"FLOAT"})
            await conn.execute(text("UPDATE characters SET source='BOT_CATALOG' WHERE original_path LIKE '%preset-characters%'"))
            await conn.execute(text("UPDATE children SET native_language='ru' WHERE native_language IS NULL OR native_language=''"))
            await conn.execute(text("UPDATE children SET target_language='en' WHERE target_language IS NULL OR target_language=''"))
