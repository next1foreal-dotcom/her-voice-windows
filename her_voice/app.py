"""Same-origin local UI, guarded Realtime bridge, and private LLM gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import secrets
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web, WSMsgType

from .demo import demo_session
from .engine import EngineError
from .gateway import CompletionGateway
from .protocol import CancelGuard
from .provider_settings import (bounded_json, candidate_settings, public_configuration,
                                save_configuration, test_configuration, catalog_candidate, list_configuration_models)
from .settings import Settings
from .tts import VoiceEngine
from .voices import MAX_AUDIO_BYTES, VoiceError, VoiceStore


@dataclass
class Runtime:
    settings: Settings
    engine: object
    voices: VoiceStore
    client: aiohttp.ClientSession | None = None
    live_in_use: bool = False
    sockets: set = field(default_factory=set)
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reconfiguring: bool = False


RUNTIME = web.AppKey("runtime", Runtime)


def browser_authorized(request: web.Request) -> bool:
    token = request.headers.get("X-Voice-Token", "") or request.query.get("token", "")
    return secrets.compare_digest(token.encode("utf-8"), request.app[RUNTIME].settings.browser_token.encode("utf-8"))


@web.middleware
async def local_only(request: web.Request, handler):
    host = request.host.split(":", 1)[0].lower()
    if host not in {"127.0.0.1", "localhost"}:
        raise web.HTTPForbidden(text="Use the loopback address shown by the launcher.")
    origin = request.headers.get("Origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise web.HTTPForbidden(text="Invalid origin.")
        if parsed.scheme != "http" or parsed.netloc != request.host:
            raise web.HTTPForbidden(text="Cross-origin requests are disabled.")
    if (request.path.startswith("/api/") and request.method != "GET") or request.path == "/ws/realtime":
        if not browser_authorized(request):
            raise web.HTTPForbidden(text="Refresh the local page to start a new session.")
    response = await handler(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    })
    return response


async def health(runtime: Runtime) -> dict:
    status = await asyncio.to_thread(runtime.engine.status)
    # Upstream prints transcripts in its terminal. Do not publish captured logs.
    status.pop("log_tail", None)
    return {**runtime.settings.public(), "engine": status, "tts": runtime.voices.public()}


async def bootstrap(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    return web.json_response({"token": rt.settings.browser_token, "health": await health(rt)})


async def status(request: web.Request) -> web.Response:
    return web.json_response(await health(request.app[RUNTIME]))


async def provider_settings(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    if not browser_authorized(request):
        raise web.HTTPForbidden(text="Refresh the local page to configure the provider.")
    if request.method == "GET":
        return web.json_response(public_configuration(rt.settings))
    try:
        data = await bounded_json(request.content)
        async with rt.control_lock:
            candidate = candidate_settings(rt.settings, data)
            if all(getattr(candidate, name) == getattr(rt.settings, name)
                   for name in ("provider", "provider_url", "model", "api_key")):
                # Persist an inherited configuration too, without unloading a
                # live engine or interrupting an unchanged conversation.
                await asyncio.to_thread(save_configuration, candidate)
                return web.json_response({**public_configuration(candidate), "unchanged": True})
            replacement = VoiceEngine(candidate, rt.voices)
            rt.reconfiguring = True
            try:
                for ws in tuple(rt.sockets):
                    await ws.close(code=1001, message=b"Provider settings changed")
                await asyncio.to_thread(rt.engine.stop)
                await asyncio.to_thread(save_configuration, candidate)
                rt.settings, rt.engine = candidate, replacement
            finally:
                rt.reconfiguring = False
        return web.json_response(public_configuration(candidate))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (OSError, EngineError):
        return web.json_response({"error": "設定未儲存，請檢查本機服務與專案資料夾權限。"}, status=409)


async def test_provider(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    try:
        candidate = candidate_settings(rt.settings, await bounded_json(request.content))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)
    assert rt.client is not None
    return web.json_response(await test_configuration(candidate, rt.client))


async def provider_models(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    try:
        candidate = catalog_candidate(rt.settings, await bounded_json(request.content))
        assert rt.client is not None
        return web.json_response(await list_configuration_models(candidate, rt.client))
    except ValueError as error:
        return web.json_response({"error": str(error)}, status=400)

async def start_engine(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    if rt.settings.demo_only:
        return web.json_response({"error": "這次以協定演練模式啟動；即時模式請執行 start.ps1。"}, status=409)
    if not rt.settings.provider_ready:
        return web.json_response({"error": "請先在本機設定語言模型／Agent 接口，然後重新啟動程式。"}, status=409)
    try:
        async with rt.control_lock:
            result = await asyncio.to_thread(rt.engine.start)
        result.pop("log_tail", None)
        return web.json_response(result)
    except EngineError as exc:
        return web.json_response({"error": str(exc)}, status=409)


async def stop_engine(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    async with rt.control_lock:
        rt.reconfiguring = True
        try:
            for ws in tuple(rt.sockets):
                await ws.close(code=1001, message=b"Engine shutdown")
            result = await asyncio.to_thread(rt.engine.stop)
        finally:
            rt.reconfiguring = False
    result.pop("log_tail", None)
    return web.json_response(result)


async def list_voices(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    try:
        voices = await asyncio.to_thread(rt.voices.list)
        return web.json_response({"voices": voices, "selected_voice_id": rt.voices.public()["voice_id"]})
    except VoiceError as error:
        return web.json_response({"error": str(error)}, status=409)


async def import_voice(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    try:
        if not request.content_type.startswith("multipart/"):
            raise VoiceError("请通过音色表单上传 WAV 和对应文字。")
        reader = await request.multipart()
        values = {}
        count = 0
        async for part in reader:
            count += 1
            if count > 3 or part.name not in {"audio", "name", "transcript"} or part.name in values:
                raise VoiceError("上传字段无效，请重新选择录音后重试。")
            limit = MAX_AUDIO_BYTES if part.name == "audio" else 12000
            content = bytearray()
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > limit:
                    raise VoiceError("录音超过 12 MiB 或文字字段过长，请缩短后重试。")
            values[part.name] = bytes(content) if part.name == "audio" else content.decode("utf-8")
        if set(values) != {"audio", "name", "transcript"}:
            raise VoiceError("请填写名称、录音对应文字，并选择 WAV 文件。")
        voice = await asyncio.to_thread(rt.voices.add, **values)
        return web.json_response({"voice": voice}, status=201)
    except (VoiceError, ValueError, UnicodeError) as error:
        message = str(error) if isinstance(error, VoiceError) else "无法读取音色上传，请重试。"
        return web.json_response({"error": message}, status=400)


async def tts_settings(request: web.Request) -> web.Response:
    rt = request.app[RUNTIME]
    if request.method == "GET":
        return web.json_response(rt.voices.public())
    try:
        if request.content_length is not None and request.content_length > 4096:
            raise VoiceError("语音设置请求过大。")
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"backend", "voice_id"}:
            raise VoiceError("请选择后端和已经保存的音色。")
        rt.voices.validate_selection(body["backend"], body["voice_id"])
        async with rt.control_lock:
            current = rt.voices.public()
            if not current.get("error") and all(current.get(name) == body[name] for name in ("backend", "voice_id")):
                return web.json_response({**current, "unchanged": True})
            rt.reconfiguring = True
            try:
                # Close this app's sessions and stop its workers before changing
                # selection. A socket-close race must not turn a valid switch
                # into an indefinite live_in_use conflict.
                for ws in tuple(rt.sockets):
                    await ws.close(code=1001, message=b"Voice backend changed")
                await asyncio.to_thread(rt.engine.stop)
                await asyncio.to_thread(rt.voices.select, body["backend"], body["voice_id"])
            finally:
                rt.reconfiguring = False
        return web.json_response(rt.voices.public())
    except (VoiceError, ValueError, TypeError) as error:
        message = str(error) if isinstance(error, VoiceError) else "语音设置格式无效。"
        return web.json_response({"error": message}, status=400)
    except (OSError, EngineError):
        return web.json_response({"error": "语音设置未保存，请检查本机进程和工作区权限后重试。"}, status=409)


async def relay(browser: web.WebSocketResponse, upstream: aiohttp.ClientWebSocketResponse) -> None:
    guard = CancelGuard()

    async def to_engine() -> None:
        async for message in browser:
            if message.type == WSMsgType.TEXT:
                try:
                    event = message.json()
                    if not isinstance(event, dict):
                        raise ValueError()
                    if guard.allow(event):
                        await upstream.send_json(event)
                    elif event.get("type") not in {"response.cancel", "output_audio_buffer.clear"}:
                        await browser.send_json({"type": "error", "error": {"type": "invalid_request_error", "message": "Unsupported client event."}})
                except (ValueError, TypeError):
                    await browser.close(code=1007, message=b"Invalid JSON event")
                    break
            elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSE}:
                break

    async def to_browser() -> None:
        async for message in upstream:
            if message.type == WSMsgType.TEXT:
                try:
                    event = message.json()
                    if not isinstance(event, dict):
                        raise ValueError()
                    guard.observe(event)
                    await browser.send_str(message.data)
                except (ValueError, TypeError):
                    await browser.close(code=1011, message=b"Invalid engine event")
                    break
            elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSED, WSMsgType.CLOSE}:
                break

    tasks = [asyncio.create_task(to_engine()), asyncio.create_task(to_browser())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # A peer closing is expected. No raw events or message text are logged.
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await upstream.close()
        await browser.close()


async def realtime(request: web.Request) -> web.WebSocketResponse:
    rt = request.app[RUNTIME]
    mode = request.query.get("mode", "live")
    if mode not in {"live", "demo"}:
        raise web.HTTPBadRequest(text="Unknown mode.")
    if mode == "live":
        if rt.reconfiguring:
            raise web.HTTPConflict(text="Voice backend is being changed.")
        if rt.settings.demo_only:
            raise web.HTTPConflict(text="Demo-only launcher.")
        if rt.live_in_use:
            raise web.HTTPConflict(text="Only one live session is supported.")
        rt.live_in_use = True
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1_048_576)
    upstream = None
    try:
        if mode == "live":
            assert rt.client is not None
            try:
                upstream = await rt.client.ws_connect(rt.settings.engine_url, heartbeat=20, max_msg_size=4_194_304, timeout=aiohttp.ClientWSTimeout(ws_close=3))
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                raise web.HTTPServiceUnavailable(text="Speech engine is not ready or its session is draining.")
            await ws.prepare(request)
            rt.sockets.add(ws)
            await relay(ws, upstream)
        else:
            await ws.prepare(request)
            rt.sockets.add(ws)
            await demo_session(ws)
    finally:
        if upstream is not None:
            await upstream.close()
        rt.sockets.discard(ws)
        if mode == "live":
            rt.live_in_use = False
    return ws


async def completions(request: web.Request) -> web.StreamResponse:
    rt = request.app[RUNTIME]
    assert rt.client is not None
    return await CompletionGateway(rt.settings, rt.client).completions(request)


async def asset(request: web.Request) -> web.FileResponse:
    name = request.match_info.get("name", "index.html")
    allowed = {"index.html", "styles.css", "ui.js", "app.js", "client.js", "audio.js", "audio-dsp.js", "capture-worklet.js", "protocol.js", "provider-settings.js"}
    if name not in allowed:
        raise web.HTTPNotFound()
    return web.FileResponse(request.app[RUNTIME].settings.web_root / name)


async def lifecycle(app: web.Application):
    rt = app[RUNTIME]
    # Deliberately direct: no implicit proxy/netrc lookup, especially for the
    # local engine. This prototype does not configure a corporate HTTP proxy.
    rt.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180, connect=10), trust_env=False)
    yield
    for ws in tuple(rt.sockets):
        await ws.close(code=1001, message=b"Application shutdown")
    await asyncio.to_thread(rt.engine.stop)
    await rt.client.close()


def create_app(settings: Settings | None = None, *, engine=None) -> web.Application:
    settings = settings or Settings.load()
    voices = VoiceStore(settings.root, default_backend=settings.tts_backend)
    if engine is None:
        engine = VoiceEngine(settings, voices)
    app = web.Application(middlewares=[local_only], client_max_size=MAX_AUDIO_BYTES + 65536)
    app[RUNTIME] = Runtime(settings, engine, voices)
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/provider", provider_settings)
    app.router.add_put("/api/provider", provider_settings)
    app.router.add_post("/api/provider/test", test_provider)
    app.router.add_post("/api/provider/models", provider_models)
    app.router.add_post("/api/engine/start", start_engine)
    app.router.add_post("/api/engine/stop", stop_engine)
    app.router.add_get("/api/voices", list_voices)
    app.router.add_post("/api/voices", import_voice)
    app.router.add_get("/api/tts", tts_settings)
    app.router.add_put("/api/tts", tts_settings)
    app.router.add_get("/ws/realtime", realtime)
    app.router.add_post("/llm/v1/chat/completions", completions)
    app.router.add_get("/", asset)
    app.router.add_get("/{name}", asset)
    return app
