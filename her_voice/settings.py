"""Small, explicit configuration with no frontend access to provider secrets."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from .providers import DEEPSEEK_URL, PROVIDERS, is_loopback_host, validate_provider_url
from .resources import default_asset_root, default_workspace


ROOT = default_workspace()
_ENV_KEYS = (
    "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "VOICE_PROVIDER", "VOICE_PROVIDER_URL",
    "VOICE_PROVIDER_API_KEY", "VOICE_PROVIDER_MODEL", "VOICE_APP_ID", "VOICE_APP_NAME",
    "VOICE_HOST", "VOICE_PORT", "VOICE_ENGINE_PORT", "VOICE_PROFILE",
    "VOICE_ASSET_ROOT", "VOICE_WEB_ROOT", "VOICE_TTS_BACKEND", "VOICE_DRAMA_PORT",
)


def _key(value: str, name: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must be on one line.")
    value = value.strip()
    return "" if value in {"your_deepseek_api_key_here", "sk-your-key-here", "your_provider_api_key_here"} else value


def _model(value: str, name: str, *, required: bool = True) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must be a single model identifier.")
    value = value.strip()
    if (required and not value) or len(value) > 256:
        raise ValueError(f"{name} must be a single model identifier.")
    return value


def _port(value: int | str, name: str) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        result = int(value)
        if not 1 <= result <= 65535:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer from 1 to 65535.") from None


def _path(value: Path | str, base: Path, name: str) -> Path:
    try:
        path = Path(value)
        return (path if path.is_absolute() else base / path).resolve()
    except (TypeError, ValueError, OSError):
        raise ValueError(f"{name} is invalid.") from None


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value lines; never evaluate shell or interpolate values."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line {number}: expected KEY=value.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "a").isalnum() or key[0].isdigit():
            raise ValueError(f"Invalid .env variable name at line {number}.")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed .env quotation at line {number}.")
            value = value[1:-1]
        result[key] = value
    return result


@dataclass(frozen=True)
class Settings:
    root: Path = field(default_factory=default_workspace)
    demo_only: bool = False
    echo_engine_logs: bool = False
    provider: str = "deepseek"
    deepseek_api_key: str = field(default="", repr=False)
    deepseek_model: str = "deepseek-flash"
    provider_api_key: str = field(default="", repr=False)
    provider_model: str = ""
    provider_url: str | None = field(default=None, repr=False)
    app_id: str = "her-voice"
    app_name: str = "Open OS1"
    host: str = "127.0.0.1"
    port: int = 8790
    engine_port: int = 8791
    drama_port: int = 8792
    tts_backend: str = "drama3"
    engine_url: str | None = field(default=None, repr=False)
    profile_path: Path | None = field(default=None, repr=False)
    asset_root: Path | None = field(default=None, repr=False)
    web_root: Path | None = field(default=None, repr=False)
    gateway_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    browser_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or self.provider not in PROVIDERS:
            raise ValueError("VOICE_PROVIDER must be deepseek or openai-compatible.")
        if self.host != "127.0.0.1":
            raise ValueError("VOICE_HOST must be 127.0.0.1.")
        if not isinstance(self.app_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.app_id):
            raise ValueError("VOICE_APP_ID must be a short application identifier.")
        if not isinstance(self.app_name, str) or not self.app_name.strip() or len(self.app_name) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in self.app_name):
            raise ValueError("VOICE_APP_NAME must be a short, single-line display name.")
        root = _path(self.root, Path.cwd(), "workspace")
        assets = _path(self.asset_root if self.asset_root is not None else default_asset_root(), root, "VOICE_ASSET_ROOT")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "asset_root", assets)
        object.__setattr__(self, "web_root", _path(self.web_root if self.web_root is not None else assets / "web", root, "VOICE_WEB_ROOT"))
        object.__setattr__(self, "profile_path", _path(self.profile_path if self.profile_path is not None else assets / "config" / "windows-4080.yaml", root, "VOICE_PROFILE"))
        object.__setattr__(self, "port", _port(self.port, "VOICE_PORT"))
        object.__setattr__(self, "engine_port", _port(self.engine_port, "VOICE_ENGINE_PORT"))
        object.__setattr__(self, "drama_port", _port(self.drama_port, "VOICE_DRAMA_PORT"))
        if self.tts_backend not in {"drama3", "qwen3"}:
            raise ValueError("VOICE_TTS_BACKEND must be drama3 or qwen3.")
        object.__setattr__(self, "deepseek_api_key", _key(self.deepseek_api_key, "DEEPSEEK_API_KEY"))
        object.__setattr__(self, "provider_api_key", _key(self.provider_api_key, "VOICE_PROVIDER_API_KEY"))
        object.__setattr__(self, "deepseek_model", _model(self.deepseek_model, "DEEPSEEK_MODEL"))
        object.__setattr__(self, "provider_model", _model(self.provider_model, "VOICE_PROVIDER_MODEL", required=self.provider == "openai-compatible"))
        endpoint = self.provider_url
        if endpoint is None:
            if self.provider != "deepseek":
                raise ValueError("VOICE_PROVIDER_URL is required for openai-compatible.")
            endpoint = DEEPSEEK_URL
        validate_provider_url(endpoint)
        object.__setattr__(self, "provider_url", endpoint)
        engine_url = self.engine_url
        if engine_url is None:
            engine_url = f"ws://127.0.0.1:{self.engine_port}/v1/realtime"
        if not isinstance(engine_url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in engine_url) or any(char in engine_url for char in "\\?#"):
            raise ValueError("engine_url must be a loopback WebSocket endpoint.")
        try:
            parsed = urlsplit(engine_url)
            parsed_port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "wss" else 80)
        except ValueError:
            raise ValueError("engine_url must be a loopback WebSocket endpoint.") from None
        if parsed.scheme not in {"ws", "wss"} or not is_loopback_host(parsed.hostname) or parsed.username is not None or parsed.password is not None:
            raise ValueError("engine_url must be a loopback WebSocket endpoint.")
        object.__setattr__(self, "engine_url", engine_url)
        object.__setattr__(self, "engine_port", _port(parsed_port, "VOICE_ENGINE_PORT"))
        if self.port == self.engine_port:
            raise ValueError("VOICE_PORT and VOICE_ENGINE_PORT must differ.")
        if self.drama_port in {self.port, self.engine_port}:
            raise ValueError("VOICE_DRAMA_PORT must differ from the web and speech-engine ports.")

    @property
    def api_key(self) -> str:
        if self.provider == "openai-compatible":
            return self.provider_api_key
        return self.provider_api_key or self.deepseek_api_key

    @property
    def model(self) -> str:
        if self.provider == "openai-compatible":
            return self.provider_model
        return self.provider_model or self.deepseek_model

    @property
    def provider_ready(self) -> bool:
        return bool(self.api_key) or (self.provider == "openai-compatible" and validate_provider_url(self.provider_url))

    @classmethod
    def load(cls, *, root: Path | None = None, demo_only: bool = False) -> "Settings":
        root = _path(root if root is not None else default_workspace(), Path.cwd(), "workspace")
        values = read_env(root / ".env")
        values.update({key: os.environ[key] for key in _ENV_KEYS if key in os.environ})
        # An explicit save in the local UI supersedes inherited launcher keys.
        from .provider_settings import saved_environment
        values.update(saved_environment(root))
        return cls(
            root=root, demo_only=demo_only,
            provider=values.get("VOICE_PROVIDER", "deepseek").strip(),
            deepseek_api_key=values.get("DEEPSEEK_API_KEY", ""),
            deepseek_model=values.get("DEEPSEEK_MODEL", "").strip() or "deepseek-flash",
            provider_api_key=values.get("VOICE_PROVIDER_API_KEY", ""),
            provider_model=values.get("VOICE_PROVIDER_MODEL", ""),
            provider_url=values.get("VOICE_PROVIDER_URL") or None,
            app_id=values.get("VOICE_APP_ID", "her-voice"),
            app_name=values.get("VOICE_APP_NAME", "Open OS1"),
            host=values.get("VOICE_HOST", "127.0.0.1"),
            port=_port(values.get("VOICE_PORT", 8790), "VOICE_PORT"),
            engine_port=_port(values.get("VOICE_ENGINE_PORT", 8791), "VOICE_ENGINE_PORT"),
            drama_port=_port(values.get("VOICE_DRAMA_PORT", 8792), "VOICE_DRAMA_PORT"),
            tts_backend=values.get("VOICE_TTS_BACKEND", "drama3"),
            profile_path=values.get("VOICE_PROFILE") or None,
            asset_root=values.get("VOICE_ASSET_ROOT") or None,
            web_root=values.get("VOICE_WEB_ROOT") or None,
        )

    def public(self) -> dict:
        return {
            "demo_only": self.demo_only,
            "provider": self.provider,
            "provider_ready": self.provider_ready,
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "app_id": self.app_id,
            "app_name": self.app_name,
            "profile": "RTX 4080 SUPER · Whisper small · Drama 3 / Qwen3-TTS Base",
            "upstream": "speech-to-speech 1.0.0",
        }
