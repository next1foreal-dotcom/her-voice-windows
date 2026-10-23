import asyncio
import base64
from dataclasses import replace
from pathlib import Path
import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from her_voice.app import RUNTIME, create_app
from her_voice.protocol import CancelGuard
from her_voice.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


class FakeEngine:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def status(self):
        return {"state": "ready", "ready": True, "log_tail": ["private-engine-transcript"], "detail": "Test engine"}

    def start(self):
        self.starts += 1
        return self.status()

    def stop(self):
        self.stops += 1
        return {"state": "stopped", "log_tail": ["private-engine-transcript"]}


class GuardTests(unittest.TestCase):
    def test_old_cancel_and_old_done_cannot_cancel_the_new_response(self):
        guard = CancelGuard()
        guard.observe({"type": "response.created", "response": {"id": "old"}})
        self.assertTrue(guard.allow({"type": "response.cancel", "response_id": "old"}))
        guard.observe({"type": "response.created", "response": {"id": "new"}})
        guard.observe({"type": "response.done", "response": {"id": "old"}})
        self.assertFalse(guard.allow({"type": "response.cancel", "response_id": "old"}))
        self.assertFalse(guard.allow({"type": "output_audio_buffer.clear", "response_id": "old"}))
        self.assertTrue(guard.allow({"type": "response.cancel", "response_id": "new"}))
        guard.observe({"type": "response.done", "response": {"id": "new"}})
        self.assertFalse(guard.allow({"type": "response.cancel", "response_id": "new"}))

    def test_unscoped_cancellation_and_unknown_events_are_rejected(self):
        guard = CancelGuard()
        guard.observe({"type": "response.created", "response": {"id": "current"}})
        self.assertFalse(guard.allow({"type": "response.cancel"}))
        self.assertFalse(guard.allow({"type": "output_audio_buffer.clear"}))
        self.assertFalse(guard.allow({"type": "run.shell", "command": "ignored"}))
        self.assertTrue(guard.allow({"type": "input_audio_buffer.append", "audio": "AA=="}))


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings(root=ROOT, demo_only=True, deepseek_api_key="private-provider-test-key", gateway_token="private-gateway-test-token", browser_token="public-bootstrap-test-token")
        self.engine = FakeEngine()
        self.app = create_app(self.settings, engine=self.engine)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def ws_path(self, mode="demo"):
        return f"/ws/realtime?mode={mode}&token={self.settings.browser_token}"

    async def receive_until(self, ws, kind, *, timeout=6):
        events = []
        async with asyncio.timeout(timeout):
            while True:
                event = await ws.receive_json()
                events.append(event)
                if event.get("type") == kind:
                    return events

    async def test_bootstrap_and_status_expose_no_provider_secret_or_engine_log(self):
        for path in ("/api/bootstrap", "/api/status"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200)
            body = await response.text()
            self.assertNotIn(self.settings.deepseek_api_key, body)
            self.assertNotIn(self.settings.gateway_token, body)
            self.assertNotIn("private-engine-transcript", body)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = await self.client.get("/api/bootstrap")
        self.assertEqual((await response.json())["token"], self.settings.browser_token)

    async def test_host_origin_and_mutation_token_guards(self):
        for headers in ({"Host": "untrusted.example"}, {"Origin": "http://untrusted.example"}):
            response = await self.client.get("/api/bootstrap", headers=headers)
            self.assertEqual(response.status, 403)
            await response.read()
        response = await self.client.post("/api/engine/start")
        self.assertEqual(response.status, 403)
        await response.read()
        origin = str(self.client.make_url("/")).rstrip("/")
        response = await self.client.post("/api/engine/start", headers={"X-Voice-Token": self.settings.browser_token, "Origin": origin})
        self.assertEqual(response.status, 409)  # Demo-only launch cannot start models.
        await response.read()
        self.assertEqual(self.engine.starts, 0)

    async def test_malformed_origin_and_non_ascii_token_are_forbidden_not_server_errors(self):
        response = await self.client.get("/api/bootstrap", headers={"Origin": "http://[bad"})
        self.assertEqual(response.status, 403)
        await response.read()
        response = await self.client.post("/api/engine/stop", params={"token": "錯誤令牌"})
        self.assertEqual(response.status, 403)
        await response.read()

    async def test_unknown_assets_and_gateway_without_its_own_token_are_inaccessible(self):
        for path in ("/.env", "/settings.py", "/config/windows-4080.yaml"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404)
            await response.read()
        response = await self.client.post("/llm/v1/chat/completions", headers={"X-Voice-Token": self.settings.browser_token}, json={"messages": []})
        self.assertEqual(response.status, 401)
        await response.read()

    async def test_demo_emits_one_complete_text_and_test_tone_turn(self):
        ws = await self.client.ws_connect(self.ws_path())
        self.assertEqual((await ws.receive_json())["type"], "session.created")
        await ws.send_json({"type": "conversation.item.create", "item": {"id": "user-one", "role": "user", "content": [{"type": "input_text", "text": "測試😀"}]}})
        self.assertEqual((await ws.receive_json())["type"], "conversation.item.created")
        await ws.send_json({"type": "response.create"})
        events = await self.receive_until(ws, "response.done")
        rid = events[0]["response"]["id"]
        self.assertEqual(events[0]["type"], "response.created")
        self.assertEqual(events[-1]["response"], {"id": rid, "status": "completed"})
        captions = [event for event in events if event["type"] == "response.output_audio_transcript.done"]
        self.assertEqual(len(captions), 1)
        self.assertIn("演示回复", captions[0]["transcript"])
        self.assertIn("測試😀", captions[0]["transcript"])
        audio = [event for event in events if event["type"] == "response.output_audio.delta"]
        self.assertTrue(audio)
        for event in audio:
            self.assertEqual(event["response_id"], rid)
            pcm = base64.b64decode(event["delta"], validate=True)
            self.assertEqual(len(pcm), 24000 * 2 * 12 // 100)
        self.assertEqual(self.engine.starts, 0)
        await ws.close()

    async def test_demo_cancel_is_scoped_and_pause_stops_the_next_turn(self):
        ws = await self.client.ws_connect(self.ws_path())
        await ws.receive_json()
        await ws.send_json({"type": "response.create"})
        created = await ws.receive_json()
        rid = created["response"]["id"]
        await ws.send_json({"type": "response.cancel", "response_id": "stale-id"})
        first = await ws.receive_json(timeout=1)
        self.assertEqual(first["type"], "response.output_audio_transcript.delta")
        await ws.send_json({"type": "response.cancel", "response_id": rid})
        events = await self.receive_until(ws, "response.done", timeout=1)
        self.assertEqual(events[-1]["response"], {"id": rid, "status": "cancelled"})
        with self.assertRaises(asyncio.TimeoutError):
            await ws.receive_json(timeout=0.08)
        await ws.send_json({"type": "response.create"})
        new = await ws.receive_json()
        new_id = new["response"]["id"]
        self.assertNotEqual(rid, new_id)
        await ws.send_json({"type": "response.cancel", "response_id": rid})
        await ws.send_json({"type": "session.update", "session": {"audio": {"input": {"turn_detection": {"create_response": False}}}}})
        events = await self.receive_until(ws, "response.done", timeout=1)
        self.assertEqual(events[-1]["response"], {"id": new_id, "status": "cancelled"})
        await ws.send_json({"type": "response.create"})
        with self.assertRaises(asyncio.TimeoutError):
            await ws.receive_json(timeout=0.08)
        await ws.close()

    async def test_failed_browser_handshake_closes_the_already_open_engine_socket(self):
        opened, closed = asyncio.Event(), asyncio.Event()
        sockets = []
        async def engine_ws(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            sockets.append(socket)
            opened.set()
            try:
                async for _message in socket:
                    pass
            finally:
                closed.set()
            return socket
        engine_app = web.Application()
        engine_app.router.add_get("/realtime", engine_ws)
        server = TestServer(engine_app)
        await server.start_server()
        self.app[RUNTIME].settings = replace(self.settings, demo_only=False, engine_url=str(server.make_url("/realtime")).replace("http://", "ws://"))
        try:
            # A plain GET has no browser Upgrade headers. The current implementation
            # has already opened its upstream socket by the time prepare fails.
            response = await self.client.get(self.ws_path(mode="live"))
            self.assertEqual(response.status, 400)
            await response.read()
            await asyncio.wait_for(opened.wait(), timeout=1)
            await asyncio.wait_for(closed.wait(), timeout=0.5)
            self.assertFalse(self.app[RUNTIME].live_in_use)
        finally:
            for socket in sockets:
                await socket.close()
            await server.close()

    async def test_live_relay_enforces_scoped_cancel_and_releases_its_single_slot(self):
        forwarded = asyncio.Queue()
        closed = asyncio.Event()
        sockets = []
        async def engine_ws(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            sockets.append(socket)
            await socket.send_json({"type": "response.created", "response": {"id": "old"}})
            try:
                async for message in socket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await forwarded.put(message.json())
            finally:
                closed.set()
            return socket
        engine_app = web.Application()
        engine_app.router.add_get("/realtime", engine_ws)
        server = TestServer(engine_app)
        await server.start_server()
        self.app[RUNTIME].settings = replace(self.settings, demo_only=False, engine_url=str(server.make_url("/realtime")).replace("http://", "ws://"))
        browser = None
        try:
            browser = await self.client.ws_connect(self.ws_path(mode="live"))
            self.assertEqual((await browser.receive_json())["response"]["id"], "old")
            with self.assertRaises(aiohttp.WSServerHandshakeError) as raised:
                await self.client.ws_connect(self.ws_path(mode="live"))
            self.assertEqual(raised.exception.status, 409)
            self.assertEqual(len(sockets), 1)
            await sockets[0].send_json({"type": "response.created", "response": {"id": "new"}})
            self.assertEqual((await browser.receive_json())["response"]["id"], "new")
            await sockets[0].send_json({"type": "response.done", "response": {"id": "old", "status": "cancelled"}})
            await browser.receive_json()
            await browser.send_json({"type": "response.cancel", "response_id": "old"})
            await browser.send_json({"type": "response.cancel", "response_id": "new"})
            self.assertEqual(await asyncio.wait_for(forwarded.get(), 1), {"type": "response.cancel", "response_id": "new"})
            await sockets[0].send_json({"type": "response.done", "response": {"id": "new", "status": "cancelled"}})
            await browser.receive_json()
            await browser.send_json({"type": "response.cancel", "response_id": "new"})
            await browser.send_json({"type": "input_audio_buffer.append", "audio": "AA=="})
            self.assertEqual((await asyncio.wait_for(forwarded.get(), 1))["type"], "input_audio_buffer.append")
            await browser.close()
            await asyncio.wait_for(closed.wait(), 1)
            async with asyncio.timeout(1):
                while self.app[RUNTIME].live_in_use:
                    await asyncio.sleep(0)
            self.assertEqual(len(self.app[RUNTIME].sockets), 0)
        finally:
            if browser is not None:
                await browser.close()
            for socket in sockets:
                await socket.close()
            await server.close()


if __name__ == "__main__":
    unittest.main()
