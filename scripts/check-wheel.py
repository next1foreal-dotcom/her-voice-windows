"""Build/install a wheel offline and serve its bundled assets from a new cwd.

Requires existing pip/setuptools/wheel and aiohttp. This checks package
resources and demo HTTP routes, not speech dependencies or Windows CUDA.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SMOKE = r'''
import asyncio, json, sys
from pathlib import Path
import her_voice
from aiohttp.test_utils import TestClient, TestServer
from her_voice.app import create_app
from her_voice.settings import Settings
from her_voice.resources import default_workspace
assert Path(her_voice.__file__).is_relative_to(Path(sys.argv[1]))
assert default_workspace() == Path.cwd()
settings = Settings(demo_only=True, app_id="installed-wheel", app_name="Installed wheel")
assert "_assets" in settings.web_root.parts
assert settings.profile_path.is_file()
assert (settings.asset_root / "config" / "upstream-lock.json").is_file()
async def verify():
    client = TestClient(TestServer(create_app(settings)))
    await client.start_server()
    try:
        for route in ["/", "/client.js", "/audio.js", "/capture-worklet.js", "/styles.css"]:
            response = await client.get(route)
            assert response.status == 200, route
            assert len(await response.read()) > 100, route
        response = await client.get("/api/bootstrap")
        result = await response.json()
        assert result["health"]["app_id"] == "installed-wheel"
        assert result["health"]["demo_only"] is True
        print(json.dumps({"result":"PASS", "version":her_voice.__version__,
          "scope":"Installed wheel resources, fresh cwd, local demo HTTP routes; no speech models/GPU/API",
          "routes":5, "assets":"packaged _assets", "python":sys.version.split()[0]}))
    finally:
        await client.close()
asyncio.run(verify())
'''


def checked(arguments, *, cwd=ROOT, env=None):
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def main():
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wheel-check-", dir=runtime) as temporary:
        directory = Path(temporary)
        wheels, site, work = directory / "dist", directory / "site", directory / "work"
        work.mkdir()
        checked([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "--wheel-dir", str(wheels), str(ROOT)])
        wheel = next(wheels.glob("her_windows_voice-*.whl"))
        checked([sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--target", str(site), str(wheel)])
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("VOICE_") or name in {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"}:
                env.pop(name)
        env["PYTHONPATH"] = str(site)
        report = json.loads(checked([sys.executable, "-P", "-c", SMOKE, str(site)], cwd=work, env=env))
        help_text = checked([sys.executable, "-P", "-m", "her_voice", "--help"], cwd=work, env=env)
        assert "--workspace" in help_text and "--engine-port" in help_text
        pin_help = checked([sys.executable, "-P", "-m", "her_voice.upstream_patch", "--help"], cwd=work, env=env)
        assert "--check" in pin_help and "--workspace" in pin_help
        destination = ROOT / "dist"
        destination.mkdir(exist_ok=True)
        shutil.copy2(wheel, destination / wheel.name)
        report["wheel"] = wheel.name
        report["dependencies"] = "not resolved; --no-deps uses existing aiohttp"
        (runtime / "wheel-check.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
