import os
import sys
import asyncio
import logging

# Ensure local configuration
os.environ.setdefault('CONTENT_STUDIO_ENABLED', 'true')
os.environ.setdefault('CONTENT_STUDIO_TOKEN', 'admin')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///storage/app.db')
os.environ.setdefault('DATA_DIR', 'storage')
os.environ.setdefault('STORAGE_ROOT', 'storage')
os.environ.setdefault('WEBAPP_PORT', '8080')

from app.core.config import settings
from app.db.session import init_db
from app.webapp.server import start_webapp_server

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('dome.admin_server')

async def main():
    port = settings.effective_webapp_port
    logger.info(f'Starting DOME Admin Server on port {port}...')
    await init_db()
    runner = await start_webapp_server()
    logger.info(f'=========================================================')
    logger.info(f'DOME Admin Panel is READY!')
    logger.info(f'URL: http://localhost:{port}/content-studio')
    logger.info(f'Direct link with token: http://localhost:{port}/content-studio?token={settings.content_studio_token}')
    logger.info(f'Token: {settings.content_studio_token}')
    logger.info(f'=========================================================')
    try:
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass