"""Narrow OpenAI-compatible proxy; the engine never receives the cloud key."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from .providers import ALLOWED_FIELDS, MissingProviderKey, build_provider_request, chat_payload

if TYPE_CHECKING:
    from .settings import Settings


class SSECompletion:
    """Observe complete SSE data events without changing forwarded bytes."""

    def __init__(self) -> None:
        self.buffer = b""
        self.data: list[bytes] = []
        self.event = b""
        self.complete = False

    def feed(self, chunk: bytes) -> list[bytes]:
        events = []
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self.event += line + b"\n"
            line = line.removesuffix(b"\r")
            if not line:
                if b"\n".join(self.data) == b"[DONE]":
                    self.complete = True
                self.data.clear()
                events.append(self.event)
                self.event = b""
            elif line.startswith(b"data:"):
                value = line[5:]
                self.data.append(value[1:] if value.startswith(b" ") else value)
            if len(self.event) > 1_048_576:
                raise ValueError("Provider SSE event is too large.")
        if len(self.buffer) > 1_048_576:
            raise ValueError("Provider SSE line is too large.")
        return events


def provider_payload(data: dict, model: str) -> dict:
    """Backward-compatible DeepSeek adapter; generic mode uses its own policy."""
    return chat_payload(data, model, provider="deepseek")


def _safe_provider_frame(frame: bytes) -> bytes:
    """Preserve normal SSE bytes; provider errors must not echo secrets/URLs."""
    data = []
    error_event = False
    for line in frame.splitlines():
        if line.startswith(b"event:") and line[6:].strip() == b"error":
            error_event = True
        elif line.startswith(b"data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(b" ") else value)
    if error_event:
        raise ValueError("Provider stream reported an error.")
    if data:
        value = b"\n".join(data)
        if value != b"[DONE]":
            decoded = json.loads(value)
            if not isinstance(decoded, dict) or decoded.get("error"):
                raise ValueError("Provider stream reported an error or invalid event.")
    return frame


def api_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"type": "gateway_error", "code": code, "message": message}}, status=status)


class CompletionGateway:
    def __init__(self, settings: "Settings", session: aiohttp.ClientSession) -> None:
        self.settings = settings
        self.session = session

    async def completions(self, request: web.Request) -> web.StreamResponse:
        expected = "Bearer " + self.settings.gateway_token
        if not secrets.compare_digest(request.headers.get("Authorization", "").encode("utf-8"), expected.encode("utf-8")):
            return api_error(401, "gateway_auth", "Local speech engine authentication failed.")
        if not self.settings.provider_ready:
            key_name = "DEEPSEEK_API_KEY" if self.settings.provider == "deepseek" else "VOICE_PROVIDER_API_KEY"
            return api_error(503, "missing_key", f"Configure {key_name} for the selected provider.")
        try:
            outgoing = build_provider_request(self.settings, await request.json())
            payload = outgoing.payload
        except MissingProviderKey:
            return api_error(503, "missing_key", "The selected provider requires a configured API key.")
        except (ValueError, TypeError, json.JSONDecodeError):
            return api_error(400, "invalid_request", "Invalid chat-completions request.")

        downstream: web.StreamResponse | None = None
        try:
            async with self.session.post(
                outgoing.url,
                headers=outgoing.headers,
                json=payload,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=180, connect=10, sock_read=90),
            ) as upstream:
                if upstream.status != 200:
                    # Never forward arbitrary provider text: errors may echo request contents.
                    code = {401: "provider_auth", 402: "provider_balance", 429: "provider_rate_limit"}.get(upstream.status, "provider_error")
                    return api_error(upstream.status if 400 <= upstream.status < 600 else 502, code,
                                     f"Configured provider returned HTTP {upstream.status}; check the local provider settings.")
                if not payload["stream"]:
                    data = await upstream.json(content_type=None)
                    if not isinstance(data, dict) or data.get("error"):
                        return api_error(502, "provider_error", "The provider returned an invalid response or an error.")
                    return web.json_response(data)
                if "text/event-stream" not in upstream.headers.get("Content-Type", ""):
                    return api_error(502, "invalid_sse", "The provider did not return an event stream.")
                downstream = web.StreamResponse(headers={
                    "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store", "X-Accel-Buffering": "no",
                })
                await downstream.prepare(request)
                # Preserve normal SSE frames and Unicode bytes. Error envelopes
                # are inspected, but normal token payloads are not re-encoded.
                terminal = SSECompletion()
                async for chunk in upstream.content.iter_any():
                    # Forward complete SSE frames unchanged. Holding an unfinished
                    # frame avoids accidentally completing a truncated [DONE]
                    # when appending an error at EOF. Latency is one SSE frame.
                    for frame in terminal.feed(chunk):
                        await downstream.write(_safe_provider_frame(frame))
                if not terminal.complete:
                    await downstream.write(b'event: error\ndata: {"error":{"type":"gateway_error","code":"incomplete_stream","message":"Provider stream ended before DONE."}}\n\n')
                await downstream.write_eof()
                return downstream
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            if downstream is not None and downstream.prepared:
                # A standard error frame is understood by OpenAI's SSE decoder.
                try:
                    await downstream.write(b'event: error\ndata: {"error":{"type":"gateway_error","code":"interrupted_stream","message":"Provider stream was interrupted."}}\n\n')
                    await downstream.write_eof()
                except (aiohttp.ClientError, OSError):
                    downstream.force_close()
                return downstream
            return api_error(502, "provider_unreachable", "The configured provider could not be reached or timed out.")


# Existing integrations/tests may keep the original import while selecting a
# provider explicitly in Settings. There is no implicit DeepSeek fallback.
DeepSeekGateway = CompletionGateway
