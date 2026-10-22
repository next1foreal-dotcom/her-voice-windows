"""Expose the pinned engine's existing first-token measurement, without new calls."""
from functools import wraps
import hashlib
from importlib import import_module
import json
import math
from pathlib import Path

SOURCE_SHA256 = "8d599a2df2b4e99ba0b213af2b7eecb6180f51b65d93009da7e9099bb1d7aef0"


def with_first_token(original):
    @wraps(original)
    def metadata(self, **kwargs):
        result = original(self, **kwargs)
        seconds = self.llm_ttft_s
        if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
            extended = {**result, "llm_ttft_s": round(seconds, 9)}
            # Preserve upstream's terminal metadata rather than overflowing its cap.
            if len(json.dumps(extended, separators=(",", ":"), sort_keys=True)) <= 512:
                return extended
        return result
    return metadata


def install_latency_metadata() -> bool:
    module = import_module("speech_to_speech.pipeline.turn_latency")
    source = Path(module.__file__).read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Latency metadata refused unknown upstream source; no file was modified.")
    if getattr(module, "_her_latency_metadata", False):
        return False
    tracker = module.TurnLatencyTracker
    tracker.metadata_payload = with_first_token(tracker.metadata_payload)
    module._her_latency_metadata = True
    return True
