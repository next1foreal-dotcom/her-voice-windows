"""Own one pinned speech-to-speech process; no GPU imports in the web process.

The synchronous methods are suitable for ``asyncio.to_thread``.  A listener on
the requested port is never adopted or terminated.  HTTP readiness uses
``/v1/pool`` so a health check does not consume the only Realtime session.
"""

from __future__ import annotations

from collections import deque
import http.client
import json
import os
from pathlib import Path
import re
import runpy
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.request


UPSTREAM_SHA = "0ed1bb95bd335aa89bb7c172b6a347b6dae596f8"
DEFAULT_ENGINE_PORT = 8791
DEFAULT_MODEL = "deepseek-flash"
_SECRET_ENV = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HER_LLM_API_KEY", "VOICE_PROVIDER_API_KEY", "HER_VOICE_GATEWAY_TOKEN")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s\"',;]+")
_KEY_VALUE = re.compile(r"(?i)((?:api[_-]?key|authorization|gateway[_-]?token)\s*[=:]\s*)[^\s,;]+")


def cuda_dll_directories(env: dict[str, str], executable: str | Path) -> list[str]:
    """Locate existing Windows runtime DLL folders without importing torch."""
    binary = Path(executable).resolve()
    prefix = binary.parent.parent if binary.parent.name.lower() == "scripts" else binary.parent
    site_packages = prefix / "Lib" / "site-packages"
    candidates = [site_packages / "torch" / "lib"]
    if env.get("CUDA_PATH"):
        candidates.append(Path(env["CUDA_PATH"]) / "bin")
    candidates.extend(Path(value) for value in env.get("HER_VOICE_CUDA_DLL_DIR", "").split(os.pathsep) if value)
    candidates.extend(site_packages / "nvidia" / name / "bin" for name in ("cublas", "cudnn"))
    return list(dict.fromkeys(str(path.resolve()) for path in candidates if path.is_dir()))


class EngineError(RuntimeError):
    """A startup or ownership error, with no credential values in the message."""


