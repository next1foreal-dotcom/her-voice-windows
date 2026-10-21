"""Reusable entry points and an actual local Agent-adapter contract."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from her_voice.app import RUNTIME, create_app
from her_voice.settings import Settings
from her_voice.resources import default_asset_root
from her_voice.__main__ import main
from her_voice.upstream_patch import _expected_environment


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reuse_agent_stub", ROOT / "examples" / "agent_stub.py")
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)


class IdleEngine:
    def __init__(self):
        self.starts = 0
    def start(self):
        self.starts += 1
        return self.status()
    def stop(self):
        return {"state": "stopped"}
    def status(self):
        return {"state": "ready", "ready": True}


class ConfigurationIntegrationTests(unittest.TestCase):
    def test_patch_environment_uses_selected_workspace_and_keeps_isolated_venv(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VOICE_WORKSPACE": directory}):
            self.assertEqual(_expected_environment(), Path(directory).resolve() / ".venv-speech")

    def test_profile_and_custom_ports_are_passed_to_the_owned_worker(self):
        settings = Settings(root=ROOT, provider="openai-compatible", provider_model="host-agent",
                            provider_url="http://127.0.0.1:8900/v1/chat/completions",
                            app_id="joyboi-test", port=8890, engine_port=8891)
        app = create_app(settings)
        engine = app[RUNTIME].engine
        env = engine._child_environment()
        self.assertEqual(engine.endpoint, settings.engine_url)
        self.assertEqual(env["VOICE_GATEWAY_BASE_URL"], "http://127.0.0.1:8890/llm/v1")
        self.assertEqual(env["VOICE_ENGINE_PORT"], "8891")
        self.assertEqual(env["VOICE_LLM_MODEL"], "host-agent")
        self.assertEqual(env["VOICE_WORKSPACE"], str(ROOT))
        self.assertTrue(engine.config_path.is_file())

    def test_cli_overrides_keep_web_engine_and_gateway_addresses_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("VOICE_PORT=8810\nVOICE_ENGINE_PORT=8811\n", encoding="utf-8")
            argv = ["voice-runtime", "--workspace", directory, "--demo", "--port", "8820",
                    "--engine-port", "8821", "--app-id", "joyboi-example", "--app-name", "Joyboi Voice"]
            with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", argv), \
                 patch("her_voice.__main__.web.run_app") as run, redirect_stdout(io.StringIO()):
                main()
            app = run.call_args.args[0]
            settings = app[RUNTIME].settings
            self.assertEqual(run.call_args.kwargs["port"], 8820)
            self.assertEqual(settings.engine_url, "ws://127.0.0.1:8821/v1/realtime")
            self.assertEqual(app[RUNTIME].engine.port, 8821)
            self.assertEqual(settings.app_id, "joyboi-example")
            self.assertEqual(settings.app_name, "Joyboi Voice")
            self.assertEqual(settings.root, root.resolve())
            self.assertTrue(settings.profile_path.is_file())

    def test_default_shipped_assets_do_not_depend_on_current_directory(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                assets = default_asset_root()
                self.assertTrue((assets / "web" / "index.html").is_file())
                self.assertTrue((assets / "config" / "windows-4080.yaml").is_file())
            finally:
                os.chdir(original)


class ReuseHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stub = TestServer(stub.create_stub_app())
        await self.stub.start_server()
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.stub.close()

    async def application(self, app_id):
        settings = Settings(root=ROOT, provider="openai-compatible", provider_model="agent-stub",
                            provider_url=str(self.stub.make_url("/v1/chat/completions")),
                            app_id=app_id, app_name=app_id + " voice")
        engine = IdleEngine()
        client = TestClient(TestServer(create_app(settings, engine=engine)))
        await client.start_server()
        self.clients.append(client)
        return settings, engine, client

    async def test_local_agent_without_deepseek_key_can_start_and_has_safe_bootstrap(self):
        settings, engine, client = await self.application("joyboi-test")
        response = await client.post("/api/engine/start", headers={"X-Voice-Token": settings.browser_token})
        self.assertEqual(response.status, 200)
        await response.read()
        self.assertEqual(engine.starts, 1)
        response = await client.get("/api/bootstrap")
        body = await response.json()
        self.assertTrue(body["health"]["provider_ready"])
        self.assertFalse(body["health"]["api_key_configured"])
        self.assertEqual(body["health"]["app_id"], "joyboi-test")
        self.assertNotIn(settings.provider_url, json.dumps(body))
        for resource in ("/client.js", "/audio.js", "/capture-worklet.js"):
            response = await client.get(resource)
            self.assertEqual(response.status, 200)
            self.assertGreater(len(await response.read()), 100)

    async def test_two_app_instances_route_their_own_text_to_explicit_stub(self):
        instances = [await self.application(name) for name in ("her-test", "joyboi-test")]
        first_settings, _, first = instances[0]
        second_settings, _, second = instances[1]
        rejected = await second.post("/api/engine/start", headers={"X-Voice-Token": first_settings.browser_token})
        self.assertEqual(rejected.status, 403)
        await rejected.read()
        for settings, _, client in instances:
            response = await client.post("/llm/v1/chat/completions", headers={"Authorization": "Bearer " + settings.gateway_token},
                                         json={"model": "ignored-incoming", "messages": [{"role": "user", "content": settings.app_id + "的句子"}], "stream": True})
            self.assertEqual(response.status, 200)
            content = await response.text()
            self.assertIn("data: [DONE]", content)
            answer = "".join(json.loads(frame[6:])["choices"][0]["delta"].get("content", "")
                             for frame in content.split("\n\n") if frame.startswith("data: {") )
            self.assertIn(settings.app_id, answer)
            self.assertNotIn("joyboi-test" if settings.app_id == "her-test" else "her-test", answer)
            self.assertIn("沒有接上真正的 Agent", answer)

    async def test_warmup_does_not_echo_user_text_or_execute_tools(self):
        settings, _, client = await self.application("joyboi-test")
        response = await client.post("/llm/v1/chat/completions", headers={"Authorization": "Bearer " + settings.gateway_token},
                                     json={"messages": [{"role": "user", "content": "must not become a task"}], "stream": False})
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual(body["choices"][0]["message"]["content"], "Adapter fixture ready.")
        self.assertNotIn("tool_calls", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
