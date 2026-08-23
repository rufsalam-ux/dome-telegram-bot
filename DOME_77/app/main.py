import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError
from aiogram.types import BotCommand, BotCommandScopeDefault
from app.bot.handlers import router
from app.core.config import settings
from app.db.session import init_db
from app.webapp.server import start_webapp_server
from app.services.lesson_loader import validate_lesson_revision
from app.services.lesson_reminders import due_now, mark_sent
from app.db.session import SessionLocal
from app.db.models import Child, Parent, Subscription
from app.services.standalone_demo_access import backfill_free_demo_entitlements
from app.services.mobile_lesson_movie import recover_interrupted_mobile_movie_jobs
from sqlalchemy import select
from zoneinfo import ZoneInfo

def configure_logging():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",stream=sys.stdout,force=True)


async def lesson_reminder_loop(bot):
    log=logging.getLogger('dome.reminders')
    while True:
        try:
            async with SessionLocal() as db:
                rows=(await db.execute(select(Child,Parent).join(Parent,Child.parent_id==Parent.id))).all()
            from datetime import datetime
            now=datetime.now(tz=ZoneInfo('UTC'))
            for child,parent in rows:
                due,cfg=due_now(child.id,now)
                if not due or not cfg: continue
                local=now.astimezone(ZoneInfo(cfg['timezone'])); hh,mm=map(int,cfg['local_time'].split(':')); lesson=local.replace(hour=hh,minute=mm,second=0,microsecond=0)
                key=lesson.strftime('%Y-%m-%dT%H:%M')
                text=(f"Через {cfg.get('remind_before_minutes',15)} минут пора на урок, {child.display_name}!" if (child.native_language or 'ru')=='ru' else f"Lesson starts in {cfg.get('remind_before_minutes',15)} minutes, {child.display_name}!")
                from app.bot.keyboards import start_lesson_keyboard
                await bot.send_message(parent.telegram_user_id,text,reply_markup=start_lesson_keyboard(child.native_language or 'en'))
                mark_sent(child.id,key)
        except Exception as exc: log.warning('Reminder loop: %s',exc)
        await asyncio.sleep(60)


async def subscription_release_loop(bot):
    """Release weekly lessons from each child's own plan; cancellation stops future releases."""
    log=logging.getLogger('dome.subscription_release')
    from app.services.subscription_release import release_due_lessons
    from app.services.platform_settings import load_settings
    while True:
        try:
            if bool(load_settings('payments').get('billing_enabled',False)):
                async with SessionLocal() as db:
                    subs=(await db.scalars(select(Subscription).where(Subscription.status=='ACTIVE'))).all()
                    children={c.id:c for c in (await db.scalars(select(Child))).all()}
                    parents={p.id:p for p in (await db.scalars(select(Parent))).all()}
                for sub in subs:
                    created=await release_due_lessons(sub.child_id,sub.course_id)
                    if not created: continue
                    child=children.get(sub.child_id); parent=parents.get(child.parent_id) if child else None
                    if parent:
                        try:
                            await bot.send_message(parent.telegram_user_id,f'📚 DOME: открыто новых уроков — {len(created)}. Они уже доступны в курсе.')
                        except Exception: pass
        except Exception as exc:
            log.warning('Subscription release loop: %s',exc)
        await asyncio.sleep(3600)

async def main():
    configure_logging(); log=logging.getLogger('dome'); # Previous verified banner: DOME v73 RUNTIME INTERACTIONS + VOICE FIX
    log.info('DOME v75 CONVERSATION ONLY SAFE MODE')
    if not settings.bot_token: raise RuntimeError('BOT_TOKEN is missing in .env')
    await init_db(); log.info('Database ready and migrations applied')
    await recover_interrupted_mobile_movie_jobs()
    free_demo_created=await backfill_free_demo_entitlements()
    log.info('Standalone free demo entitlements created: %s',free_demo_created)
    orders = validate_lesson_revision('demo_001')
    log.info('Legacy conversation lesson validated: %s runtime slides', len(orders))
    web_runner=await start_webapp_server(); log.info('Mini App server started on port %s',settings.effective_webapp_port)
    bot=Bot(settings.bot_token); me=await bot.get_me(); log.info('Connected to Telegram as @%s',me.username)
    reminder_task=asyncio.create_task(lesson_reminder_loop(bot))
    release_task=asyncio.create_task(subscription_release_loop(bot))
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands([
        BotCommand(command='menu',description='Open DOME menu'),
        BotCommand(command='reset_session',description='Close a stuck lesson session'),
        BotCommand(command='set_email',description='Set parent progress email'),
        BotCommand(command='progress',description='Show child progress'),
        BotCommand(command='version',description='Show running DOME version'),
        BotCommand(command='whoami',description='Show my Telegram ID'),
        BotCommand(command='activity',description='Admin: recent lesson activity'),
        BotCommand(command='start',description='Start DOME')],scope=BotCommandScopeDefault())
    dp=Dispatcher();dp.include_router(router);log.info('Polling started. Bot is ready.')
    try: await dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types())
    except TelegramConflictError:
        log.critical('Another bot instance uses the same token. Stop the old instance.');raise
    finally:
        reminder_task.cancel(); release_task.cancel()
        await bot.session.close();await web_runner.cleanup();log.info('DOME stopped')

if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
