"""Guard the narrow startup patch: preserve other AST nodes and reject drift."""
import ast
import hashlib
import unittest
from unittest.mock import patch
from her_voice import startup_patch as target
from her_voice.upstream_patch import UpstreamPatchError


class StartupPatchTests(unittest.TestCase):
    def test_removes_only_unused_bootstrap_and_is_idempotent(self):
        source = ('import os\nimport nltk\n' + target.BOOTSTRAP + '\ndef serve():\n    return 42\n').encode()
        expected = source.decode().replace('import nltk\n', '').replace(target.BOOTSTRAP,
            '# This pinned pipeline does not use NLTK; no tokenizer/tagger bootstrap is needed.\n').encode()
        with patch.object(target, 'ORIGINAL_SHA256', hashlib.sha256(source).hexdigest()), \
             patch.object(target, 'PATCHED_SHA256', hashlib.sha256(expected).hexdigest()):
            actual = target.transform(source)
            self.assertEqual(ast.dump(ast.parse(actual)), ast.dump(ast.parse('import os\ndef serve():\n    return 42\n')))
            self.assertEqual(target.transform(actual), actual)
            self.assertEqual(target.transform(source.replace(b'\n', b'\r\n')), actual)
        self.assertEqual(source.count(b'import nltk'), 1)

    def test_unknown_source_is_rejected(self):
        with self.assertRaisesRegex(UpstreamPatchError, 'Unknown'):
            target.transform(b'import nltk\n')

    def test_verification_does_not_modify_unknown_installed_source(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 's2s_pipeline.py'
            path.write_bytes(b'foreign = True\n')
            with patch.object(target, 'installed_pipeline', return_value=path):
                with self.assertRaises(UpstreamPatchError):
                    target.verify_installed_startup_patch()
            self.assertEqual(path.read_bytes(), b'foreign = True\n')
