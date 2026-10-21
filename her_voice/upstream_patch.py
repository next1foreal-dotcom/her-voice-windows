"""One explicit, hash-guarded Silero revision patch in this project's venv.

This module never imports the speech package or torch, and never downloads
anything. Installation applies the patch; each live worker only verifies it.
"""

from __future__ import annotations

import hashlib
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .resources import default_workspace


UPSTREAM_SHA = "0ed1bb95bd335aa89bb7c172b6a347b6dae596f8"
SILERO_SHA = "1e261b036686cd0017d500ee96acd1c4ba572a9d"
ORIGINAL_SHA256 = "d6feeb9f4442240c9a493ac86ec076033882c8efece6020d2ec7cfbfebd70b7c"
PATCHED_SHA256 = "2613d149b238b4c50b9ff2ae9da3f22ff6db527d9a2efac40fb9dfcc764db7f1"
_OLD = b"snakers4/silero-vad:master"
_NEW = f"snakers4/silero-vad:{SILERO_SHA}".encode("ascii")
_REPOSITORY_URL = "https://github.com/huggingface/speech-to-speech"
_ARCHIVE_URL = f"{_REPOSITORY_URL}/archive/{UPSTREAM_SHA}.zip"
_TARGET = "speech_to_speech/VAD/vad_handler.py"


class UpstreamPatchError(RuntimeError):
    """The installed package is not the exact, supported patched source."""


def _expected_environment() -> Path:
    return default_workspace() / ".venv-speech"


def _installed_handler() -> Path:
    prefix = Path(sys.prefix).resolve()
    if prefix != _expected_environment().resolve():
        raise UpstreamPatchError("Use this project's .venv-speech Python for the speech engine and Silero patch.")
    try:
        distribution = metadata.distribution("speech-to-speech")
    except metadata.PackageNotFoundError as exc:
        raise UpstreamPatchError("Pinned speech-to-speech is not installed; run scripts/install-windows.ps1.") from exc
    if distribution.version != "1.0.0":
        raise UpstreamPatchError("Unsupported speech-to-speech version; reinstall the pinned requirements.")
    try:
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    except (OSError, ValueError, TypeError) as exc:
        raise UpstreamPatchError("Cannot verify the speech-to-speech installation source.") from exc
    if not isinstance(direct, dict):
        raise UpstreamPatchError("Cannot verify the speech-to-speech installation source.")
    vcs = direct.get("vcs_info", {})
    from_archive = direct.get("url") == _ARCHIVE_URL and isinstance(direct.get("archive_info"), dict)
    from_vcs = (
        direct.get("url") in (_REPOSITORY_URL, _REPOSITORY_URL + ".git")
        and isinstance(vcs, dict) and vcs.get("vcs") == "git"
        and vcs.get("commit_id") == UPSTREAM_SHA
    )
    if not (from_archive or from_vcs):
        raise UpstreamPatchError("speech-to-speech must come from the documented fixed commit/archive.")
    path = Path(distribution.locate_file(_TARGET)).resolve()
    if not path.is_relative_to(prefix) or not path.is_file():
        raise UpstreamPatchError("The VAD source is missing or outside the isolated .venv-speech environment.")
    return path


def _read_source(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise UpstreamPatchError("Could not read the installed VAD source.") from exc


def _result(path: Path, state: str) -> dict[str, str]:
    return {
        "state": state,
        "path": str(path),
        "sha256": PATCHED_SHA256,
        "silero_revision": SILERO_SHA,
        "upstream_revision": UPSTREAM_SHA,
    }


def _verify_file(path: Path) -> dict[str, str]:
    digest = hashlib.sha256(_read_source(path)).hexdigest()
    if digest == ORIGINAL_SHA256:
        raise UpstreamPatchError("Silero is still unpinned; run .venv-speech Python with scripts/pin-silero.py.")
    if digest != PATCHED_SHA256:
        raise UpstreamPatchError("Unknown VAD source hash; reinstall the fixed upstream package and reapply the documented patch.")
    return _result(path, "verified")


def _patch_file(path: Path) -> dict[str, str]:
    original = _read_source(path)
    digest = hashlib.sha256(original).hexdigest()
    if digest == PATCHED_SHA256:
        return _result(path, "already_patched")
    if digest != ORIGINAL_SHA256:
        raise UpstreamPatchError("Unknown VAD source hash; no files were changed.")
    if original.count(_OLD) != 1:
        raise UpstreamPatchError("Expected exactly one Silero master reference; no files were changed.")
    patched = original.replace(_OLD, _NEW, 1)
    if hashlib.sha256(patched).hexdigest() != PATCHED_SHA256:
        raise UpstreamPatchError("Patch output hash differs from the reviewed source; no files were changed.")
    temporary: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".her-silero-", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if _read_source(path) != original:
            raise UpstreamPatchError("VAD source changed during installation; no patch was applied.")
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise UpstreamPatchError("Could not write the isolated VAD patch; stop the engine and retry installation.") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _verify_file(path)
    return _result(path, "patched")


def apply_installed_silero_patch() -> dict[str, str]:
    """Patch only the installed, pinned source in this project's .venv-speech."""
    return _patch_file(_installed_handler())


def verify_installed_silero_patch() -> dict[str, str]:
    """Read-only startup gate. An unpatched or unknown package fails closed."""
    return _verify_file(_installed_handler())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply or verify the documented fixed Silero source patch.")
    parser.add_argument("--check", action="store_true", help="Verify without modifying the installed package.")
    parser.add_argument("--workspace", type=Path, help="Workspace whose .venv-speech is the current interpreter.")
    args = parser.parse_args(argv)
    previous_workspace = os.environ.get("VOICE_WORKSPACE")
    try:
        if args.workspace is not None:
            os.environ["VOICE_WORKSPACE"] = str(args.workspace.resolve())
        result = verify_installed_silero_patch() if args.check else apply_installed_silero_patch()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except UpstreamPatchError as error:
        print(f"Silero pin failed: {error}", file=sys.stderr)
        return 1
    finally:
        if args.workspace is not None:
            if previous_workspace is None:
                os.environ.pop("VOICE_WORKSPACE", None)
            else:
                os.environ["VOICE_WORKSPACE"] = previous_workspace


if __name__ == "__main__":
    raise SystemExit(main())
