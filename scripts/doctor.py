#!/usr/bin/env python3
"""Local diagnostics: no network, microphone capture, model download, or API spend."""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata as metadata
import json
import os
import platform
import re
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
ENGINE_SHA = "0ed1bb95bd335aa89bb7c172b6a347b6dae596f8"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def read_pins(path: Path) -> dict[str, str]:
    pins = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        if not match:
            raise ValueError(f"Expected an exact name==version constraint on line {number}.")
        name, version = match.groups()
        if name in pins:
            raise ValueError(f"Duplicate constraint for {name}.")
        pins[name] = version
    return pins


def installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def platform_checks(system: str, machine: str, version: tuple, bits: int) -> list[Check]:
    windows = system == "Windows" and machine.lower() in {"amd64", "x86_64"} and bits == 64
    python = tuple(version[:2])
    status = "PASS" if python == (3, 11) else "WARN" if python == (3, 12) else "FAIL"
    return [
        Check("platform", "PASS" if windows else "FAIL", f"{system} {machine}, {bits}-bit; target is native Windows x64."),
        Check("python", status, f"{'.'.join(map(str, version))}; installer profile requires CPython 3.11 x64; 3.12 has upstream wheels but is not the pinned installer target."),
    ]


def package_checks(pins: Mapping[str, str], get_version: Callable = installed_version) -> list[Check]:
    return [Check(f"package:{name}", "PASS" if get_version(name) == wanted else "FAIL",
                  f"installed={get_version(name) or 'missing'}; expected={wanted}")
            for name, wanted in pins.items()]


def qwen_distribution_check(get_version: Callable = installed_version) -> Check:
    if get_version("qwen-tts"):
        return Check("qwen_distribution", "FAIL", "qwen-tts conflicts with the Transformers-5 qwen-tts-hf distribution. Use a clean .venv-speech; both own qwen_tts.")
    return Check("qwen_distribution", "PASS", "No conflicting upstream qwen-tts distribution detected.")


def key_check(env: Mapping[str, str]) -> Check:
    present = bool(env.get("DEEPSEEK_API_KEY", "").strip())
    return Check("deepseek_key", "PASS" if present else "WARN",
                 "DEEPSEEK_API_KEY is present; validity was NOT tested." if present else
                 "DEEPSEEK_API_KEY is not configured. Set it in .env or the process environment before a live conversation; no API request was made.")


def load_local_settings():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from her_voice.settings import Settings
    return Settings.load(root=ROOT)


def engine_source_check() -> Check:
    try:
        raw = metadata.distribution("speech-to-speech").read_text("direct_url.json")
        direct = json.loads(raw or "{}")
        revision = direct.get("vcs_info", {}).get("commit_id")
        url = direct.get("url", "")
        good = revision == ENGINE_SHA or f"/{ENGINE_SHA}." in url or f"/{ENGINE_SHA}/" in url
        return Check("engine_revision", "PASS" if good else "FAIL",
                     f"Expected inspected upstream commit {ENGINE_SHA}. " +
                     ("Direct-install provenance matches." if good else "Reinstall with install-windows.ps1; a package version alone does not prove the source revision."))
    except (metadata.PackageNotFoundError, ValueError, TypeError):
        return Check("engine_revision", "FAIL", "Cannot verify the installed upstream source revision.")


def silero_patch_check() -> Check:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from her_voice.upstream_patch import UpstreamPatchError, verify_installed_silero_patch
    try:
        result = verify_installed_silero_patch()
        return Check("silero_revision", "PASS",
                     f"Installed VAD source hash and Silero commit {result['silero_revision']} match the disclosed patch. No weights were loaded.")
    except UpstreamPatchError as error:
        # The patch module emits fixed local diagnostics, never credentials.
        return Check("silero_revision", "FAIL", str(error))


def prepare_cuda_paths() -> list:
    """Keep DLL-directory handles alive; changes apply only to this process."""
    if os.name != "nt":
        return []
    candidates = []
    try:
        candidates.append(Path(metadata.distribution("torch").locate_file("torch/lib")))
    except metadata.PackageNotFoundError:
        pass
    if os.environ.get("CUDA_PATH"):
        candidates.append(Path(os.environ["CUDA_PATH"]) / "bin")
    candidates.extend(Path(value) for value in os.environ.get("HER_VOICE_CUDA_DLL_DIR", "").split(os.pathsep) if value)
    directories = [str(path.resolve()) for path in candidates if path.is_dir()]
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(directories + os.environ.get("PATH", "").split(os.pathsep)))
    handles = []
    for directory in directories:
        handles.append(os.add_dll_directory(directory))
    return handles


