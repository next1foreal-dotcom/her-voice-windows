#!/usr/bin/env python3
"""Inspect a short DeepSeek SSE reply. Default is dry-run; --live may incur API cost."""
from __future__ import annotations

import argparse
import asyncio
import codecs
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class ProbeError(ValueError):
    """A safe-to-display error that does not include provider bodies or credentials."""


def completion_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as error:
        raise ProbeError("The API base URL is malformed.") from error
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    official = parsed.hostname == "api.deepseek.com" and parsed.scheme == "https" and port in {None, 443}
    if not (official or local and parsed.scheme in {"http", "https"}):
        raise ProbeError("The probe accepts official HTTPS DeepSeek or a loopback mock endpoint only.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/v1", "/beta"}:
        raise ProbeError("The API base URL must not contain credentials, query parameters, or a completion path.")
    return base_url.rstrip("/") + "/chat/completions"


def load_local_settings():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from her_voice.settings import Settings
    try:
        return Settings.load(root=ROOT)
    except (OSError, ValueError) as error:
        raise ProbeError("Cannot load local .env settings; check its KEY=value syntax. Values are omitted.") from error


def request_body(model: str, max_tokens: int = 64) -> dict:
    if not 1 <= max_tokens <= 256:
        raise ProbeError("Probe max_tokens must be between 1 and 256.")
    return {"model": model, "messages": [
        {"role": "system", "content": "Reply with one short Chinese sentence."},
        {"role": "user", "content": "请简短地打个招呼。"},
    ], "thinking": {"type": "disabled"}, "temperature": 0.7, "max_tokens": max_tokens,
            "stream": True, "stream_options": {"include_usage": True}}


class SSEDecoder:
    def __init__(self, max_event_bytes: int = 1024 * 1024):
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer = ""
        self.lines: list[str] = []
        self.event_size = 0
        self.limit = max_event_bytes

    def feed(self, data: bytes) -> list[str]:
        try:
            self.buffer += self.decoder.decode(data)
        except UnicodeDecodeError as error:
            raise ProbeError("Invalid UTF-8 in the SSE stream.") from error
        messages = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.removesuffix("\r")
            self.event_size += len(line.encode("utf-8"))
            if self.event_size > self.limit:
                raise ProbeError("SSE event exceeds the probe size limit.")
            if line == "":
                if self.lines:
                    messages.append("\n".join(self.lines))
                self.lines = []
                self.event_size = 0
            elif line.startswith("data:"):
                self.lines.append(line[5:].removeprefix(" "))
        if len(self.buffer.encode("utf-8")) + self.event_size > self.limit:
            raise ProbeError("SSE event exceeds the probe size limit.")
        return messages

    def finish(self) -> list[str]:
        try:
            tail = self.decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise ProbeError("Truncated UTF-8 in the SSE stream.") from error
        if tail:
            self.buffer += tail
        # SSE requires the event separator; don't bless an unterminated [DONE].
        if self.buffer or self.lines:
            raise ProbeError("SSE stream ended inside an event.")
        return []


