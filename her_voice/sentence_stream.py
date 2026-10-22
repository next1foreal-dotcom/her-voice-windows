"""CJK sentence boundaries and first-segment timing for the pinned speech worker.

Worker-local bindings preserve upstream Markdown,
cancellation, history and text-only paths, and its source files stay untouched.
"""
from __future__ import annotations

import hashlib
import logging
from time import perf_counter_ns
from importlib import import_module
from pathlib import Path
import re
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps

MODULE = "speech_to_speech.LLM.base_openai_compatible_language_model"
SOURCE_SHA256 = "238c8cfaa2a15de75c1ecc6f4eb63e9b103216eed6be2f35538d1d0d41882609"
UTILS_SHA256 = "23159aa9539974feb02261c1decefa929b43d3f0294ca0f97347af08cfea4d11"
_BOUNDARY = re.compile(r'[。！？]+["\')）]*')


# The generator wrapper binds only while advancing upstream, not while yielding.
# Independent turns and consumers never share first-clause state.
_TURN = ContextVar("her_first_clause_turn", default=None)
_LOG = logging.getLogger(__name__)


def _log_first_segment(handler, turn, chunk, started_ns: int, first_text_ns: int | None) -> None:
    store = getattr(handler, "turn_latency_store", None)
    tracker = store.get_response(turn.response_key) if store else None
    now_ns = perf_counter_ns()
    # No text, audio, or provider credentials are retained in timing logs.
    _LOG.info(
        "HER_FIRST_SEGMENT response_key=%s turn=%s rev=%s at_ns=%d consume_ms=%.3f first_text_observed_ns=%s text_to_segment_ms=%s provider_ttft_s=%s chars=%d",
        getattr(turn, "response_key", None), turn.turn_id, turn.turn_revision,
        now_ns, (now_ns - started_ns) / 1e6,
        first_text_ns, None if first_text_ns is None else (now_ns - first_text_ns) / 1e6,
        getattr(tracker, "llm_ttft_s", None), len(chunk.text),
    )


def _first_clause(text: str) -> int | None:
    if _BOUNDARY.search(text) or "`" in text or "http://" in text or "https://" in text:
        return None
    closers = {"（": "）", "(": ")", "[": "]", "【": "】", "“": "”", "「": "」", "『": "』", '"': '"'}
    stack: list[str] = []
    for index, char in enumerate(text):
        if stack and char == stack[-1]:
            stack.pop()
        elif char in closers:
            stack.append(closers[char])
        elif char in "，；" and not stack:
            prefix = text[:index + 1]
            if len(prefix.strip()) >= 24 and len(re.findall(r"[\u3400-\u9fff]", prefix)) >= 12:
                return index + 1
    return None


def first_clause_stream(consume, text_delta_type=None):
    """Keep upstream cancellation/history/Markdown handling; scope one early split."""
    @wraps(consume)
    def stream(self, events, state, turn):
        started_ns = perf_counter_ns()
        first_text_ns = None
        first_text_logged = False

        def observed_events():
            nonlocal first_text_ns
            for event in events:
                if first_text_ns is None and isinstance(event, text_delta_type) and event.text.strip():
                    first_text_ns = perf_counter_ns()
                yield event

        # This records delivery into the consumer, not raw network/SSE arrival.
        observed = observed_events() if text_delta_type is not None and turn.wants_audio else events
        original = consume(self, observed, state, turn)
        try:
            while True:
                token = _TURN.set((state, turn))
                try:
                    chunk = next(original)
                except StopIteration as finished:
                    return finished.value
                finally:
                    _TURN.reset(token)
                if turn.wants_audio and not first_text_logged and getattr(chunk, "text", "").strip():
                    _log_first_segment(self, turn, chunk, started_ns, first_text_ns)
                    first_text_logged = True
                yield chunk
        finally:
            original.close()
    return stream

def sentence_tokenizer(original: Callable[[str], list[str]]) -> Callable[[str], list[str]]:
    """Keep English segmentation; flush complete CJK sentences immediately.

    The trailing empty entry is the upstream consumer's unspoken pending tail.
    Without it a sentence ending at the current delta waits for the next delta.
    This binding is called inside upstream's existing Markdown protection.
    """
    def tokenize(text: str) -> list[str]:
        active = _TURN.get()
        if active is not None and active[1].wants_audio and not active[0].output_emitted:
            end = _first_clause(text)
            if end is not None:
                return [text[:end], *original(text[end:])] if text[end:].strip() else [text[:end], ""]
        matches = list(_BOUNDARY.finditer(text))
        if not matches:
            return original(text)
        result: list[str] = []
        start = 0
        for match in matches:
            result.extend(original(text[start:match.end()]))
            start = match.end()
        tail = text[start:]
        result.extend(original(tail) if tail.strip() else [""])
        return result
    return tokenize


def install_sentence_streaming() -> bool:
    module = import_module(MODULE)
    utils = import_module("speech_to_speech.LLM.utils")
    for item, expected in ((module, SOURCE_SHA256), (utils, UTILS_SHA256)):
        source = Path(item.__file__).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(source).hexdigest() != expected:
            raise RuntimeError("Sentence streaming refused unknown upstream source; no file was modified.")
    if getattr(module, "_her_sentence_streaming", False):
        return False
    module.sent_tokenize = sentence_tokenizer(module.sent_tokenize)
    handler = module.BaseOpenAICompatibleHandler
    handler._consume_streaming = first_clause_stream(handler._consume_streaming, module.TextDelta)
    module._her_sentence_streaming = True
    return True