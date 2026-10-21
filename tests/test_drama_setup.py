"""Small-file tests for the real preparation/rollback paths, without a GPU."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare-drama.py"
MODULE_SPEC = importlib.util.spec_from_file_location("voice_prepare_drama", SCRIPT)
setup = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = setup
MODULE_SPEC.loader.exec_module(setup)


class DramaPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.source = self.root / ".runtime" / "drama3" / setup.SOURCE_DIRECTORY
        self.exe = self.source / "build" / "windows-cuda-release" / "bin" / "audiocpp_server.exe"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"test executable fixture")
        self.payload = b"GGUF" + bytes(range(128))
        self.spec = setup.ModelSpec("fixture/drama", "a" * 40, "model.gguf", len(self.payload),
                                   hashlib.sha256(self.payload).hexdigest())
        self.model = Path(self.temporary.name) / "existing-model.gguf"
        self.model.write_bytes(self.payload)
        self.build = {
            "executable": str(self.exe), "source_dir": str(self.source),
            "runtime_repository": setup.RUNTIME_REPOSITORY,
            "runtime_revision": setup.RUNTIME_REVISION,
            "executable_sha256": hashlib.sha256(self.exe.read_bytes()).hexdigest(),
        }

    def prepare(self, **kwargs):
        with patch.object(setup, "validate_build", return_value=self.build):
            return setup.prepare(self.root, executable=self.exe, source_dir=self.source,
                                 model_file=self.model, spec=self.spec, **kwargs)

    def test_default_cli_is_read_only_and_does_not_require_hub(self):
        destination = Path(self.temporary.name) / "unused"
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.main(["--workspace", str(destination)]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["mode"], "dry-run")
        self.assertFalse(report["network_requests"])
        self.assertFalse(destination.exists())

    def test_reuses_file_without_copy_and_records_integrity(self):
        report = self.prepare()
        runtime_dir = self.root / ".runtime" / "drama3"
        saved = json.loads((runtime_dir / "runtime.json").read_text())
        self.assertEqual(saved, report)
        self.assertEqual(saved["model_path"], str(self.model.resolve()))
        self.assertEqual(saved["model_mtime_ns"], self.model.stat().st_mtime_ns)
        self.assertFalse(saved["inference_validated"])
        self.assertEqual(list(self.root.rglob("*.gguf")), [])
        config = json.loads(Path(saved["server_config_path"]).read_text())
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["models"][0]["mode"], "streaming")
        options = config["models"][0]["session_options"]
        self.assertTrue(options["fish_drama3.disk_experts"])
        self.assertTrue(options["fish_drama3.pin_experts"])
        self.assertEqual(options["fish_drama3.cpu_expert_layers"], 0)
        self.assertEqual(options["fish_drama3.expert_cache_size"], 8)

    def test_repeat_prepare_is_idempotent(self):
        first = self.prepare()
        self.assertEqual(self.prepare(), first)
        self.assertEqual(len(list((self.root / ".runtime" / "drama3").glob("server-*.json"))), 1)

    def test_bad_hash_preserves_working_manifest_and_config(self):
        first = self.prepare()
        manifest = self.root / ".runtime" / "drama3" / "runtime.json"
        previous_manifest = manifest.read_bytes()
        previous_config = Path(first["server_config_path"]).read_bytes()
        self.model.write_bytes(self.payload[:-1] + b"\xff")
        with self.assertRaisesRegex(setup.PreparationError, "SHA-256"):
            self.prepare(port=9000)
        self.assertEqual(manifest.read_bytes(), previous_manifest)
        self.assertEqual(Path(first["server_config_path"]).read_bytes(), previous_config)
        self.assertTrue(self.model.exists())

    def test_wrong_size_is_rejected_before_hashing(self):
        self.model.write_bytes(b"partial download")
        with patch.object(setup, "sha256_file") as digest:
            with self.assertRaisesRegex(setup.PreparationError, "size differs"):
                setup.verify_model(self.model, self.spec)
        digest.assert_not_called()

    def test_manifest_publish_failure_keeps_old_config_reachable(self):
        first = self.prepare()
        manifest = self.root / ".runtime" / "drama3" / "runtime.json"
        previous = manifest.read_bytes()
        config_before = Path(first["server_config_path"]).read_bytes()
        replace = setup.os.replace

        def fail_manifest(source, destination):
            if Path(destination) == manifest:
                raise OSError("simulated manifest permission error")
            return replace(source, destination)

        with patch.object(setup.os, "replace", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.prepare(port=9000)
        self.assertEqual(manifest.read_bytes(), previous)
        self.assertEqual(Path(first["server_config_path"]).read_bytes(), config_before)
        self.assertEqual(list(manifest.parent.glob("*.tmp")), [])

    def test_downloader_receives_immutable_revision_and_no_token(self):
        cache = self.root / "cache"
        snapshot = cache / "models--fixture--drama" / "snapshots" / self.spec.revision / self.spec.filename
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(self.payload)
        downloader = Mock(return_value=str(snapshot))
        result = setup.download_model(cache, self.spec, downloader)
        self.assertEqual(result, snapshot.resolve())
        downloader.assert_called_once_with(repo_id=self.spec.repository, filename=self.spec.filename,
                                           revision=self.spec.revision, cache_dir=str(cache), token=False)

    def test_unexpected_download_path_is_refused_even_if_bytes_match(self):
        with self.assertRaisesRegex(setup.PreparationError, "unexpected snapshot"):
            setup.download_model(self.root / "cache", self.spec, Mock(return_value=str(self.model)))

    def test_download_error_does_not_expose_proxy_credentials(self):
        downloader = Mock(side_effect=RuntimeError("https://user:password@proxy.example.invalid"))
        with self.assertRaises(setup.PreparationError) as context:
            setup.download_model(self.root / "cache", self.spec, downloader)
        self.assertNotIn("password", str(context.exception))
        self.assertIn("RuntimeError", str(context.exception))

    def test_invalid_configuration_does_not_start_download(self):
        downloader = Mock()
        with patch.object(setup, "validate_build", return_value=self.build):
            with self.assertRaisesRegex(setup.PreparationError, "port"):
                setup.prepare(self.root, executable=self.exe, source_dir=self.source,
                              download=True, port=0, spec=self.spec, downloader=downloader)
        downloader.assert_not_called()

    def test_build_wrong_commit_and_tracked_edits_are_refused(self):
        with patch.object(setup, "_git", side_effect=[str(self.source), "b" * 40]):
            with self.assertRaisesRegex(setup.PreparationError, "reviewed commit"):
                setup.validate_build(self.exe, self.source)
        with patch.object(setup, "_git", side_effect=[str(self.source), setup.RUNTIME_REVISION, " M tracked.cpp"]):
            with self.assertRaisesRegex(setup.PreparationError, "tracked edits"):
                setup.validate_build(self.exe, self.source)

    def test_build_validates_actual_checkout_and_binary_hash(self):
        with patch.object(setup, "_git", side_effect=[str(self.source), setup.RUNTIME_REVISION, ""]):
            verified = setup.validate_build(self.exe, self.source)
        self.assertEqual(verified["executable_sha256"], self.build["executable_sha256"])


if __name__ == "__main__":
    unittest.main()
