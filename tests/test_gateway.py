import asyncio
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from her_voice.gateway import CompletionGateway, DeepSeekGateway, provider_payload
from her_voice.settings import Settings


def sse(value):
    return ("data: " + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")


COMPLETE_STREAM = (
    b": keep-alive\n\n"
    + sse({"choices": [{"delta": {"content": "你好，Hello! GPU memory 儲存模型權重。"}, "finish_reason": None}]})
    + sse({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 8}})
    + b"data: [DONE]\n\n"
)


class PayloadTests(unittest.TestCase):
    def test_client_cannot_override_provider_model_thinking_or_add_extra_fields(self):
        original = {"model": "untrusted-model", "thinking": {"type": "enabled"}, "messages": [{"role": "user", "content": "test"}], "stream": True, "stream_options": {"include_usage": True}, "api_key": "untrusted", "extra_body": {"thinking": "enabled"}}
        result = provider_payload(original, "deepseek-flash")
        self.assertEqual(result["model"], "deepseek-flash")
        self.assertEqual(result["thinking"], {"type": "disabled"})
        self.assertNotIn("api_key", result)
        self.assertNotIn("extra_body", result)
        self.assertEqual(original["thinking"], {"type": "enabled"})

    def test_non_streaming_drops_stream_only_options(self):
        result = provider_payload({"messages": [{"role": "user", "content": "test"}], "stream": False, "stream_options": {"include_usage": True}}, "deepseek-flash")
        self.assertFalse(result["stream"])
        self.assertNotIn("stream_options", result)

    def test_missing_empty_excessive_or_invalid_messages_are_rejected(self):
        for data in ([], {}, {"messages": []}, {"messages": [{}]}, {"messages": [{"role": "developer"}]}, {"messages": [{"role": "user"}] * 101}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                provider_payload(data, "deepseek-flash")


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider_calls = []
        self.provider_behavior = None

        async def provider(request):
            self.provider_calls.append({"authorization": request.headers.get("Authorization"), "body": await request.json(),
                                        "app_id": request.headers.get("X-Voice-App-ID"), "purpose": request.headers.get("X-Voice-Request-Purpose")})
            if self.provider_behavior is not None:
                return await self.provider_behavior(request)
            return web.Response(body=COMPLETE_STREAM, content_type="text/event-stream")

        provider_app = web.Application()
        provider_app.router.add_post("/completion", provider)
        self.provider_server = TestServer(provider_app)
        await self.provider_server.start_server()
        self.session = aiohttp.ClientSession(trust_env=False)
        self.settings = Settings(deepseek_api_key="private-provider-test-key", gateway_token="private-local-test-token", provider_url=str(self.provider_server.make_url("/completion")))
        self.gateway = DeepSeekGateway(self.settings, self.session)
        gateway_app = web.Application(client_max_size=1_048_576)
        gateway_app.router.add_post("/chat/completions", self.gateway.completions)
        self.client = TestClient(TestServer(gateway_app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.session.close()
        await self.provider_server.close()

    def request_args(self, *, stream=True):
        return {"headers": {"Authorization": "Bearer " + self.settings.gateway_token},
                "json": {"messages": [{"role": "user", "content": "private-test-transcript"}], "stream": stream, "model": "ignored-client-model", "thinking": {"type": "enabled"}}}

    def assert_stream_failure(self, body):
        # OpenAI's SDK stops on a data value starting with [DONE], and raises on
        # JSON.error or malformed JSON. Merely appending an error after a DONE
        # value would not make that SDK surface the earlier truncation.
        try:
            text = body.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            return
        for block in text.split("\n\n")[:-1]:
            data = "\n".join(line[5:].removeprefix(" ") for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            self.assertFalse(data.startswith("[DONE]"), "The SDK would stop at DONE and never see the following error.")
            try:
                event = json.loads(data)
            except ValueError:
                return
            if isinstance(event, dict) and event.get("error"):
                return
        self.fail("Incomplete SSE must cause a decoder or provider error, not graceful partial success.")

    async def test_authentication_and_missing_key_prevent_provider_calls(self):
        response = await self.client.post("/chat/completions", json={"messages": []})
        self.assertEqual(response.status, 401)
        await response.read()
        self.gateway.settings = replace(self.settings, deepseek_api_key="")
        response = await self.client.post("/chat/completions", **self.request_args())
        self.assertEqual(response.status, 503)
        await response.read()
        self.assertEqual(self.provider_calls, [])

    async def test_non_ascii_gateway_authorization_is_rejected_without_a_server_error(self):
        response = await self.client.post("/chat/completions", headers={"Authorization": "Bearer 錯誤"}, json={"messages": []})
        self.assertEqual(response.status, 401)
        await response.read()
        self.assertEqual(self.provider_calls, [])

    async def test_invalid_json_and_message_shape_never_reach_provider(self):
        headers = {"Authorization": "Bearer " + self.settings.gateway_token, "Content-Type": "application/json"}
        for value in ("{", "[]", '{"messages":[]}'):
            response = await self.client.post("/chat/completions", data=value, headers=headers)
            self.assertEqual(response.status, 400)
            await response.read()
        self.assertEqual(self.provider_calls, [])

    async def test_stream_preserves_unicode_bytes_and_usage(self):
        async def provider(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for offset in range(0, len(COMPLETE_STREAM), 7):
                await response.write(COMPLETE_STREAM[offset:offset + 7])
                await asyncio.sleep(0)
            await response.write_eof()
            return response
        self.provider_behavior = provider
        args = self.request_args()
        args["json"]["messages"] = [{"role": "user", "content": "請用 English 解釋 GPU memory，再用中文總結。"}]
        response = await self.client.post("/chat/completions", **args)
        self.assertEqual(response.status, 200)
        body = await response.read()
        self.assertEqual(body, COMPLETE_STREAM)
        call = self.provider_calls[0]
        self.assertEqual(call["authorization"], "Bearer " + self.settings.deepseek_api_key)
        self.assertEqual(call["body"]["model"], "deepseek-flash")
        self.assertEqual(call["body"]["thinking"], {"type": "disabled"})
        self.assertEqual(call["body"]["messages"], args["json"]["messages"])
        self.assertNotIn(self.settings.deepseek_api_key.encode(), body)

    async def test_provider_errors_are_sanitized(self):
        async def provider(_request):
            return web.json_response({"error": "private-test-transcript " + self.settings.deepseek_api_key}, status=401)
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args())
        self.assertEqual(response.status, 401)
        body = await response.text()
        self.assertIn("provider_auth", body)
        self.assertNotIn("private-test-transcript", body)
        self.assertNotIn(self.settings.deepseek_api_key, body)

    async def test_generic_loopback_without_auth_streams_with_no_deepseek_fields(self):
        self.assertIs(CompletionGateway, DeepSeekGateway)
        self.gateway.settings = replace(self.settings, provider="openai-compatible", provider_model="host-agent", app_id="joyboi")
        args = self.request_args()
        args["headers"]["X-Voice-App-ID"] = "untrusted-client-app"
        response = await self.client.post("/chat/completions", **args)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), COMPLETE_STREAM)
        self.assertEqual(len(self.provider_calls), 1)
        call = self.provider_calls[0]
        self.assertIsNone(call["authorization"])
        self.assertEqual(call["body"]["model"], "host-agent")
        self.assertNotIn("thinking", call["body"])
        self.assertEqual(call["app_id"], "joyboi")
        self.assertEqual(call["purpose"], "conversation")

    async def test_generic_nonstream_uses_own_key_and_warmup_hint(self):
        self.gateway.settings = replace(self.settings, provider="openai-compatible", provider_model="host-agent", provider_api_key="private-adapter-key")
        async def provider(_request):
            return web.json_response({"choices": [{"message": {"role": "assistant", "content": "warmup"}}]})
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args(stream=False))
        self.assertEqual(response.status, 200)
        await response.read()
        self.assertEqual(self.provider_calls[0]["authorization"], "Bearer private-adapter-key")
        self.assertEqual(self.provider_calls[0]["purpose"], "warmup")
        self.assertNotIn("thinking", self.provider_calls[0]["body"])

    async def test_generic_remote_missing_key_prevents_any_request_or_fallback(self):
        self.gateway.settings = replace(self.settings, provider="openai-compatible", provider_model="host-agent",
                                        provider_url="https://private-host.invalid/private-path")
        with patch.object(self.session, "post") as post:
            response = await self.client.post("/chat/completions", **self.request_args())
            body = await response.text()
        self.assertEqual(response.status, 503)
        post.assert_not_called()
        self.assertNotIn("private-host", body)
        self.assertNotIn(self.settings.deepseek_api_key, body)

    async def test_generic_network_failure_is_sanitized_and_does_not_fallback(self):
        selected = replace(self.settings, provider="openai-compatible", provider_model="host-agent")
        self.gateway.settings = selected
        with patch.object(self.session, "post", side_effect=aiohttp.ClientConnectionError("private-endpoint private-key")) as post:
            response = await self.client.post("/chat/completions", **self.request_args())
            body = await response.text()
        self.assertEqual(response.status, 502)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], selected.provider_url)
        self.assertNotIn("private", body)

    async def test_http_200_json_error_does_not_leak_provider_details(self):
        async def provider(_request):
            return web.json_response({"error": {"message": "private-endpoint " + self.settings.deepseek_api_key}})
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args(stream=False))
        body = await response.text()
        self.assertEqual(response.status, 502)
        self.assertNotIn("private", body)

    async def test_http_200_sse_error_does_not_leak_provider_details(self):
        for partial in (sse({"error": {"message": "private-endpoint " + self.settings.deepseek_api_key}}),
                        b"event: error\ndata: private-endpoint private-key\n\n",
                        b"data: invalid-json-private-endpoint\n\n"):
            with self.subTest(partial=partial):
                async def provider(_request):
                    return web.Response(body=partial, content_type="text/event-stream")
                self.provider_behavior = provider
                response = await self.client.post("/chat/completions", **self.request_args())
                body = await response.read()
                self.assert_stream_failure(body)
                self.assertNotIn(b"private", body)

    async def test_non_sse_response_is_rejected(self):
        async def provider(_request):
            return web.json_response({"choices": []})
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args())
        self.assertEqual(response.status, 502)
        self.assertEqual((await response.json())["error"]["code"], "invalid_sse")

    async def test_non_streaming_completion_is_supported(self):
        data = {"id": "mock", "choices": [{"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}], "usage": {"total_tokens": 5}}
        async def provider(_request):
            return web.json_response(data)
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args(stream=False))
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), data)

    async def test_redirect_never_forwards_the_transcript_to_another_endpoint(self):
        redirected = []
        async def destination(request):
            redirected.append(await request.read())
            return web.Response(body=COMPLETE_STREAM, content_type="text/event-stream")
        destination_app = web.Application()
        destination_app.router.add_post("/collect", destination)
        server = TestServer(destination_app)
        await server.start_server()
        try:
            async def provider(_request):
                return web.Response(status=307, headers={"Location": str(server.make_url("/collect"))})
            self.provider_behavior = provider
            response = await self.client.post("/chat/completions", **self.request_args())
            await response.read()
            self.assertEqual(redirected, [], "The gateway must not send a transcript to a redirected endpoint.")
            self.assertEqual(response.status, 502)
        finally:
            await server.close()

    async def test_clean_http_eof_without_done_is_not_a_successful_sse_reply(self):
        partial = sse({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})
        async def provider(_request):
            return web.Response(body=partial, content_type="text/event-stream")
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args())
        try:
            body = await response.read()
        except aiohttp.ClientPayloadError:
            return  # Abrupt chunked termination is an explicit transport failure.
        self.assert_stream_failure(body)
        self.assertNotIn(b"data: [DONE]", body)

    async def test_done_text_inside_json_or_comment_does_not_finish_the_stream(self):
        for partial in (
            sse({"choices": [{"delta": {"content": "Explain the literal data: [DONE] marker"}}]}),
            b": comment containing data: [DONE]\n\n" + sse({"choices": [{"delta": {"content": "partial"}}]}),
        ):
            with self.subTest(partial=partial):
                async def provider(_request):
                    return web.Response(body=partial, content_type="text/event-stream")
                self.provider_behavior = provider
                response = await self.client.post("/chat/completions", **self.request_args())
                try:
                    body = await response.read()
                except aiohttp.ClientPayloadError:
                    continue
                self.assert_stream_failure(body)

    async def test_done_marker_must_be_a_complete_sse_event(self):
        partial = sse({"choices": [{"delta": {"content": "partial"}}]}) + b"data: [DONE]"
        async def provider(_request):
            return web.Response(body=partial, content_type="text/event-stream")
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args())
        try:
            body = await response.read()
        except aiohttp.ClientPayloadError:
            return
        self.assert_stream_failure(body)

    async def test_cancelling_client_releases_provider_request(self):
        provider_cancelled = asyncio.Event()
        async def provider(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(sse({"choices": [{"delta": {"content": "start"}}]}))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                provider_cancelled.set()
                raise
        self.provider_behavior = provider
        response = await self.client.post("/chat/completions", **self.request_args())
        self.assertTrue(await response.content.readany())
        response.close()
        await asyncio.wait_for(provider_cancelled.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
