"""Explicit local contract fixture. No real Agent, memory, tools, or API calls.

Run this separately, then configure VOICE_PROVIDER_URL to the printed endpoint.
The stub demonstrates the adapter protocol; it does not implement HER/Joyboi.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid

from aiohttp import web


async def complete(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            raise ValueError()
        if any(not isinstance(message, dict) for message in body["messages"]):
            raise ValueError()
    except (ValueError, TypeError):
        return web.json_response({"error": {"message": "messages must be an array of objects"}}, status=400)
    app_id = request.headers.get("X-Voice-App-ID", "standalone")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", app_id):
        return web.json_response({"error": {"message": "invalid routing label"}}, status=400)
    identifier = "stub-" + uuid.uuid4().hex
    model = str(body.get("model", "agent-stub"))[:128]
    created = int(time.time())
    if not body.get("stream"):
        # The pinned speech engine uses non-streaming during startup. Never
        # translate that warmup into a user-authorized tool or durable task.
        return web.json_response({"id": identifier, "object": "chat.completion", "created": created,
                                  "model": model, "choices": [{"index": 0, "message": {
                                      "role": "assistant", "content": "Adapter fixture ready."}, "finish_reason": "stop"}]})
    latest = next((message.get("content", "") for message in reversed(body["messages"])
                   if message.get("role") == "user"), "")
    if not isinstance(latest, str):
        latest = "（非純文字輸入）"
    answer = f"這是 {app_id} 的本機接口測試，沒有接上真正的 Agent。收到的文字是：{latest[:400]}"
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store"})
    await response.prepare(request)
    try:
        for start in range(0, len(answer), 6):
            event = {"id": identifier, "object": "chat.completion.chunk", "created": created, "model": model,
                     "choices": [{"index": 0, "delta": {"content": answer[start:start + 6]}, "finish_reason": None}]}
            await response.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8"))
            await asyncio.sleep(0.025)
        event = {"id": identifier, "object": "chat.completion.chunk", "created": created, "model": model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        await response.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())
        await response.write_eof()
    except (ConnectionResetError, asyncio.CancelledError):
        # Only this disposable stream stops. No durable work exists here.
        pass
    return response


def create_stub_app() -> web.Application:
    app = web.Application(client_max_size=1_048_576)
    app.router.add_post("/v1/chat/completions", complete)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8900)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    print(f"TEST FIXTURE ONLY — http://127.0.0.1:{args.port}/v1/chat/completions")
    web.run_app(create_stub_app(), host="127.0.0.1", port=args.port, access_log=None, handler_cancellation=True)


if __name__ == "__main__":
    main()
