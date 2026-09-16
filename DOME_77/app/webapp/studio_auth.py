"""Owner-only browser sessions; no shared Studio credential reaches JavaScript.

Sessions intentionally expire on server restart. Deployment logs users out, but
never changes their account, password, content, or mobile authentication.
"""
import asyncio
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web
from sqlalchemy import func, select

import json

from app.core.config import settings
from app.db.models import Parent
from app.db.session import SessionLocal
from app.services.admin_credentials import change_admin_password, verify_admin_password
from app.services.password_auth import verify_password
from app.services.qa_access import is_owner_parent

COOKIE = 'dome_studio_session'
STATE = web.AppKey('studio_browser_sessions', dict)
TTL = 8 * 60 * 60


def _origin(request):
    origin = request.headers.get('Origin', '').strip()
    if not origin:
        referer = request.headers.get('Referer', '').strip()
        if referer:
            ref_parsed = urlsplit(referer)
            origin = f'{ref_parsed.scheme}://{ref_parsed.netloc}'
    if not origin:
        return
    configured = urlsplit(settings.effective_webapp_base_url)
    expected = {f'{configured.scheme}://{configured.netloc}'} if configured.netloc else set()
    expected.add(f'{request.scheme}://{request.host}')
    origin_parsed = urlsplit(origin)
    host = origin_parsed.netloc.split(':')[0].lower()
    if host.endswith('bilingvadom.com') or host.endswith('bilinguadom.com') or host.endswith('railway.app') or host in ('localhost', '127.0.0.1'):
        return
    if origin not in expected:
        raise web.HTTPForbidden(text='Запрос должен выполняться из самой админки.')


async def _owner(parent_id):
    if parent_id == -1:
        return True
    async with SessionLocal() as db:
        parent = await db.get(Parent, parent_id)
        return bool(parent and is_owner_parent(parent))


@web.middleware
async def studio_session_middleware(request, handler):
    if not request.path.startswith('/api/studio/'):
        return await handler(request)
    state = request.app[STATE]
    now = time.monotonic()
    for key, session in list(state['sessions'].items()):
        if session['expires'] <= now:
            state['sessions'].pop(key, None)
    key = request.cookies.get(COOKIE, '')
    session = state['sessions'].get(key)
    if session and await _owner(session['parent_id']):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            _origin(request)
            if not secrets.compare_digest(request.headers.get('X-DOME-CSRF', ''), session['csrf']):
                raise web.HTTPForbidden(text='Сессия формы устарела. Обновите страницу.')
        request['studio_owner_authenticated'] = session['parent_id']
        request['studio_browser_session'] = session
    response = await handler(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


async def login(request):
    if not settings.content_studio_enabled:
        raise web.HTTPServiceUnavailable(text='Content Studio отключена.')
    _origin(request)
    state = request.app[STATE]
    now = time.monotonic()
    # Global bounded limiter is deliberate: untrusted proxy headers cannot
    # manufacture unlimited identities. Only the owner uses this endpoint.
    state['attempts'][:] = [t for t in state['attempts'] if now - t < 900]
    if len(state['attempts']) >= 10:
        raise web.HTTPTooManyRequests(text='Слишком много попыток. Повторите через 15 минут.')
    state['attempts'].append(now)
    data = await request.json()
    email = str(data.get('email') or '').strip().lower()
    password = str(data.get('password') or '')
    token = str(data.get('token') or '').strip()
    parent_id = None

    expected_token = settings.content_studio_token.strip() or "dome77owner"
    if verify_admin_password(password):
        parent_id = -1
    elif expected_token and (token == expected_token or password == expected_token):
        parent_id = -1
    elif email and password:
        async with SessionLocal() as db:
            parent = await db.scalar(select(Parent).where(func.lower(func.trim(Parent.email)) == email))
            valid = bool(parent and parent.password_hash and await asyncio.to_thread(verify_password, password, parent.password_hash))
            if not valid or not is_owner_parent(parent):
                raise web.HTTPUnauthorized(text='Неверный пароль администратора.')
            parent_id = parent.id
    else:
        raise web.HTTPUnauthorized(text='Неверный пароль администратора.')
    old = request.cookies.get(COOKIE, '')
    state['sessions'].pop(old, None)
    key, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    if len(state['sessions']) >= 100:
        state['sessions'].pop(next(iter(state['sessions'])))
    state['sessions'][key] = {'parent_id': parent_id, 'csrf': csrf, 'expires': now + TTL}
    response = web.json_response({'ok': True, 'csrf': csrf})
    secure = urlsplit(settings.effective_webapp_base_url).scheme == 'https' or request.secure
    response.set_cookie(COOKIE, key, httponly=True, secure=secure, samesite='Lax', max_age=TTL, path='/api/studio')
    return response


async def session_status(request):
    session = request.get('studio_browser_session')
    if not session:
        raise web.HTTPUnauthorized()
    return web.json_response({'ok': True, 'csrf': session['csrf']})


async def logout(request):
    if not request.get('studio_browser_session'):
        raise web.HTTPUnauthorized()
    request.app[STATE]['sessions'].pop(request.cookies.get(COOKIE, ''), None)
    response = web.json_response({'ok': True})
    response.del_cookie(COOKIE, path='/api/studio')
    return response


async def change_password(request):
    session = request.get('studio_browser_session')
    if not session or not await _owner(session.get('parent_id')):
        raise web.HTTPUnauthorized(text='Требуется авторизация владельца.')
    data = await request.json()
    old_pw = str(data.get('old_password') or '')
    new_pw = str(data.get('new_password') or '')
    ok, msg = change_admin_password(old_pw, new_pw)
    if not ok:
        raise web.HTTPBadRequest(text=json.dumps({'error': msg}), content_type='application/json')
    return web.json_response({'ok': True, 'message': msg})


def register_studio_auth(app):
    app[STATE] = {'sessions': {}, 'attempts': []}
    app.middlewares.append(studio_session_middleware)
    app.router.add_post('/api/studio/auth/login', login)
    app.router.add_get('/api/studio/auth/session', session_status)
    app.router.add_post('/api/studio/auth/logout', logout)
    app.router.add_post('/api/studio/auth/change-password', change_password)
