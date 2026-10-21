"""Drama-only streaming resampling compatibility hook for the pinned engine.

The inspected upstream HTTP/cancellation implementation is retained.  Its
insert-zeros FIR converter is prohibitively expensive for the 160/441 ratio;
only that in-memory resampler class is replaced by libsoxr's stateful converter.
The upstream source file is never edited, and unknown source is rejected.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
import os
from pathlib import Path
from typing import Any


UPSTREAM_HANDLER_SHA256 = "8723e77e9ba7ebe6e0ae9a3b746cdf87b1faefeeb0c7114aed24986c98c3d3a8"
UPSTREAM_HANDLER_MODULE = "speech_to_speech.TTS.openai_compatible_handler"
SOXR_VERSION = "1.0.0"


class StreamingSoxrResampler:
    """Drop-in PCM16-unit resampler; each response receives independent state."""

    def __init__(self, source_rate: int, target_rate: int) -> None:
        if type(source_rate) is not int or type(target_rate) is not int or source_rate <= 0 or target_rate <= 0:
            raise ValueError("sample rates must be positive integers")
        import numpy as np
        import soxr

        if soxr.__version__ != SOXR_VERSION:
            raise RuntimeError("Drama streaming requires the pinned soxr==1.0.0 package; rerun the Windows installer.")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._np = np
        self._finalized = False
        self._stream = soxr.ResampleStream(source_rate, target_rate, 1, dtype="float32", quality="HQ")

    def push(self, samples: Any, *, final: bool = False):
        if self._finalized:
            raise RuntimeError("resampler has already been finalized")
        np = self._np
        incoming = np.ascontiguousarray(np.asarray(samples, dtype=np.float32).reshape(-1))
        if incoming.size and not np.isfinite(incoming).all():
            raise ValueError("audio samples must be finite")
        result = self._stream.resample_chunk(incoming, last=final)
        if final:
            self._finalized = True
        return np.clip(np.rint(result), -32768, 32767).astype(np.int16)


def install_drama_resampler(*, backend: str | None = None) -> bool:
    """Install only in a Drama worker after validating the exact upstream file.

    Worker processes are restarted when the selected backend changes.  Calling
    this for Qwen or an unspecified backend imports no inference dependencies
    and leaves upstream untouched.
    """
    selected = backend if backend is not None else os.environ.get("VOICE_TTS_BACKEND", "")
    if selected != "drama3":
        return False
    module = import_module(UPSTREAM_HANDLER_MODULE)
    try:
        path = Path(module.__file__)
        # Git for Windows can check out the pinned Python file with CRLF.
        # Accept that exact line-ending conversion, but no other source change.
        source = path.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(source).hexdigest() != UPSTREAM_HANDLER_SHA256:
            raise ValueError("source checksum differs")
        current = module._StreamingFIRResampler
        if not callable(getattr(current, "push", None)):
            raise ValueError("resampler API differs")
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError("Drama resampling hook refused unknown speech-to-speech source. Reinstall the pinned engine; no file was modified.") from exc
    # Fail before serving rather than silently selecting the slow fallback.
    StreamingSoxrResampler(44100, 16000)
    module._StreamingFIRResampler = StreamingSoxrResampler
    return True
