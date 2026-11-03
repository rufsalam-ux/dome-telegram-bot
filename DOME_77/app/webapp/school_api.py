"""Narrow server-to-server bridge; Wix must authenticate the member first."""
import hmac
import json
import re
from aiohttp import web


def register_school_routes(app, links, bridge_key, ready=lambda: True):
    if not isinstance(bridge_key, str) or len(bridge_key) < 43:
        raise ValueError('SCHOOL_BRIDGE_KEY must contain at least 32 random bytes encoded as text')

    async def handle(request):
        supplied = request.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied.encode(), ('Bearer ' + bridge_key).encode()):
            return web.json_response({'error': 'UNAUTHORIZED'}, status=401)
        if not ready():
            return web.json_response({'error': 'BOT_UNAVAILABLE'}, status=503)
        if request.content_type != 'application/json':
            return web.json_response({'error': 'INVALID_REQUEST'}, status=400)
        body = bytearray()
        async for chunk in request.content.iter_chunked(1024):
            body.extend(chunk)
            if len(body) > 2048:
                return web.json_response({'error': 'REQUEST_TOO_LARGE'}, status=413)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            return web.json_response({'error': 'INVALID_REQUEST'}, status=400)
        if not isinstance(data, dict) or set(data) != {'memberId'} or not isinstance(data['memberId'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', data['memberId']):
            return web.json_response({'error': 'INVALID_MEMBER'}, status=400)
        member = data['memberId']
        action = request.match_info['action']
        if action == 'status':
            result = {'linked': links.chat_for_member(member) is not None, 'botUsername': 'BilingvaDom_bot'}
        elif action == 'link':
            result = {'url': 'https://t.me/BilingvaDom_bot?start=' + links.issue(member), 'expiresIn': 600}
        elif action == 'unlink':
            links.unlink(member)
            result = {'linked': False}
        else:
            return web.json_response({'error': 'NOT_FOUND'}, status=404)
        return web.json_response(result, headers={'Cache-Control': 'no-store'})

    app.router.add_post('/school/telegram/{action:status|link|unlink}', handle)
