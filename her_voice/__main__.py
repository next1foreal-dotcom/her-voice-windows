from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import threading
import webbrowser
from aiohttp import web

from .app import create_app
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Reusable local speech runtime with a standalone voice interface")
    parser.add_argument("--demo", action="store_true", help="Protocol exercise only: no models, GPU, ASR, or provider calls")
    parser.add_argument("--open", action="store_true", help="Open the local interface in your default browser")
    parser.add_argument("--engine-log", action="store_true", help="Print redacted engine diagnostics locally (may include conversation text)")
    parser.add_argument("--workspace", type=Path, help="Application workspace containing .env and .venv-speech")
    parser.add_argument("--app-id", help="Application routing label; not authentication or a tenant boundary")
    parser.add_argument("--app-name", help="Display name for the standalone interface")
    parser.add_argument("--port", type=int, help="Loopback web and private gateway port")
    parser.add_argument("--engine-port", type=int, help="Owned loopback speech-engine port")
    parser.add_argument("--profile", type=Path, help="Speech profile YAML (pipeline name: windows4080)")
    args = parser.parse_args()
    try:
        options = {"demo_only": args.demo}
        if args.workspace is not None:
            options["root"] = args.workspace
        settings = Settings.load(**options)
        overrides = {"echo_engine_logs": args.engine_log}
        for argument, field in (("app_id", "app_id"), ("app_name", "app_name"), ("port", "port"), ("engine_port", "engine_port"), ("profile", "profile_path")):
            value = getattr(args, argument)
            if value is not None:
                overrides[field] = value
        if args.engine_port is not None:
            overrides["engine_url"] = None
        settings = replace(settings, **overrides)
    except ValueError as exc:
        parser.error(str(exc))
    address = f"http://{settings.host}:{settings.port}"
    print(f"{settings.app_name} — {address}")
    print("Mode: protocol exercise (test tones, no ASR/TTS/API)" if args.demo else f"Mode: local speech + {settings.provider}; click Start in the browser to load models")
    if args.open:
        opener = threading.Timer(1.2, lambda: webbrowser.open(address))
        opener.daemon = True
        opener.start()
    # Access logs are disabled so URLs containing session tokens are not logged.
    web.run_app(create_app(settings), host=settings.host, port=settings.port, access_log=None, handler_cancellation=True)


if __name__ == "__main__":
    main()
