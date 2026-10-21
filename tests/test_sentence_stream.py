"""Exercise the actual pinned stream consumer without GPU or cloud calls."""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import logging
import re
from collections.abc import Callable
from typing import Optional

from nltk import sent_tokenize
from her_voice.sentence_stream import sentence_tokenizer, install_sentence_streaming, SOURCE_SHA256, UTILS_SHA256

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".venv-speech/Lib/site-packages/speech_to_speech/LLM"


def pinned_source(name, expected):
    source = (BASE / name).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(source).hexdigest() == expected
    return source.decode("utf-8")


def consumer(tokenizer):
    # Retain the original helpers, Markdown protection, and actual streaming body.
    helpers = ast.parse(pinned_source("utils.py", UTILS_SHA256))
    helpers.body = [node for node in helpers.body if isinstance(node, (ast.FunctionDef, ast.Assign, ast.AnnAssign))]
    ns = {"re": re, "Callable": Callable, "Optional": Optional}
    exec(compile(ast.fix_missing_locations(helpers), "pinned-utils", "exec", flags=__import__('__future__').annotations.compiler_flag), ns)
    tree = ast.parse(pinned_source("base_openai_compatible_language_model.py", SOURCE_SHA256))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BaseOpenAICompatibleHandler")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_consume_streaming")
    ns.update(sent_tokenize=tokenizer, logger=logging.getLogger("sentence-test"), transcript_for_log=lambda value: value)
    for name in ("Usage", "ProviderResponseEnd", "AssistantMessage", "ResponseReasoningItem", "ToolCall", "TextDelta"):
        ns[name] = type(name, (), {"__init__": lambda self, text="": setattr(self, "text", text)})
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "pinned-consumer", "exec", flags=__import__('__future__').annotations.compiler_flag), ns)
    return ns


class Handler:
    stream_batch_sentences = 1
    cancelled = False
    def _turn_is_cancelled(self, turn): return self.cancelled
    def _turn_is_latest(self, *args): return True
    def _turn_output_allowed(self, *args): return True
    def _chunk(self, turn, *, text): return text


class SentenceStreamingTests(unittest.TestCase):
    def run_stream(self, parts, *, fixed=True, wants_audio=True, handler=None):
        ns = consumer(sentence_tokenizer(sent_tokenize) if fixed else sent_tokenize)
        state = SimpleNamespace(clean_text="", output_emitted=False, history_items=[], tools=[], ending=SimpleNamespace(status="completed"))
        turn = SimpleNamespace(turn_id="t", turn_revision=0, wants_audio=wants_audio)
        events = (ns["TextDelta"](part) for part in parts)
        return ns["_consume_streaming"](handler or Handler(), events, state, turn), state

    def test_actual_consumer_flushes_chinese_before_provider_finishes(self):
        ns = consumer(sentence_tokenizer(sent_tokenize))
        reached_tail = []
        def provider():
            yield ns["TextDelta"]("先说重点。")
            reached_tail.append(True)
            yield ns["TextDelta"]("接着补充细节。")
        state = SimpleNamespace(clean_text="", output_emitted=False, history_items=[], tools=[], ending=SimpleNamespace(status="completed"))
        turn = SimpleNamespace(turn_id="t", turn_revision=0, wants_audio=True)
        stream = ns["_consume_streaming"](Handler(), provider(), state, turn)
        self.assertEqual(next(stream), "先说重点。")
        self.assertFalse(reached_tail, "First sentence must leave before the provider completes its reply")
        self.assertEqual(list(stream), ["接着补充细节。"])
        self.assertEqual(state.clean_text, "先说重点。接着补充细节。")

    def test_original_path_reproduces_whole_reply_wait(self):
        stream, _ = self.run_stream(["先说重点。", "接着补充细节。"], fixed=False)
        self.assertEqual(list(stream), ["先说重点。接着补充细节。"])

    def test_split_deltas_and_mixed_languages_keep_complete_text(self):
        stream, state = self.run_stream(["这里用 Deep", "Seek。", "It works. ", "第二句", "！"])
        output = list(stream)
        self.assertEqual(output[0], "这里用 DeepSeek。")
        self.assertEqual("".join(output).replace(" ", ""), state.clean_text.replace(" ", ""))
        self.assertEqual(state.clean_text, "这里用 DeepSeek。It works. 第二句！")

    def test_english_sentence_boundaries_are_unchanged(self):
        text = "Dr. Smith uses v1.2. It costs 3.14 dollars."
        self.assertEqual(sentence_tokenizer(sent_tokenize)(text), sent_tokenize(text))

    def test_markdown_code_is_protected_and_text_only_is_verbatim(self):
        parts = ["Use `中文。代码`", " safely。", "完成。"]
        stream, _ = self.run_stream(parts)
        self.assertEqual(list(stream)[0], "Use 中文。代码 safely。")
        stream, state = self.run_stream(parts, wants_audio=False)
        self.assertEqual(list(stream), parts)
        self.assertEqual(state.clean_text, "".join(parts))

    def test_cancellation_after_first_sentence_drops_remaining_speech(self):
        handler = Handler()
        stream, _ = self.run_stream(["第一句。", "第二句。"], handler=handler)
        self.assertEqual(next(stream), "第一句。")
        handler.cancelled = True
        self.assertEqual(list(stream), [])

    def test_unknown_source_is_refused_without_modifying_binding(self):
        module = SimpleNamespace(__file__=__file__, sent_tokenize=sent_tokenize)
        with patch("her_voice.sentence_stream.import_module", return_value=module):
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                install_sentence_streaming()
        self.assertIs(module.sent_tokenize, sent_tokenize)


if __name__ == "__main__": unittest.main()