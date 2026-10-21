"""Stage reviewed model revisions before starting the speech engine.

Importing this module is stdlib-only. Downloading is explicit: ``ensure_models``
uses the normal Hugging Face cache, exact commit IDs, and public repositories.
It does not load a model, import torch, or make an LLM request.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ModelDownloadError(RuntimeError):
    """A fixed model revision could not be staged completely."""


@dataclass(frozen=True)
class SnapshotSpec:
    repo_id: str
    revision: str
    environment_key: str
    required_files: tuple[str, ...]


ASR = SnapshotSpec(
    repo_id="Systran/faster-whisper-small",
    revision="536b0662742c02347bc0e980a01041f333bce120",
    environment_key="HER_ASR_MODEL_PATH",
    required_files=("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
)
TTS = SnapshotSpec(
    repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    revision="5d83992436eae1d760afd27aff78a71d676296fc",
    environment_key="HER_TTS_MODEL_PATH",
    required_files=(
        "config.json", "generation_config.json", "model.safetensors",
        "merges.txt", "vocab.json", "tokenizer_config.json", "preprocessor_config.json",
        "speech_tokenizer/config.json", "speech_tokenizer/configuration.json",
        "speech_tokenizer/model.safetensors", "speech_tokenizer/preprocessor_config.json",
    ),
)
SNAPSHOTS = (ASR, TTS)
SMART_TURN_REPO = "pipecat-ai/smart-turn-v3"
SMART_TURN_REVISION = "f766f81d3cfdf7737ac64aad813d91bbfd56bf93"
SMART_TURN_FILENAME = "smart-turn-v3.2-cpu.onnx"
SMART_TURN_ENVIRONMENT_KEY = "HER_SMART_TURN_MODEL_PATH"


def model_manifest(*, tts_backend: str = "qwen3") -> list[dict]:
    """Public metadata; calling this never inspects credentials or the network."""
    manifest = [
        {
            "repository": spec.repo_id,
            "revision": spec.revision,
            "download": "complete snapshot",
            "environment_key": spec.environment_key,
            "required_files": list(spec.required_files),
            "source": f"https://huggingface.co/{spec.repo_id}/commit/{spec.revision}",
        }
        for spec in (SNAPSHOTS if tts_backend == "qwen3" else (ASR,))
    ]
    manifest.append({
        "repository": SMART_TURN_REPO,
        "revision": SMART_TURN_REVISION,
        "download": SMART_TURN_FILENAME,
        "environment_key": SMART_TURN_ENVIRONMENT_KEY,
        "required_files": [SMART_TURN_FILENAME],
        "source": f"https://huggingface.co/{SMART_TURN_REPO}/commit/{SMART_TURN_REVISION}",
    })
    return manifest


def _snapshot_path(raw: str, repo_id: str, revision: str) -> Path:
    # No local_dir argument is used. The Hub cache therefore retains the
    # repository name and immutable revision in .../snapshots/<commit>/.
    # Keeping this path (instead of resolving individual blob symlinks) also
    # preserves the nested speech_tokenizer directory used by Qwen's loader.
    path = Path(raw).absolute()
    if not path.is_dir() or path.name != revision or path.parent.name != "snapshots":
        raise ModelDownloadError(f"{repo_id}: Hub returned an unexpected snapshot path; refusing a mutable model reference.")
    return path


def _require_files(path: Path, repo_id: str, filenames: tuple[str, ...]) -> None:
    for filename in filenames:
        candidate = path / filename
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise ModelDownloadError(f"{repo_id}: fixed snapshot is incomplete; missing or empty {filename}.")


def _cached_or_download(download, **options) -> str:
    # An immutable, already staged revision needs no remote metadata request.
    # Let the Hub fill missing cache entries only when the local lookup fails.
    try:
        return download(**options, local_files_only=True)
    except Exception:
        return download(**options)


def ensure_models(*, tts_backend: str = "qwen3") -> dict[str, str]:
    """Return local model paths after staging exact revisions in the HF cache.

    Each selected snapshot is complete, including Qwen's speech_tokenizer
    weights in qwen3 mode. Drama mode stages only ASR and Smart Turn here;
    its native runtime/model is prepared separately by install-drama.ps1.
    Existing cache entries are reused. Standard Hub cache / offline / proxy
    settings still apply; no proxy settings or global environment are changed.
    Public models do not need a Hugging Face token. Exceptions omit raw network
    errors, which may contain proxy credentials or other local configuration.

    This pins requested model content; it does not checksum a cache that a user
    has manually modified, prove deterministic GPU output, or freeze DeepSeek.
    """
    if tts_backend not in {"qwen3", "drama3"}:
        raise ModelDownloadError("Unsupported TTS backend; choose drama3 or qwen3.")
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError:
        raise ModelDownloadError("huggingface_hub is unavailable. Run the Windows package installer first.") from None

    paths: dict[str, str] = {}
    for spec in (SNAPSHOTS if tts_backend == "qwen3" else (ASR,)):
        try:
            # Do not filter this snapshot: Qwen's tokenizer is a second weight
            # file in a child directory, not merely text tokenization metadata.
            raw = _cached_or_download(snapshot_download, repo_id=spec.repo_id, revision=spec.revision, token=False)
            path = _snapshot_path(raw, spec.repo_id, spec.revision)
            _require_files(path, spec.repo_id, spec.required_files)
        except ModelDownloadError:
            raise
        except Exception as error:
            raise ModelDownloadError(f"{spec.repo_id}: fixed-revision staging failed ({type(error).__name__}). Check connectivity, cache permissions, and available disk space.") from None
        paths[spec.environment_key] = str(path)

    try:
        raw = _cached_or_download(hf_hub_download, repo_id=SMART_TURN_REPO, revision=SMART_TURN_REVISION,
                              filename=SMART_TURN_FILENAME, token=False)
        file_path = Path(raw).absolute()
        path = _snapshot_path(str(file_path.parent), SMART_TURN_REPO, SMART_TURN_REVISION)
        if file_path.name != SMART_TURN_FILENAME:
            raise ModelDownloadError(f"{SMART_TURN_REPO}: Hub returned an unexpected model filename.")
        _require_files(path, SMART_TURN_REPO, (SMART_TURN_FILENAME,))
    except ModelDownloadError:
        raise
    except Exception as error:
        raise ModelDownloadError(f"{SMART_TURN_REPO}: fixed-revision staging failed ({type(error).__name__}). Check connectivity, cache permissions, and available disk space.") from None
    paths[SMART_TURN_ENVIRONMENT_KEY] = str(file_path)
    return paths
