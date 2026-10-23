"""Explicit protocol exercise with text and a test tone, never fake ASR/TTS."""

from __future__ import annotations

import asyncio
import base64
import math
import struct
import uuid

from aiohttp import web, WSMsgType


def identifier(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:12]


def tone_pcm(duration: float = 0.12) -> str:
    samples = int(24000 * duration)
    data = bytearray(samples * 2)
    for i in range(samples):
        # Quiet sine with a fade at both ends; intentionally not human speech.
        envelope = min(1.0, i / 240, (samples - 1 - i) / 240)
        value = int(2200 * envelope * math.sin(2 * math.pi * 440 * i / 24000))
        struct.pack_into("<h", data, i * 2, value)
    return base64.b64encode(data).decode("ascii")


async def demo_session(ws: web.WebSocketResponse) -> None:
    task: asyncio.Task | None = None
    active: str | None = None
    latest = ""
    paused = False

    async def send(kind: str, **fields) -> None:
        if not ws.closed:
            await ws.send_json({"type": kind, "event_id": identifier("evt"), **fields})

    async def answer(text: str) -> None:
        nonlocal active
        rid = identifier("demo_response")
        active = rid
        item = identifier("demo_assistant")
        common = {"response_id": rid, "item_id": item, "output_index": 0, "content_index": 0}
        await send("response.created", response={"id": rid, "status": "in_progress"})
        result = f"演示回复：「{text[:80]}」。"
        try:
            await asyncio.sleep(0.18)
            for offset in range(0, len(result), 5):
                await send("response.output_audio_transcript.delta", **common, delta=result[offset:offset + 5])
                await send("response.output_audio.delta", **common, delta=tone_pcm())
                await asyncio.sleep(0.12)
            await send("response.output_audio_transcript.done", **common, transcript=result)
            await send("response.output_audio.done", **common)
            await send("response.done", response={"id": rid, "status": "completed"})
        except asyncio.CancelledError:
            await send("response.done", response={"id": rid, "status": "cancelled"})
            raise
        finally:
            if active == rid:
                active = None

    async def cancel() -> None:
        nonlocal task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        task = None

    await send("session.created", session={"id": identifier("demo_session"), "type": "realtime"})
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                if message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
                continue
            try:
                event = message.json()
                kind = event.get("type")
                if kind == "session.update":
                    session = event.get("session", {})
                    paused = not session.get("audio", {}).get("input", {}).get("turn_detection", {}).get("create_response", True)
                    await send("session.updated", session={"id": "demo", **session})
                    if paused:
                        await cancel()
                elif kind == "conversation.item.create":
                    item = event.get("item", {})
                    latest = "".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "input_text")[:8000]
                    await send("conversation.item.created", item={"id": item.get("id", identifier("demo_user")), **item})
                elif kind == "response.create" and not paused:
                    await cancel()
                    task = asyncio.create_task(answer(latest or "测试语音流程"))
                elif kind == "response.cancel" and event.get("response_id") == active:
                    await cancel()
            except (ValueError, TypeError, AttributeError):
                await send("error", error={"type": "invalid_request_error", "message": "Invalid demo protocol event."})
    finally:
        await cancel()
