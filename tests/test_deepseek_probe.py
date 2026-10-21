import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deepseek_probe", ROOT / "scripts" / "probe-deepseek.py")
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


def event(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


class DecoderTests(unittest.TestCase):
    def test_fragmented_utf8_comments_and_multiline_data(self):
        wire = b": keep-alive\r\n\r\n" + event({"choices": [{"delta": {"content": "你好"}}]}) + b"data: one\ndata: two\n\ndata: [DONE]\n\n"
        decoder = probe.SSEDecoder()
        messages = []
        for byte in wire:
            messages.extend(decoder.feed(bytes([byte])))
        messages.extend(decoder.finish())
        self.assertEqual(json.loads(messages[0])["choices"][0]["delta"]["content"], "你好")
        self.assertEqual(messages[1:], ["one\ntwo", "[DONE]"])

    def test_current_deepseek_usage_on_terminal_choice_is_read(self):
        state = probe.ProbeState()
        state.accept(json.dumps({"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]}))
        state.accept(json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}))
        state.accept("[DONE]")
        result = state.result()
        self.assertEqual(result["content_characters"], 2)
        self.assertEqual(result["usage"]["total_tokens"], 7)
        self.assertEqual(result["finish_reason"], "stop")

    def test_compatible_usage_only_chunk_is_also_tolerated(self):
        state = probe.ProbeState()
        state.accept('{"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}')
        state.accept('{"choices":[],"usage":{"total_tokens":3}}')
        state.accept("[DONE]")
        self.assertEqual(state.result()["usage"]["total_tokens"], 3)

    def test_reasoning_and_tool_arguments_are_not_spoken_content(self):
        state = probe.ProbeState()
        state.accept('{"choices":[{"delta":{"reasoning_content":"private reasoning","tool_calls":[{"index":0,"function":{"arguments":"secret"}}]}}]}')
        self.assertEqual(state.content_characters, 0)
        state.accept('{"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}],"usage":{"total_tokens":3}}')
        state.accept("[DONE]")
        with self.assertRaises(probe.ProbeError):
            state.result()

    def test_truncated_or_oversized_stream_fails(self):
        state = probe.ProbeState()
        state.accept('{"choices":[{"delta":{"content":"partial"}}]}')
        with self.assertRaises(probe.ProbeError):
            state.result()
        with self.assertRaises(probe.ProbeError):
            probe.SSEDecoder(max_event_bytes=8).feed(b"data: " + b"x" * 20)

    def test_request_explicitly_disables_thinking(self):
        body = probe.request_body("deepseek-flash", 64)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertNotIn("tools", body)

    def test_endpoint_prevents_credentials_being_sent_to_arbitrary_host(self):
        self.assertEqual(probe.completion_url("https://api.deepseek.com/"), "https://api.deepseek.com/chat/completions")
        self.assertEqual(probe.completion_url("http://127.0.0.1:8765"), "http://127.0.0.1:8765/chat/completions")
        for value in ["https://evil.example", "http://api.deepseek.com", "https://user:pass@api.deepseek.com", "https://api.deepseek.com/?key=secret", "https://api.deepseek.com:invalid"]:
            with self.subTest(value=value), self.assertRaises(probe.ProbeError):
                probe.completion_url(value)


class CliTests(unittest.TestCase):
    def test_default_dry_run_never_calls_api_even_with_a_configured_key(self):
        settings = SimpleNamespace(deepseek_api_key="never-print-configured-key", deepseek_model="deepseek-flash")
        output = io.StringIO()
        with patch.object(probe, "load_local_settings", return_value=settings), patch.object(probe, "run_probe", new_callable=AsyncMock) as run, patch("sys.stdout", output):
            self.assertEqual(probe.main([]), 0)
        run.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertFalse(result["live"])
        self.assertNotIn(settings.deepseek_api_key, output.getvalue())

    def test_live_cli_uses_shared_settings_and_has_no_key_argument(self):
        settings = SimpleNamespace(deepseek_api_key="test-only-key", deepseek_model="deepseek-flash")
        output = io.StringIO()
        with patch.object(probe, "load_local_settings", return_value=settings), patch.object(probe, "run_probe", new_callable=AsyncMock, return_value={"ok": True}) as run, patch("sys.stdout", output):
            self.assertEqual(probe.main(["--live"]), 0)
        run.assert_awaited_once_with("https://api.deepseek.com", "test-only-key", "deepseek-flash", max_tokens=64)
        self.assertNotIn(settings.deepseek_api_key, output.getvalue())


class FakeResponse:
    def __init__(self, chunks, status=200, content_type="text/event-stream"):
        self.chunks, self.status = chunks, status
        self.headers = {"Content-Type": content_type}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeSession:
    def __init__(self, response):
        self.response, self.calls = response, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_transport_with_mock_stream_and_no_content_disclosure(self):
        content = "你好，不输出完整回答"
        chunks = [event({"choices": [{"delta": {"content": content}}]}),
                  event({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 9}}), b"data: [DONE]\n\n"]
        session = FakeSession(FakeResponse(chunks))
        result = await probe.run_probe("https://api.deepseek.com", "never-echo-this-key", "deepseek-flash", session=session)
        self.assertEqual(result["content_characters"], len(content))
        self.assertNotIn(content, json.dumps(result, ensure_ascii=False))
        self.assertNotIn("never-echo-this-key", json.dumps(result))
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_http_failure_and_non_sse_are_explicit(self):
        for response in [FakeResponse([], status=401), FakeResponse([], content_type="application/json")]:
            with self.subTest(status=response.status), self.assertRaises(probe.ProbeError):
                await probe.run_probe("https://api.deepseek.com", "test-key", "deepseek-flash", session=FakeSession(response))


if __name__ == "__main__":
    unittest.main()
