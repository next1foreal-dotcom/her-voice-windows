"""Exercise the real pinned metadata method, without loading models or cloud calls."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from her_voice.latency import SOURCE_SHA256, with_first_token


class LatencyTests(unittest.TestCase):
    def original(self):
        path = Path(__file__).resolve().parents[1] / ".venv-speech/Lib/site-packages/speech_to_speech/pipeline/turn_latency.py"
        source = path.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(source).hexdigest(), SOURCE_SHA256)
        tree = ast.parse(source.decode())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TurnLatencyTracker")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "metadata_payload")
        ns = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "pinned-latency", "exec", flags=__import__('__future__').annotations.compiler_flag), ns)
        return ns['metadata_payload']

    def test_existing_timings_are_preserved_and_ttft_is_added(self):
        tracker = SimpleNamespace(source="speech", turn_id="turn1", turn_revision=0, stt_s=0.1,
            llm_s=1.2, tts_ttfa_s=1.8, e2e_s=3.2, vad_decision_s=0.1,
            smart_turn_status="complete", smart_turn_wait_s=0.0, llm_ttft_s=0.2345678912)
        original = self.original()
        baseline = original(tracker, response_key="r", status="completed")
        result = with_first_token(original)(tracker, response_key="r", status="completed")
        self.assertEqual(result.pop('llm_ttft_s'), 0.234567891)
        self.assertEqual(result, baseline)

    def test_missing_and_oversize_metrics_keep_original_payload(self):
        for seconds in (None, -1, float('nan'), float('inf')):
            self.assertEqual(with_first_token(lambda self, **kw: {'version': 2})(SimpleNamespace(llm_ttft_s=seconds)), {'version': 2})
        original = {'version': 2, 'padding': 'x' * 480}
        result = with_first_token(lambda self, **kw: original)(SimpleNamespace(llm_ttft_s=0.1))
        self.assertIs(result, original)
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':'))), 512)


if __name__ == '__main__': unittest.main()
