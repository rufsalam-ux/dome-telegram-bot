import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import settings
from app.db.models import Base, Parent
from app.services.password_auth import hash_password
from app.webapp import studio_auth, content_studio


@pytest.mark.asyncio
async def test_owner_cookie_csrf_logout_and_non_owner_denial(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        db.add_all([
            Parent(email='krisriskrisris@gmail.com', email_verified=True, password_hash=hash_password('test-secret'), account_role='OWNER'),
            Parent(email='ordinary@example.test', email_verified=True, password_hash=hash_password('test-secret')),
        ])
        await db.commit()
    monkeypatch.setattr(studio_auth, 'SessionLocal', sessions)
    monkeypatch.setattr(settings, 'content_studio_enabled', True)
    monkeypatch.setattr(settings, 'content_studio_token', '')
    app = web.Application()
    studio_auth.register_studio_auth(app)
    async def protected(request):
        content_studio._authorized(request)
        return web.json_response({'ok': True})
    app.router.add_get('/api/studio/protected', protected)
    app.router.add_post('/api/studio/protected', protected)
    client = TestClient(TestServer(app))
    await client.start_server()
    monkeypatch.setattr(settings, 'webapp_base_url', str(client.make_url('')).rstrip('/'))
    origin = str(client.make_url('')).rstrip('/')
    try:
        bad = await client.post('/api/studio/auth/login', headers={'Origin': origin}, json={'email':'ordinary@example.test','password':'test-secret'})
        assert bad.status == 401
        foreign = await client.post('/api/studio/auth/login', headers={'Origin':'https://evil.example'}, json={})
        assert foreign.status == 403
        response = await client.post('/api/studio/auth/login', headers={'Origin':origin}, json={'email':' KRISRISKRISRIS@gmail.com ','password':'test-secret'})
        assert response.status == 200
        csrf = (await response.json())['csrf']
        cookie = response.cookies[studio_auth.COOKIE]
        assert cookie['httponly'] and cookie['samesite'] in ('Lax', 'Strict')
        assert cookie.value != csrf
        assert (await client.get('/api/studio/protected')).status == 200
        assert (await client.post('/api/studio/protected', headers={'Origin':origin})).status == 403
        headers = {'Origin':origin, 'X-DOME-CSRF':csrf}
        assert (await client.post('/api/studio/protected', headers=headers)).status == 200

        # Test change password
        ch_bad = await client.post('/api/studio/auth/change-password', headers=headers, json={'old_password':'wrong','new_password':'newsecret123'})
        assert ch_bad.status == 400
        ch_ok = await client.post('/api/studio/auth/change-password', headers=headers, json={'old_password':'11111111','new_password':'newsecret123'})
        assert ch_ok.status == 200

        assert (await client.post('/api/studio/auth/logout', headers=headers)).status == 200
        assert (await client.get('/api/studio/auth/session')).status == 401

        # Test login with new password directly (without email)
        relogin = await client.post('/api/studio/auth/login', headers={'Origin':origin}, json={'password':'newsecret123'})
        assert relogin.status == 200
    finally:
        await client.close()
        await engine.dispose()