class EngineSupervisor:
    def __init__(
        self,
        config_path: str | Path,
        root_dir: str | Path,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_ENGINE_PORT,
        python_executable: str | Path | None = None,
        startup_timeout: float = 1200.0,
        stop_timeout: float = 6.0,
        poll_interval: float = 0.25,
        env: dict[str, str] | None = None,
        echo_logs: bool = False,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("The prototype engine must listen on 127.0.0.1.")
        if not 1 <= port <= 65535:
            raise ValueError("Engine port must be in 1..65535.")
        if startup_timeout <= 0 or stop_timeout <= 0 or poll_interval <= 0:
            raise ValueError("Engine timeouts must be positive.")
        self.root_dir = Path(root_dir).resolve()
        path = Path(config_path)
        self.config_path = (path if path.is_absolute() else self.root_dir / path).resolve()
        self.host = host
        self.port = port
        self.python_executable = str(python_executable or sys.executable)
        self.startup_timeout = startup_timeout
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        self.extra_env = dict(env or {})
        self.echo_logs = bool(echo_logs)
        self.endpoint = f"ws://{host}:{port}/v1/realtime"
        self.pool_url = f"http://{host}:{port}/v1/pool"
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._state = "stopped"
        self._detail = "Engine has not been started."
        self._ready = False
        self._pool: dict[str, Any] | None = None
        self._logs: deque[str] = deque(maxlen=80)
        self._redact_values: set[str] = set()
        # Only loopback URLs are used. Do not send health checks through a
        # machine's HTTP proxy configuration.
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def build_command(self) -> list[str]:
        """Use the 1.0.0 file-mode CLI; no deprecated --mode flags."""
        # Every platform uses the worker to resolve pinned model snapshots.
        # Windows also needs add_dll_directory for extension dependencies.
        return [
            self.python_executable, "-u", "-m", "her_voice.engine", "--worker", "serve",
            "--file", str(self.config_path), "--name", "windows4080",
        ]

    def _child_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.extra_env)
        self._redact_values.update(env[k] for k in _SECRET_ENV if env.get(k))
        if not env.get("HER_VOICE_GATEWAY_TOKEN"):
            raise EngineError("HER_VOICE_GATEWAY_TOKEN is missing; start the local gateway first.")
        # The cloud credential belongs to the parent gateway, not this model
        # process. Upstream authenticates only with the local gateway token.
        for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HER_LLM_API_KEY", "VOICE_PROVIDER_API_KEY"):
            env.pop(name, None)
        env.setdefault("DEEPSEEK_MODEL", DEFAULT_MODEL)
        env.setdefault("VOICE_LLM_MODEL", env["DEEPSEEK_MODEL"])
        env.setdefault("VOICE_GATEWAY_BASE_URL", "http://127.0.0.1:8790/llm/v1")
        env["VOICE_ENGINE_PORT"] = str(self.port)
        env["VOICE_WORKSPACE"] = str(self.root_dir)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # HTTPX follows proxy environment variables by default. Local gateway
        # and native TTS traffic must stay on loopback even on proxied PCs.
        bypass = []
        for value in (env.get("NO_PROXY", ""), env.get("no_proxy", ""), "127.0.0.1,localhost"):
            bypass.extend(item.strip() for item in value.split(",") if item.strip())
        env["NO_PROXY"] = env["no_proxy"] = ",".join(dict.fromkeys(bypass))
        if os.name == "nt":
            paths = cuda_dll_directories(env, self.python_executable)
            env["PATH"] = os.pathsep.join([*paths, env.get("PATH", "")])
        return env

    def _redact(self, text: str) -> str:
        text = _ANSI.sub("", text)
        for secret in sorted(self._redact_values, key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        text = _BEARER.sub(r"\1[redacted]", text)
        return _KEY_VALUE.sub(r"\1[redacted]", text)

    def _collect_output(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                with self._lock:
                    redacted = self._redact(line.rstrip())[-4000:]
                    self._logs.append(redacted)
                    if self.echo_logs:
                        try:
                            print("[engine] " + redacted, flush=True)
                        except (OSError, ValueError):
                            # A closed optional terminal must not stop draining
                            # the child's pipe and block the speech process.
                            self.echo_logs = False
        except (OSError, ValueError):
            pass
        finally:
            process.stdout.close()

    def _port_in_use(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=0.3):
                return True
        except OSError:
            return False

    def _probe_pool(self) -> dict[str, Any] | None:
        try:
            with self._http.open(self.pool_url, timeout=0.5) as response:
                if response.status != 200:
                    return None
                data = json.loads(response.read(65537))
            if not isinstance(data, dict):
                return None
            size = data.get("size")
            in_use = data.get("in_use")
            units = data.get("units")
            if type(size) is not int or size < 1 or type(in_use) is not int or not 0 <= in_use <= size:
                return None
            if not isinstance(units, list) or len(units) != size:
                return None
            if not all(isinstance(unit, dict) and unit.get("state") in {"idle", "active", "draining", "stuck"} for unit in units):
                return None
            return data
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            return None

    def _snapshot_locked(self) -> dict[str, Any]:
        process = self._process
        exit_code = process.poll() if process is not None else None
        running = process is not None and exit_code is None
        if process is not None and exit_code is not None and self._state not in {"stopped", "stopping", "failed"}:
            self._state = "failed"
            self._ready = False
            self._detail = self._exit_detail(exit_code)
        return {
            "state": self._state,
            "running": running,
            "ready": self._ready and running,
            "owned": process is not None,
            "pid": process.pid if process is not None else None,
            "exit_code": exit_code,
            "endpoint": self.endpoint,
            "health_url": self.pool_url,
            "pool": self._pool,
            "detail": self._detail,
            "log_tail": list(self._logs),
            "upstream_sha": UPSTREAM_SHA,
        }

    def _exit_detail(self, exit_code: int) -> str:
        # Surface a fixed, actionable message, never arbitrary provider text
        # or private transcript lines from the worker's output.
        pattern = r'HTTP Request: POST http://127\.0\.0\.1:\d+/llm/v1/chat/completions "HTTP/1\.1 402 '
        with self._lock:
            if any(re.search(pattern, line) for line in self._logs):
                return "語言模型服務回傳 HTTP 402（帳戶餘額／付款問題）；請檢查供應商帳戶或更換 API 金鑰後重試。"
            if any("HER_DRAMA_WARMUP_FAILED" in line for line in self._logs):
                return "Drama 3 語音預熱失敗或逾時；請查看本機引擎日誌後重試。"
            if any(line == "Configured startup failed; check the selected backends and runtime resources." for line in self._logs):
                return "語音引擎啟動失敗；請查看本機引擎日誌後重試。"
        return f"Engine exited with code {exit_code}; inspect log_tail."

    def _startup_failure(self) -> str | None:
        with self._lock:
            lines = list(self._logs)
        if any("HER_DRAMA_WARMUP_FAILED" in line for line in lines):
            return "Drama 3 語音預熱失敗或逾時；請查看本機引擎日誌後重試。"
        if any(line == "Configured startup failed; check the selected backends and runtime resources." for line in lines):
            detail = self._exit_detail(1)
            return detail if "HTTP 402" in detail else "語音引擎啟動失敗；請查看本機引擎日誌後重試。"
        return None

    def status(self, *, probe: bool = True) -> dict[str, Any]:
        with self._lock:
            process = self._process
            should_probe = probe and self._state in {"ready", "unhealthy"} and process is not None and process.poll() is None
        pool = self._probe_pool() if should_probe else None
        with self._lock:
            if should_probe and process is self._process and self._state in {"ready", "unhealthy"}:
                self._pool = pool
                self._ready = pool is not None
                if pool is None:
                    self._state = "unhealthy"
                    self._detail = "Engine process is running, but /v1/pool is unavailable."
                else:
                    self._state = "ready"
                    self._detail = "Engine HTTP pool endpoint is ready; a live voice test is still required."
            return self._snapshot_locked()
    def start(self, *, wait: bool = False) -> dict[str, Any]:
        """Launch once; return immediately unless ``wait=True`` is requested."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if self._state == "starting":
                    raise EngineError("Engine startup is already in progress.")
                return self._snapshot_locked()
            if self._state == "stopping":
                raise EngineError("Wait for engine shutdown before restarting.")
            if not self.config_path.is_file():
                raise EngineError("Engine configuration file does not exist.")
            env = self._child_environment()
            if self._port_in_use():
                self._state = "failed"
                self._detail = f"Port {self.port} is already in use; the existing listener was not adopted or stopped."
                raise EngineError(self._detail)
            self._stop_requested.clear()
            self._logs.clear()
            self._pool = None
            self._ready = False
            self._state = "starting"
            self._detail = "Loading speech models and warming the provider; first downloads can take several minutes."
            options: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            try:
                process = subprocess.Popen(
                    self.build_command(), cwd=self.root_dir, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    **options,
                )
            except OSError as exc:
                self._state = "failed"
                self._detail = "Could not launch the speech engine; check the Python environment."
                raise EngineError(self._detail) from exc
            self._process = process
            self._reader = threading.Thread(target=self._collect_output, args=(process,), daemon=True, name="voice-engine-log")
            self._reader.start()

        if wait:
            return self._wait_for_ready(process)
        threading.Thread(target=self._background_readiness, args=(process,), daemon=True, name="voice-engine-readiness").start()
        return self.status(probe=False)

    def _background_readiness(self, process: subprocess.Popen[str]) -> None:
        try:
            self._wait_for_ready(process)
        except EngineError:
            # Failure detail and the redacted output are exposed by status().
            pass

    def _wait_for_ready(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        deadline = time.monotonic() + self.startup_timeout
        failure = "Engine readiness timed out; inspect log_tail and model download progress."
        while not self._stop_requested.is_set() and time.monotonic() < deadline:
            with self._lock:
                if process is not self._process:
                    raise EngineError("Engine startup was superseded.")
            if process.poll() is not None:
                if self._reader is not None:
                    self._reader.join(timeout=0.5)
                failure = self._exit_detail(process.returncode)
                break
            reported_failure = self._startup_failure()
            if reported_failure:
                failure = reported_failure
                break
            pool = self._probe_pool()
            with self._lock:
                if pool is not None and process is self._process and process.poll() is None and not self._stop_requested.is_set():
                    self._ready = True
                    self._pool = pool
                    self._state = "ready"
                    self._detail = "Engine HTTP pool endpoint is ready; a live voice test is still required."
                    return self._snapshot_locked()
            self._stop_requested.wait(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
        if self._stop_requested.is_set():
            raise EngineError("Engine startup was stopped.")
        self.stop(_expected=process, _failure=failure)
        raise EngineError(failure)

    def stop(self, *, _expected: subprocess.Popen[str] | None = None, _failure: str | None = None) -> dict[str, Any]:
        with self._lock:
            if _expected is not None and _expected is not self._process:
                return self._snapshot_locked()
            self._stop_requested.set()
            process = self._process
            reader = self._reader
            self._ready = False
            self._state = "stopping"
        if process is not None and process.poll() is None:
            deadline = time.monotonic() + self.stop_timeout
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.send_signal(signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=max(0.05, (deadline - time.monotonic()) * 0.5))
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=max(0.05, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process.wait(timeout=2.0)
                    except (OSError, ProcessLookupError):
                        pass
                except (OSError, ProcessLookupError):
                    pass
        if reader is not None:
            reader.join(timeout=0.5)
        with self._lock:
            if process is self._process:
                self._process = None
                self._reader = None
                self._pool = None
                self._state = "failed" if _failure else "stopped"
                self._detail = _failure or ("Owned engine process stopped." if process is not None else "No owned engine process is running.")
            return self._snapshot_locked()


def _worker_main() -> None:
    """Stage pinned weights, retain Windows DLL handles, then run upstream CLI."""
    if sys.argv[1:2] != ["--worker"]:
        raise SystemExit("This module is started by the local gateway.")
    worker_started = time.perf_counter()
    handles = []
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for folder in cuda_dll_directories(dict(os.environ), sys.executable):
            handles.append(os.add_dll_directory(folder))
    sys.argv = ["speech-to-speech", *sys.argv[2:]]
    try:
        from her_voice.upstream_patch import verify_installed_silero_patch

        # Refuse stale/unreviewed VAD code before downloading models or
        # importing the upstream package. Installation applies the patch.
        verify_installed_silero_patch()
        from her_voice.startup_patch import verify_installed_startup_patch
        verify_installed_startup_patch()
        # Import lazily: loading the UI/doctor/supervisor must not download
        # weights or require inference dependencies. Only the live worker does.
        from her_voice.models import ensure_models

        backend = os.environ.get("VOICE_TTS_BACKEND", "qwen3")
        if backend == "drama3":
            from her_voice.drama_resampler import install_drama_resampler
            install_drama_resampler()
            print(f"HER_STARTUP phase=tts_import_ready elapsed_s={time.perf_counter() - worker_started:.3f}", flush=True)
            from her_voice.drama_warmup import install_drama_warmup
            install_drama_warmup(backend=backend)
            from her_voice.drama_voice import install_drama_voice
            install_drama_voice(backend=backend)
            from her_voice.drama_language import install_drama_language_path
            install_drama_language_path(backend=backend)
            print(f"HER_STARTUP phase=language_hook_ready elapsed_s={time.perf_counter() - worker_started:.3f}", flush=True)
        from her_voice.sentence_stream import install_sentence_streaming
        install_sentence_streaming()
        from her_voice.latency import install_latency_metadata
        install_latency_metadata()
        print(f"HER_STARTUP phase=compatibility_ready elapsed_s={time.perf_counter() - worker_started:.3f}", flush=True)
        os.environ.update(ensure_models(tts_backend=backend))
        print(f"HER_STARTUP phase=model_paths_ready elapsed_s={time.perf_counter() - worker_started:.3f}", flush=True)
        runpy.run_module("speech_to_speech.cli", run_name="__main__")
    finally:
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    _worker_main()
