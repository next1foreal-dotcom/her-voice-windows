"""Validated, auditioned reference codes; never re-encode or truncate the WAV."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import wave


def reference_options(audio: Path, transcript: str, voice_id: str) -> dict | None:
    cache = audio.with_suffix(".drama-reference.json")
    if not cache.exists():
        return None
    try:
        if cache.stat().st_size > 262144:
            raise ValueError("oversized cache")
        value = json.loads(cache.read_text(encoding="utf-8"))
        from .drama import MODEL_SHA256
        if (value["schema"] != 1 or value["voice_id"] != voice_id
                or value["model_sha256"] != MODEL_SHA256
                or value["audio_sha256"] != hashlib.sha256(audio.read_bytes()).hexdigest()
                or value["transcript_sha256"] != hashlib.sha256(transcript.strip().encode()).hexdigest()):
            raise ValueError("reference binding changed")
        with wave.open(str(audio), "rb") as wav:
            frames = (wav.getnframes() * 44100 + wav.getframerate() * 2048 - 1) // (wav.getframerate() * 2048)
        codes = value["vq_codes"]
        if (not isinstance(codes, list) or len(codes) != 10 or not 1 <= frames <= 647
                or any(not isinstance(book, list) or len(book) != frames for book in codes)
                or any(type(code) is not int or not 0 <= code < (4096 if index == 0 else 1024)
                       for index, book in enumerate(codes) for code in book)):
            raise ValueError("incomplete or invalid reference codes")
        if value["codes_sha256"] != hashlib.sha256(json.dumps(codes, separators=(",", ":")).encode()).hexdigest():
            raise ValueError("reference code checksum changed")
        return {"multi_reference_cond": json.dumps([{"text": transcript.strip(), "vq_codes": codes}], separators=(",", ":")),
                "temperature": 1.0, "top_p": 0.9, "top_k": 30, "seed": 0}
    except (KeyError, TypeError, ValueError, OSError, wave.Error) as exc:
        raise ValueError("Saved Drama reference no longer matches the complete recording; restore its verified cache before starting.") from exc