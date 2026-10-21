"""Explicit Chat Completions request adapters, without model/tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .settings import Settings


PROVIDERS = {"deepseek", "openai-compatible"}
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
ALLOWED_FIELDS = {
    "messages", "max_tokens", "temperature", "top_p", "stream", "stream_options",
    "tools", "tool_choice", "parallel_tool_calls", "stop", "presence_penalty",
    "frequency_penalty", "response_format", "logprobs", "top_logprobs",
}


class MissingProviderKey(ValueError):
    """The selected endpoint requires a configured server-side credential."""


def is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_provider_url(url: str) -> bool:
    """Return whether an explicit, safe-transport endpoint is loopback.

    URL contents never enter exceptions. HTTPS remote endpoints are permitted;
    their separate key requirement is enforced before making a request.
    """
    if not isinstance(url, str) or not url or any(ord(char) <= 32 or ord(char) == 127 for char in url) or any(char in url for char in "\\?#"):
        raise ValueError("VOICE_PROVIDER_URL must be an absolute endpoint without query or fragment.")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("VOICE_PROVIDER_URL is invalid.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("VOICE_PROVIDER_URL must use HTTP(S) without user information.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("VOICE_PROVIDER_URL has an invalid port.")
    local = is_loopback_host(parsed.hostname)
    if parsed.scheme == "http" and not local:
        raise ValueError("VOICE_PROVIDER_URL requires HTTPS outside loopback.")
    return local


def chat_payload(data: dict, model: str, *, provider: str) -> dict:
    """Keep the common request surface; provider-specific policy is explicit."""
    if provider not in PROVIDERS:
        raise ValueError("VOICE_PROVIDER is unsupported.")
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        raise ValueError("A messages array is required.")
    if not data["messages"] or len(data["messages"]) > 100:
        raise ValueError("messages must contain between 1 and 100 items.")
    roles = {"system", "user", "assistant", "tool"}
    if provider == "openai-compatible":
        roles.add("developer")
    if not all(isinstance(message, dict) and message.get("role") in roles for message in data["messages"]):
        raise ValueError("Invalid chat message role.")
    payload = {key: value for key, value in data.items() if key in ALLOWED_FIELDS and value is not None}
    payload["model"] = model
    payload["stream"] = bool(payload.get("stream", False))
    if not payload["stream"]:
        payload.pop("stream_options", None)
    if provider == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    # Generic mode deliberately has no DeepSeek extras or provider fallback.
    # Tool definitions/results may pass through, but this adapter executes none.
    return payload


@dataclass(frozen=True)
class ProviderRequest:
    url: str = field(repr=False)
    headers: dict[str, str] = field(repr=False)
    payload: dict = field(repr=False)


def build_provider_request(settings: "Settings", data: dict) -> ProviderRequest:
    local = validate_provider_url(settings.provider_url)
    key = settings.api_key
    if not key and (settings.provider == "deepseek" or not local):
        raise MissingProviderKey("The selected provider requires a configured API key.")
    payload = chat_payload(data, settings.model, provider=settings.provider)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if settings.provider == "openai-compatible":
        # Routing hints only: these are not authentication, a tenant boundary,
        # a conversation ID, or permission for the host to execute tools.
        headers["X-Voice-App-ID"] = settings.app_id
        headers["X-Voice-Request-Purpose"] = "conversation" if payload["stream"] else "warmup"
    return ProviderRequest(url=settings.provider_url, headers=headers, payload=payload)
