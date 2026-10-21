import json
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from her_voice.settings import Settings, read_env


class SettingsTests(unittest.TestCase):
    def test_env_is_literal_and_supports_quotes_bom_and_equals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("\ufeff# local settings\nDEEPSEEK_API_KEY='fake=key'\nDEEPSEEK_MODEL=deepseek-flash\nLITERAL=$(not-a-command)\n", encoding="utf-8")
            values = read_env(path)
        self.assertEqual(values["DEEPSEEK_API_KEY"], "fake=key")
        self.assertEqual(values["LITERAL"], "$(not-a-command)")

    def test_invalid_env_never_echoes_the_value(self):
        for text in ("=private-value", "1BAD=private-value", "BAD NAME=private-value", "NO_EQUALS_private-value", "DEEPSEEK_API_KEY='private-value"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / ".env"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError) as raised:
                    read_env(path)
                self.assertNotIn("private-value", str(raised.exception))

    def test_process_environment_overrides_file_without_importing_other_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "process-test-key", "DEEPSEEK_MODEL": "model-from-process", "OPENAI_API_KEY": "unrelated-key"}, clear=True):
            root = Path(directory)
            (root / ".env").write_text("DEEPSEEK_API_KEY=file-test-key\nDEEPSEEK_MODEL=file-model\n", encoding="utf-8")
            settings = Settings.load(root=root)
        self.assertEqual(settings.deepseek_api_key, "process-test-key")
        self.assertEqual(settings.deepseek_model, "model-from-process")
        self.assertNotIn("unrelated-key", repr(settings))

    def test_placeholder_keys_are_not_treated_as_configured(self):
        for key in ("", "your_deepseek_api_key_here", "sk-your-key-here"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": key}, clear=True):
                settings = Settings.load(root=Path(directory))
                self.assertEqual(settings.deepseek_api_key, "")
                self.assertFalse(settings.public()["api_key_configured"])

    def test_secrets_never_appear_in_repr_or_public_settings(self):
        settings = Settings(deepseek_api_key="private-provider-key", gateway_token="private-engine-token", browser_token="private-browser-token")
        visible = repr(settings) + json.dumps(settings.public())
        for secret in (settings.deepseek_api_key, settings.gateway_token, settings.browser_token):
            self.assertNotIn(secret, visible)
        self.assertTrue(settings.public()["api_key_configured"])

    def test_new_launches_have_independent_local_tokens(self):
        a, b = Settings(), Settings()
        self.assertNotEqual(a.gateway_token, b.gateway_token)
        self.assertNotEqual(a.browser_token, b.browser_token)
        self.assertNotEqual(a.gateway_token, a.browser_token)

    def test_multiline_provider_key_and_model_fail_without_echoing_values(self):
        for key, value in (("DEEPSEEK_API_KEY", "private\nvalue"), ("DEEPSEEK_MODEL", "model\nvalue")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {key: value}, clear=True):
                with self.assertRaises(ValueError) as raised:
                    Settings.load(root=Path(directory))
                self.assertNotIn(value, str(raised.exception))

    def test_generic_loopback_is_ready_without_key_and_never_uses_deepseek_credentials(self):
        settings = Settings(provider="openai-compatible", provider_url="http://127.0.0.1:9001/v1/chat/completions",
                            provider_model="her-agent", deepseek_api_key="unused-private-cloud-key", deepseek_model="unused-cloud-model")
        self.assertEqual(settings.api_key, "")
        self.assertEqual(settings.model, "her-agent")
        self.assertTrue(settings.provider_ready)
        self.assertFalse(settings.public()["api_key_configured"])
        self.assertEqual(settings.public()["provider"], "openai-compatible")
        self.assertNotIn("unused-private-cloud-key", repr(settings) + json.dumps(settings.public()))

    def test_generic_provider_requires_explicit_endpoint_and_model(self):
        for values in (
            {"provider_model": "host-agent"},
            {"provider_url": "http://127.0.0.1:9001/v1/chat/completions"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings(provider="openai-compatible", deepseek_api_key="private-legacy-key", **values)

    def test_remote_provider_requires_key_to_be_ready(self):
        settings = Settings(provider="openai-compatible", provider_url="https://private-host.invalid/private-path", provider_model="host-agent")
        self.assertFalse(settings.provider_ready)
        self.assertFalse(settings.public()["api_key_configured"])
        configured = replace(settings, provider_api_key="private-adapter-key")
        self.assertTrue(configured.provider_ready)
        visible = repr(configured) + json.dumps(configured.public())
        for secret in ("private-adapter-key", "private-host", "private-path"):
            self.assertNotIn(secret, visible)

    def test_provider_url_policy_errors_never_echo_sensitive_values(self):
        invalid = (
            "http://private-host.invalid/chat/completions",
            "https://private-user:private-key@example.invalid/chat/completions",
            "http://@127.0.0.1:9001/chat/completions",
            "https://example.invalid/chat/completions?private-key=secret",
            "https://example.invalid/chat/completions#private-fragment",
            "https://example.invalid:private-port/chat/completions",
            "https://example.invalid:65536/chat/completions",
            "https://example.invalid/\nprivate-path",
            "https://example.invalid/\\private-path",
            "ftp://private-host.invalid/chat/completions",
            "private-relative-path",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError) as raised:
                Settings(provider="openai-compatible", provider_url=endpoint, provider_model="host-agent")
            self.assertNotIn("private", str(raised.exception))

    def test_supported_loopback_hosts_and_remote_https(self):
        for endpoint in ("http://127.0.0.1:9001/chat/completions", "http://localhost:9001/chat/completions", "http://[::1]:9001/chat/completions"):
            with self.subTest(endpoint=endpoint):
                self.assertTrue(Settings(provider="openai-compatible", provider_url=endpoint, provider_model="host-agent").provider_ready)

    def test_voice_environment_overrides_file_and_resolves_workspace_paths(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "VOICE_PROVIDER": "openai-compatible", "VOICE_PROVIDER_URL": "http://127.0.0.1:9001/v1/chat/completions",
            "VOICE_PROVIDER_MODEL": "process-model", "VOICE_APP_ID": "joyboi", "VOICE_APP_NAME": "Joyboi Voice",
            "VOICE_PORT": "8890", "VOICE_ENGINE_PORT": "8891", "VOICE_PROFILE": "profiles/custom.yaml",
            "VOICE_WEB_ROOT": "custom-web", "VOICE_ASSET_ROOT": "shared-assets",
        }, clear=True):
            root = Path(directory)
            (root / ".env").write_text("VOICE_APP_ID=file-id\nVOICE_PROVIDER_MODEL=file-model\nVOICE_PORT=8990\n", encoding="utf-8")
            settings = Settings.load(root=root)
        self.assertEqual((settings.app_id, settings.app_name, settings.port, settings.engine_port), ("joyboi", "Joyboi Voice", 8890, 8891))
        self.assertEqual(settings.model, "process-model")
        self.assertEqual(settings.engine_url, "ws://127.0.0.1:8891/v1/realtime")
        self.assertEqual(settings.profile_path, root / "profiles/custom.yaml")
        self.assertEqual(settings.web_root, root / "custom-web")
        self.assertEqual(settings.asset_root, root / "shared-assets")

    def test_asset_defaults_are_independent_of_user_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            assets = Path(directory) / "installed-assets"
            with patch("her_voice.settings.default_asset_root", return_value=assets):
                settings = Settings(root=root)
        self.assertEqual(settings.root, root)
        self.assertEqual(settings.web_root, assets / "web")
        self.assertEqual(settings.profile_path, assets / "config/windows-4080.yaml")

    def test_cli_can_recompute_engine_url_when_replacing_port(self):
        settings = replace(Settings(), port=8890, engine_port=8891, engine_url=None)
        self.assertEqual(settings.engine_url, "ws://127.0.0.1:8891/v1/realtime")

    def test_invalid_shared_settings_do_not_echo_values(self):
        for values in (
            {"provider": "private-provider"}, {"host": "private-host"}, {"port": "private-port"},
            {"engine_port": 0}, {"app_id": "private\nheader"}, {"app_name": "private\nname"},
            {"provider_api_key": "private\nkey"}, {"engine_url": "ws://private-host.invalid/v1/realtime"},
            {"engine_url": "ws://127.0.0.1:0/v1/realtime"}, {"port": 8791},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError) as raised:
                Settings(**values)
            self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
