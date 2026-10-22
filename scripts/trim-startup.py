"""Apply the reviewed unused-bootstrap patch in the isolated speech environment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from her_voice.startup_patch import main
if __name__ == "__main__":
    raise SystemExit(main())
