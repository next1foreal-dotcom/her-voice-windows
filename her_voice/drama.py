"""Owned audio.cpp process for the pinned, offloaded Drama 3 voice backend.

The speech-to-speech ``openai`` backend supplies the cancellable HTTP reader and
stateful 44.1 kHz -> 16 kHz resampler.  This module manages only the native server,
its verified installation receipt, and its selected reference-voice preset.
No inference package is imported and no model is downloaded by this module.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import urllib.error
import urllib.request
import wave

from .engine import EngineError, EngineSupervisor, _SECRET_ENV


AUDIOCPP_REVISION = "0bf4d155f2af5f85853415170622dc145e3d62c3"
MODEL_REVISION = "17a5b40c5dc2c2be986f5371a7e650108b828e29"
MODEL_SHA256 = "05af716baf78e7c20863b9ccebb3ebdeca9607dd3ea7d0b72c35079e675b212b"
MODEL_SIZE_BYTES = 24596497888
MODEL_ID = "drama-3"
SOURCE_SAMPLE_RATE = 44100
DEFAULT_PORT = 8792
_VOICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")


def _check_voice_id(voice_id: str) -> str:
    if not isinstance(voice_id, str) or not _VOICE_ID.fullmatch(voice_id):
        raise EngineError("Select a valid imported voice before starting Drama 3.")
    return voice_id


def build_profile_settings(voice_id: str, *, port: int = DEFAULT_PORT) -> dict[str, Any]:
    """Settings for the pinned upstream ``backend: openai`` block.

    ``stream=False`` disables the unrelated vLLM extension.  The upstream PCM
    path still sends ``stream_format=audio`` and reads chunked HTTP audio.
    audio.cpp does not resample its raw PCM, hence the explicit 44100 rate.
    """
    _check_voice_id(voice_id)
    if type(port) is not int or not 1 <= port <= 65535:
        raise EngineError("Drama server port must be an integer in 1..65535.")
    return {
        "openai_tts_base_url": f"http://127.0.0.1:{port}/v1",
        "openai_tts_api_key": "",
        "openai_tts_model": MODEL_ID,
        "openai_tts_voice": voice_id,
        "openai_tts_language": None,
        "openai_tts_response_format": "pcm",
        "openai_tts_sample_rate": SOURCE_SAMPLE_RATE,
        "openai_tts_stream": False,
        "openai_tts_speed": 1.0,
        "openai_tts_timeout": 300.0,
        "openai_tts_blocksize": 512,
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > 262144:
            raise ValueError("oversized configuration")
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError("expected object")
        return result
    except (OSError, ValueError, UnicodeError) as exc:
        raise EngineError("Drama installation is missing or invalid. Run scripts/install-drama.ps1 first.") from exc


def _absolute_file(value: Any, *, label: str) -> Path:
    try:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("invalid path")
        path = Path(value)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("missing absolute file")
        return path.resolve()
    except (OSError, ValueError) as exc:
        raise EngineError(f"Drama {label} is missing. Run scripts/install-drama.ps1 again.") from exc


class DramaSupervisor(EngineSupervisor):
    """Start/stop only the native process this instance creates.

    ``prepare_voice`` must precede ``start`` and is rejected while running.
    The installer's server.json stays unchanged; a derived session-server.json
    contains exactly the selected voice.  ``ready`` means the HTTP server and
    model registration are ready.  Upstream's TTS warm-up performs inference.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        port: int = DEFAULT_PORT,
        startup_timeout: float = 1200.0,
        stop_timeout: float = 6.0,
        poll_interval: float = 0.25,
        echo_logs: bool = False,
    ) -> None:
        root = Path(root_dir).resolve()
        self.install_dir = root / ".runtime" / "drama3"
        self.manifest_path = self.install_dir / "runtime.json"
        self._loaded_host_mb = 0.0
        self._native_executable: Path | None = None
        self._voice_id: str | None = None
        self._manifest: dict[str, Any] | None = None
        super().__init__(
            self.install_dir / "session-server.json", root,
            port=port, startup_timeout=startup_timeout,
            stop_timeout=stop_timeout, poll_interval=poll_interval,
            echo_logs=echo_logs,
        )
        self.endpoint = f"http://127.0.0.1:{port}/v1/audio/speech"
        self.pool_url = f"http://127.0.0.1:{port}/health"
        self.models_url = f"http://127.0.0.1:{port}/v1/models"
        self._detail = "Drama server is stopped; import and select a reference voice first."

    def _installation(self) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
        manifest = _read_json(self.manifest_path)
        schema = manifest.get("schema_version", manifest.get("schema"))
        if schema != 1 or any(
            manifest.get(key) != value for key, value in (
                ("runtime_revision", AUDIOCPP_REVISION),
                ("model_revision", MODEL_REVISION),
                ("model_sha256", MODEL_SHA256),
            )
        ):
            raise EngineError("Drama installation receipt does not match the pinned runtime and model. Run scripts/install-drama.ps1 again.")
        executable = _absolute_file(manifest.get("executable"), label="server executable")
        model = _absolute_file(manifest.get("model_path"), label="Q4 model")
        config = _absolute_file(manifest.get("server_config_path"), label="server configuration")
        model_stat = model.stat()
        if model_stat.st_size != MODEL_SIZE_BYTES:
            raise EngineError("Drama Q4 model is incomplete or has changed; rerun scripts/install-drama.ps1 to verify it.")
        recorded_size = manifest.get("model_size_bytes")
        if recorded_size is not None and recorded_size != model_stat.st_size:
            raise EngineError("Drama model no longer matches its installation receipt.")
        recorded_mtime = manifest.get("model_mtime_ns")
        if recorded_mtime is not None and recorded_mtime != model_stat.st_mtime_ns:
            raise EngineError("Drama model changed after SHA-256 verification; rerun scripts/install-drama.ps1.")
        with model.open("rb") as handle:
            if handle.read(4) != b"GGUF":
                raise EngineError("Drama model is not a GGUF package.")
        executable_hash = manifest.get("executable_sha256")
        if executable_hash is not None:
            if not isinstance(executable_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", executable_hash):
                raise EngineError("Drama executable verification record is invalid.")
            with executable.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual_hash != executable_hash:
                raise EngineError("Drama executable changed after preparation; rerun scripts/install-drama.ps1.")
        return manifest, _read_json(config), executable, model

    def prepare_voice(self, voice_id: str, reference_audio: str | Path, reference_text: str) -> Path:
        """Create the native selected-voice configuration without loading a model."""
        _check_voice_id(voice_id)
        if not isinstance(reference_text, str) or not reference_text.strip() or len(reference_text) > 8000 or "\x00" in reference_text:
            raise EngineError("Drama reference transcript must contain 1..8000 characters.")
        reference = _absolute_file(str(reference_audio), label="reference WAV")
        try:
            with wave.open(str(reference), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
                if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or wav.getnchannels() != 1 or not 1 <= duration <= 30:
                    raise ValueError("expected 1..30 seconds, mono PCM16")
        except (wave.Error, EOFError, OSError, ValueError, ZeroDivisionError) as exc:
            raise EngineError("Drama reference must be a 1..30 second mono PCM16 WAV. Reimport it in the voice panel.") from exc
        with self._lock:
            if self._state in {"starting", "stopping"} or (self._process is not None and self._process.poll() is None):
                raise EngineError("Stop the current voice engine before changing the Drama voice.")
            manifest, base_config, executable, model_path = self._installation()
            configured = base_config.get("models")
            if not isinstance(configured, list) or len(configured) != 1 or not isinstance(configured[0], dict):
                raise EngineError("Drama server configuration must contain exactly one model.")
            entry = configured[0]
            if entry.get("id") != MODEL_ID or entry.get("family") != "fish_drama3" or entry.get("task") != "tts" or entry.get("mode") != "streaming":
                raise EngineError("Drama server configuration must use the pinned streaming fish_drama3 entry.")
            result = copy.deepcopy(base_config)
            result.update(host="127.0.0.1", port=self.port, backend="cuda", lazy_load=False, max_loaded_models=1,
                          ui=False, ui_management=False, log_request_body=False)
            model = result["models"][0]
            model["lazy"] = False
            self._loaded_host_mb = 0.0
            model["path"] = str(model_path)
            from .drama_reference import reference_options
            try:
                cached = reference_options(reference, reference_text, voice_id)
            except ValueError as exc:
                raise EngineError(str(exc)) from exc
            preset = ({"options": cached} if cached is not None else
                      {"voice_ref": str(reference), "reference_text": reference_text.strip()})
            model["voice_presets"] = {voice_id: preset}
            model["default_voice_preset"] = voice_id
            options = model.setdefault("session_options", {})
            if not isinstance(options, dict):
                raise EngineError("Drama session_options must be an object.")
            options.update({
                "fish_drama3.disk_experts": True,
                # Keep expert weights in pinned host memory; the verified Windows
                # runtime streams them directly without a second mapped copy.
                "fish_drama3.pin_experts": True,
                "fish_drama3.expert_cache_size": 8,
                "fish_drama3.cpu_expert_layers": 0,
                "fish_drama3.codec_backend": "same",
                # A full 30-second encoder graph exceeds 16 GB alongside the
                # model and desktop. Overlapping windows retain the entire
                # selected recording while bounding convolution workspace.
                "fish_drama3.codec_encoder_chunk_samples": 65536,
                "fish_drama3.context_length": 2048,
            })
            self.install_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.install_dir,
                                             prefix="session-server-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            try:
                os.replace(temporary, self.config_path)
            finally:
                temporary.unlink(missing_ok=True)
            self._native_executable = executable
            self._voice_id = voice_id
            self._manifest = manifest
            return self.config_path

    def prepare_reference(self) -> None:
        """Encode the complete voice and warm the codec before loading ASR.

        The normal TTS warmup still must return PCM before the engine is ready.
        Stopping the owned server interrupts this request; no retry is made.
        """
        with self._lock:
            if self._state != "ready" or self._voice_id is None:
                raise EngineError("Drama server must be ready before preparing its reference.")
            config = _read_json(self.config_path)
            preset = config["models"][0]["voice_presets"][self._voice_id]
        cached = preset.get("options", {})
        if "multi_reference_cond" in cached:
            request_body = {"text": "Hello.", "options": {**cached, "encode_reference_only": True}}
        else:
            request_body = {"text": "Hello.", "voice_ref": preset["voice_ref"],
                            "options": {"reference_text": preset["reference_text"], "encode_reference_only": True}}
        payload = {"model": MODEL_ID, "request": request_body}
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/tasks/run",
            data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        try:
            with self._http.open(request, timeout=300) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if response.status != 200 or len(raw) > 2 * 1024 * 1024:
                    raise ValueError("invalid preparation response")
            result = json.loads(raw)
            artifacts = result.get("artifacts", [])
            if not isinstance(artifacts, list) or not any(
                isinstance(item, dict) and item.get("kind") == "acoustic_tokens" and item.get("payload")
                for item in artifacts
            ):
                raise ValueError("missing encoded reference")
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            raise EngineError("Drama 3 參考音色預熱失敗；請檢查本機引擎日誌與顯示記憶體。") from None

    def build_command(self) -> list[str]:
        if self._native_executable is None or self._voice_id is None:
            raise EngineError("Prepare the selected reference voice before starting Drama 3.")
        return [str(self._native_executable), "--config", str(self.config_path), "--no-ui", "--backend", "cuda", "--log", "--load-progress", "1"]

    def _child_environment(self) -> dict[str, str]:
        self.build_command()  # Check preparation before spawning a native child.
        env = os.environ.copy()
        for name in _SECRET_ENV:
            if env.get(name):
                self._redact_values.add(env[name])
            env.pop(name, None)
        # The native TTS process never receives the cloud key or web gateway token.
        env.pop("HER_VOICE_BROWSER_TOKEN", None)
        return env

    def _probe_pool(self) -> dict[str, Any] | None:
        try:
            documents = []
            for url in (self.pool_url, self.models_url):
                with self._http.open(url, timeout=0.5) as response:
                    if response.status != 200:
                        return None
                    raw = response.read(65537)
                if len(raw) > 65536:
                    return None
                documents.append(json.loads(raw))
            health, models = documents
            if not isinstance(health, dict) or health.get("status") != "ok" or health.get("backend") != "cuda":
                return None
            if not isinstance(models, dict) or not isinstance(models.get("data"), list):
                return None
            selected = [entry for entry in models["data"] if isinstance(entry, dict) and entry.get("id") == MODEL_ID]
            if len(selected) != 1:
                return None
            model = selected[0]
            if (model.get("family"), model.get("task"), model.get("mode")) != ("fish_drama3", "tts", "streaming"):
                return None
            if model.get("loaded") is not True:
                return None
            return {"http_ready": True, "model": MODEL_ID, "model_loaded": True}
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            return None

    def _redact(self, text: str) -> str:
        text = super()._redact(text)
        match = re.search(r"disk_experts\.loaded_host_mb (\d+(?:\.\d+)?)$", text)
        if match:
            self._loaded_host_mb = max(self._loaded_host_mb, min(17280.0, float(match[1])))
        return text

    def _snapshot_locked(self) -> dict[str, Any]:
        snapshot = super()._snapshot_locked()
        snapshot.pop("upstream_sha", None)
        snapshot.update(backend="drama3", audio_cpp_revision=AUDIOCPP_REVISION, source_sample_rate=SOURCE_SAMPLE_RATE,
                        voice_id=self._voice_id, prepared=self._native_executable is not None)
        if snapshot["state"] == "starting":
            snapshot["detail"] = "正在從本機載入 Drama 3 模型。"
            if self._loaded_host_mb:
                snapshot["detail"] = f"正在載入 Drama 3：專家權重已讀入 {self._loaded_host_mb / 1024:.1f} / 16.9 GiB。"
            if self._loaded_host_mb >= 17280:
                snapshot["detail"] = "專家權重已載入；正在準備主幹與語音編解碼器。"
        snapshot["loaded_expert_mib"] = self._loaded_host_mb
        if snapshot["ready"]:
            snapshot["detail"] = "Drama HTTP server and model registration are ready. TTS warm-up and Windows audio/GPU acceptance are separate checks."
        return snapshot
