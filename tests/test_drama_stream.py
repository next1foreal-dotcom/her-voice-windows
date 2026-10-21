"""Actual pinned upstream HTTP/PCM methods against a local synthetic server.

The heavyweight inference imports are excluded with AST loading; the HTTP,
cancellation, request builder, and decoder method bodies are unchanged and the
whole source file SHA is verified.  Set VOICE_UPSTREAM_HTTP_SOURCE to an inspected
copy, or install the pinned speech-to-speech package.  No real TTS model is used.
"""
from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
from queue import Empty, Full, Queue
import tempfile
from threading import Event, Lock, Thread
from time import perf_counter
from types import ModuleType
from typing import Any, Iterator, cast
import unittest
from unittest.mock import patch
import wave

from her_voice.drama import build_profile_settings
from her_voice.drama_resampler import (
    StreamingSoxrResampler, UPSTREAM_HANDLER_SHA256, install_drama_resampler,
)


HAS_RESAMPLER = all(importlib.util.find_spec(name) for name in ("numpy", "soxr"))


@unittest.skipUnless(HAS_RESAMPLER, "Install numpy and pinned soxr for the audio DSP tests.")
class ResamplerTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np

    def signal(self, frames=44100):
        np = self.np
        t = np.arange(frames, dtype=np.float64) / 44100
        return (10000 * np.sin(2 * np.pi * 440 * t) + 2000 * np.sin(2 * np.pi * 1234 * t)).astype(np.int16)

    def test_odd_chunk_boundaries_match_continuous_resampling_and_flush_tail(self):
        np = self.np
        audio = self.signal(17643)
        whole = StreamingSoxrResampler(44100, 16000).push(audio, final=True)
        converter = StreamingSoxrResampler(44100, 16000)
        pieces = []
        offset = 0
        for width in [1, 17, 32, 3, 511, 2205, 7, 101, 8000, 4096]:
            pieces.append(converter.push(audio[offset:offset + width]))
            offset += width
        pieces.append(converter.push(audio[offset:]))
        before_flush = sum(len(part) for part in pieces)
        pieces.append(converter.push(np.empty(0), final=True))
        joined = np.concatenate(pieces)
        self.assertLess(before_flush, len(joined))
        self.assertEqual(len(joined), round(len(audio) * 16000 / 44100))
        np.testing.assert_allclose(joined, whole, rtol=0, atol=1)
        with self.assertRaisesRegex(RuntimeError, "finalized"):
            converter.push(np.empty(0), final=True)

    def test_cancelled_resampler_state_does_not_leak_into_a_new_response(self):
        np = self.np
        old = StreamingSoxrResampler(44100, 16000)
        old.push(self.signal(4410))
        # The HTTP handler creates a fresh converter for each response.
        fresh = StreamingSoxrResampler(44100, 16000)
        silence = fresh.push(np.zeros(4410, dtype=np.int16), final=True)
        self.assertEqual(np.count_nonzero(silence), 0)

    def test_downsampling_suppresses_above_nyquist_energy(self):
        np = self.np
        t = np.arange(44100) / 44100
        high = (12000 * np.sin(2 * np.pi * 12000 * t)).astype(np.int16)
        out = StreamingSoxrResampler(44100, 16000).push(high, final=True)
        self.assertLess(float(np.sqrt(np.mean(out[1000:-1000].astype(float) ** 2))), 30)

    def test_hook_is_backend_scoped_and_refuses_unknown_upstream(self):
        from her_voice.drama_warmup import install_drama_warmup
        with patch("her_voice.drama_warmup.import_module") as importer:
            self.assertFalse(install_drama_warmup(backend="qwen3"))
            importer.assert_not_called()
        with patch("her_voice.drama_resampler.import_module") as importer:
            self.assertFalse(install_drama_resampler(backend="qwen3"))
            importer.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "handler.py"
            file.write_text("# unknown upstream\n")
            fake = ModuleType("fixture")
            fake.__file__ = str(file)
            fake._StreamingFIRResampler = type("Original", (), {"push": lambda *a: None})
            with patch("her_voice.drama_resampler.import_module", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "unknown"):
                    install_drama_resampler(backend="drama3")
            self.assertEqual(fake._StreamingFIRResampler.__name__, "Original")
            with patch("her_voice.drama_warmup.import_module", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "unknown"):
                    install_drama_warmup(backend="drama3")
            self.assertFalse(hasattr(fake, "OpenAICompatibleTTSHandler"))


