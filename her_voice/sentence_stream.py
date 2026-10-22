"""CJK sentence boundaries for the pinned streaming speech worker.

Only the worker's tokenizer binding changes. Upstream retains its Markdown,
cancellation, history and text-only paths, and its source files stay untouched.
"""
from __future__ import annotations

import hashlib
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


def first_clause_stream(consume):
    """Keep upstream cancellation/history/Markdown handling; scope one early split."""
    @wraps(consume)
    def stream(self, events, state, turn):
        original = consume(self, events, state, turn)
        try:
            while True:
                token = _TURN.set((state, turn))
                try:
                    chunk = next(original)
                except StopIteration as finished:
                    return finished.value
                finally:
                    _TURN.reset(token)
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
    handler._consume_streaming = first_clause_stream(handler._consume_streaming)
    module._her_sentence_streaming = True
    return True