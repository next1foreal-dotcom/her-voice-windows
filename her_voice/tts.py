"""Select one TTS backend and own its processes for a single voice session.

Drama's HTTP server and speech-to-speech have separate lifetimes. Changing a
backend stops both owned processes before the next model is loaded. The web
process never imports torch, performs inference, or downloads model weights.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading
import time

from .engine import EngineError, EngineSupervisor
from .settings import Settings
from .voices import VoiceError, VoiceStore, atomic_json


def profile_for_backend(template: dict, backend: str, *, voice_id: str, drama_port: int) -> dict:
    """Build data accepted by the pinned upstream file-mode schema."""
    value = deepcopy(template)
    if not isinstance(value.get("blocks"), dict) or "tts" not in value["blocks"]:
        raise VoiceError("语音配置缺少 blocks.tts，无法应用所选后端。")
    if backend == "drama3":
        from .drama import build_profile_settings
        value["blocks"]["tts"] = {
            "kind": "tts", "backend": "openai",
            "settings": build_profile_settings(voice_id, port=drama_port),
        }
    elif backend == "qwen3":
        value["blocks"]["tts"] = {
            "kind": "tts", "backend": "qwen3", "settings": {
                "qwen3_tts_model_name": {"env": "HER_TTS_MODEL_PATH"},
                "qwen3_tts_backend": "torch", "qwen3_tts_device": "cuda",
                "qwen3_tts_dtype": "float16", "qwen3_tts_attn_implementation": "eager",
                "qwen3_tts_speaker": None,
                "qwen3_tts_ref_audio": {"env": "VOICE_REFERENCE_AUDIO"},
                "qwen3_tts_ref_text": {"env": "VOICE_REFERENCE_TEXT"},
                "qwen3_tts_xvec_only": False, "qwen3_tts_language": "auto",
                "qwen3_tts_non_streaming_mode": True,
                "qwen3_tts_streaming_chunk_size": 8,
            },
        }
    else:
        raise VoiceError("不支持的语音后端。")
    return value


class VoiceEngine:
    """Nonblocking startup, explicit backend selection, and owned cleanup."""
    @staticmethod
    def _speech_python(root: Path) -> str:
        # The UI may run in a lightweight environment; inference and the
        # pinned upstream verification belong to the workspace's speech venv.
        relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        executable = root / ".venv-speech" / relative
        return str(executable) if executable.is_file() else sys.executable

    def __init__(self, settings: Settings, voices: VoiceStore, *, speech=None, drama=None) -> None:
        self.settings, self.voices = settings, voices
        self._env = {
            "HER_VOICE_GATEWAY_TOKEN": settings.gateway_token,
            "DEEPSEEK_MODEL": settings.model, "VOICE_LLM_MODEL": settings.model,
            "VOICE_GATEWAY_BASE_URL": f"http://{settings.host}:{settings.port}/llm/v1",
            "VOICE_ENGINE_PORT": str(settings.engine_port), "VOICE_WORKSPACE": str(settings.root),
        }
        self.speech = speech or EngineSupervisor(
            settings.profile_path, settings.root, port=settings.engine_port,
            python_executable=self._speech_python(settings.root),
            echo_logs=settings.echo_engine_logs, env=self._env,
        )
        if drama is None:
            from .drama import DramaSupervisor
            drama = DramaSupervisor(settings.root, port=settings.drama_port,
                                    echo_logs=settings.echo_engine_logs)
        self.drama = drama
        self._lock = threading.RLock()
        # An old startup failure must finish stopping its children before a
        # user stop can publish "stopped" and allow a new generation to start.
        self._cleanup_lock = threading.Lock()
        self._state = "stopped"
        self._detail = "请导入参考音色、选择后端，然后开始对话。"
        self._epoch = 0
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_backend: str | None = None

    @property
    def port(self):
        return self.speech.port

    @property
    def endpoint(self):
        return self.speech.endpoint

    @property
    def config_path(self):
        return self.speech.config_path

    def _child_environment(self):
        return self.speech._child_environment()

    def status(self, *, probe: bool = True) -> dict:
        speech = self.speech.status(probe=probe)
        native = self.drama.status(probe=probe)
        with self._lock:
            state, detail, backend = self._state, self._detail, self._active_backend
            if state == "starting" and backend == "drama3" and native.get("state") == "starting":
                detail = native.get("detail", detail)
            if state == "ready":
                if not speech.get("ready"):
                    state, detail = speech.get("state", "unhealthy"), speech.get("detail", "语音引擎不可用。")
                elif backend == "drama3" and not native.get("ready"):
                    state, detail = "unhealthy", "Drama 3 服务不可用，请停止后重新开始。"
            return {**speech, "state": state, "detail": detail,
                    "ready": state == "ready", "running": bool(speech.get("running") or native.get("running")),
                    "tts_backend": backend, "tts_process": {key: item for key, item in native.items() if key != "log_tail"}}

    def start(self, *, wait: bool = False) -> dict:
        with self._lock:
            if self._state in {"starting", "ready"}:
                return self.status(probe=False)
            if self._state == "stopping":
                raise EngineError("语音后端正在停止，请稍后重试。")
            try:
                selected = self.voices.selection()
                self.voices.validate_selection(selected["backend"], selected["voice_id"])
                voice = self.voices.get(selected["voice_id"])
                audio = self.voices.audio_path(voice["id"])
            except VoiceError as error:
                raise EngineError(str(error)) from None
            self._epoch += 1
            epoch = self._epoch
            self._cancel = threading.Event()
            cancel = self._cancel
            self._active_backend = selected["backend"]
            self._state = "starting"
            self._detail = "正在准备所选音色与语音后端。"
            thread = threading.Thread(target=self._start, args=(epoch, cancel, selected, voice, audio),
                                      daemon=True, name="voice-backend-start")
            self._thread = thread
            thread.start()
        if wait:
            thread.join(timeout=2800)
            result = self.status()
            if not result["ready"]:
                raise EngineError(result["detail"])
            return result
        return self.status(probe=False)

    def _current(self, epoch: int, cancel: threading.Event) -> bool:
        return epoch == self._epoch and not cancel.is_set()

    def _wait(self, child, epoch: int, cancel: threading.Event) -> bool:
        deadline = time.monotonic() + 1250
        while not cancel.is_set() and time.monotonic() < deadline:
            state = child.status(probe=False)
            if state.get("ready"):
                return self._current(epoch, cancel)
            if state.get("state") in {"failed", "stopped"}:
                raise EngineError(state.get("detail", "模型进程启动失败。"))
            cancel.wait(0.1)
        if cancel.is_set():
            return False
        raise EngineError("模型启动超时，请检查本机日志、模型缓存和显存。")

    def _start(self, epoch: int, cancel: threading.Event, selected: dict, voice: dict, audio: Path) -> None:
        try:
            try:
                import yaml
            except ImportError:
                raise EngineError("缺少完整语音环境，请先运行 scripts/install-windows.ps1。") from None
            template = yaml.safe_load(self.settings.profile_path.read_text(encoding="utf-8"))
            profile = profile_for_backend(template, selected["backend"], voice_id=voice["id"],
                                          drama_port=self.settings.drama_port)
            # Upstream's loader only accepts .yaml/.yml. JSON is a YAML subset,
            # which lets the web runtime write the generated profile safely.
            path = self.settings.root / ".runtime" / "selected-voice-pipeline.yaml"
            with self._lock:
                if not self._current(epoch, cancel):
                    return
                atomic_json(path, profile)
                self.speech.config_path = path
                self.speech.extra_env = {**self._env, "VOICE_TTS_BACKEND": selected["backend"],
                    "VOICE_REFERENCE_AUDIO": str(audio), "VOICE_REFERENCE_TEXT": voice["transcript"]}
                if selected["backend"] == "drama3":
                    self._detail = "正在從本機載入 Drama 3 與所選音色。"
                    self.drama.prepare_voice(voice["id"], str(audio), voice["transcript"])
                    self.drama.start(wait=False)
            if selected["backend"] == "drama3":
                if not self._wait(self.drama, epoch, cancel):
                    return
                with self._lock:
                    if not self._current(epoch, cancel):
                        return
                    self._detail = "正在編碼完整參考音色並預熱 Drama 3 解碼器。"
                self.drama.prepare_reference()
            with self._lock:
                if not self._current(epoch, cancel):
                    return
                self._detail = "正在載入語音識別與會話引擎，並驗證首段語音。"
                self.speech.start(wait=False)
            if not self._wait(self.speech, epoch, cancel):
                return
            with self._lock:
                if self._current(epoch, cancel):
                    self._state = "ready"
                    self._detail = "所选语音后端已就绪，可以开始实时对话。"
        except Exception as error:
            with self._cleanup_lock:
                with self._lock:
                    if not self._current(epoch, cancel):
                        return
                    self._state = "stopping"
                self.speech.stop()
                self.drama.stop()
                with self._lock:
                    if self._current(epoch, cancel):
                        self._state = "failed"
                        self._detail = str(error) if isinstance(error, (EngineError, VoiceError)) else f"准备语音后端失败（{type(error).__name__}），请检查安装和模型准备步骤。"

    def stop(self) -> dict:
        with self._cleanup_lock:
            with self._lock:
                self._epoch += 1
                self._cancel.set()
                self._state = "stopping"
            # Upstream cancels its pending HTTP request before the native
            # process is stopped. Qwen releases GPU allocations on exit.
            self.speech.stop()
            self.drama.stop()
            with self._lock:
                self._state = "stopped"
                self._detail = "本应用的语音进程已停止；可以选择后端并重新开始。"
        return self.status(probe=False)
