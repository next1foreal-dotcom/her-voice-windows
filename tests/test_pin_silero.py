"""Hash/provenance gates on temporary source fixtures; no installed edits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from her_voice import upstream_patch as pin


class SileroPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.prefix = self.root / ".venv-speech"
        self.target = self.prefix / "Lib" / "site-packages" / pin._TARGET
        self.target.parent.mkdir(parents=True)
        self.original = b'model = torch.hub.load("snakers4/silero-vad:master", "silero_vad")\n'
        self.patched = self.original.replace(pin._OLD, pin._NEW)
        self.target.write_bytes(self.original)
        self.hashes = patch.multiple(
            pin,
            ORIGINAL_SHA256=hashlib.sha256(self.original).hexdigest(),
            PATCHED_SHA256=hashlib.sha256(self.patched).hexdigest(),
        )
        self.hashes.start()
        self.distribution = Mock()
        self.distribution.version = "1.0.0"
        self.distribution.read_text.return_value = json.dumps({"url": pin._ARCHIVE_URL, "archive_info": {}})
        self.distribution.locate_file.return_value = self.target

    def tearDown(self):
        self.hashes.stop()
        self.temp.cleanup()

    def installed(self):
        return (
            patch.object(pin.sys, "prefix", str(self.prefix)),
            patch.object(pin, "_expected_environment", return_value=self.prefix),
            patch.object(pin.metadata, "distribution", return_value=self.distribution),
        )

    def test_exact_patch_is_idempotent_and_verified(self):
        prefix, expected, distribution = self.installed()
        with prefix, expected, distribution:
            result = pin.apply_installed_silero_patch()
            self.assertEqual(result["state"], "patched")
            self.assertEqual(self.target.read_bytes(), self.patched)
            before = self.target.stat().st_mtime_ns
            self.assertEqual(pin.apply_installed_silero_patch()["state"], "already_patched")
            self.assertEqual(self.target.stat().st_mtime_ns, before)
            self.assertEqual(pin.verify_installed_silero_patch()["state"], "verified")
        self.assertEqual(list(self.target.parent.glob(".her-silero-*")), [])

    def test_unknown_source_is_never_rewritten(self):
        unknown = self.original + b"# unexpected upstream change\n"
        self.target.write_bytes(unknown)
        with self.assertRaisesRegex(pin.UpstreamPatchError, "Unknown VAD source hash"):
            pin._patch_file(self.target)
        self.assertEqual(self.target.read_bytes(), unknown)

    def test_startup_rejects_unpatched_and_corrupted_source(self):
        with self.assertRaisesRegex(pin.UpstreamPatchError, "still unpinned"):
            pin._verify_file(self.target)
        self.target.write_bytes(self.patched + b"# unknown\n")
        with self.assertRaisesRegex(pin.UpstreamPatchError, "Unknown VAD source hash"):
            pin._verify_file(self.target)

    def test_incorrect_expected_patch_hash_changes_nothing(self):
        with patch.object(pin, "PATCHED_SHA256", "0" * 64):
            with self.assertRaisesRegex(pin.UpstreamPatchError, "output hash"):
                pin._patch_file(self.target)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_duplicate_master_literal_is_rejected(self):
        original = self.original + self.original
        self.target.write_bytes(original)
        with patch.object(pin, "ORIGINAL_SHA256", hashlib.sha256(original).hexdigest()):
            with self.assertRaisesRegex(pin.UpstreamPatchError, "exactly one"):
                pin._patch_file(self.target)
        self.assertEqual(self.target.read_bytes(), original)

    def test_other_python_environment_is_refused(self):
        with patch.object(pin.sys, "prefix", str(self.root / "other")), patch.object(pin, "_expected_environment", return_value=self.prefix), patch.object(pin.metadata, "distribution") as distribution:
            with self.assertRaisesRegex(pin.UpstreamPatchError, "Use this project's"):
                pin.apply_installed_silero_patch()
        distribution.assert_not_called()

    def test_source_outside_isolated_environment_is_refused(self):
        outside = self.root / "source-checkout.py"
        outside.write_bytes(self.original)
        self.distribution.locate_file.return_value = outside
        prefix, expected, distribution = self.installed()
        with prefix, expected, distribution:
            with self.assertRaisesRegex(pin.UpstreamPatchError, "outside"):
                pin.apply_installed_silero_patch()
        self.assertEqual(outside.read_bytes(), self.original)

    def test_unpinned_provenance_is_refused_even_if_source_hash_matches(self):
        self.distribution.read_text.return_value = json.dumps({"url": pin._REPOSITORY_URL, "vcs_info": {"vcs": "git", "commit_id": "moving-main"}})
        prefix, expected, distribution = self.installed()
        with prefix, expected, distribution:
            with self.assertRaisesRegex(pin.UpstreamPatchError, "fixed commit/archive"):
                pin.apply_installed_silero_patch()
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_pinned_vcs_install_is_also_accepted(self):
        self.distribution.read_text.return_value = json.dumps({"url": pin._REPOSITORY_URL + ".git", "vcs_info": {"vcs": "git", "commit_id": pin.UPSTREAM_SHA}})
        prefix, expected, distribution = self.installed()
        with prefix, expected, distribution:
            self.assertEqual(pin.apply_installed_silero_patch()["state"], "patched")

    def test_failed_atomic_replace_preserves_original_and_cleans_temp(self):
        with patch.object(pin.os, "replace", side_effect=OSError("fixture access denied")):
            with self.assertRaisesRegex(pin.UpstreamPatchError, "Could not write"):
                pin._patch_file(self.target)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(list(self.target.parent.glob(".her-silero-*")), [])


if __name__ == "__main__":
    unittest.main()
