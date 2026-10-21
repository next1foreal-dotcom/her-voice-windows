#!/usr/bin/env python3
"""List fixed local models; --download explicitly stages their weights."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from her_voice.models import ModelDownloadError, ensure_models, model_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true",
                        help="Download/cache the pinned ASR, TTS and SmartTurn models (about 3 GB). No inference or API request.")
    parser.add_argument("--backend", choices=("drama3", "qwen3"), default="qwen3",
                        help="drama3 stages ASR/SmartTurn only; install-drama.ps1 prepares its GGUF separately.")
    args = parser.parse_args(argv)
    report = {
        "models": model_manifest(tts_backend=args.backend),
        "backend": args.backend,
        "scope": "Fixed HF model content only. Silero is pinned separately by scripts/pin-silero.py; its Torch Hub archive loads at engine startup. No DeepSeek request or GPU inference.",
    }
    if not args.download:
        report.update(mode="manifest", staged=False,
                      next_step="Add --download to fetch the fixed HF revisions. The live engine also stages them before startup.")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    try:
        paths = ensure_models(tts_backend=args.backend)
    except ModelDownloadError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    report.update(mode="download", staged=True, paths=paths,
                  note="Paths are ready for the engine. Model inference and Windows audio still need a live check.")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
