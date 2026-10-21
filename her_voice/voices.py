"""Local reference voices and the selected TTS backend; no inference imports.

Voice IDs are generated here, never derived from upload filenames. WAV data is
decoded, checked, and rewritten as PCM16 mono before either backend sees it.
"""
from __future__ import annotations

from array import array
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sys
import threading
import uuid
import wave


MAX_AUDIO_BYTES = 12 * 1024 * 1024
BACKENDS = (
    {"id": "drama3", "label": "Drama 3 · 主用"},
    {"id": "qwen3", "label": "Qwen3-TTS Base · 备用"},
)
_VOICE_ID = re.compile(r"[a-f0-9]{32}\Z")


class VoiceError(ValueError):
    """A user-correctable local voice or selection error."""


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_wav(data: bytes) -> tuple[bytes, dict]:
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("请上传不超过 12 MiB 的 PCM16 WAV 录音。")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels, width, rate, frames = audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
            if audio.getcomptype() != "NONE" or width != 2 or channels not in (1, 2):
                raise VoiceError("录音需要是 16 位 PCM WAV，支持单声道或双声道。")
            if not 8000 <= rate <= 96000:
                raise VoiceError("WAV 采样率需要在 8000–96000 Hz 之间。")
            duration = frames / rate
            if not 1 <= duration <= 30:
                raise VoiceError("参考录音需要为 1–30 秒，建议使用干净的 5–15 秒人声。")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * channels * width:
                raise VoiceError("WAV 音频数据不完整，请重新导出录音。")
    except (wave.Error, EOFError, OSError, OverflowError) as error:
        raise VoiceError("无法读取 WAV；请导出为 16 位 PCM WAV 后重试。") from error
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if channels == 2:
        samples = array("h", ((int(samples[index]) + int(samples[index + 1])) // 2 for index in range(0, len(samples), 2)))
    if not any(samples):
        raise VoiceError("参考录音是静音，请使用包含清晰人声的录音。")
    if sys.byteorder != "little":
        samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.tobytes())
    return output.getvalue(), {"duration_seconds": round(duration, 3), "sample_rate": rate, "channels": 1}


class VoiceStore:
    def __init__(self, workspace: str | Path, *, default_backend: str = "drama3") -> None:
        self.root = Path(workspace).resolve()
        self.directory = self.root / "voices"
        self.selection_path = self.root / ".runtime" / "voice-selection.json"
        self.default_backend = default_backend
        self._lock = threading.RLock()

    def _check_directory(self) -> None:
        if self.directory.is_symlink() or not self.directory.resolve().is_relative_to(self.root):
            raise VoiceError("音色目录不能指向工作区外部。")

    @staticmethod
    def _text(value: str, name: str, maximum: int) -> str:
        if not isinstance(value, str) or any(ord(char) < 32 and char not in "\r\n\t" for char in value):
            raise VoiceError(f"{name}包含无效字符。")
        value = " ".join(value.split())
        if not value or len(value) > maximum:
            raise VoiceError(f"请填写{name}，最多 {maximum} 个字符。")
        return value

    def add(self, *, name: str, transcript: str, audio: bytes) -> dict:
        name = self._text(name, "音色名称", 80)
        transcript = self._text(transcript, "录音对应文字", 2000)
        wav, facts = normalize_wav(audio)
        with self._lock:
            self._check_directory()
            self.directory.mkdir(parents=True, exist_ok=True)
            voice_id = uuid.uuid4().hex
            audio_path = self.directory / f"{voice_id}.wav"
            metadata = {"id": voice_id, "name": name, "transcript": transcript, **facts,
                        "created_at": datetime.now(timezone.utc).isoformat()}
            try:
                with audio_path.open("xb") as output:
                    output.write(wav)
                audio_path.chmod(0o600)
                atomic_json(self.directory / f"{voice_id}.json", metadata)
            except OSError:
                audio_path.unlink(missing_ok=True)
                raise VoiceError("无法保存音色，请检查工作区权限和磁盘空间。") from None
            return metadata

    def get(self, voice_id: str) -> dict:
        if not isinstance(voice_id, str) or not _VOICE_ID.fullmatch(voice_id):
            raise VoiceError("请选择已经保存的音色。")
        self._check_directory()
        path = self.directory / f"{voice_id}.json"
        audio = self.directory / f"{voice_id}.wav"
        if path.is_symlink() or audio.is_symlink():
            raise VoiceError("音色文件不能是符号链接。")
        try:
            if path.stat().st_size > 16384 or not audio.is_file():
                raise ValueError()
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("id") != voice_id:
                raise ValueError()
            self._text(value["name"], "音色名称", 80)
            self._text(value["transcript"], "录音对应文字", 2000)
            if not isinstance(value["duration_seconds"], (int, float)) or not 1 <= value["duration_seconds"] <= 30:
                raise ValueError()
            if type(value["sample_rate"]) is not int or not 8000 <= value["sample_rate"] <= 96000:
                raise ValueError()
            return {key: value[key] for key in ("id", "name", "transcript", "duration_seconds", "sample_rate", "channels", "created_at")}
        except (OSError, ValueError, KeyError, TypeError):
            raise VoiceError("这个音色不存在或文件已损坏，请重新导入。") from None

    def audio_path(self, voice_id: str) -> Path:
        self.get(voice_id)
        return self.directory / f"{voice_id}.wav"

    def list(self) -> list[dict]:
        self._check_directory()
        result = []
        for path in sorted(self.directory.glob("*.json")):
            if not _VOICE_ID.fullmatch(path.stem):
                continue
            try:
                result.append(self.get(path.stem))
            except VoiceError:
                continue
        return sorted(result, key=lambda voice: (voice["created_at"], voice["id"]), reverse=True)

    def selection(self) -> dict:
        if not self.selection_path.exists():
            return {"backend": self.default_backend, "voice_id": None}
        try:
            if self.selection_path.stat().st_size > 4096:
                raise ValueError()
            value = json.loads(self.selection_path.read_text(encoding="utf-8"))
            self.validate_selection(value.get("backend"), value.get("voice_id"))
            return {"backend": value["backend"], "voice_id": value["voice_id"]}
        except (OSError, ValueError, AttributeError, KeyError):
            raise VoiceError("已保存的语音设置不可用，请重新选择后端和音色并点击应用。") from None

    def validate_selection(self, backend: str, voice_id: str) -> None:
        if not isinstance(backend, str) or backend not in {item["id"] for item in BACKENDS}:
            raise VoiceError("语音后端只能选择 Drama 3 或 Qwen3-TTS Base。")
        self.get(voice_id)

    def select(self, backend: str, voice_id: str) -> dict:
        with self._lock:
            self.validate_selection(backend, voice_id)
            value = {"backend": backend, "voice_id": voice_id}
            atomic_json(self.selection_path, value)
            return value

    def public(self) -> dict:
        error = None
        try:
            selection = self.selection()
        except VoiceError as failure:
            selection = {"backend": self.default_backend, "voice_id": None}
            error = str(failure)
        return {**selection, "backends": list(BACKENDS), "reference_required": True,
                "reference_ready": bool(selection["voice_id"]), "error": error,
                "reference_limits": {"max_bytes": MAX_AUDIO_BYTES, "min_seconds": 1, "max_seconds": 30,
                                     "format": "PCM16 WAV", "recommended_seconds": "5–15"}}
