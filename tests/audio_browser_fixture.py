#!/usr/bin/env python3
"""Loopback-only app + fake speech engine for native browser audio testing.

This does not import torch, download models, or contact DeepSeek. The browser
uses Chromium's synthetic microphone; this fixture records frame metadata and
echoes captured PCM through the real application's Realtime bridge.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
from collections import Counter
import json
from pathlib import Path
import signal
import struct
import sys

from aiohttp import web, WSMsgType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from her_voice.app import create_app
from her_voice.settings import Settings


class AudioFixture:
    def __init__(self):
        self.sockets: set[web.WebSocketResponse] = set()
        self.frames = 0
        self.frame_lengths: Counter = Counter()
        self.invalid_frames = 0
        self.nonzero_frames = 0
        self.peak = 0
        self.last_nonzero = b""
        self.cancels: list[str] = []
        self.session_updates: list[dict] = []
        self.sessions_opened = 0
        self.sessions_closed = 0
        self.provider_requests = 0
        self.responses_sent = 0
        self.late_audio_sent = 0
        self.speech_started_sent = 0
        self.engine_starts = 0
        self.engine_stops = 0
        self.old_id = "browser-fixture-old"
        self.new_id = "browser-fixture-new"

    def status(self):
        return {
            "state": "ready", "ready": True, "detail": "Synthetic browser-audio fixture; no speech models",
            "pool": {"in_use": len(self.sockets), "units": [{"state": "busy" if self.sockets else "idle"}]},
        }

    def start(self):
        self.engine_starts += 1
        return self.status()

    def stop(self):
        self.engine_stops += 1
        return {"state": "stopped"}

    def report(self):
        return {
            "scope": "Synthetic Chromium microphone and fake Realtime engine; no ASR, TTS, GPU, real microphone, or paid API.",
            "frames": self.frames, "frame_lengths": dict(self.frame_lengths),
            "invalid_frames": self.invalid_frames, "nonzero_frames": self.nonzero_frames,
            "peak_pcm16": self.peak, "cancels": self.cancels,
            "session_updates": self.session_updates, "sessions_opened": self.sessions_opened,
            "sessions_closed": self.sessions_closed, "active_sockets": len(self.sockets),
            "provider_requests": self.provider_requests, "responses_sent": self.responses_sent,
            "late_audio_sent": self.late_audio_sent, "speech_started_sent": self.speech_started_sent,
            "engine_starts": self.engine_starts, "engine_stops": self.engine_stops,
        }

    async def realtime(self, request):
        socket = web.WebSocketResponse(max_msg_size=1_048_576)
        await socket.prepare(request)
        self.sockets.add(socket)
        self.sessions_opened += 1
        await socket.send_json({"type": "session.created", "session": {"id": "fixture-session", "type": "realtime"}})
        try:
            async for message in socket:
                if message.type != WSMsgType.TEXT:
                    continue
                event = message.json()
                kind = event.get("type")
                if kind == "session.update":
                    session = event.get("session", {})
                    self.session_updates.append(session)
                    await socket.send_json({"type": "session.updated", "session": session})
                elif kind == "input_audio_buffer.append":
                    try:
                        pcm = base64.b64decode(event.get("audio", ""), validate=True)
                        if not pcm or len(pcm) % 2:
                            raise ValueError("Invalid PCM16")
                        peak = max(abs(sample[0]) for sample in struct.iter_unpack("<h", pcm))
                    except (ValueError, TypeError, binascii.Error):
                        self.invalid_frames += 1
                        continue
                    self.frames += 1
                    self.frame_lengths[len(pcm)] += 1
                    self.peak = max(self.peak, peak)
                    if peak:
                        self.nonzero_frames += 1
                        # Keep just one synthetic frame in RAM, never record it.
                        self.last_nonzero = pcm
                elif kind == "response.cancel":
                    self.cancels.append(event.get("response_id"))
                    await socket.send_json({"type": "response.done", "response": {
                        "id": event.get("response_id"), "status": "cancelled"}})
                elif kind == "conversation.item.create":
                    await socket.send_json({"type": "conversation.item.created", "item": event.get("item", {})})
        finally:
            self.sockets.discard(socket)
            self.sessions_closed += 1
        return socket

    async def stats(self, request):
        return web.json_response(self.report())

    async def forbidden_provider(self, request):
        self.provider_requests += 1
        return web.json_response({"error": "The audio fixture must not call an LLM."}, status=500)

    async def send(self, event):
        if len(self.sockets) != 1:
            raise web.HTTPConflict(text="Exactly one browser session is required.")
        await next(iter(self.sockets)).send_json(event)

    async def speech_started(self, item_id):
        self.speech_started_sent += 1
        await self.send({"type": "input_audio_buffer.speech_started", "item_id": item_id})

    async def response(self, rid, *, copies, complete):
        if not self.last_nonzero:
            raise web.HTTPConflict(text="Wait for a nonzero synthetic microphone frame first.")
        await self.speech_started("user-" + rid)
        await self.send({"type": "input_audio_buffer.speech_stopped", "item_id": "user-" + rid})
        await self.send({"type": "conversation.item.input_audio_transcription.completed",
                         "item_id": "user-" + rid, "transcript": "合成麥克風 synthetic microphone 測試；沒有執行語音辨識。"})
        await self.send({"type": "response.created", "response": {"id": rid, "status": "in_progress"}})
        await self.send({"type": "response.output_audio_transcript.delta", "response_id": rid,
                         "delta": "合成音訊完成測試。Synthetic audio complete." if complete else "合成音訊播放中，waiting for interruption。"})
        await self.send({"type": "response.output_audio.delta", "response_id": rid,
                         "delta": base64.b64encode(self.last_nonzero * copies).decode("ascii")})
        self.responses_sent += 1
        if complete:
            await self.send({"type": "response.done", "response": {"id": rid, "status": "completed"}})

    async def action(self, request):
        action = request.match_info["name"]
        if action == "reply":
            # 125 captured 40 ms frames create five seconds of real WebAudio
            # playback, leaving time to prove interruption stops a live source.
            await self.response(self.old_id, copies=125, complete=False)
        elif action == "barge-in":
            await self.speech_started("user-barge-in")
            # Let the browser process speech_started and send the scoped cancel,
            # then deliberately deliver stale old-response text and PCM anyway.
            await asyncio.sleep(0.12)
            await self.send({"type": "response.output_audio_transcript.delta", "response_id": self.old_id,
                             "delta": "STALE_AUDIO_SHOULD_BE_IGNORED"})
            await self.send({"type": "response.output_audio.delta", "response_id": self.old_id,
                             "delta": base64.b64encode(self.last_nonzero * 25).decode("ascii")})
            self.late_audio_sent += 1
            await self.send({"type": "response.done", "response": {"id": self.old_id, "status": "cancelled"}})
        elif action == "complete":
            # Done arrives immediately, but 800 ms of queued playback remains.
            await self.response(self.new_id, copies=20, complete=True)
        else:
            raise web.HTTPNotFound()
        return web.json_response(self.report())


async def serve(app_port: int, engine_port: int):
    fixture = AudioFixture()
    engine = web.Application()
    engine.router.add_get("/v1/realtime", fixture.realtime)
    engine.router.add_get("/test/stats", fixture.stats)
    engine.router.add_post("/test/action/{name}", fixture.action)
    engine.router.add_post("/never-provider", fixture.forbidden_provider)
    engine_runner = web.AppRunner(engine, access_log=None)
    await engine_runner.setup()
    engine_site = web.TCPSite(engine_runner, "127.0.0.1", engine_port)
    await engine_site.start()
    actual_engine_port = engine_site._server.sockets[0].getsockname()[1]
    engine_origin = f"http://127.0.0.1:{actual_engine_port}"
    settings = Settings(root=ROOT, demo_only=False,
                        deepseek_api_key="synthetic-fixture-key-never-sent",
                        provider_url=engine_origin + "/never-provider",
                        engine_url=engine_origin.replace("http:", "ws:") + "/v1/realtime")
    app_runner = web.AppRunner(create_app(settings, engine=fixture), access_log=None)
    try:
        await app_runner.setup()
        app_site = web.TCPSite(app_runner, "127.0.0.1", app_port)
        await app_site.start()
        actual_app_port = app_site._server.sockets[0].getsockname()[1]
        print(json.dumps({"fixture": "ready", "app_url": f"http://127.0.0.1:{actual_app_port}",
                          "control_url": engine_origin, "scope": fixture.report()["scope"]}), flush=True)
        stopped = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_running_loop().add_signal_handler(name, stopped.set)
            except (NotImplementedError, RuntimeError):
                pass
        await stopped.wait()
    finally:
        for socket in tuple(fixture.sockets):
            await socket.close()
        await app_runner.cleanup()
        await engine_runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-port", type=int, default=8792)
    parser.add_argument("--engine-port", type=int, default=8793)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.app_port, args.engine_port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
