#!/usr/bin/env python3
"""Inspect or prepare the pinned Drama 3 runtime; the default is a dry run.

The explicit --download action fetches about 22.91 GiB. --model-file reuses an
existing identical GGUF without copying it. No model is loaded and no cloud LLM
request is made. A failed model check never replaces a working runtime manifest.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_REPOSITORY = "https://github.com/0xShug0/audio.cpp.git"
RUNTIME_REVISION = "0bf4d155f2af5f85853415170622dc145e3d62c3"
SOURCE_DIRECTORY = "audio.cpp-" + RUNTIME_REVISION[:12]


@dataclass(frozen=True)
class ModelSpec:
    repository: str
    revision: str
    filename: str
    size_bytes: int
    sha256: str


MODEL = ModelSpec(
    repository="Jmica/Drama-3-GGUF",
    revision="17a5b40c5dc2c2be986f5371a7e650108b828e29",
    filename="drama-3-backbone-8bit-experts-Q4_K_S.gguf",
    size_bytes=24596497888,
    sha256="05af716baf78e7c20863b9ccebb3ebdeca9607dd3ea7d0b72c35079e675b212b",
)


class PreparationError(RuntimeError):
    """A setup error safe to display without network/credential details."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(path: Path, spec: ModelSpec = MODEL) -> Path:
    """Require the exact packaged file, not just a plausible .gguf extension."""
    path = Path(path).resolve()
    if not path.is_file():
        raise PreparationError("The Drama 3 GGUF file is missing.")
    if path.stat().st_size != spec.size_bytes:
        raise PreparationError("The Drama 3 GGUF size differs from the pinned Q4/Q8 package. The previous configuration was kept.")
    if sha256_file(path) != spec.sha256:
        raise PreparationError("The Drama 3 GGUF SHA-256 check failed. The previous configuration was kept; no file was deleted.")
    return path


def _git(source_dir: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_dir), *arguments],
            check=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise PreparationError("Could not verify the audio.cpp checkout. Install Git and rerun scripts/install-drama.ps1.") from None
    return result.stdout.strip()


def validate_build(executable: Path, source_dir: Path) -> dict:
    executable, source_dir = Path(executable).resolve(), Path(source_dir).resolve()
    if not executable.is_file() or executable.stat().st_size == 0:
        raise PreparationError("audiocpp_server.exe is missing. Run scripts/install-drama.ps1 first.")
    if executable.name.lower() not in {"audiocpp_server.exe", "audiocpp_server"}:
        raise PreparationError("The selected executable must be audiocpp_server.exe.")
    expected_bin = source_dir / "build" / "windows-cuda-release" / "bin"
    if executable.parent != expected_bin.resolve():
        raise PreparationError("The executable must remain in the pinned checkout's build/windows-cuda-release/bin directory with its DLLs.")
    if Path(_git(source_dir, "rev-parse", "--show-toplevel")).resolve() != source_dir:
        raise PreparationError("The audio.cpp source directory is not a repository root.")
    if _git(source_dir, "rev-parse", "HEAD") != RUNTIME_REVISION:
        raise PreparationError("The audio.cpp checkout is not at the reviewed commit. No checkout or reset was performed.")
    if _git(source_dir, "status", "--porcelain", "--untracked-files=no"):
        raise PreparationError("The audio.cpp checkout has tracked edits. Preserve them separately before rebuilding; this helper will not reset them.")
    return {
        "executable": str(executable), "source_dir": str(source_dir),
        "runtime_repository": RUNTIME_REPOSITORY, "runtime_revision": RUNTIME_REVISION,
        "executable_sha256": sha256_file(executable),
    }


def server_config(model_path: Path, *, port: int = 8792, threads: int = 8) -> dict:
    if type(port) is not int or not 1 <= port <= 65535:
        raise PreparationError("The Drama server port must be an integer in 1..65535.")
    if type(threads) is not int or not 1 <= threads <= 64:
        raise PreparationError("Drama CPU threads must be an integer in 1..64.")
    return {
        "host": "127.0.0.1", "port": port, "backend": "cuda", "device": 0,
        "threads": threads, "lazy_load": True, "max_loaded_models": 1,
        "idle_unload_ms": 0, "ui": False, "ui_management": False,
        "log_request_body": False, "max_request_body_bytes": 1048576,
        "busy_timeout_ms": 10000,
        "models": [{
            "id": "drama-3", "family": "fish_drama3", "path": str(Path(model_path).resolve()),
            "task": "tts", "mode": "streaming",
            "session_options": {
                "fish_drama3.disk_experts": True,
                "fish_drama3.pin_experts": True,
                "fish_drama3.expert_cache_size": 8,
                "fish_drama3.cpu_expert_layers": 0,
                "fish_drama3.codec_backend": "same",
                "fish_drama3.context_length": 2048,
            },
            "default_request_options": {"max_tokens": 500},
        }],
    }