def load_verified_upstream():
    try:
        import httpx
        import numpy as np
        from scipy.signal import firwin, lfilter
        import soxr  # noqa: F401
    except ImportError as exc:
        raise unittest.SkipTest("HTTP/audio contract requires httpx, numpy, scipy and soxr.") from exc
    source = os.environ.get("VOICE_UPSTREAM_HTTP_SOURCE")
    if source:
        path = Path(source)
    else:
        try:
            dist = importlib.metadata.distribution("speech-to-speech")
            path = Path(dist.locate_file("speech_to_speech/TTS/openai_compatible_handler.py"))
        except importlib.metadata.PackageNotFoundError as exc:
            raise unittest.SkipTest("Install pinned speech-to-speech or set VOICE_UPSTREAM_HTTP_SOURCE for this contract.") from exc
    content = path.read_bytes()
    if hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() != UPSTREAM_HANDLER_SHA256:
        raise AssertionError("HTTP contract test refuses an unverified upstream source file.")
    parsed = ast.parse(content)
    classes = {"SpeechRequestCancelled", "SpeechRequestError", "HttpSpeechOperation",
               "_StreamingFIRResampler", "_StreamingByteReader", "OpenAICompatibleTTSHandler"}
    nodes = []
    for node in parsed.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in classes:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id.upper() == target.id for target in node.targets):
            nodes.append(node)
    class StubBase:
        @classmethod
        def __class_getitem__(cls, item):
            return cls
    module = ModuleType("verified_upstream_http_fixture")
    module.__file__ = str(path)
    module.__dict__.update(
        asyncio=asyncio, io=io, logging=logging, os=os, wave=wave, Callable=Callable,
        Empty=Empty, Full=Full, Queue=Queue, Event=Event, Lock=Lock, Thread=Thread,
        perf_counter=perf_counter, Any=Any, Iterator=Iterator, cast=cast, httpx=httpx,
        np=np, firwin=firwin, lfilter=lfilter, BaseHandler=StubBase, TTSIn=object, TTSOut=object,
        logger=logging.getLogger("drama-contract"),
    )
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), module.__dict__)
    with patch("her_voice.drama_resampler.import_module", return_value=module):
        if not install_drama_resampler(backend="drama3"):
            raise AssertionError("Drama hook was not installed")
    return module


class UpstreamDramaHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = load_verified_upstream()

    def setUp(self):
        import numpy as np
        self.np = np
        self.started = Event()
        self.release = Event()
        self.completed = Event()
        self.calls = []
        self.hold_before_audio = False
        self.media_type = "audio/pcm"
        self.status_code = 200
        self.audio = (12000 * np.sin(np.arange(17640) * 2 * np.pi * 440 / 44100)).astype("<i2").tobytes()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append(body)
                self.send_response(owner.status_code)
                self.send_header("Content-Type", owner.media_type)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                owner.started.set()
                def chunk(data):
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                try:
                    if owner.hold_before_audio:
                        owner.release.wait(4)
                    if owner.status_code != 200 or owner.media_type != "audio/pcm":
                        chunk(b'{"error":"fixture"}')
                    else:
                        chunk(owner.audio[:4093])  # Intentionally splits a PCM16 sample.
                        chunk(owner.audio[4093:17640])
                        owner.release.wait(4)
                        chunk(owner.audio[17640:])
                    self.wfile.write(b"0\r\n\r\n"); self.wfile.flush()
                except (OSError, ConnectionError):
                    pass
                finally:
                    owner.completed.set()
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        self.env.start()

    def tearDown(self):
        self.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.env.stop()

    def handler(self):
        cls = self.upstream.OpenAICompatibleTTSHandler
        handler = cls.__new__(cls)
        settings = build_profile_settings("voice_reference", port=self.server.server_port)
        for key, value in settings.items():
            setattr(handler, key.removeprefix("openai_tts_"), value)
        handler.endpoint_url = handler.base_url + "/audio/speech"
        handler.gen_kwargs = {}
        handler.task_type = None
        handler.instructions = None
        handler.timeout = 5.0
        return handler

    def test_warmup_has_short_budget_and_normal_requests_keep_their_options(self):
        from her_voice.drama_warmup import install_drama_warmup, WARMUP_MAX_FRAMES
        with patch("her_voice.drama_warmup.import_module", return_value=self.upstream):
            self.assertTrue(install_drama_warmup(backend="drama3"))
            first_hook = self.upstream.OpenAICompatibleTTSHandler.warmup
            install_drama_warmup(backend="drama3")
            self.assertIs(first_hook, self.upstream.OpenAICompatibleTTSHandler.warmup)
        handler = self.handler()
        handler.stop_event = Event()
        handler.gen_kwargs = {"max_tokens": 500, "seed": 123}
        self.release.set()
        handler.warmup()
        self.assertEqual(self.calls[0]["max_tokens"], WARMUP_MAX_FRAMES)
        self.assertEqual(self.calls[0]["input"], "Hello.")
        self.assertEqual(self.calls[0]["seed"], 0)
        self.assertFalse(handler._her_drama_warming)
        self.assertEqual(handler.gen_kwargs, {"max_tokens": 500, "seed": 123})
        normal = handler._request_payload(text="Warmup", voice=handler.voice)
        self.assertEqual(normal["input"], "Warmup")
        self.assertEqual(normal["max_tokens"], 500)
        self.assertEqual(normal["seed"], 123)

    def test_failed_warmup_restores_regular_request_budget(self):
        from her_voice.drama_warmup import install_drama_warmup
        with patch("her_voice.drama_warmup.import_module", return_value=self.upstream):
            install_drama_warmup(backend="drama3")
        handler = self.handler()
        handler.stop_event = Event()
        self.media_type = "application/json"
        self.release.set()
        with self.assertRaises(self.upstream.SpeechRequestError):
            handler.warmup()
        self.assertFalse(handler._her_drama_warming)
        self.assertNotIn("max_tokens", handler._request_payload(text="Hello", voice=handler.voice))

    def test_pinned_windows_crlf_source_is_accepted_without_modifying_it(self):
        source = Path(self.upstream.__file__).read_bytes().replace(b"\r\n", b"\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "windows_handler.py"
            windows_source = source.replace(b"\n", b"\r\n")
            path.write_bytes(windows_source)
            module = ModuleType("verified_windows_fixture")
            module.__file__ = str(path)
            module._StreamingFIRResampler = type("Original", (), {"push": lambda *a: None})
            with patch("her_voice.drama_resampler.import_module", return_value=module):
                self.assertTrue(install_drama_resampler(backend="drama3"))
            self.assertIs(module._StreamingFIRResampler, StreamingSoxrResampler)
            self.assertEqual(path.read_bytes(), windows_source)
            path.write_bytes(windows_source + b"# actual content modification\r\n")
            with patch("her_voice.drama_resampler.import_module", return_value=module):
                with self.assertRaisesRegex(RuntimeError, "unknown"):
                    install_drama_resampler(backend="drama3")

    def test_actual_upstream_streams_before_completion_and_preserves_pitch_duration(self):
        handler = self.handler()
        operation = handler._make_operation(text="你好。 Hello from Drama.", voice=handler.voice)
        byte_stream = operation.iter_bytes(lambda: False)
        chunks = handler._decode_pcm_stream(byte_stream)
        first = next(chunks)
        self.assertFalse(self.completed.is_set(), "Audio must be yielded before the server finishes the response")
        self.assertEqual(first.shape, (512,))
        self.release.set()
        result = self.np.concatenate([first, *chunks])
        self.assertTrue(self.completed.wait(1))
        self.assertEqual(self.calls[0]["stream_format"], "audio")
        self.assertEqual(self.calls[0]["response_format"], "pcm")
        self.assertNotIn("stream", self.calls[0])
        self.assertEqual(self.calls[0]["voice"], "voice_reference")
        original = self.np.frombuffer(self.audio, dtype="<i2")
        expected = StreamingSoxrResampler(44100, 16000).push(original, final=True)
        self.assertEqual(len(expected), 6400)
        self.assertEqual(len(result), 6656)  # Fixed-size upstream blocks pad the last chunk.
        self.np.testing.assert_allclose(result[:6400], expected, rtol=0, atol=1)
        self.assertEqual(self.np.count_nonzero(result[6400:]), 0)
        frequencies = self.np.fft.rfftfreq(6000, 1 / 16000)
        spectrum = abs(self.np.fft.rfft(result[200:6200]))
        self.assertLess(abs(frequencies[spectrum.argmax()] - 440), 4)

    def test_cancel_before_first_audio_closes_transport_promptly(self):
        self.hold_before_audio = True
        handler = self.handler()
        operation = handler._make_operation(text="Cancel this reply", voice=handler.voice)
        cancel = Event()
        errors = []
        received = []
        def consume():
            try:
                received.extend(operation.iter_bytes(cancel.is_set))
            except BaseException as exc:
                errors.append(exc)
        thread = Thread(target=consume)
        thread.start()
        self.assertTrue(self.started.wait(2))
        started = perf_counter()
        cancel.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertLess(perf_counter() - started, 1)
        self.assertEqual(received, [])
        self.assertTrue(any(isinstance(error, self.upstream.SpeechRequestCancelled) for error in errors))
        self.release.set()

    def test_cancel_during_stream_does_not_emit_late_bytes(self):
        handler = self.handler()
        operation = handler._make_operation(text="interrupt", voice=handler.voice)
        cancel = Event()
        stream = operation.iter_bytes(cancel.is_set)
        self.assertTrue(next(stream))
        cancel.set(); self.release.set()
        with self.assertRaises(self.upstream.SpeechRequestCancelled):
            next(stream)

    def test_error_json_is_never_decoded_as_audio(self):
        self.media_type = "application/json"
        handler = self.handler()
        operation = handler._make_operation(text="bad server", voice=handler.voice)
        self.release.set()
        with self.assertRaisesRegex(self.upstream.SpeechRequestError, "non-audio"):
            list(operation.iter_bytes(lambda: False))


if __name__ == "__main__":
    unittest.main()
