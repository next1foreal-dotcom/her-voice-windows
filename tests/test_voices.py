import asyncio
from array import array
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from her_voice.app import RUNTIME, create_app
from her_voice.settings import Settings
from her_voice.voices import VoiceError, VoiceStore, normalize_wav


def recording(*, rate=16000, seconds=1, channels=1, silent=False):
    data = io.BytesIO()
    samples = array("h", ((0 if silent else (index % 100 - 50) * 100) for index in range(rate * seconds * channels)))
    with wave.open(data, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())
    return data.getvalue()


class VoiceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = VoiceStore(self.root)

    def test_import_normalizes_stereo_and_persists_reference_across_restart(self):
        voice = self.store.add(name="我的声音", transcript="你好，hello world。", audio=recording(channels=2))
        self.assertEqual(voice["channels"], 1)
        with wave.open(str(self.store.audio_path(voice["id"])), "rb") as saved:
            self.assertEqual(saved.getnchannels(), 1)
            self.assertEqual(saved.getnframes(), 16000)
        self.store.select("drama3", voice["id"])
        restarted = VoiceStore(self.root)
        self.assertEqual(restarted.selection(), {"backend": "drama3", "voice_id": voice["id"]})
        self.assertEqual(restarted.list(), [voice])
        restarted.select("qwen3", voice["id"])
        self.assertEqual(restarted.get(voice["id"])["transcript"], "你好，hello world。")

    def test_invalid_or_truncated_audio_does_not_create_a_voice(self):
        for data in (b"not WAV", recording()[:-30], recording(silent=True), recording(seconds=31)):
            with self.subTest(size=len(data)), self.assertRaises(VoiceError):
                self.store.add(name="test", transcript="test", audio=data)
        self.assertEqual(self.store.list(), [])

    def test_missing_transcript_or_voice_and_path_traversal_are_rejected(self):
        with self.assertRaises(VoiceError):
            self.store.add(name="name", transcript=" ", audio=recording())
        for voice_id in (None, "../../outside", "../voice.wav", "f" * 32):
            with self.subTest(voice_id=voice_id), self.assertRaises(VoiceError):
                self.store.select("drama3", voice_id)
        self.assertFalse(self.store.selection_path.exists())

    def test_failed_selection_write_preserves_previous_choice(self):
        voice = self.store.add(name="name", transcript="test", audio=recording())
        self.store.select("drama3", voice["id"])
        with patch("her_voice.voices.os.replace", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                self.store.select("qwen3", voice["id"])
        self.assertEqual(self.store.selection()["backend"], "drama3")

    def test_corrupt_saved_selection_is_repairable_and_not_silently_used(self):
        voice = self.store.add(name="name", transcript="test", audio=recording())
        self.store.select("drama3", voice["id"])
        self.store.selection_path.write_text("{}")
        public = self.store.public()
        self.assertFalse(public["reference_ready"])
        self.assertIsNotNone(public["error"])
        self.store.select("qwen3", voice["id"])
        self.assertTrue(self.store.public()["reference_ready"])


class FakeEngine:
    def __init__(self):
        self.stops = 0
        self.starts = 0
    def status(self):
        return {"state": "stopped", "ready": False, "log_tail": []}
    def start(self):
        self.starts += 1
        return self.status()
    def stop(self):
        self.stops += 1
        return self.status()


class VoiceAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.settings = Settings(root=Path(self.temporary.name), demo_only=True, browser_token="voice-test-token")
        self.engine = FakeEngine()
        self.app = create_app(self.settings, engine=self.engine)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.headers = {"X-Voice-Token": self.settings.browser_token}

    async def asyncTearDown(self):
        await self.client.close()
        self.temporary.cleanup()

    def form(self, *, audio=None, transcript="你好，hello."):
        form = aiohttp.FormData()
        form.add_field("name", "我的测试声音")
        form.add_field("transcript", transcript)
        form.add_field("audio", recording() if audio is None else audio,
                       filename="../../untrusted.wav", content_type="audio/wav")
        return form

    async def upload(self):
        response = await self.client.post("/api/voices", data=self.form(), headers=self.headers)
        self.assertEqual(response.status, 201, await response.text())
        return (await response.json())["voice"]

    async def test_upload_list_switch_and_no_automatic_inference(self):
        response = await self.client.get("/api/tts")
        self.assertEqual((await response.json())["backend"], "drama3")
        voice = await self.upload()
        response = await self.client.get("/api/voices")
        self.assertEqual((await response.json())["voices"], [voice])
        for backend in ("drama3", "qwen3"):
            response = await self.client.put("/api/tts", json={"backend": backend, "voice_id": voice["id"]}, headers=self.headers)
            self.assertEqual(response.status, 200, await response.text())
            self.assertEqual((await response.json())["backend"], backend)
        self.assertEqual(self.engine.stops, 2)
        self.assertEqual(self.engine.starts, 0)
        self.assertFalse((Path(self.temporary.name).parent / "untrusted.wav").exists())

    async def test_identical_selection_keeps_engine_and_live_socket(self):
        voice = await self.upload()
        self.app[RUNTIME].voices.select('drama3', voice['id'])
        socket = await self.client.ws_connect(f'/ws/realtime?mode=demo&token={self.settings.browser_token}')
        await socket.receive_json()
        response = await self.client.put('/api/tts', headers=self.headers,
            json={'backend': 'drama3', 'voice_id': voice['id']})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.engine.stops, 0)
        self.assertTrue((await response.json())['unchanged'])
        self.assertFalse(socket.closed)
        await socket.close()

    async def test_mutations_need_browser_token_and_reject_unregistered_voice(self):
        response = await self.client.post("/api/voices", data=self.form())
        self.assertEqual(response.status, 403)
        for backend, voice_id in (("drama3", "f" * 32), ("external", "test"), ({}, None)):
            response = await self.client.put("/api/tts", json={"backend": backend, "voice_id": voice_id}, headers=self.headers)
            self.assertEqual(response.status, 400)
        self.assertEqual(self.engine.stops, 0)

    async def test_invalid_upload_is_visible_and_does_not_replace_selected_voice(self):
        voice = await self.upload()
        await self.client.put("/api/tts", json={"backend": "drama3", "voice_id": voice["id"]}, headers=self.headers)
        response = await self.client.post("/api/voices", data=self.form(audio=b"MP3"), headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertIn("error", await response.json())
        response = await self.client.get("/api/tts")
        self.assertEqual((await response.json())["voice_id"], voice["id"])

    async def test_applying_voice_closes_existing_socket_and_preserves_saved_recording(self):
        voice = await self.upload()
        socket = await self.client.ws_connect(f"/ws/realtime?mode=demo&token={self.settings.browser_token}")
        await socket.receive_json()
        response = await self.client.put("/api/tts", json={"backend": "qwen3", "voice_id": voice["id"]}, headers=self.headers)
        self.assertEqual(response.status, 200)
        async with asyncio.timeout(2):
            while not socket.closed:
                await socket.receive()
        self.assertTrue(self.app[RUNTIME].voices.audio_path(voice["id"]).is_file())
        self.assertEqual(self.engine.starts, 0)


if __name__ == "__main__":
    unittest.main()
