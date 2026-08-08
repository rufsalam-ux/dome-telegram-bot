from __future__ import annotations

from pathlib import Path

from aiohttp import web

from app.core.config import settings


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "DOME Mini App"})


async def index(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static"
    return web.FileResponse(static / "index.html")


async def free_topic_task(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static"
    return web.FileResponse(static / "free_topic_task.html")


async def free_topic_media_file(request: web.Request) -> web.StreamResponse:
    child_id=request.match_info["child_id"]
    lesson_key=request.match_info["lesson_key"]
    filename=request.match_info["filename"]
    # Tight path validation: only generated free-topic PNG/JPG files are public here.
    if not child_id.isdigit() or "/" in lesson_key or ".." in lesson_key or "/" in filename or ".." in filename:
        raise web.HTTPNotFound()
    path=settings.storage_root/"children"/child_id/"free-topic-media"/lesson_key/filename
    if not path.exists() or path.suffix.lower() not in {".png",".jpg",".jpeg",".webp"}:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def games_index(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static" / "games"
    return web.FileResponse(static / "index.html")


async def start_webapp_server():
    app = web.Application()
    static = Path(__file__).parent / "static"
    app.router.add_get("/health", health)
    app.router.add_get("/", index)
    app.router.add_get("/games", games_index)
    app.router.add_get("/free-topic-task", free_topic_task)
    app.router.add_get("/free-topic-media/{child_id}/{lesson_key}/{filename}", free_topic_media_file)
    app.router.add_static("/assets", static / "assets", show_index=False)
    app.router.add_static("/media", static, show_index=False)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.webapp_port)
    await site.start()
    return runner
