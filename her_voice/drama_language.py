"""Avoid eager all-language warmup on Drama's first spoken sentence.

The pinned processor still detects assistant language and preserves routing and
history. Pure CJK uses lazy construction; short probes skip impossible detection. An explicitly
enabled/prewarmed detector is left intact. No upstream source file is edited.
"""
from __future__ import annotations

from functools import wraps
import hashlib
from importlib import import_module
import os
import logging
from time import perf_counter
from pathlib import Path

MODULE = "speech_to_speech.LLM.lm_output_processor"
SOURCE_SHA256 = "548688c16310ff9c2866e3723a6ca701b5839785e3227a00aadc02fc0f985c7d"
DETECTOR_SHA256 = "caa982e26e6fb7b95dfe210a8f948ba058b06465c93f9189be30fa39dda589ae"
logger = logging.getLogger(__name__)


def _cjk_only(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and all(
        "\u3400" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff"
        or "\uac00" <= char <= "\ud7af" for char in letters)


def lazy_language_observer(original, build_detector, warm_detector):
    @wraps(original)
    def observe(self, text):
        started = perf_counter()
        initial = self._language_detector is None
        if self._detected_assistant_language is None:
            probe = (self._assistant_language_probe + " " + text).strip()[-256:]
            # The pinned detector returns None below these exact thresholds.
            # Preserve the accumulated probe, without loading models whose
            # result is already known. The detector-source hash guards this rule.
            has_cjk = any("\u3400" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff"
                          or "\uac00" <= c <= "\ud7af" for c in probe)
            if self._language_detector is None and len(probe) < (4 if has_cjk else 20):
                self._assistant_language_probe = probe
                logger.info("HER_LANGUAGE skipped_short_probe chars=%d", len(probe))
                return None
            if self._language_detector is None and _cjk_only(probe):
                # Same detector/confidence rules. Unique scripts do not need
                # eager loading of every unrelated language model.
                self._language_detector = build_detector()
                self._her_lazy_script_detector = self._language_detector
            elif (self._language_detector is not None
                  and self._language_detector is getattr(self, "_her_lazy_script_detector", None)
                  and not _cjk_only(probe)):
                # Ambiguous/Latin text benefits from the original parallel
                # preload. Do not carry a slow lazy Latin path into later turns.
                self._language_detector = warm_detector()
                self._her_lazy_script_detector = None
        result = original(self, text)
        if initial:
            logger.info("HER_LANGUAGE first_detection_ms=%.3f route=%s",
                        (perf_counter() - started) * 1000,
                        "script_lazy" if _cjk_only(text) else "preloaded")
        return result
    return observe

def install_drama_language_path(*, backend: str | None = None) -> bool:
    selected = backend if backend is not None else os.environ.get("VOICE_TTS_BACKEND", "")
    if selected != "drama3":
        return False
    module = import_module(MODULE)
    source = Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Drama language optimization refused unknown upstream source; no file was modified.")
    detector_source = Path(module.language_detection.__file__).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(detector_source).hexdigest() != DETECTOR_SHA256:
        raise RuntimeError("Drama language optimization refused unknown detector source; no file was modified.")
    if getattr(module, "_her_lazy_language", False):
        return False
    processor = module.LMOutputProcessor
    processor._observe_assistant_language = lazy_language_observer(
        processor._observe_assistant_language, module.language_detection.build_language_detector,
        module.language_detection.warm_language_detector)
    module._her_lazy_language = True
    return True
