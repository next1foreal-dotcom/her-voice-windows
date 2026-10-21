"""Local stdlib child-process tests. No speech models, GPU, or API calls."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from her_voice.engine import EngineError, EngineSupervisor, _worker_main, cuda_dll_directories


FAKE_SERVER = r'''
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, sys, time
port = int(sys.argv[1])
time.sleep(float(sys.argv[2]))
print("engine fixture boot", flush=True)
print("Bearer " + os.environ["HER_VOICE_GATEWAY_TOKEN"], flush=True)
assert "DEEPSEEK_API_KEY" not in os.environ
assert "OPENAI_API_KEY" not in os.environ
assert "HER_LLM_API_KEY" not in os.environ
assert "VOICE_PROVIDER_API_KEY" not in os.environ
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/pool":
            self.send_error(404)
            return
        data = json.dumps({"size": 1, "in_use": 0, "units": [{"index": 0, "state": "idle", "session_id": None}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "profile.yaml"
        self.config.write_text("schema_version: 1\n", encoding="utf-8")
        self.engines: list[EngineSupervisor] = []

    def tearDown(self) -> None:
        for engine in self.engines:
            engine.stop()
        self.temp.cleanup()

    def engine(self, **kwargs) -> EngineSupervisor:
        engine = EngineSupervisor(
            self.config, self.root, port=kwargs.pop("port", free_port()),
            startup_timeout=kwargs.pop("startup_timeout", 4.0), stop_timeout=0.4,
            poll_interval=0.025, env={"HER_VOICE_GATEWAY_TOKEN": "test-gateway-token-private"},
            **kwargs,
        )
        self.engines.append(engine)
        return engine

    def fake_command(self, engine, delay=0.0):
        return [sys.executable, "-u", "-c", FAKE_SERVER, str(engine.port), str(delay)]

    def test_transient_http_protocol_errors_do_not_kill_readiness(self):
        engine = self.engine()
        original = engine._http.open
        faults = [http.client.BadStatusLine("GET /v1/pool HTTP/1.1"),
                  http.client.IncompleteRead(b"{"), http.client.RemoteDisconnected()]
        def flaky(*args, **kwargs):
            if faults:
                raise faults.pop(0)
            return original(*args, **kwargs)
        with patch.object(engine, "build_command", return_value=self.fake_command(engine)), \
             patch.object(engine._http, "open", side_effect=flaky):
            self.assertTrue(engine.start(wait=True)["ready"])
        self.assertEqual(faults, [])
    def test_command_is_new_cli_and_contains_no_key(self):
        engine = self.engine()
        command = engine.build_command()
        self.assertEqual(command[1:6], ["-u", "-m", "her_voice.engine", "--worker", "serve"])
        self.assertEqual(command[-2:], ["--name", "windows4080"])
        self.assertIn(str(self.config.resolve()), command)
        self.assertNotIn("--mode", command)
        self.assertNotIn("test-gateway-token-private", " ".join(command))

    def test_provider_payment_failure_is_actionable_without_private_output(self):
        engine = self.engine()
        code = 'print(\'HTTP Request: POST http://127.0.0.1:8790/llm/v1/chat/completions "HTTP/1.1 402 Payment Required"\', flush=True); print("private conversation fixture", flush=True); raise SystemExit(1)'
        with patch.object(engine, "build_command", return_value=[sys.executable, "-u", "-c", code]):
            with self.assertRaisesRegex(EngineError, "HTTP 402"):
                engine.start(wait=True)
        status = engine.status(probe=False)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('HTTP 402', status['detail'])
        self.assertNotIn('private conversation', status['detail'])
        self.assertFalse(status['ready'])

    def test_worker_stages_models_before_upstream_cli(self):
        fake_models = ModuleType("her_voice.models")
        staged = {
            "HER_ASR_MODEL_PATH": str(self.root / "asr-snapshot"),
            "HER_TTS_MODEL_PATH": str(self.root / "tts-snapshot"),
            "HER_SMART_TURN_MODEL_PATH": str(self.root / "smart-turn-v3.2-cpu.onnx"),
        }
        fake_models.ensure_models = Mock(return_value=staged)
        observed = []
        def run(module, *, run_name):
            observed.append((module, run_name, list(sys.argv), {key: os.environ[key] for key in staged}))
        args = ["engine", "--worker", "serve", "--file", str(self.config), "--name", "windows4080"]
        with patch.dict(sys.modules, {"her_voice.models": fake_models}), patch.dict(os.environ, {"HER_ASR_MODEL_PATH": "stale-unpinned-path"}), patch.object(sys, "argv", args), patch("her_voice.upstream_patch.verify_installed_silero_patch") as verify, patch("her_voice.engine.runpy.run_module", side_effect=run):
            _worker_main()
        verify.assert_called_once_with()
        fake_models.ensure_models.assert_called_once_with(tts_backend="qwen3")
        self.assertEqual(observed, [("speech_to_speech.cli", "__main__", ["speech-to-speech", *args[2:]], staged)])

    def test_model_staging_failure_does_not_launch_upstream(self):
        fake_models = ModuleType("her_voice.models")
        fake_models.ensure_models = Mock(side_effect=RuntimeError("fixture missing model files"))
        with patch.dict(sys.modules, {"her_voice.models": fake_models}), patch.object(sys, "argv", ["engine", "--worker", "serve"]), patch("her_voice.upstream_patch.verify_installed_silero_patch"), patch("her_voice.engine.runpy.run_module") as run:
            with self.assertRaisesRegex(RuntimeError, "missing model files"):
                _worker_main()
        run.assert_not_called()

    def test_unverified_upstream_does_not_download_models_or_launch(self):
        fake_models = ModuleType("her_voice.models")
        fake_models.ensure_models = Mock()
        with patch.dict(sys.modules, {"her_voice.models": fake_models}), patch.object(sys, "argv", ["engine", "--worker", "serve"]), patch("her_voice.upstream_patch.verify_installed_silero_patch", side_effect=RuntimeError("fixture unknown VAD hash")), patch("her_voice.engine.runpy.run_module") as run:
            with self.assertRaisesRegex(RuntimeError, "unknown VAD hash"):
                _worker_main()
        fake_models.ensure_models.assert_not_called()
        run.assert_not_called()

    def test_windows_cuda_directories_are_existing_and_deduplicated(self):
        environment = self.root / ".venv-speech"
        binary = environment / "Scripts" / "python.exe"
        torch_lib = environment / "Lib" / "site-packages" / "torch" / "lib"
        cuda_bin = self.root / "cuda" / "bin"
        torch_lib.mkdir(parents=True)
        cuda_bin.mkdir(parents=True)
        paths = cuda_dll_directories({"CUDA_PATH": str(cuda_bin.parent), "HER_VOICE_CUDA_DLL_DIR": str(torch_lib)}, binary)
        self.assertEqual(paths, [str(torch_lib.resolve()), str(cuda_bin.resolve())])

    def test_loopback_bypasses_proxies_without_losing_existing_exclusions(self):
        engine = self.engine()
        exclusions = {"NO_PROXY": ".example.test,localhost"}
        expected = {".example.test", "localhost", "127.0.0.1"}
        # Windows treats environment variable names as case insensitive.
        if os.name != "nt":
            exclusions["no_proxy"] = "company.test"
            expected.add("company.test")
        with patch.dict(os.environ, exclusions):
            env = engine._child_environment()
        self.assertEqual(env["NO_PROXY"], env["no_proxy"])
        self.assertEqual(set(env["NO_PROXY"].split(",")), expected)

    def test_real_child_http_readiness_and_stop(self):
        engine = self.engine()
        with patch.object(engine, "build_command", return_value=self.fake_command(engine)):
            status = engine.start(wait=True)
        self.assertTrue(status["ready"])
        self.assertTrue(status["owned"])
        self.assertEqual(status["pool"]["units"][0]["state"], "idle")
        self.assertGreater(status["pid"], 0)
        self.assertEqual(engine.start()["pid"], status["pid"])
        stopped = engine.stop()
        self.assertEqual(stopped["state"], "stopped")
        self.assertFalse(stopped["owned"])
        self.assertFalse(stopped["running"])

    def test_default_start_returns_before_model_readiness(self):
        engine = self.engine()
        with patch.object(engine, "build_command", return_value=self.fake_command(engine, delay=1.0)):
            before = time.monotonic()
            status = engine.start()
        self.assertLess(time.monotonic() - before, 0.8)
        self.assertEqual(status["state"], "starting")
        self.assertFalse(status["ready"])
        with self.assertRaises(EngineError):
            engine.start()
        engine.stop()
        self.assertEqual(engine.status()["state"], "stopped")

    def test_foreign_listener_is_not_adopted_or_stopped(self):
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", 0))
            foreign.listen()
            port = foreign.getsockname()[1]
            engine = self.engine(port=port)
            with patch("her_voice.engine.subprocess.Popen") as popen:
                with self.assertRaisesRegex(EngineError, "already in use"):
                    engine.start()
            popen.assert_not_called()
            engine.stop()
            self.assertFalse(engine.status()["owned"])
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass

    def test_credentials_are_removed_from_child_and_log_tail(self):
        engine = self.engine(echo_logs=True)
        cloud_key = "sk-private-cloud-value"
        terminal = io.StringIO()
        with redirect_stdout(terminal), patch.dict(os.environ, {"DEEPSEEK_API_KEY": cloud_key, "OPENAI_API_KEY": "private-other-key", "HER_LLM_API_KEY": "private-her-key", "VOICE_PROVIDER_API_KEY": "private-agent-key"}), patch.object(engine, "build_command", return_value=self.fake_command(engine)):
            status = engine.start(wait=True)
            rendered = json.dumps(engine.stop())
        self.assertTrue(status["ready"])
        self.assertNotIn("test-gateway-token-private", rendered)
        self.assertNotIn(cloud_key, rendered)
        self.assertIn("[redacted]", rendered)
        self.assertIn("engine fixture boot", rendered)
        self.assertIn("[engine] engine fixture boot", terminal.getvalue())
        self.assertIn("[engine] Bearer [redacted]", terminal.getvalue())
        self.assertNotIn("test-gateway-token-private", terminal.getvalue())
        self.assertNotIn(cloud_key, terminal.getvalue())

    def test_engine_terminal_echo_is_off_by_default(self):
        engine = self.engine()
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            engine._collect_output(Mock(stdout=io.StringIO("conversation fixture\n")))
        self.assertEqual(terminal.getvalue(), "")
        self.assertIn("conversation fixture", engine.status(probe=False)["log_tail"])

    def test_exit_before_readiness_exposes_failure(self):
        engine = self.engine()
        command = [sys.executable, "-u", "-c", "print('missing CUDA fixture', flush=True); raise SystemExit(7)"]
        with patch.object(engine, "build_command", return_value=command):
            with self.assertRaisesRegex(EngineError, "code 7"):
                engine.start(wait=True)
        status = engine.status()
        self.assertEqual(status["state"], "failed")
        self.assertFalse(status["running"])
        self.assertIn("missing CUDA fixture", "\n".join(status["log_tail"]))

    def test_startup_failure_log_stops_a_worker_that_has_not_exited(self):
        engine = self.engine(startup_timeout=2.0)
        code = 'import time; print("HER_DRAMA_WARMUP_FAILED", flush=True); print("Configured startup failed; check the selected backends and runtime resources.", flush=True); time.sleep(60)'
        with patch.object(engine, "build_command", return_value=[sys.executable, "-u", "-c", code]):
            with self.assertRaisesRegex(EngineError, "語音預熱失敗"):
                engine.start(wait=True)
        self.assertEqual(engine.status(probe=False)["state"], "failed")
        self.assertFalse(engine.status(probe=False)["running"])

    def test_readiness_timeout_terminates_owned_child(self):
        engine = self.engine(startup_timeout=0.15)
        command = [sys.executable, "-u", "-c", "import time; time.sleep(60)"]
        with patch.object(engine, "build_command", return_value=command):
            with self.assertRaisesRegex(EngineError, "timed out"):
                engine.start(wait=True)
        self.assertEqual(engine.status()["state"], "failed")
        self.assertFalse(engine.status()["running"])

    def test_stop_interrupts_an_inflight_readiness_wait(self):
        engine = self.engine()
        errors = []
        def start():
            try:
                engine.start(wait=True)
            except EngineError as exc:
                errors.append(str(exc))
        with patch.object(engine, "build_command", return_value=self.fake_command(engine, delay=20)):
            thread = threading.Thread(target=start)
            thread.start()
            deadline = time.monotonic() + 2
            while engine.status(probe=False)["state"] != "starting" and time.monotonic() < deadline:
                time.sleep(0.01)
            engine.stop()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(engine.status()["state"], "stopped")
        self.assertTrue(errors)

    def test_missing_local_gateway_token_fails_before_launch(self):
        engine = self.engine()
        engine.extra_env.clear()
        with patch.dict(os.environ, {}, clear=True), patch("her_voice.engine.subprocess.Popen") as popen:
            with self.assertRaisesRegex(EngineError, "GATEWAY_TOKEN is missing"):
                engine.start()
        popen.assert_not_called()

    def test_http_health_failure_can_recover_without_restart(self):
        engine = self.engine()
        with patch.object(engine, "build_command", return_value=self.fake_command(engine)):
            engine.start(wait=True)
        with patch.object(engine, "_probe_pool", return_value=None):
            self.assertEqual(engine.status()["state"], "unhealthy")
        self.assertTrue(engine.status()["ready"])

    def test_stale_readiness_worker_cannot_stop_a_new_process(self):
        engine = self.engine()
        with patch.object(engine, "build_command", return_value=self.fake_command(engine)):
            engine.start(wait=True)
            old_process = engine._process
            engine.stop()
            engine.start(wait=True)
            current_pid = engine.status()["pid"]
            engine.stop(_expected=old_process, _failure="old timeout")
        self.assertEqual(engine.status()["pid"], current_pid)
        self.assertTrue(engine.status()["ready"])


if __name__ == "__main__":
    unittest.main()
