# 0.4：简体中文、English 与同句混说

默认使用中英自动模式：中文问题默认以简体中文回答，英文问题以英文回答；明确的回答语言要求优先。Drama 3 为主用合成后端，Qwen3-TTS 0.6B Base 为备用。两个后端沿用同一套识别、字幕、回答语言和打断流程。

这是已经实现的配置与软件契约。真实识别率、跨语言音色相似度、混说发音、自然插话和 RTX 4080 SUPER 的延迟仍需分别验收；不能把配置通过当作音质测量。

## 预期使用方式

| 输入 | 默认行为与验收重点 |
|---|---|
| “帮我解释一下显存。” | 自动识别，助理以简体中文回答。 |
| “Could you explain GPU memory?” | 自动识别，字幕和回答保留英文。 |
| “帮我 explain 这个 Python error。” | 保留有意义的英文名称和术语，回答跟随主要语言。 |
| “这次请用英文回答。” | 明确要求优先，不被上一轮中文或 ASR 标签锁住。 |
| “Please answer in Chinese.” | 回答改用简体中文。 |
| 中文回答过程中用英文插话，或相反 | 停止旧音频，新问题进入同一套字幕和回复流程。 |

这些规则来自默认 system prompt，实际遵循仍取决于 DeepSeek 或宿主 Agent。用户明确要求其他中文字形时可覆盖默认；ASR 字幕保留识别器原始结果，程序不会把转录统一转换成简体。非常短的 “OK”、人名、缩写、口音和背景噪声需要单独测试。

## 实际配置

| 位置 | 0.4 的值 | 作用 |
|---|---|---|
| `faster_whisper_stt_gen_language` | `auto` | 上游转换为不强制语言，让多语 Whisper 判断。 |
| `enable_lang_prompt` | `false` | 不额外把单一 ASR 标签作为回答语言指令。 |
| `init_chat_prompt` | 简体中文／英文策略 | 遵从明确要求，否则跟随最近用户消息的主要语言。 |
| `detect_llm_output_language` | `false` | 关闭按首批文字固定整个回答语言的分类路径。 |
| `session.audio.input.transcription.language` | JSON `null` | 清除会话语言覆盖；不是字符串 `"auto"`。 |
| `session.audio.output.voice` | 省略 | 使用已保存的后端音色配置，不由浏览器填入固定说话人。 |
| Drama `openai_tts_language` | `null` | 不在 HTTP TTS profile 强制单一语言；由模型处理输入文字。 |
| Drama `openai_tts_voice` | 已导入音色 ID | 选择本地 native voice preset。 |
| Qwen `qwen3_tts_language` | `auto` | 使用 Qwen 的模型级自动语言模式。 |
| Qwen `qwen3_tts_speaker` | `null` | 采用 Base 参考音频克隆路径。 |
| Qwen `qwen3_tts_ref_audio` / `ref_text` | 本地 WAV 路径／准确文字 | 保留所选参考音色的输入。 |

这些值必须一起使用。旧客户端若继续传 `transcription.language: "zh"` 或固定 `output.voice`，会覆盖后端配置。升级时同时更新网页与配置；自定义 `VOICE_PROFILE` 应合并这些策略，保留自己的角色提示。

## 为什么语言使用 null、音色使用省略

固定 S2S 为 [`0ed1bb95bd335aa89bb7c172b6a347b6dae596f8`](https://github.com/huggingface/speech-to-speech/tree/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8)。其会话 `transcription.language` 也参与 TTS 语言选择：`zh` / `en` 会固定语言，字符串 `auto` 会启用按助理首批文字分类的路径，而省略字段无法清除既有覆盖。因此网页显式发送 `null`，并关闭输出语言 detector。[固定 RuntimeConfig](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/api/openai_realtime/runtime_config.py)、[Qwen 语言路由](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/qwen3_tts_handler.py)

音色的默认情况不同：固定 OpenAI SDK 2.28.0 的 `RealtimeAudioConfigOutput.voice` 默认 `None`，S2S 初始化不另外插入说话人。网页省略该字段后，Drama 的 HTTP handler 使用 profile 的音色 ID；Qwen 不触发会话音色覆盖，保留参考音频与文字。新会话与后端切换不会依赖旧会话的固定音色。[SDK 默认值](https://github.com/openai/openai-python/blob/v2.28.0/src/openai/types/realtime/realtime_audio_config_output.py)、[HTTP TTS handler](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/openai_compatible_handler.py)

`VoiceClient.updateSettings()` 没有新增独立辨识语言选择器；语言仍按上述自动策略运行。宿主如果直接构造会话事件，也应遵守这个契约。

## 两个模型如何复用音色

ASR 使用多语 [`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small/commit/536b0662742c02347bc0e980a01041f333bce120)。TTS 分别为：

| 后端 | 固定模型 | 本地参考输入 |
|---|---|---|
| Drama 3 主用 | [`Jmica/Drama-3-GGUF`](https://huggingface.co/Jmica/Drama-3-GGUF/tree/17a5b40c5dc2c2be986f5371a7e650108b828e29)，Q8 主干、`Q4_K_S` 专家 | `voice_presets[id].voice_ref` 和 `reference_text`。 |
| Qwen 备用 | [`Qwen/Qwen3-TTS-12Hz-0.6B-Base`](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base/commit/5d83992436eae1d760afd27aff78a71d676296fc) | `ref_audio` 和 `ref_text`，使用 `generate_voice_clone_streaming()`。 |

参考录音要求 **1–30 秒 PCM16 WAV**，建议 **5–15 秒**、干净、单说话人，文字与录音相符。导入后同一个 WAV 和文字可用于两个后端；两者不共享 speaker embedding、声学 token 或内部缓存。相同输入也不保证听起来完全相同，应分别记录中文自然度、英文口音、混语停顿和相似度。

界面选择后端并应用时会结束当前会话、停止本应用拥有的语音进程，再在下一次开始时加载选定后端。不会同时常驻两个 TTS，也不会按每个英文单词切换模型。Drama 的 22.91 GiB GGUF 与 Qwen Base 分别准备，见 [DEPENDENCIES.md](DEPENDENCIES.md)。

## HER / Joyboi 接入

复用 `VoiceClient`、本地音色接口、语音 profile 和 gateway 即可沿用双语流程。宿主自行管理 system prompt 时应保留等价语言策略；角色、记忆与任务逻辑继续由宿主管理。辨识与回答文字按原文经过 gateway。接入方式见 [REUSE.md](REUSE.md)。

## 已验证与待验证

无权重检查已验证两个后端生成 profile 通过固定 S2S `load_config / validate_config / resolve_config`，Qwen 的本地 Base 路径、参考音频、参考文字和 `auto` 均能保留；音色省略时的默认行为也已按固定源码核对。

自动化测试覆盖语言字段序列化、英文／混语字幕、UTF-8 分段、完成历史恢复及取消后旧连接隔离。此前 0.3 的路由证据仍可解释共用语言逻辑，但不能代替 0.4 新增两个参考音色后端的真实推理验收。具体软件测试边界见 [VALIDATION.md](VALIDATION.md)。

目标 Windows 机器应按 [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md)，对 Drama 和 Qwen 各完成中文、英文、混说、跨语言回答、短句、打断和连续输出测试。界面回答语言正确不代表 TTS 发音已经达到目标。
