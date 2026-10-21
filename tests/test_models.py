import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from her_voice import models


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("download_voice_models", ROOT / "scripts" / "download-models.py")
download_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download_cli)


class ModelStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="voice models ")
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)
        self.paths = {}
        for item in models.SNAPSHOTS:
            path = self.cache / ("models--" + item.repo_id.replace("/", "--")) / "snapshots" / item.revision
            for name in item.required_files:
                file = path / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"mock model content")
            self.paths[item.repo_id] = path
        self.smart = (self.cache / "models--pipecat-ai--smart-turn-v3" / "snapshots" /
                      models.SMART_TURN_REVISION / models.SMART_TURN_FILENAME)
        self.smart.parent.mkdir(parents=True)
        self.smart.write_bytes(b"mock onnx")
        self.hub = types.ModuleType("huggingface_hub")
        self.hub.snapshot_download = mock.Mock(side_effect=lambda **kw: str(self.paths[kw["repo_id"]]))
        self.hub.hf_hub_download = mock.Mock(return_value=str(self.smart))
        patcher = mock.patch.dict(sys.modules, {"huggingface_hub": self.hub})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_exact_revisions_full_snapshots_and_actual_cache_paths(self):
        before = os.environ.copy()
        result = models.ensure_models()
        self.assertEqual(os.environ, before)
        self.assertEqual(result, {
            models.ASR.environment_key: str(self.paths[models.ASR.repo_id]),
            models.TTS.environment_key: str(self.paths[models.TTS.repo_id]),
            models.SMART_TURN_ENVIRONMENT_KEY: str(self.smart),
        })
        self.assertEqual(self.hub.snapshot_download.call_args_list, [
            mock.call(repo_id=item.repo_id, revision=item.revision, token=False, local_files_only=True)
            for item in models.SNAPSHOTS
        ])
        self.hub.hf_hub_download.assert_called_once_with(
            repo_id=models.SMART_TURN_REPO, revision=models.SMART_TURN_REVISION,
            filename=models.SMART_TURN_FILENAME, token=False, local_files_only=True)
        self.assertTrue(all(Path(value).is_absolute() for value in result.values()))

    def test_missing_nested_qwen_weight_stops_before_engine_or_smartturn(self):
        (self.paths[models.TTS.repo_id] / "speech_tokenizer" / "model.safetensors").unlink()
        with self.assertRaisesRegex(models.ModelDownloadError, "speech_tokenizer/model.safetensors"):
            models.ensure_models()
        self.hub.hf_hub_download.assert_not_called()

    def test_drama_only_stages_asr_and_smartturn_without_qwen_weights(self):
        # A Drama-only setup must work even when the backup TTS is absent.
        (self.paths[models.TTS.repo_id] / "model.safetensors").unlink()
        result = models.ensure_models(tts_backend="drama3")
        self.assertNotIn(models.TTS.environment_key, result)
        self.hub.snapshot_download.assert_called_once_with(
            repo_id=models.ASR.repo_id, revision=models.ASR.revision, token=False, local_files_only=True)
        self.assertEqual(set(result), {models.ASR.environment_key, models.SMART_TURN_ENVIRONMENT_KEY})
        self.assertEqual(len(models.model_manifest(tts_backend="drama3")), 2)

    def test_missing_cache_uses_network_only_for_that_revision(self):
        def staged(**options):
            if options.get('local_files_only') and options['repo_id'] == models.ASR.repo_id:
                raise FileNotFoundError('cache miss')
            return str(self.paths[options['repo_id']])
        self.hub.snapshot_download.side_effect = staged
        result = models.ensure_models(tts_backend='drama3')
        self.assertIn(models.ASR.environment_key, result)
        self.assertEqual(self.hub.snapshot_download.call_args_list, [
            mock.call(repo_id=models.ASR.repo_id, revision=models.ASR.revision, token=False, local_files_only=True),
            mock.call(repo_id=models.ASR.repo_id, revision=models.ASR.revision, token=False),
        ])

    def test_unexpected_mutable_snapshot_is_rejected(self):
        wrong = self.paths[models.ASR.repo_id].parent / "main"
        wrong.mkdir()
        self.hub.snapshot_download.side_effect = None
        self.hub.snapshot_download.return_value = str(wrong)
        with self.assertRaisesRegex(models.ModelDownloadError, "unexpected snapshot"):
            models.ensure_models()
        self.hub.hf_hub_download.assert_not_called()

    def test_empty_asr_weights_are_not_ready(self):
        (self.paths[models.ASR.repo_id] / "model.bin").write_bytes(b"")
        with self.assertRaisesRegex(models.ModelDownloadError, "model.bin"):
            models.ensure_models()

    def test_smartturn_must_exist_at_the_pinned_snapshot_path(self):
        self.smart.unlink()
        with self.assertRaisesRegex(models.ModelDownloadError, "smart-turn-v3.2-cpu.onnx"):
            models.ensure_models()

    def test_download_errors_omit_original_text_and_chained_traceback(self):
        self.hub.snapshot_download.side_effect = OSError("proxy password very-secret-value")
        with self.assertRaises(models.ModelDownloadError) as result:
            models.ensure_models()
        self.assertNotIn("very-secret-value", str(result.exception))
        self.assertIn("OSError", str(result.exception))
        self.assertTrue(result.exception.__suppress_context__)

    def test_module_import_never_imports_hub_torch_or_transformers(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'huggingface_hub', 'torch', 'transformers'}:
        raise AssertionError('heavy dependency imported')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from her_voice.models import model_manifest
assert len(model_manifest()) == 3
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_defaults_to_manifest_without_network(self):
        output = io.StringIO()
        with mock.patch.object(download_cli, "ensure_models") as download, contextlib.redirect_stdout(output):
            self.assertEqual(download_cli.main([]), 0)
        download.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report["mode"], "manifest")
        self.assertFalse(report["staged"])
        self.assertEqual(len(report["models"]), 3)

    def test_cli_download_is_explicit_and_reports_local_paths(self):
        output = io.StringIO()
        with mock.patch.object(download_cli, "ensure_models", return_value={"HER_ASR_MODEL_PATH": "/mock/path"}) as download, contextlib.redirect_stdout(output):
            self.assertEqual(download_cli.main(["--download"]), 0)
        download.assert_called_once_with(tts_backend="qwen3")
        self.assertEqual(json.loads(output.getvalue())["paths"], {"HER_ASR_MODEL_PATH": "/mock/path"})

    def test_cli_download_failure_exits_nonzero(self):
        error = io.StringIO()
        with mock.patch.object(download_cli, "ensure_models", side_effect=models.ModelDownloadError("fixed revision unavailable")), contextlib.redirect_stderr(error):
            self.assertEqual(download_cli.main(["--download"]), 1)
        self.assertFalse(json.loads(error.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
