"""Native process ownership/configuration tests; no real model or GPU."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from her_voice.drama import (
    AUDIOCPP_REVISION, MODEL_REVISION, MODEL_SHA256, DramaSupervisor, build_profile_settings,
)
from her_voice.engine import EngineError


FAKE_NATIVE = r'''
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, sys
for key in ('DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'VOICE_PROVIDER_API_KEY', 'HER_VOICE_GATEWAY_TOKEN'):
    assert key not in os.environ
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            data = {'status': 'ok', 'backend': 'cuda', 'models': 1}
        elif self.path == '/v1/models':
            data = {'object': 'list', 'data': [{'id': 'drama-3', 'family': 'fish_drama3', 'task': 'tts', 'mode': sys.argv[2], 'loaded': True}]}
        else:
            self.send_error(404); return
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        assert self.path == '/v1/tasks/run'
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert payload['model'] == 'drama-3'
        request = payload['request']
        assert os.path.isfile(request['voice_ref'])
        assert request['options']['encode_reference_only'] is True
        assert request['options']['reference_text'] == '\u4f60\u597d\uff0c\u8fd9\u662f\u6211\u7684\u58f0\u97f3\u3002 Hello, this is my voice.'
        body = json.dumps({'artifacts': [{'kind': 'acoustic_tokens', 'payload': 'e30='}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DramaRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.root / ".runtime" / "drama3"
        self.install.mkdir(parents=True)
        self.model = self.root / "model.gguf"
        self.model.write_bytes(b"GGUF" + bytes(252))
        self.executable = self.root / "server.exe"
        self.executable.write_bytes(b"fake-native-server")
        self.base = self.install / "server-base.json"
        self.base.write_text(json.dumps({
            "host": "127.0.0.1", "port": 8792, "backend": "cuda", "models": [{
                "id": "drama-3", "family": "fish_drama3", "task": "tts", "mode": "streaming",
                "path": str(self.model), "default_request_options": {"max_tokens": 500},
            }],
        }))
        self.manifest_path = self.install / "runtime.json"
        self.manifest = {
            "schema_version": 1, "runtime_revision": AUDIOCPP_REVISION,
            "model_revision": MODEL_REVISION, "model_sha256": MODEL_SHA256,
            "model_size_bytes": self.model.stat().st_size,
            "model_mtime_ns": self.model.stat().st_mtime_ns,
            "executable": str(self.executable), "model_path": str(self.model),
            "source_dir": str(self.root), "server_config_path": str(self.base),
            "executable_sha256": hashlib.sha256(self.executable.read_bytes()).hexdigest(),
        }
        self.write_manifest()
        self.voice = self.root / "参考 声音.wav"
        with wave.open(str(self.voice), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(bytes(24000 * 2))
        self.patcher = patch("her_voice.drama.MODEL_SIZE_BYTES", 256)
        self.patcher.start()
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.stop()
        self.patcher.stop()
        self.temp.cleanup()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def server(self, *, port=None, timeout=3):
        server = DramaSupervisor(self.root, port=port or free_port(), startup_timeout=timeout,
                                 stop_timeout=0.3, poll_interval=0.025)
        self.servers.append(server)
        return server

    def prepare(self, server):
        return server.prepare_voice("voice_123", self.voice, "你好，这是我的声音。 Hello, this is my voice.")

    def test_http_protocol_failure_is_not_native_readiness(self):
        server = self.server()
        for error in (http.client.BadStatusLine("GET /health HTTP/1.1"),
                      http.client.IncompleteRead(b"{"), http.client.RemoteDisconnected()):
            with self.subTest(error=type(error).__name__), patch.object(server._http, "open", side_effect=error):
                self.assertIsNone(server._probe_pool())
    def test_profile_selects_real_audio_stream_and_actual_source_rate(self):
        settings = build_profile_settings("voice_123", port=9002)
        self.assertEqual(settings["openai_tts_base_url"], "http://127.0.0.1:9002/v1")
        self.assertEqual(settings["openai_tts_sample_rate"], 44100)
        self.assertEqual(settings["openai_tts_response_format"], "pcm")
        self.assertEqual(settings["openai_tts_model"], "drama-3")
        self.assertFalse(settings["openai_tts_stream"])  # vLLM-only extension
        self.assertIsNone(settings["openai_tts_language"])
        self.assertNotIn("openai_tts_gen_kwargs", settings)

    def test_selected_voice_and_offload_are_written_without_changing_installer_config(self):
        original = self.base.read_bytes()
        server = self.server()
        path = self.prepare(server)
        config = json.loads(path.read_text())
        self.assertEqual(self.base.read_bytes(), original)
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["port"], server.port)
        self.assertEqual(config["max_loaded_models"], 1)
        model = config["models"][0]
        self.assertEqual(model["default_voice_preset"], "voice_123")
        self.assertEqual(model["voice_presets"]["voice_123"]["voice_ref"], str(self.voice))
        self.assertIn("你好", model["voice_presets"]["voice_123"]["reference_text"])
        self.assertTrue(model["session_options"]["fish_drama3.pin_experts"])
        self.assertEqual(model["session_options"]["fish_drama3.cpu_expert_layers"], 0)
        self.assertEqual(model["session_options"]["fish_drama3.expert_cache_size"], 8)
        self.assertEqual(model["session_options"]["fish_drama3.codec_encoder_chunk_samples"], 65536)
        self.assertEqual(server.build_command()[0], str(self.executable))
        self.assertIn("--no-ui", server.build_command())

    def test_cold_load_progress_is_measured_and_stale_progress_is_cleared(self):
        server = self.server()
        self.prepare(server)
        config = json.loads(server.config_path.read_text())
        self.assertFalse(config["lazy_load"])
        self.assertFalse(config["models"][0]["lazy"])
        server._state = "starting"
        server._redact("[TIMING ts=20261021] disk_experts.loaded_host_mb 5616")
        for _ in range(100):
            server._logs.append("unrelated line")
        snapshot = server.status(probe=False)
        self.assertEqual(snapshot["loaded_expert_mib"], 5616)
        self.assertIn("5.5 / 16.9 GiB", snapshot["detail"])
        server._state = "stopped"
        self.prepare(server)
        self.assertEqual(server.status(probe=False)["loaded_expert_mib"], 0)

    def test_unknown_revision_and_changed_weights_are_refused(self):
        self.manifest["runtime_revision"] = "f" * 40
        self.write_manifest()
        with self.assertRaisesRegex(EngineError, "pinned"):
            self.prepare(self.server())
        self.manifest["runtime_revision"] = AUDIOCPP_REVISION
        self.write_manifest()
        self.model.write_bytes(b"GGUFbroken")
        with self.assertRaisesRegex(EngineError, "incomplete"):
            self.prepare(self.server())

    def test_changed_executable_is_refused(self):
        self.executable.write_bytes(b"changed executable")
        with self.assertRaisesRegex(EngineError, "executable changed"):
            self.prepare(self.server())

    def test_mtime_change_requires_model_reverification(self):
        stat = self.model.stat()
        os.utime(self.model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10000000))
        with self.assertRaisesRegex(EngineError, "after SHA-256"):
            self.prepare(self.server())

    def test_invalid_or_absent_voice_does_not_start_a_process(self):
        server = self.server()
        with self.assertRaises(EngineError):
            server.prepare_voice("../voice", self.voice, "hello")
        with self.assertRaises(EngineError):
            server.prepare_voice("valid", self.voice, "")
        with patch("her_voice.engine.subprocess.Popen") as spawn:
            with self.assertRaises(EngineError):
                server.start()
        spawn.assert_not_called()

    def test_real_child_health_models_and_owned_shutdown(self):
        server = self.server()
        self.prepare(server)
        command = [sys.executable, "-u", "-c", FAKE_NATIVE, str(server.port), "streaming"]
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "private-test", "HER_VOICE_GATEWAY_TOKEN": "private-local"}), \
             patch.object(server, "build_command", return_value=command):
            status = server.start(wait=True)
        self.assertTrue(status["ready"])
        self.assertTrue(status["pool"]["model_loaded"])
        server.prepare_reference()  # Real HTTP fixture validates the full selected reference.
        self.assertEqual(status["source_sample_rate"], 44100)
        self.assertNotIn("upstream_sha", status)
        with self.assertRaisesRegex(EngineError, "Stop"):
            self.prepare(server)
        self.assertFalse(server.stop()["owned"])

    def test_wrong_model_mode_never_passes_readiness(self):
        server = self.server(timeout=0.3)
        self.prepare(server)
        command = [sys.executable, "-u", "-c", FAKE_NATIVE, str(server.port), "offline"]
        with patch.object(server, "build_command", return_value=command):
            with self.assertRaisesRegex(EngineError, "timed out"):
                server.start(wait=True)
        self.assertFalse(server.status()["running"])

    def test_foreign_port_is_never_adopted_or_stopped(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0)); other.listen()
            port = other.getsockname()[1]
            server = self.server(port=port)
            self.prepare(server)
            with patch("her_voice.engine.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(EngineError, "already in use"):
                    server.start()
            spawn.assert_not_called()
            server.stop()
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass


if __name__ == "__main__":
    unittest.main()
