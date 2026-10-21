"""Bound only Drama's startup probe, preserving normal synthesis and transport."""
from __future__ import annotations

from functools import wraps
import hashlib
from importlib import import_module
from pathlib import Path

from .drama_resampler import UPSTREAM_HANDLER_MODULE, UPSTREAM_HANDLER_SHA256

WARMUP_MAX_FRAMES = 8


def install_drama_warmup(*, backend: str) -> bool:
    if backend != "drama3":
        return False
    module = import_module(UPSTREAM_HANDLER_MODULE)
    source = Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(source).hexdigest() != UPSTREAM_HANDLER_SHA256:
        raise RuntimeError("Drama warmup hook refused unknown speech-to-speech source.")
    handler = module.OpenAICompatibleTTSHandler
    if getattr(handler.warmup, "_her_drama_bounded", False):
        return True
    original_warmup, original_payload = handler.warmup, handler._request_payload

    @wraps(original_payload)
    def payload(self, *args, **kwargs):
        value = original_payload(self, *args, **kwargs)
        if getattr(self, "_her_drama_warming", False):
            value = {**value, "input": "Hello.", "max_tokens": WARMUP_MAX_FRAMES, "seed": 0}
        return value

    @wraps(original_warmup)
    def warmup(self):
        self._her_drama_warming = True
        try:
            # Retain the pinned HTTP deadline, full PCM decode, nonempty-audio
            # check and completion requirement. A 200 response is not readiness.
            return original_warmup(self)
        except Exception:
            module.logger.error("HER_DRAMA_WARMUP_FAILED")
            raise
        finally:
            self._her_drama_warming = False

    warmup._her_drama_bounded = True
    handler._request_payload, handler.warmup = payload, warmup
    return True