def gpu_worker() -> dict:
    """Runs in an isolated, time-limited child so native import failures stay bounded."""
    result: dict = {"cuda_available": False, "dll_errors": []}
    handles = prepare_cuda_paths()
    try:
        import torch
        result["cuda_available"] = bool(torch.cuda.is_available())
        result["cuda_runtime"] = torch.version.cuda
        result["cudnn_version"] = torch.backends.cudnn.version()
        if not result["cuda_available"]:
            return result
        device = torch.cuda.get_device_properties(0)
        result.update(name=device.name, total_memory_gib=round(device.total_memory / 2**30, 2))
        tensor = torch.ones(1, device="cuda")
        torch.cuda.synchronize()
        result["allocation_ok"] = tensor.item() == 1
        if os.name == "nt":
            for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll"):
                try:
                    ctypes.WinDLL(name)
                except OSError:
                    result["dll_errors"].append(name)
        try:
            import ctranslate2
            result["ctranslate_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda", 0))
        except Exception as error:
            result["ctranslate_error"] = type(error).__name__
        return result
    finally:
        for handle in handles:
            handle.close()


def gpu_result_checks(result: Mapping) -> list[Check]:
    if not result.get("cuda_available"):
        return [Check("cuda", "FAIL", "CUDA unavailable. Check the NVIDIA driver and CUDA-enabled PyTorch wheel; model inference has not been tested.")]
    allocation = result.get("allocation_ok") is True
    runtime = result.get("cuda_runtime") == "12.8"
    cudnn = result.get("cudnn_version")
    cudnn_ok = isinstance(cudnn, int) and 90000 <= cudnn < 100000
    compute_types = result.get("ctranslate_compute_types", [])
    dlls = result.get("dll_errors", [])
    vram = float(result.get("total_memory_gib", 0))
    return [
        Check("cuda_allocation", "PASS" if allocation else "FAIL", "Small CUDA tensor allocation and synchronization " + ("succeeded." if allocation else "failed.")),
        Check("cuda_runtime", "PASS" if runtime and cudnn_ok else "FAIL", f"CUDA={result.get('cuda_runtime')}, cuDNN={cudnn}; expected CUDA 12.8 / cuDNN 9."),
        Check("gpu", "PASS" if vram >= 15 else "WARN", f"{result.get('name', 'unknown')}, {vram:.2f} GiB total VRAM. Profile targets an RTX 4080 SUPER 16 GB; current free memory and ASR/TTS inference are not tested."),
        Check("cuda_dlls", "FAIL" if dlls else "PASS", "Missing or unloadable: " + ", ".join(dlls) if dlls else "Required cuBLAS/cuDNN DLLs loaded in the diagnostic process."),
        Check("ctranslate_cuda", "PASS" if "float16" in compute_types else "FAIL", "CUDA compute types: " + ", ".join(compute_types) if compute_types else "CTranslate2 CUDA support could not be initialized. Check Visual C++ Runtime and CUDA DLL search paths."),
    ]


def run_gpu_check() -> list[Check]:
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--gpu-worker"],
                                 capture_output=True, text=True, timeout=60, check=False)
        if process.returncode:
            return [Check("cuda", "FAIL", f"Native diagnostic subprocess exited {process.returncode}. Check PyTorch, Visual C++ Runtime, and CUDA/cuDNN libraries.")]
        return gpu_result_checks(json.loads(process.stdout.strip().splitlines()[-1]))
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, TypeError):
        return [Check("cuda", "FAIL", "Native diagnostic failed or exceeded 60 seconds. No model was loaded.")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-gpu", action="store_true", help="Environment inspection only; does not validate CUDA.")
    parser.add_argument("--gpu-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.gpu_worker:
        try:
            print(json.dumps(gpu_worker()))
            return 0
        except Exception as error:
            print(json.dumps({"worker_error": type(error).__name__}))
            return 1
    checks = platform_checks(platform.system(), platform.machine(), sys.version_info[:3], struct.calcsize("P") * 8)
    try:
        checks += package_checks(read_pins(ROOT / "config" / "dependencies-windows.txt"))
    except (OSError, ValueError):
        checks.append(Check("constraints", "FAIL", "Cannot read exact dependency constraints."))
    checks += [qwen_distribution_check(), engine_source_check(), silero_patch_check()]
    try:
        settings = load_local_settings()
        if settings.provider == "deepseek":
            checks.append(key_check({"DEEPSEEK_API_KEY": settings.api_key}))
        else:
            checks.append(Check("agent_provider", "PASS" if settings.provider_ready else "WARN",
                                "Agent adapter configuration is present; no connection or authentication check was made."
                                if settings.provider_ready else "Configure the Agent endpoint and model locally; no request was made."))
    except (OSError, ValueError):
        checks.append(Check("configuration", "FAIL", "Cannot load local .env settings; check its KEY=value syntax. Values are omitted."))
    if args.skip_gpu or os.name != "nt":
        checks.append(Check("gpu", "SKIP", "GPU diagnostics were not run; this report is not a Windows GPU validation."))
    else:
        checks += run_gpu_check()
    report = {"ok": not any(item.status == "FAIL" for item in checks),
              "scope": "Local environment only: no network, model download, microphone capture, or paid API request.",
              "checks": [asdict(item) for item in checks]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["scope"])
        for item in checks:
            print(f"[{item.status}] {item.name}: {item.detail}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
