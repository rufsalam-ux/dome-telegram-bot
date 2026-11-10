"""Opt-in school bot runtime, separate from the existing DOME dispatcher."""
import asyncio
import logging
import os
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from app.services.school_ledger import SchoolLedger, SchoolLedgerError
from app.services.school_telegram_links import SchoolTelegramLinks
from app.services.school_notifications import SchoolNotifications
from app.webapp.school_api import register_school_routes

CABINET = 'https://bilinguadom.wixsite.com/bilingvadom-cabinet'


def install_school_runtime(app, storage_root, *, env=None):
    env = os.environ if env is None else env
    if env.get('SCHOOL_ENABLED') != 'true':
        return False
    token, bridge_key = env.get('SCHOOL_BOT_TOKEN', ''), env.get('SCHOOL_BRIDGE_KEY', '')
    if not token or len(bridge_key) < 43:
        logging.getLogger('school').error('School runtime configuration is incomplete')
        return False
    directory = Path(storage_root)
    directory.mkdir(parents=True, exist_ok=True)
    links = SchoolTelegramLinks(SchoolLedger(directory / 'school.db'))
    outbox = SchoolNotifications(links)
    state = {'ready': False}
    register_school_routes(app, links, bridge_key, lambda: state['ready'])

    async def lifecycle(_app):
        bot = None
        dispatcher = Dispatcher()

        @dispatcher.message(CommandStart())
        async def start(message, command):
            if message.chat.type != 'private' or not message.from_user or message.from_user.is_bot:
                return
            if not command.args:
                await message.answer('Здравствуйте! Подключите личные уведомления через свой учебный кабинет: ' + CABINET)
                return
            try:
                links.claim(command.args, chat_id=message.chat.id, sender_id=message.from_user.id, chat_type=message.chat.type)
            except SchoolLedgerError:
                await message.answer('Не удалось подключить аккаунт. Откройте кабинет и получите новую ссылку подключения Telegram. Если аккаунт уже связан с другим чатом, сначала отключите его в кабинете.')
                return
            await message.answer('Telegram подключён к вашему учебному кабинету. Личные уведомления будут приходить сюда.')

        async def deliver():
            async def send(chat, body):
                receipt = await bot.send_message(chat, body)
                return receipt.message_id
            while True:
                await outbox.deliver_one(send)
                await asyncio.sleep(1)

        async def run():
            nonlocal bot
            workers = []
            try:
                bot = Bot(token)
                me = await bot.get_me()
                if (me.username or '').lower() != 'bilingvadom_bot':
                    raise ValueError('SCHOOL_BOT_IDENTITY_MISMATCH')
                if (await bot.get_webhook_info()).url:
                    raise ValueError('SCHOOL_BOT_HAS_EXISTING_WEBHOOK')
                outbox.recover_interrupted()
                state['ready'] = True
                workers = [asyncio.create_task(deliver()), asyncio.create_task(dispatcher.start_polling(
                    bot, allowed_updates=['message'], handle_signals=False, close_bot_session=False,
                    handle_as_tasks=False))]
                done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except Exception as error:
                # Never log tokens, request URLs, or message bodies.
                logging.getLogger('school').error('School bot stopped: %s', type(error).__name__)
            finally:
                state['ready'] = False
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                if bot is not None:
                    await bot.session.close()

        task = asyncio.create_task(run())
        yield
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    app.cleanup_ctx.append(lifecycle)
    return True
