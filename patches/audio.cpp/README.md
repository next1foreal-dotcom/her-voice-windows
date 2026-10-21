# audio.cpp Windows cold-read patch

Base: 0xShug0/audio.cpp commit `0bf4d155f2af5f85853415170622dc145e3d62c3`.

This patch preserves the installed native changes outside the ignored local runtime: deferred pinned expert-bank loading in file-offset order, Windows sequential reads directly into pinned buffers, CUDA fixture coverage, and the MSVC test-target exception setting. It does not include models, binaries or local credentials.

Apply once to a clean checkout at the pinned revision, before building:

```powershell
git -C <audio.cpp-checkout> apply --check <absolute-path-to-windows-pinned-sequential-read.patch>
git -C <audio.cpp-checkout> apply <absolute-path-to-windows-pinned-sequential-read.patch>
```

The existing installer does not automatically apply this patch. The checked-in patch was verified with `git apply --reverse --check` against the running local source. The previously built CUDA fixture passed all six modes; live native inference was verified. See `docs/PATCHES.md` for measurements and limitations. No model rebuild was performed for the initial Git delivery.

Upstream-derived code retains the upstream Apache-2.0 license, copied as `LICENSE.upstream`; the root project license does not replace it. The optional C-drive Torch library cache is a machine-local setup, not a repository dependency or committed cache.
