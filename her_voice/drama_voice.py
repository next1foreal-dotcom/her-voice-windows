"""Apply the auditioned language hint only to TTS, preserving dialogue text."""
from __future__ import annotations

from functools import wraps
import hashlib
from importlib import import_module
import os
from pathlib import Path

from .drama_reference import reference_options
from .drama_resampler import UPSTREAM_HANDLER_MODULE, UPSTREAM_HANDLER_SHA256


def chinese_hint(text: str) -> str:
    if ("[language:" not in text and any("\u3400" <= c <= "\u9fff" for c in text)
            and not any("\u3040" <= c <= "\u30ff" or "\uac00" <= c <= "\ud7af" for c in text)):
        return text + " [language: zh]"
    return text


def install_drama_voice(*, backend: str) -> bool:
    if backend != "drama3":
        return False
    raw = os.environ.get("VOICE_REFERENCE_AUDIO")
    if not raw:
        return False
    audio = Path(raw)
    voice_id = audio.stem
    if reference_options(audio, os.environ.get("VOICE_REFERENCE_TEXT", ""), voice_id) is None:
        return False
    module = import_module(UPSTREAM_HANDLER_MODULE)
    if hashlib.sha256(Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest() != UPSTREAM_HANDLER_SHA256:
        raise RuntimeError("Drama voice hook refused unknown upstream source.")
    handler = module.OpenAICompatibleTTSHandler
    if getattr(handler._request_payload, "_her_drama_voice", False):
        return True
    original = handler._request_payload

    @wraps(original)
    def payload(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        voice = value.get("voice")
        selected = voice.get("id") if isinstance(voice, dict) else voice
        if value.get("model") == "drama-3" and selected == voice_id:
            value = {**value, "input": chinese_hint(value["input"])}
        return value

    payload._her_drama_voice = True
    handler._request_payload = payload
    return True