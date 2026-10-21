import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from her_voice.upstream_patch import UpstreamPatchError


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("voice_doctor", ROOT / "scripts" / "doctor.py")
doctor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = doctor
spec.loader.exec_module(doctor)


class DoctorTests(unittest.TestCase):
    def test_constraints_only_accept_exact_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "constraints.txt"
            path.write_text("# versions\ntorch==2.11.0+cu128\naiohttp==3.14.5 # bridge\n", encoding="utf-8")
            self.assertEqual(doctor.read_pins(path), {"torch": "2.11.0+cu128", "aiohttp": "3.14.5"})
            path.write_text("torch>=2.4\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                doctor.read_pins(path)

    def test_windows_x64_python311_is_the_installer_target(self):
        checks = doctor.platform_checks("Windows", "AMD64", (3, 11, 9), 64)
        self.assertTrue(all(item.status == "PASS" for item in checks))
        self.assertTrue(any(item.status == "FAIL" for item in doctor.platform_checks("Windows", "ARM64", (3, 11, 9), 64)))
        self.assertTrue(any(item.status == "FAIL" for item in doctor.platform_checks("Windows", "AMD64", (3, 10, 11), 64)))
        self.assertTrue(any(item.status == "WARN" for item in doctor.platform_checks("Windows", "AMD64", (3, 12, 8), 64)))

    def test_cpu_torch_or_wrong_versions_are_not_a_cuda_pass(self):
        versions = {"torch": "2.11.0", "faster-whisper": "1.2.1"}
        checks = doctor.package_checks({"torch": "2.11.0+cu128", "faster-whisper": "1.2.1"}, versions.get)
        self.assertEqual(checks[0].status, "FAIL")
        self.assertEqual(checks[1].status, "PASS")

    def test_conflicting_qwen_distributions_fail(self):
        versions = {"qwen-tts": "0.1.1", "qwen-tts-hf": "0.1.1.post1"}
        self.assertEqual(doctor.qwen_distribution_check(versions.get).status, "FAIL")
        self.assertEqual(doctor.qwen_distribution_check({"qwen-tts-hf": "0.1.1.post1"}.get).status, "PASS")

    def test_key_presence_never_discloses_a_key(self):
        key = "sk-test-do-not-print-this"
        check = doctor.key_check({"DEEPSEEK_API_KEY": key})
        self.assertEqual(check.status, "PASS")
        self.assertNotIn(key, str(check))
        self.assertEqual(doctor.key_check({}).status, "WARN")

    def test_gpu_result_requires_actual_runtime_and_ctranslate_support(self):
        good = {"cuda_available": True, "allocation_ok": True, "name": "RTX 4080 SUPER",
                "total_memory_gib": 15.99, "cuda_runtime": "12.8", "cudnn_version": 91002,
                "ctranslate_compute_types": ["float16", "int8_float16"], "dll_errors": []}
        self.assertTrue(all(item.status == "PASS" for item in doctor.gpu_result_checks(good)))
        bad = dict(good, dll_errors=["cudnn64_9.dll"], ctranslate_compute_types=[])
        self.assertGreaterEqual(sum(item.status == "FAIL" for item in doctor.gpu_result_checks(bad)), 2)
        unavailable = doctor.gpu_result_checks({"cuda_available": False})
        self.assertTrue(any(item.status == "FAIL" for item in unavailable))

    def test_silero_patch_check_requires_verified_installed_source(self):
        with mock.patch("her_voice.upstream_patch.verify_installed_silero_patch",
                        return_value={"silero_revision": "fixed-commit"}) as verify:
            check = doctor.silero_patch_check()
        verify.assert_called_once_with()
        self.assertEqual(check.status, "PASS")
        self.assertIn("No weights were loaded", check.detail)
        with mock.patch("her_voice.upstream_patch.verify_installed_silero_patch",
                        side_effect=UpstreamPatchError("Silero is still unpinned")):
            self.assertEqual(doctor.silero_patch_check().status, "FAIL")


if __name__ == "__main__":
    unittest.main()
