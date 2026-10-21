"""Keep source assets editable, and include the same files in built wheels."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithAssets(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        for directory in ("web", "config"):
            for source in sorted((root / directory).rglob("*")):
                if not source.is_file() or any(part.startswith(".") for part in source.relative_to(root).parts):
                    continue
                destination = Path(self.build_lib) / "her_voice" / "_assets" / source.relative_to(root)
                self.mkpath(str(destination.parent))
                self.copy_file(str(source), str(destination))


setup(cmdclass={"build_py": BuildWithAssets})
