"""Locate immutable shipped assets separately from the application's workspace."""
from __future__ import annotations

import os
from pathlib import Path


def default_asset_root() -> Path:
    packaged = Path(__file__).resolve().parent / "_assets"
    if (packaged / "web" / "index.html").is_file():
        return packaged
    source = Path(__file__).resolve().parents[1]
    if (source / "web" / "index.html").is_file() and (source / "config").is_dir():
        return source
    raise RuntimeError("Voice package assets are missing; reinstall the complete package.")


def default_workspace() -> Path:
    configured = os.environ.get("VOICE_WORKSPACE")
    if configured:
        return Path(configured).expanduser().resolve()
    assets = default_asset_root()
    return Path.cwd().resolve() if assets.name == "_assets" else assets
