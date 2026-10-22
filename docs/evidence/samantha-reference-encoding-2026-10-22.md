# Samantha voice identity integration — 2026-10-22

User acceptance: both encoder-parity-english.wav and encoder-parity-chinese.wav sound like the original Samantha. Earlier Chinese language-hint-only output was closer but rejected.

## Change and bounds

The selected voice now reuses the accepted full-reference code grid (601 frames x10 books) from the official encoder/quantizer algorithm running on the same converted GGUF codec weights. This is not a replacement model and not an independent original-codec.pth equivalence claim. The original 27.873-second WAV, transcript, LLM Prompt, Drama model/executable, DeepSeek and Nuroum selection are retained.

A local per-voice cache binds codes to the WAV SHA256, transcript SHA256, voice ID and pinned model SHA256, checks complete frame count and code ranges, and verifies the code checksum. Invalid present caches fail visibly; absent caches retain existing behavior for other voices. The accepted cache uses temperature1/top_p0.9/top_k30/seed0. It is private under voices/ and not a Git artifact.

Affected callers: DramaSupervisor.prepare_voice builds one code-only native preset; prepare_reference warms the decoder with those same codes. The speech worker installs a source-verified TTS payload hook for this cached voice. Chinese TTS input receives the tested language hint; dialogue text and exact LLM Prompt remain unchanged. Existing transport, streaming resampler and cancellation implementation are retained. No new GPU inference package is imported by the web process.

The native speech endpoint automatically adds the selected preset's reference. Keeping raw reference audio while also adding codes would duplicate conditioning. The code-only preset prevents this and was checked through the real endpoint.

## Verified live evidence

Raw evidence is in work/voice-drift-20261022/:
- official-encoder-parity/official-encoder-audit.json: actual reference algorithm probe; native whole differs1167/6010 codes, native chunked1231/6010. This isolates an effective encoding-path difference, not one specific arithmetic operation as root cause.
- encoder-parity-comparison.json, encoder-parity-transcript-audit.json: auditioned samples and complete words.
- live-integration-activation.json: native46352, frontend13056, speech30684, worker63136; ready. Original reference, executable and dialogue profile hashes unchanged. Temporary no-paid LLM warmup hook restored byte exact. No diagnostic provider calls.
- live-reference-acceptance.json: real /v1/audio/speech preset and actual pinned upstream payload/HTTP/resampler bodies.

| Check | Result |
|---|---|
| English formal preset | PCM exactly equals user-approved sample |
| Chinese formal preset | PCM exactly equals user-approved sample |
| Chinese streaming,112 source chunks | All229376 source samples exactly equal approved PCM |
| Cancellation | Next iterator read raises cancellation; no late audio; local4.009ms |
| Chinese after cancellation | PCM exactly equals approved sample |
| Ready after checks | true; native PID unchanged |

English PCM SHA256:45c89ec85505393af09215411c29f9230cd7262dd7b599752249cc5d57b34bb0.
Chinese PCM SHA256:05f92498a99e77ea8fba3e57a78532332cd0204eac20a1a22b673d266fb4d01b.

Native TTS request -> first source PCM1781.002ms, n=1, known text. Not mic-end -> speaker onset, not a distribution and not proof of500/1000ms response. Owned service restart ->ready320.875s, n=1, OS caches uncontrolled; not a cold-start optimization result. Arbitrary new text/multiturn identity remains a real-use acceptance question; the exact approved pair and repeat are established.

Regression:8 reference/hint tests and12 runtime tests pass. Streaming suite:10 checks passed initially;1 first-use DSP cold-load test timed out under its5s fixture deadline. The identical failing test passed after explicitly warming the resampler, with original deadline unchanged. All11 streaming checks thus covered successfully, not a claim of one clean initial suite run.

## Environment repair and recovery

Before restart, project package junctions pointed to missing/incomplete SSD cache contents (including NumPy/Torch). Restored15 project-local links to their existing complete .runtime/ssd-python-packages-original-20261022 packages; no large download/copy or cache deletion. Original junctions retained in integration-before/, old/new targets recorded in dependency-link-repair.json. The initial frontend restart used the base Python and failed missing aiohttp; corrected to .venv-speech. Raw initial error retained. Current service startup and live acceptance succeeded.

Recovery copies: work/voice-drift-20261022/integration-before/ contains drama.py, engine.py, prior derived configs and original dependency junctions. To revert voice behavior, stop this app's engines, move the selected *.drama-reference.json cache out of voices/, then restart; uncached preset path is retained and tested. Do not restore incomplete dependency links as part of a voice rollback. No Git commit, push, packaging or deployment performed.