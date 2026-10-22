"""Remove unused NLTK bootstrap from one reviewed pipeline; no package import."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .upstream_patch import _installed_handler, UpstreamPatchError

ORIGINAL_SHA256 = "2b3fed85d07a2efbb4b705c6604f6baa1d8ddd4d643cb0e4e1e6e79814d10c47"
PATCHED_SHA256 = "77735f305eb134726252eb7f3df704f0e1e33fbe703e5b0b57958de17374a591"
BOOTSTRAP = '''# Ensure that the necessary NLTK resources are available
try:
    nltk.data.find("tokenizers/punkt_tab")
except (LookupError, OSError):
    nltk.download("punkt_tab")
try:
    nltk.data.find("taggers/averaged_perceptron_tagger_eng")
except (LookupError, OSError):
    nltk.download("averaged_perceptron_tagger_eng")'''


def transform(source: bytes) -> bytes:
    source = source.replace(b"\r\n", b"\n")
    digest = hashlib.sha256(source).hexdigest()
    if digest == PATCHED_SHA256:
        return source
    if digest != ORIGINAL_SHA256:
        raise UpstreamPatchError("Unknown pipeline source; startup optimization refused.")
    text = source.decode("utf-8")
    if text.count("import nltk\n") != 1 or text.count(BOOTSTRAP) != 1:
        raise UpstreamPatchError("Pipeline bootstrap anchors changed; no patch applied.")
    patched = text.replace("import nltk\n", "").replace(BOOTSTRAP,
        "# This pinned pipeline does not use NLTK; no tokenizer/tagger bootstrap is needed.\n").encode()
    if hashlib.sha256(patched).hexdigest() != PATCHED_SHA256:
        raise UpstreamPatchError("Unexpected pipeline patch output; no patch applied.")
    return patched


def installed_pipeline() -> Path:
    # Reuse the pinned distribution, isolated environment and source provenance gate.
    return _installed_handler().parents[1] / "s2s_pipeline.py"


def verify_installed_startup_patch() -> dict:
    path = installed_pipeline()
    source = path.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(source).hexdigest() != PATCHED_SHA256:
        raise UpstreamPatchError("Run scripts/trim-startup.py with .venv-speech Python before starting.")
    return {"state": "verified", "path": str(path), "sha256": PATCHED_SHA256}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        path = installed_pipeline()
        if not args.check:
            original = path.read_bytes()
            patched = transform(original)
            if original != patched:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".her-startup-", delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(patched)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if path.read_bytes() != original:
                        raise UpstreamPatchError("Pipeline changed during preparation; no patch applied.")
                    os.replace(temporary, path)
                    temporary = None
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        print(json.dumps(verify_installed_startup_patch()))
        return 0
    except (OSError, UpstreamPatchError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
