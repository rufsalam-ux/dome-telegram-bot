import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiohttp import web
from app.services.school_runtime import install_school_runtime


class SchoolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_disabled_runtime_does_not_register_routes(self):
        app = web.Application()
        self.assertFalse(install_school_runtime(app, '.', env={}))
        self.assertEqual(len(list(app.router.routes())), 0)

    async def exercise(self, username='BilingvaDom_bot', webhook=''):
        with tempfile.TemporaryDirectory() as directory:
            app = web.Application()
            bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username=username)),
                get_webhook_info=AsyncMock(return_value=SimpleNamespace(url=webhook)),
                session=SimpleNamespace(close=AsyncMock()), send_message=AsyncMock())
            started = asyncio.Event()
            async def polling(*args, **kwargs):
                self.assertFalse(kwargs['handle_signals'])
                started.set()
                await asyncio.Event().wait()
            with patch('app.services.school_runtime.Bot', return_value=bot), patch('app.services.school_runtime.Dispatcher.start_polling', side_effect=polling):
                install_school_runtime(app, directory, env={'SCHOOL_ENABLED':'true',
                    'SCHOOL_BOT_TOKEN':'not-a-real-token','SCHOOL_BRIDGE_KEY':'x'*43})
                context = app.cleanup_ctx[0](app)
                await anext(context)
                for _ in range(10):
                    await asyncio.sleep(0)
                did_start = started.is_set()
                with self.assertRaises(StopAsyncIteration):
                    await anext(context)
                bot.session.close.assert_awaited_once()
                bot.send_message.assert_not_awaited()
                return did_start

    async def test_expected_bot_starts_and_closes_cleanly(self):
        self.assertTrue(await self.exercise())

    def test_missing_school_configuration_does_not_break_existing_app(self):
        for env in [{'SCHOOL_ENABLED':'true'}, {'SCHOOL_ENABLED':'true', 'SCHOOL_BOT_TOKEN':'x'}]:
            app = web.Application()
            with self.assertLogs('school', level='ERROR'):
                self.assertFalse(install_school_runtime(app, '.', env=env))
            self.assertEqual(len(list(app.router.routes())), 0)

    async def test_invalid_token_does_not_break_existing_app_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            app = web.Application()
            install_school_runtime(app, directory, env={'SCHOOL_ENABLED':'true',
                'SCHOOL_BOT_TOKEN':'invalid', 'SCHOOL_BRIDGE_KEY':'x'*43})
            with patch('app.services.school_runtime.Bot', side_effect=ValueError('sensitive detail')), self.assertLogs('school',level='ERROR') as logs:
                context = app.cleanup_ctx[0](app)
                await anext(context)
                await asyncio.sleep(0)
                with self.assertRaises(StopAsyncIteration):
                    await anext(context)
            self.assertNotIn('sensitive detail', '\n'.join(logs.output))

    async def test_other_bot_or_existing_webhook_never_started(self):
        self.assertFalse(await self.exercise(username='BilingvadomLessons_Bot'))
        self.assertFalse(await self.exercise(webhook='https://existing.example/webhook'))
