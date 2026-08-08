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

def configure_logging():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",stream=sys.stdout,force=True)

async def main():
    configure_logging(); log=logging.getLogger('dome'); log.info('DOME v37 FREE TOPIC + PAYMENT + SKIP + CARTOON starting')
    if not settings.bot_token: raise RuntimeError('BOT_TOKEN is missing in .env')
    await init_db(); log.info('Database ready and migrations applied')
    orders = validate_lesson_revision('demo_001')
    log.info('Lesson v25 validated: %s runtime slides; route 24 -> 40; removed 2 and 25-39', len(orders))
    web_runner=await start_webapp_server(); log.info('Mini App server started on port %s',settings.webapp_port)
    bot=Bot(settings.bot_token); me=await bot.get_me(); log.info('Connected to Telegram as @%s',me.username)
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
        await bot.session.close();await web_runner.cleanup();log.info('DOME stopped')

if __name__=='__main__':
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