@dataclass
class ProbeState:
    content_characters: int = 0
    reasoning_characters: int = 0
    tool_calls_seen: bool = False
    done: bool = False
    finish_reason: str | None = None
    usage: dict | None = None

    def accept(self, data: str) -> None:
        if data == "[DONE]":
            self.done = True
            return
        try:
            chunk = json.loads(data)
        except (ValueError, TypeError) as error:
            raise ProbeError("SSE data is not valid JSON.") from error
        if not isinstance(chunk, dict) or "error" in chunk:
            raise ProbeError("The provider returned an error or invalid stream object.")
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = {key: value for key, value in usage.items()
                          if key in {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
                          and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        choices = chunk.get("choices", [])
        if not isinstance(choices, list):
            raise ProbeError("Invalid choices in the SSE stream.")
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(choice.get("delta", {}), dict):
                raise ProbeError("Invalid delta in the SSE stream.")
            delta = choice.get("delta", {})
            if isinstance(delta.get("content"), str):
                self.content_characters += len(delta["content"])
            if isinstance(delta.get("reasoning_content"), str):
                self.reasoning_characters += len(delta["reasoning_content"])
            self.tool_calls_seen |= bool(delta.get("tool_calls"))
            if choice.get("finish_reason") is not None:
                reason = choice["finish_reason"]
                self.finish_reason = reason if reason in {"stop", "length", "tool_calls", "content_filter", "insufficient_system_resource"} else "unknown"

    def result(self) -> dict:
        if not self.done:
            raise ProbeError("Stream ended without data: [DONE].")
        if not self.content_characters or not self.finish_reason:
            raise ProbeError("Stream has no spoken content or terminal finish_reason.")
        if self.reasoning_characters or self.tool_calls_seen:
            raise ProbeError("The text-only, non-thinking probe unexpectedly received reasoning or tool calls.")
        if not self.usage or "total_tokens" not in self.usage:
            raise ProbeError("The completed stream did not report usable token usage.")
        if self.finish_reason not in {"stop", "length"}:
            raise ProbeError("The provider did not finish a normal text reply.")
        return {"ok": True, "content_characters": self.content_characters,
                "finish_reason": self.finish_reason, "usage": self.usage,
                "thinking": "disabled", "done_marker": True}


async def run_probe(base_url: str, api_key: str, model: str, *, max_tokens: int = 64,
                    timeout: float = 45, session=None) -> dict:
    import aiohttp
    endpoint = completion_url(base_url)
    if not api_key.strip():
        raise ProbeError("DEEPSEEK_API_KEY is missing.")
    body = request_body(model, max_tokens)
    if session is None:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, connect=10), trust_env=True) as owned:
            return await run_probe(base_url, api_key, model, max_tokens=max_tokens, timeout=timeout, session=owned)
    started = time.monotonic()
    first_content_ms = None
    state, decoder = ProbeState(), SSEDecoder()
    try:
        async with session.post(endpoint, json=body, headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"}, allow_redirects=False) as response:
            if response.status != 200:
                raise ProbeError(f"DeepSeek probe failed with HTTP {response.status}; response body omitted to avoid credential or content disclosure.")
            if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                raise ProbeError("The provider returned HTTP 200 without an SSE content type.")
            async for chunk in response.content.iter_chunked(4096):
                for data in decoder.feed(chunk):
                    state.accept(data)
                    if state.content_characters and first_content_ms is None:
                        first_content_ms = round((time.monotonic() - started) * 1000)
                    if state.done:
                        result = state.result()
                        result.update(first_content_ms=first_content_ms, elapsed_ms=round((time.monotonic() - started) * 1000))
                        return result
            decoder.finish()
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise ProbeError("Network/TLS/proxy failure or timeout during the probe. Check connectivity; provider bodies and credentials are omitted.") from error
    return state.result()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly make one short, potentially billable API request.")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", help="Defaults to DEEPSEEK_MODEL from the shared .env/environment settings.")
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        settings = load_local_settings()
        if getattr(settings, "provider", "deepseek") != "deepseek":
            raise ProbeError("This probe is DeepSeek-only. Test the configured Agent adapter separately; no request was made.")
        model = args.model or settings.deepseek_model
        endpoint = completion_url(args.base_url)
        request_body(model, args.max_tokens)
        if not args.live:
            print(json.dumps({"live": False, "endpoint": endpoint, "model": model,
                              "thinking": "disabled", "max_tokens": args.max_tokens,
                              "note": "No request made. --live explicitly authorizes one potentially billable probe."}, ensure_ascii=False, indent=2))
            return 0
        result = asyncio.run(run_probe(args.base_url, settings.deepseek_api_key, model, max_tokens=args.max_tokens))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ProbeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
