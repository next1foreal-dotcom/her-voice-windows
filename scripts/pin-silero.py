"""Apply the documented one-literal upstream patch; no network/model calls."""

from __future__ import annotations

import argparse
import json
import sys

from her_voice.upstream_patch import (
    UpstreamPatchError,
    apply_installed_silero_patch,
    verify_installed_silero_patch,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the installed patch without changing files.")
    options = parser.parse_args()
    try:
        result = verify_installed_silero_patch() if options.check else apply_installed_silero_patch()
    except UpstreamPatchError as exc:
        print(f"Silero pin failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