def atomic_json(path: Path, value: dict) -> None:
    """Publish complete JSON only; a replace failure preserves the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def download_model(cache_dir: Path, spec: ModelSpec = MODEL,
                   download: Callable | None = None) -> Path:
    if download is None:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            raise PreparationError("huggingface_hub is unavailable. Run scripts/install-windows.ps1 first.") from None
        download = hf_hub_download
    try:
        raw = download(repo_id=spec.repository, filename=spec.filename, revision=spec.revision,
                       cache_dir=str(cache_dir), token=False)
    except Exception as error:
        raise PreparationError(f"The pinned Drama 3 download failed ({type(error).__name__}). Rerun to resume the Hub cache download; no previous configuration was changed.") from None
    candidate = Path(raw).absolute()
    # The Hub cache path must retain immutable revision metadata. Do not accept
    # an unexpected mutable filename returned by a replacement downloader.
    if candidate.name != spec.filename or candidate.parent.name != spec.revision or candidate.parent.parent.name != "snapshots":
        raise PreparationError("The Hub returned an unexpected snapshot path; refusing the model reference.")
    return verify_model(candidate, spec)


def prepare(root: Path, *, executable: Path, source_dir: Path, model_file: Path | None = None,
            download: bool = False, port: int = 8792, threads: int = 8,
            spec: ModelSpec = MODEL, downloader: Callable | None = None) -> dict:
    """Validate first, then atomically publish the selected model and runtime."""
    if bool(download) == (model_file is not None):
        raise PreparationError("Choose exactly one of --download or --model-file.")
    root = Path(root).resolve()
    runtime_dir = root / ".runtime" / "drama3"
    build = validate_build(executable, source_dir)
    # Validate settings before a large network download.
    server_config(Path("model.gguf"), port=port, threads=threads)
    model_path = (download_model(runtime_dir / "hf-cache", spec, downloader) if download
                  else verify_model(Path(model_file), spec))
    config = server_config(model_path, port=port, threads=threads)
    config_digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
    config_path = runtime_dir / f"server-{config_digest[:12]}.json"
    manifest = {
        "schema_version": 1, **build,
        "model_path": str(model_path), "model_repository": spec.repository,
        "model_revision": spec.revision, "model_filename": spec.filename,
        "model_size_bytes": spec.size_bytes, "model_sha256": spec.sha256,
        "model_mtime_ns": model_path.stat().st_mtime_ns,
        "server_config_path": str(config_path), "port": port, "model_id": "drama-3",
        "prepared": True, "inference_validated": False,
    }
    # No writes above this line: hash/download/build errors preserve the
    # existing manifest and its content-addressed server config. If publishing
    # the final manifest fails, the old config still points to the old bytes.
    atomic_json(config_path, config)
    atomic_json(runtime_dir / "runtime.json", manifest)
    return manifest


def inspect_plan(root: Path, *, executable: Path, source_dir: Path, port: int = 8792,
                 threads: int = 8) -> dict:
    runtime_dir = root / ".runtime" / "drama3"
    return {
        "mode": "dry-run", "network_requests": False, "files_written": False,
        "runtime_repository": RUNTIME_REPOSITORY, "runtime_revision": RUNTIME_REVISION,
        "source_dir": str(source_dir), "executable": str(executable),
        "model_repository": MODEL.repository, "model_revision": MODEL.revision,
        "model_filename": MODEL.filename, "model_size_bytes": MODEL.size_bytes,
        "model_size_gib": round(MODEL.size_bytes / 1024**3, 2), "model_sha256": MODEL.sha256,
        "manifest_path": str(runtime_dir / "runtime.json"),
        "server_config": server_config(runtime_dir / "<HF snapshot>" / MODEL.filename, port=port, threads=threads),
        "next_step": "Run scripts/install-drama.ps1 to build the pinned CUDA runtime and download/verify this approximately 23 GiB model. Use --model-file PATH to reuse an existing byte-identical GGUF.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--threads", type=int, default=8)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--download", action="store_true", help="Download and verify 22.91 GiB of fixed Q4/Q8 weights; no inference.")
    actions.add_argument("--model-file", type=Path, help="Verify and register an existing exact Q4/Q8 GGUF without copying.")
    actions.add_argument("--register-build", action="store_true", help="Record a verified source checkout and binary; do not download weights.")
    args = parser.parse_args(argv)
    root = args.workspace.resolve()
    source = (args.source_dir or root / ".runtime" / "drama3" / SOURCE_DIRECTORY).resolve()
    executable = (args.executable or source / "build" / "windows-cuda-release" / "bin" / "audiocpp_server.exe").resolve()
    try:
        if args.register_build:
            build = {"schema_version": 1, **validate_build(executable, source), "model_prepared": False}
            atomic_json(root / ".runtime" / "drama3" / "build.json", build)
            result = {"ok": True, "mode": "build-only", **build}
        elif args.download or args.model_file is not None:
            result = {"ok": True, "mode": "prepared", **prepare(
                root, executable=executable, source_dir=source, download=args.download,
                model_file=args.model_file, port=args.port, threads=args.threads,
            )}
        else:
            result = inspect_plan(root, executable=executable, source_dir=source, port=args.port, threads=args.threads)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PreparationError, OSError) as error:
        message = str(error) if isinstance(error, PreparationError) else f"A local file operation failed ({type(error).__name__}). Check file permissions and free disk space."
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
