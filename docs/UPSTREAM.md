# 0.4 upstream integration record

This prototype combines the pinned Hugging Face speech-to-speech engine, a local gateway and a browser client. VAD, ASR and TTS run locally; the gateway sends text to DeepSeek or the selected host Agent. Version 0.4 uses **Drama 3 as the primary TTS and Qwen3-TTS 0.6B Base as the manually selected fallback**. Only the selected TTS runs. JOSIE's public interaction design is a reference; no private JOSIE application source is used.

The implementation and metadata contracts have been checked without loading real speech weights. Native Windows compilation, CUDA inference, hardware memory peaks, microphones, speaker output and paid provider requests have not been validated in this delivery environment.

## Fixed source baseline

| Component | Fixed source / revision | Selection |
|---|---|---|
| speech-to-speech | [`0ed1bb95bd335aa89bb7c172b6a347b6dae596f8`](https://github.com/huggingface/speech-to-speech/commit/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8), version 1.0.0, commit timestamp 2026-10-19 18:13:59 UTC | `serve --file ...yaml --name windows4080`; GA Realtime schema. |
| Native Drama runtime | [`0xShug0/audio.cpp@0bf4d155f2af5f85853415170622dc145e3d62c3`](https://github.com/0xShug0/audio.cpp/tree/0bf4d155f2af5f85853415170622dc145e3d62c3) | Windows CUDA build, `fish_drama3`, `audiocpp_server`, CUDA architecture 89, CPU AVX2. |
| ASR | [`Systran/faster-whisper-small@536b0662742c02347bc0e980a01041f333bce120`](https://huggingface.co/Systran/faster-whisper-small/commit/536b0662742c02347bc0e980a01041f333bce120) | Complete multilingual snapshot via `HER_ASR_MODEL_PATH`. |
| Drama TTS | [`Jmica/Drama-3-GGUF@17a5b40c5dc2c2be986f5371a7e650108b828e29`](https://huggingface.co/Jmica/Drama-3-GGUF/tree/17a5b40c5dc2c2be986f5371a7e650108b828e29) | `drama-3-backbone-8bit-experts-Q4_K_S.gguf`, 24,596,497,888 bytes. |
| Qwen TTS | [`Qwen/Qwen3-TTS-12Hz-0.6B-Base@5d83992436eae1d760afd27aff78a71d676296fc`](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base/commit/5d83992436eae1d760afd27aff78a71d676296fc) | Complete snapshot including `speech_tokenizer/`, via `HER_TTS_MODEL_PATH`. |
| SmartTurn | [`pipecat-ai/smart-turn-v3@f766f81d3cfdf7737ac64aad813d91bbfd56bf93`](https://huggingface.co/pipecat-ai/smart-turn-v3/commit/f766f81d3cfdf7737ac64aad813d91bbfd56bf93) | `smart-turn-v3.2-cpu.onnx` via `HER_SMART_TURN_MODEL_PATH`. |
| Silero | [`snakers4/silero-vad@1e261b036686cd0017d500ee96acd1c4ba572a9d`](https://github.com/snakers4/silero-vad/commit/1e261b036686cd0017d500ee96acd1c4ba572a9d) | Explicit Torch Hub commit in the [documented installed-package patch](PATCHES.md). |
| Streaming resampler | [`soxr==1.0.0`](https://pypi.org/project/soxr/1.0.0/) | Stateful mono Drama conversion from 44,100 to 16,000 Hz. |

The Drama GGUF SHA-256 is `05af716baf78e7c20863b9ccebb3ebdeca9607dd3ea7d0b72c35079e675b212b`. The installer validates both size and complete SHA-256 before publishing its runtime manifest. Fixed code/model revisions, dependency constraints and the original Silero patch hashes are recorded in [upstream-lock.json](../config/upstream-lock.json). They do not freeze the full Windows transitive environment, compiler, driver or remote LLM implementation. Preserve `.runtime/pip-freeze-windows.txt` and the native build receipt after successful target-machine installation.

The worker calls `ensure_models(tts_backend=...)` only during live startup. Drama mode stages ASR and SmartTurn; Qwen mode also stages its complete Base snapshot. The native GGUF is prepared separately by `install-drama.ps1` / `prepare-drama.py`. HF calls use immutable revisions and `token=False`. Opening the UI, running doctor and ordinary unit tests do not download weights. Model staging itself does not perform inference.

## Process and configuration contract

`her_voice.tts.VoiceEngine` coordinates `EngineSupervisor` and `DramaSupervisor`. An imported reference voice is required. Applying a backend or voice selection ends the current session and stops the owned speech/native processes before a new start. A failure is reported for the selected backend; fallback selection is explicit.

`EngineSupervisor` starts one owned Python child and uses `/v1/pool` readiness. `DramaSupervisor` starts its owned executable from the verified installation manifest and checks `/health` plus the `drama-3` registration in `/v1/models`. Existing listeners are refused, never adopted or stopped. HTTP readiness is a service condition; successful model loading and audible inference still require the live acceptance tests.

The controller derives `.runtime/selected-voice-pipeline.yaml` from `config/windows-4080.yaml`. Its JSON-compatible mapping is valid YAML. The `.yaml` suffix is required: the pinned `load_config` rejects `.json` in file mode. Field names come from the checked-in argument dataclasses and [upstream schema](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/docs/configuration.md).

The speech child runs:

```powershell
python -m her_voice.engine --worker serve --file .runtime/selected-voice-pipeline.yaml --name windows4080
```

The worker verifies the installed Silero patch, installs the Drama-only resampling hook when selected, stages the appropriate snapshots and forwards the CLI arguments. It runs inside `.venv-speech`. Windows DLL-directory handles remain alive for the worker and include existing torch, CUDA, optional `HER_VOICE_CUDA_DLL_DIR` and NVIDIA runtime directories; doctor separately verifies their actual loadability.

| Component | Profile | Boundary |
|---|---|---|
| VAD / turn completion | Silero; SmartTurn v3.2 CPU ONNX | Retains upstream speculative-turn behavior; actual turn accuracy needs audio tests. |
| ASR | Multilingual faster-whisper small, CUDA float16, `auto` | Shared by both TTS backends. |
| LLM | `chat-completions`, model from `VOICE_LLM_MODEL` | No local large language model is loaded. |
| Drama TTS | S2S `backend: openai` to native `http://127.0.0.1:8792/v1` | Model `drama-3`, selected reference ID, PCM16 at 44,100 Hz. |
| Qwen TTS | S2S `backend: qwen3`, Base, torch/CUDA float16/eager, language `auto` | Selected `ref_audio` and `ref_text`; `speaker: null`. |
| Concurrency | One speech pipeline, one selected TTS | Raising pipeline count can duplicate handlers and GPU resources. |
| Speech engine | `127.0.0.1:8791` | Internal listener has no upstream authentication. |
| UI / LLM gateway | `127.0.0.1:8790` | Owns the cloud credential and provider-specific request policy. |

### Native Drama and voice references

The native session uses `disk_experts=true`, `pin_experts=true`, `expert_cache_size=8`, `cpu_expert_layers=0`, `codec_backend=same` and `context_length=2048`. Expert banks reside in pinned host memory, and missed experts are transferred into the GPU cache for computation. The cache value is slots per layer, not GB; this is not CPU expert computation. The default request budget is 500 tokens. Reference audio, text and generated tokens share the context budget. These are resource settings selected for the 16 GB / 64 GB target, not measured peak-memory or real-time guarantees. See the [fixed model documentation](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/docs/models/fish_drama3.md) and [DRAMA_SETUP.md](DRAMA_SETUP.md).

The voice store accepts 1–30 seconds of PCM16 WAV and an accurate transcript, with 5–15 seconds recommended. The saved audio is mono. Drama receives `voice_presets[id] = {voice_ref, reference_text}` and `default_voice_preset=id` in a derived `session-server.json`. Qwen receives the same WAV path and transcript through `qwen3_tts_ref_audio` / `qwen3_tts_ref_text`; its handler calls `generate_voice_clone_streaming`. The models do not share embeddings, acoustic tokens or internal caches.

The browser omits `audio.output.voice`. In pinned OpenAI SDK 2.28.0 the field defaults to `None`, and S2S does not insert a default speaker during initialization. The HTTP TTS handler therefore falls back to `openai_tts_voice`, while Qwen preserves its configured reference. See [the SDK model](https://github.com/openai/openai-python/blob/v2.28.0/src/openai/types/realtime/realtime_audio_config_output.py), [HTTP voice selection](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/openai_compatible_handler.py) and [Qwen reference routing](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/qwen3_tts_handler.py).

### Native PCM and the SoXR compatibility hook

Native Drama raw PCM is mono 44.1 kHz. It is resampled to the speech pipeline's internal 16 kHz, then the existing Realtime output conversion supplies the browser's configured 24 kHz wire audio. Changing a header to 24 kHz would give the wrong duration and pitch.

The pinned upstream HTTP handler's FIR implementation is inefficient for this conversion ratio. `her_voice/drama_resampler.py` checks its complete LF-normalized source SHA-256 (`8723e77e9ba7ebe6e0ae9a3b746cdf87b1faefeeb0c7114aed24986c98c3d3a8`) and replaces only `_StreamingFIRResampler` in memory with a `soxr==1.0.0` stateful converter. It retains upstream HTTP reads and cancellation, checks the SoXR version and refuses unknown source. It does not edit the installed file or apply to Qwen workers. Every resampler instance owns independent state, with final flushing handled at the end of each synthesis stream. The [SoXR streaming interface](https://pypi.org/project/soxr/1.0.0/) documents variable output-chunk sizes.

### DeepSeek gateway

The LLM block uses `responses_api_base_url: {env: VOICE_GATEWAY_BASE_URL}`, `responses_api_api_key: {env: HER_VOICE_GATEWAY_TOKEN}` and `model_name: {env: VOICE_LLM_MODEL}`. The shared `responses_api_*` names are intentional because the upstream [Chat Completions arguments inherit them](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/arguments_classes/chat_completions_language_model_arguments.py).

Only the parent gateway receives the cloud credential. The speech child receives a private local gateway token; the native child receives neither. The supervisor strips the known cloud/provider key environment variables. Credentials are not command-line arguments.

`responses_api_disable_thinking` remains false because that generic flag produces Qwen-specific template controls. The gateway instead sends DeepSeek's `thinking: {type: disabled}`. The default model label is `deepseek-flash`; a compatible Agent provider can supply its own label. See [the upstream extra-body builder](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/LLM/base_openai_compatible_language_model.py) and [DEPENDENCIES.md](DEPENDENCIES.md).

Start the gateway before the speech engine: upstream [LLM warmup](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/LLM/chat_completions_language_model.py) can make a real provider request. The Python supervisor's default readiness budget is 1,200 seconds and can be stopped. Opening the UI or installing packages does not make that provider request.

### Text streaming and audio streaming

`stream_batch_sentences: 1` releases LLM text at sentence boundaries. Handlers can coalesce queued text from one response; every text token is not immediately synthesized.

For Qwen, `qwen3_tts_non_streaming_mode: true` governs text prefill, while `generate_voice_clone_streaming(..., chunk_size=8, ...)` continues yielding waveform chunks. For Drama, `openai_tts_stream: false` disables an unrelated vLLM extension; the PCM response is still read through the upstream streaming HTTP path with `stream_format=audio`. These flags do not guarantee first-audio latency or uninterrupted real-time generation.

## WebSocket wire protocol

Endpoint: `ws://127.0.0.1:8791/v1/realtime`. The application bridges this over its own same-origin WebSocket. Upstream accepts the optional `realtime` subprotocol.

The wire format is JSON events with base64 audio. There is **no custom binary header and no required `turn_id` on audio deltas**. The application uses `response_id` plus its own connection/playback epoch to discard old output.

The browser sends the following after `session.created` and waits for `session.updated` before microphone upload:

```json
{
  "type": "session.update",
  "event_id": "configure-session",
  "session": {
    "type": "realtime",
    "output_modalities": ["audio"],
    "audio": {
      "input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"language": null},
        "turn_detection": {
          "type": "server_vad",
          "create_response": true,
          "interrupt_response": true
        }
      },
      "output": {
        "format": {"type": "audio/pcm", "rate": 24000}
      }
    },
    "extensions": ["speech_to_speech.input_audio_transcription.snapshot"]
  }
}
```

Audio is **signed little-endian PCM16, mono, 24 kHz** under this configuration. Microphone chunks are `{ "type": "input_audio_buffer.append", "audio": "<base64>" }`. Upstream resamples to its internal 16 kHz pipeline and groups 512 samples; these internal values must not be mistaken for the configured wire rate. See [input handling](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/handlers/audio.py#L273-L323).

| Event | Relevant fields / application meaning |
|---|---|
| `session.created`, `session.updated` | `event_id`, `session.id`, effective session config. |
| `input_audio_buffer.speech_started` | `item_id`, `audio_start_ms`; user speech detected. Stop/clear audible assistant output immediately. |
| `input_audio_buffer.speech_stopped` | `item_id`, `audio_end_ms`; a committed input item closes. Speculative pauses may reopen before commitment. |
| `conversation.item.input_audio_transcription.delta` | `item_id`, `content_index`, append-only `delta`. |
| `speech_to_speech.input_audio_transcription.snapshot` | `item_id`, `content_index`, **replacement** `transcript`. Opt-in, provisional and revisable. |
| `conversation.item.input_audio_transcription.completed` | `item_id`, `content_index`, authoritative `transcript`, usage. Replace provisional text even when the final string is empty. |
| `conversation.item.input_audio_transcription.failed` | Input item identity and `error`; show a recognition failure instead of a completed transcript. |
| `response.created` | `response.id`, `response.status: in_progress`. Establish active response identity. |
| `response.output_audio.delta` | `response_id`, `item_id`, `output_index`, `content_index`, base64 `delta`. Decode and queue only for the accepted response/epoch. |
| `response.output_audio.done` | Same output identity; generated audio is complete. Playback may still contain queued audio. |
| `response.output_audio_transcript.delta` | Same output identity and append-only assistant `delta`. |
| `response.output_audio_transcript.done` | Same output identity and full assistant `transcript`; on cancellation this can be a partial response. |
| `response.done` | `response.id`, `status`, `status_details`, output and usage. Status is `completed`, `cancelled`, `incomplete` or `failed`. |
| `error` | `error.type`, optional `error.code`, `error.message`; correlated client event ID is `error.event_id`, not the outer event ID. |

Status reasons include `turn_detected`, `client_cancelled`, `max_output_tokens`, and `content_filter`. Successful text generation, completed audio delivery, and the last sample actually heard are separate events. The UI should remain in its speaking state while the accepted local playback queue still contains audio.

The audio field definitions are in [the encoder](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/handlers/audio.py#L501-L546); completion status is in [the response builder](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/handlers/response.py#L476-L545).

### Bilingual language routing

The shared 0.4 profile retains `faster_whisper_stt_gen_language: auto`, `qwen3_tts_language: auto`, `enable_lang_prompt: false` in the LLM block, and `detect_llm_output_language: false` in pipeline options. The default system prompt follows the user's language and explicit language requests; Chinese replies default to Simplified Chinese, with Traditional Chinese used only when explicitly requested. A host replacing that prompt must retain its own equivalent policy.

The explicit session `transcription.language: null` above is intentional. At this pinned revision, a session `zh` or `en` affects TTS as well as recognition, and the literal string `auto` activates assistant-output language detection. That detector selects one language from the first text batch for the whole response. Omitting the field preserves an existing session override; explicit null clears it. Combined with the disabled detector, this allows Qwen to use its configured `auto` mode, rather than a single ASR/output-classifier label. This is a verified routing contract, not a measurement of mixed-language pronunciation. See [BILINGUAL.md](BILINGUAL.md) and the validation evidence before changing these values.

Exact routing sources: [RuntimeConfig merge and selected language](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/runtime_config.py), [Qwen language selection](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/qwen3_tts_handler.py), [Whisper normalization](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/STT/faster_whisper_handler.py), and [assistant first-batch language handling](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/LLM/lm_output_processor.py).

### Chinese, English and mixed live subtitles

The public standard transcription delta is now an append-only suffix. The snapshot extension supplies the cumulative hypothesis and is better suited to replaceable provisional Chinese subtitles; the client uses the same handling for English and mixed-language text. The upstream [stable-prefix algorithm](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/handlers/conversation.py#L49-L77) operates on whitespace-delimited words, so Chinese text can have few standard delta updates despite active recognition. Do not concatenate snapshot text. Do not both append standard deltas and replace from snapshots into one untracked buffer. Always key the user item by `item_id` and let the completed event win over provisional text. Transcripts are not translated or normalized between Traditional and Simplified Chinese by this client.

## Cancellation, pause and reset boundaries

The [actual dispatch table](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/service.py#L118-L128) accepts:

- `input_audio_buffer.append`, `input_audio_buffer.commit`.
- `session.update`.
- `conversation.item.create`, `conversation.item.delete`, `conversation.item.truncate`.
- `response.create`, `response.cancel`.
- `output_audio_buffer.clear`, **WebRTC only**; sending it over WS returns `invalid_event_for_transport`.

There is **no native `pause`, `reset` or `input_audio_buffer.clear` event** in this revision. Pause/resume and reset are application controls, and must not be presented as upstream protocol primitives.

### Response cancellation ignores its supplied response ID

Upstream [response.cancel dispatch](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/websocket_router.py#L495-L507) cancels the current/pending response without examining `event.response_id`. An old cancel forwarded after a new response starts can therefore cancel that new response. The bridge must reject stale response IDs before forwarding, and the browser must flush playback immediately and discard late old response chunks.

This guard does not create an atomic upstream conditional-cancel primitive. A response transition racing between the bridge's observation and upstream processing remains a protocol limitation. Do not retry an old targeted cancel against a newer response. The prototype uses a fresh session boundary for manual interruption, pause/resume and replacement typed input; natural voice barge-in uses the upstream same-session VAD cancellation path.

The current engine uses [generation-counter cancellation](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/pipeline/cancel_scope.py), response-key checks and speculative-turn revisions. This discards stale output without killing and reloading the model process at every interruption. Local playback still needs its own flush; server cancellation cannot unplay samples already queued in the browser.

### Truncation does not implement heard-only history

`conversation.item.truncate` is an accepted **no-op with no acknowledgement** in [this router](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/websocket_router.py#L476-L482). Tracking playback samples is useful for the UI, but sending the stock truncate event does not edit upstream conversation history to match the exact words heard. This prototype must not claim that interrupted history is a perfect transcript of audible output.

`conversation.item.delete` is different: [it really calls `chat.delete_item`](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/handlers/conversation.py#L154-L201) and emits `conversation.item.deleted`, or an item-not-found error. During an active/pending response it is deferred. Deleting a whole item is not equivalent to truncating its unheard tail. The prototype does not automatically delete conversation items to approximate what was heard.

### Pause, manual interruption and replacement typed input

There is no public upstream response-idle acknowledgement for the interval after ASR begins and before `response.created`. Cancelling a queued response can produce no terminal event because no public response identity exists yet. `input_audio_buffer.commit` flushes resampling bookkeeping; it does not force the VAD to end an utterance. Merely muting the microphone can therefore leave a half-spoken VAD turn buffered. The prototype uses the following explicit boundary instead of assuming a cancel acknowledgement means all input has settled:

1. Clear browser playback, mute microphone upload, invalidate the old client epoch and close its WebSocket. Late events from that connection are discarded.
2. Keep the speech model process running. On resume/manual interruption/replacement text, wait for the old pipeline session to drain and for an idle slot, then open a fresh session.
3. Wait for `session.updated`, restore the **latest 30 complete, non-empty user/assistant messages** as text, and wait for every `conversation.item.created` acknowledgement. No generation is requested during restoration.
4. Only then re-enable microphone upload, or create the new typed user item and request its response.

Pause holds the connection closed until the user resumes. Manual interruption creates the new listening session after cancelling local playback. Typed input uses the same boundary before submitting the replacement question, which prevents an old pending ASR response from racing with the new text.

User transcript fragments and interrupted/failed/incomplete assistant messages are not replayed into the fresh session. They should remain visibly marked as interrupted/incomplete in the local conversation display. This deliberately preserves a bounded set of completed text messages, **not every partial utterance, hidden model state, tool state, or a word-accurate heard-only history**. A browser page reload loses this in-memory conversation. Stop/start can restore its completed text while the page remains open; clearing history removes it.

The accepted seed shapes are:

```json
{"type":"conversation.item.create","item":{"id":"seed_user_1","type":"message","role":"user","content":[{"type":"input_text","text":"已确认的用户内容"}]}}
```

```json
{"type":"conversation.item.create","item":{"id":"seed_assistant_1","type":"message","role":"assistant","content":[{"type":"output_text","text":"完整的助理回答"}]}}
```

Upstream [accepts user input_text and assistant output_text](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/LLM/chat.py#L360-L383), while [conversation.item.create does not request generation](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/handlers/conversation.py#L118-L152). Assistant `output_audio` with only a transcript is not a substitute for `output_text` in this history path.

Upstream drains `SESSION_END` before reusing a pipeline. Immediate reconnect can encounter `session_limit_reached`; wait for an idle slot with bounded retries. The [pool endpoint](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/websocket_router.py#L639-L674) reports `idle`, `active`, `draining`, or `stuck`. A stuck unit requires an explicit engine restart; it is not proof that a new session is ready. A slow old provider call can delay session release even after local playback has stopped.

## Echo and health boundaries

Use the browser's `echoCancellation`, `noiseSuppression` and `autoGainControl` constraints. Their actual effect depends on the browser and audio devices. Server VAD/SmartTurn is not an acoustic echo canceller and does not reliably identify the assistant's own loudspeaker output. Begin acceptance testing with headphones, then test speakers at realistic volume. Do not automatically disable the microphone whenever the assistant speaks if natural barge-in is a requirement.

The supervisor checks `GET /v1/pool`, not merely an open TCP port and not a probe WS that would occupy the sole session. HTTP readiness confirms that the initialized engine listener is serving its pool endpoint. It does not certify intelligible ASR, TTS sound, CUDA performance, echo rejection or a successful real conversation.

## Logs and validation

For 0.4, both dynamically generated backend profiles passed the original pinned `load_config`, `validate_config` and `resolve_config` functions against the fixed metadata subset. The Drama result preserved the native 44,100 Hz PCM rate and selected voice ID; the Qwen result preserved the Base snapshot path, `ref_audio`, bilingual `ref_text`, `speaker: null` and `language: auto`. The same mapping with a `.json` filename was rejected as expected. See [the recorded profile-contract evidence](evidence/tts-profile-contract-2026-10-20.json). This check does not import or run real inference models and is separate from a complete Windows installation test.

`runtime.log_transcripts: false` disables full transcript text in upstream application loggers. It does **not** suppress the Rich live conversation display: the [logging module explicitly excludes terminal display](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/pipeline/transcript_logging.py), and the [Qwen handler prints the synthesis text](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/qwen3_tts_handler.py#L849-L858). Consequently captured child stdout can contain conversation text.

The supervisor retains a bounded, in-memory log tail for local diagnosis and redacts known credentials/Bearer values. The public application status response omits it. `EngineSupervisor(..., echo_logs=True)` also prints those already-redacted lines to the local terminal, with an `[engine]` prefix; it is off by default and does not write a log file. This explicit diagnostic mode may display conversation text. There is no `runtime.server.access_log` YAML option in this upstream revision; [RealtimeServer's uvicorn configuration](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/server.py#L47-L52) does not explicitly disable access logging. Avoid putting secrets or transcript text in URLs.

After installing the pinned package, configuration validation can run without constructing models:

```python
from pathlib import Path
from speech_to_speech.config import load_config, validate_config, resolve_config

document = load_config("config/windows-4080.yaml")
validate_config(document)
fake_models = (Path.cwd() / "validation-only").resolve()
resolved = resolve_config(
    document,
    names=["windows4080"],
    environ={
        "VOICE_LLM_MODEL": "deepseek-flash",
        "VOICE_GATEWAY_BASE_URL": "http://127.0.0.1:8790/llm/v1",
        "VOICE_ENGINE_PORT": "8791",
        "HER_VOICE_GATEWAY_TOKEN": "validation-only-not-a-real-key",
        "HER_ASR_MODEL_PATH": str(fake_models / "asr-snapshot"),
        "HER_TTS_MODEL_PATH": str(fake_models / "tts-snapshot"),
        "VOICE_REFERENCE_AUDIO": str(fake_models / "reference.wav"),
        "VOICE_REFERENCE_TEXT": "你好，这是测试。 Hello, this is a test.",
        "HER_SMART_TURN_MODEL_PATH": str(fake_models / "smart-turn-v3.2-cpu.onnx"),
    },
)
assert resolved.pipelines["windows4080"].stages["llm"].backend == "chat-completions"
# Do not print resolved settings: they contain credentials in a real run.
```

`load_config` needs PyYAML; normal metadata imports also need the package's ordinary dependencies such as the OpenAI SDK. This validation performs no model construction/download and no provider requests. It still does not replace installation, CUDA DLL checks, a WebSocket handshake or the real microphone/speaker acceptance test.

During development the configuration passed the original pinned upstream `load_config`, `validate_config` and `resolve_config` functions using an isolated 40-file metadata source subset. That subset contains the original registry and argument dataclasses and omits the unrelated pipeline package initializer, so no inference modules or models load. It confirmed one `windows4080` pipeline, loopback port 8791, `silero` / `faster-whisper` / `chat-completions` / `qwen3`, and the normalized TTS `torch` backend. Version 0.2 was checked again with a different model label, gateway port 8890 and engine port 8891; the environment references resolved correctly, including the integer server port. This is configuration validation, not a full installed-package or Windows dependency test.

Version 0.3 additionally passed 29 configuration/language-routing assertions with the updated bilingual YAML, real RuntimeConfig and OpenAI SDK 2.28.0 models. GPU-dependent handler methods were extracted unchanged from their source AST and `transcribe()` used a test double; there was no inference. The associated 52 source SHA256 checks are reported separately. See [the exact scope and results](VALIDATION.md), [routing report](evidence/bilingual-routing.json), and [source inventory](evidence/bilingual-sources.json).

`tests/test_engine.py` exercises the supervisor with real local Python subprocesses and a stdlib HTTP fixture. It covers ownership, readiness, early failure, credential removal/redaction including optional terminal echo, startup interruption, bounded timeout, protection from stale readiness workers, and the ordering of source verification/model staging/CLI launch. `tests/test_pin_silero.py` checks the patch's hash/provenance gates, isolation, idempotence and atomic-write failure with temporary fixtures. These tests make no speech-model or DeepSeek calls.
