import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import yaml

from her_voice.engine import EngineError
from her_voice.settings import Settings
from her_voice.tts import VoiceEngine, profile_for_backend
from her_voice.voices import VoiceStore
from test_voices import recording


class Child:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.ready = False
        self.port = 8791
        self.endpoint = "fixture"
        self.config_path = None
        self.extra_env = {}
        self.prepared = []
        self.reference_prepared = False
    def prepare_voice(self, *args):
        self.prepared.append(args)
    def prepare_reference(self):
        self.reference_prepared = True
    def start(self, **kwargs):
        self.started += 1
        self.ready = True
    def status(self, **kwargs):
        return {"state": "ready" if self.ready else "stopped", "ready": self.ready, "running": self.ready, "log_tail": []}
    def stop(self):
        self.stopped += 1
        self.ready = False


class BackendLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(root=Path(self.temporary.name))
        self.voices = VoiceStore(self.settings.root)
        self.voice = self.voices.add(name="test", transcript="你好，hello", audio=recording())
        self.speech, self.drama = Child(), Child()
        self.engine = VoiceEngine(self.settings, self.voices, speech=self.speech, drama=self.drama)
        self.addCleanup(self.engine.stop)

    def test_reference_is_required_before_any_model_starts(self):
        with self.assertRaises(EngineError):
            self.engine.start()
        self.assertEqual(self.speech.started + self.drama.started, 0)

    def test_ui_environment_launches_workspace_speech_python(self):
        relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        executable = self.settings.root / ".venv-speech" / relative
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        engine = VoiceEngine(self.settings, self.voices, drama=self.drama)
        self.assertEqual(engine.speech.build_command()[0], str(executable))

    def test_drama_uses_native_pcm_rate_and_selected_voice_then_qwen_reuses_reference(self):
        self.voices.select("drama3", self.voice["id"])
        self.assertTrue(self.engine.start(wait=True)["ready"])
        profile = json.loads(self.speech.config_path.read_text())
        self.assertEqual(self.speech.config_path.suffix, ".yaml")
        settings = profile["blocks"]["tts"]["settings"]
        self.assertEqual(settings["openai_tts_sample_rate"], 44100)
        self.assertEqual(settings["openai_tts_voice"], self.voice["id"])
        self.assertFalse(settings["openai_tts_stream"])
        self.assertEqual(self.speech.extra_env["VOICE_TTS_BACKEND"], "drama3")
        self.assertEqual(self.drama.prepared[0][2], "你好，hello")
        self.assertTrue(self.drama.reference_prepared)
        self.engine.stop()
        self.assertFalse(self.drama.ready)
        self.voices.select("qwen3", self.voice["id"])
        self.assertTrue(self.engine.start(wait=True)["ready"])
        profile = json.loads(self.speech.config_path.read_text())
        self.assertEqual(profile["blocks"]["tts"]["backend"], "qwen3")
        self.assertIsNone(profile["blocks"]["tts"]["settings"]["qwen3_tts_speaker"])
        self.assertEqual(self.speech.extra_env["VOICE_REFERENCE_AUDIO"], str(self.voices.audio_path(self.voice["id"])))
        self.assertEqual(self.drama.started, 1)

    def test_stop_during_native_warmup_prevents_late_speech_start(self):
        entered = threading.Event()
        class LoadingChild(Child):
            def start(self, **kwargs):
                self.started += 1
                entered.set()
            def status(self, **kwargs):
                return {"state": "starting", "ready": False, "running": bool(self.started and not self.stopped)}
        self.engine.drama = LoadingChild()
        self.voices.select("drama3", self.voice["id"])
        self.engine.start()
        self.assertTrue(entered.wait(2))
        self.engine.stop()
        self.engine._thread.join(2)
        self.assertFalse(self.engine._thread.is_alive())
        self.assertEqual(self.speech.started, 0)
        self.assertEqual(self.engine.status()["state"], "stopped")

    def test_failed_native_start_never_starts_qwen_or_cloud_worker(self):
        class BrokenChild(Child):
            def start(self, **kwargs):
                raise EngineError("fixture native failure")
        self.engine.drama = BrokenChild()
        self.voices.select("drama3", self.voice["id"])
        with self.assertRaisesRegex(EngineError, "fixture native failure"):
            self.engine.start(wait=True)
        self.assertEqual(self.speech.started, 0)
        self.assertEqual(self.engine.status()["state"], "failed")

    def test_reference_preparation_failure_does_not_start_cloud_worker(self):
        class BrokenReference(Child):
            def prepare_reference(self):
                raise EngineError("reference preparation failed")
        self.engine.drama = BrokenReference()
        self.voices.select("drama3", self.voice["id"])
        with self.assertRaisesRegex(EngineError, "reference preparation failed"):
            self.engine.start(wait=True)
        self.assertEqual(self.speech.started, 0)
        self.assertFalse(self.engine.drama.ready)

    def test_stop_during_reference_preparation_prevents_speech_start(self):
        entered, release = threading.Event(), threading.Event()
        class PreparingReference(Child):
            def prepare_reference(self):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("preparation was not released")
            def stop(self):
                super().stop()
                release.set()
        self.engine.drama = PreparingReference()
        self.voices.select("drama3", self.voice["id"])
        self.engine.start()
        self.assertTrue(entered.wait(2))
        self.engine.stop()
        self.engine._thread.join(2)
        self.assertFalse(self.engine._thread.is_alive())
        self.assertEqual(self.speech.started, 0)
        self.assertEqual(self.engine.status()["state"], "stopped")

    def test_failure_cleanup_cannot_kill_a_new_generation_after_stop(self):
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        class BlockingCleanup(Child):
            fail_once = True
            cleanup_once = True
            def start(self, **kwargs):
                if self.fail_once:
                    self.fail_once = False
                    raise EngineError("first start fails")
                super().start(**kwargs)
            def stop(self):
                if self.cleanup_once:
                    self.cleanup_once = False
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("test did not release failed startup cleanup")
                super().stop()
        self.engine.speech = BlockingCleanup()
        self.voices.select("drama3", self.voice["id"])
        self.engine.start()
        old_start = self.engine._thread
        self.assertTrue(entered.wait(2))
        def stop():
            self.engine.stop()
            stopped.set()
        stopper = threading.Thread(target=stop)
        stopper.start()
        try:
            # Stop must not publish a reusable state while the old cleanup
            # still has permission to terminate either child process.
            self.assertFalse(stopped.wait(.1))
            with self.assertRaises(EngineError):
                self.engine.start()
        finally:
            release.set()
            stopper.join(2)
            old_start.join(2)
        self.assertTrue(stopped.is_set())
        self.assertFalse(old_start.is_alive())
        self.assertTrue(self.engine.start(wait=True)["ready"])
        self.assertTrue(self.engine.speech.ready)
        self.assertTrue(self.drama.ready)

    def test_provider_and_language_policy_survive_both_backend_profiles(self):
        original = yaml.safe_load(self.settings.profile_path.read_text())
        for backend in ("drama3", "qwen3"):
            result = profile_for_backend(original, backend, voice_id=self.voice["id"], drama_port=9002)
            self.assertEqual(result["blocks"]["llm"], original["blocks"]["llm"])
            self.assertEqual(result["blocks"]["stt"], original["blocks"]["stt"])
            self.assertEqual(result["pipelines"], original["pipelines"])
            self.assertIn("简体中文", result["blocks"]["llm"]["settings"]["init_chat_prompt"])
        self.assertEqual(original["blocks"]["tts"]["backend"], "qwen3")


if __name__ == "__main__":
    unittest.main()
