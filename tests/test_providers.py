"""Request-adapter tests: no sockets, provider requests, models, or tools."""

import unittest

from her_voice.providers import MissingProviderKey, build_provider_request, chat_payload
from her_voice.settings import Settings


class ProviderAdapterTests(unittest.TestCase):
    def generic(self, **changes):
        values = {"provider": "openai-compatible", "provider_url": "http://127.0.0.1:9001/v1/chat/completions",
                  "provider_model": "local-host-agent", "app_id": "joyboi"}
        values.update(changes)
        return Settings(**values)

    def message(self, **changes):
        value = {"messages": [{"role": "user", "content": "private-transcript"}], "stream": True}
        value.update(changes)
        return value

    def test_generic_no_auth_has_no_deepseek_extras_and_uses_trusted_hints(self):
        settings = self.generic(deepseek_api_key="private-unused-key", deepseek_model="unused-cloud-model")
        request = build_provider_request(settings, self.message(model="untrusted-model", thinking={"type": "enabled"},
                                         extra_body={"thinking": {"type": "disabled"}}, api_key="untrusted-key", app_id="untrusted-app"))
        self.assertEqual(request.url, settings.provider_url)
        self.assertEqual(request.payload["model"], "local-host-agent")
        self.assertNotIn("thinking", request.payload)
        self.assertNotIn("extra_body", request.payload)
        self.assertNotIn("api_key", request.payload)
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.headers["X-Voice-App-ID"], "joyboi")
        self.assertEqual(request.headers["X-Voice-Request-Purpose"], "conversation")

    def test_generic_optional_key_is_its_own_and_repr_is_private(self):
        request = build_provider_request(self.generic(provider_api_key="private-host-key", deepseek_api_key="unused-cloud-key"), self.message())
        self.assertEqual(request.headers["Authorization"], "Bearer private-host-key")
        for value in ("private-host-key", "private-transcript", "127.0.0.1", "unused-cloud-key"):
            self.assertNotIn(value, repr(request))

    def test_generic_remote_without_key_never_builds_a_request(self):
        settings = self.generic(provider_url="https://private-host.invalid/private-endpoint", deepseek_api_key="unused-cloud-key")
        with self.assertRaises(MissingProviderKey) as raised:
            build_provider_request(settings, self.message())
        self.assertNotIn("private", str(raised.exception))

    def test_generic_nonstream_is_marked_warmup_and_drops_stream_options(self):
        request = build_provider_request(self.generic(), self.message(stream=False, stream_options={"include_usage": True}))
        self.assertEqual(request.headers["X-Voice-Request-Purpose"], "warmup")
        self.assertNotIn("stream_options", request.payload)
        self.assertNotIn("thinking", request.payload)

    def test_deepseek_policy_and_original_configuration_are_preserved(self):
        request = build_provider_request(Settings(deepseek_api_key="private-cloud-key"), self.message())
        self.assertEqual(request.url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.payload["model"], "deepseek-flash")
        self.assertEqual(request.payload["thinking"], {"type": "disabled"})
        self.assertEqual(request.headers["Authorization"], "Bearer private-cloud-key")
        self.assertNotIn("X-Voice-App-ID", request.headers)
        self.assertNotIn("X-Voice-Request-Purpose", request.headers)

    def test_tools_are_plain_data_and_not_executed_or_rewritten(self):
        tools = [{"type": "function", "function": {"name": "host_tool", "parameters": {"type": "object"}}}]
        messages = [{"role": "developer", "content": "host policy"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "host_tool", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call_1", "content": "existing result"}]
        request = build_provider_request(self.generic(), self.message(messages=messages, tools=tools, tool_choice="auto"))
        self.assertEqual(request.payload["messages"], messages)
        self.assertEqual(request.payload["tools"], tools)
        self.assertEqual(request.payload["tool_choice"], "auto")

    def test_unsupported_provider_cannot_select_a_default_adapter(self):
        with self.assertRaisesRegex(ValueError, "VOICE_PROVIDER"):
            chat_payload(self.message(), "model", provider="unsupported")


if __name__ == "__main__":
    unittest.main()
