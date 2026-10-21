# 0.4：Windows 安装、依赖与 API 契约

核对日期：2026-10-20。目标为原生 Windows x64、RTX 4080 SUPER 16 GB、64 GB RAM。语音识别、轮次判断和语音合成在本机运行，文字回答交给 DeepSeek API 或明确配置的兼容 Agent 接口。

0.4 提供两个手动选择的 TTS 后端：**Drama 3 主用，Qwen3-TTS 0.6B Base 备用**。同一时刻只启动选中的后端；失败时显示错误，由用户切换，不会自动加载另一个模型。两者共用导入的参考 WAV 和准确文字，各自生成自己的模型内部表示。

本开发环境没有 Windows、NVIDIA GPU 或真实音频设备。下面的版本、配置及调用契约已经核对，但 Windows 安装、实际显存、音质和端到端延迟仍未验证。

## 1. Python 环境与固定依赖

安装器使用 CPython 3.11 x64 和独立的 `.venv-speech`。实际约束见 [dependencies-windows.txt](../config/dependencies-windows.txt)，机器可读记录见 [upstream-lock.json](../config/upstream-lock.json)。

| 组件 | 固定值 | 用途与来源 |
|---|---|---|
| Python | CPython 3.11 x64 | 安装器检查 Windows、64 位、x64 和 Python minor；不以 WSL 或其他 Python 版本代替此目标。 |
| speech-to-speech | `1.0.0`，commit `0ed1bb95bd335aa89bb7c172b6a347b6dae596f8` | 固定源码归档和 `faster-whisper` extra。[固定 pyproject](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/pyproject.toml) |
| torch / torchaudio | `2.11.0+cu128` / `2.11.0+cu128` | 从官方 CUDA 12.8 索引安装。[torch wheels](https://download.pytorch.org/whl/cu128/torch/)、[torchaudio wheels](https://download.pytorch.org/whl/cu128/torchaudio/) |
| transformers | `5.18.0` | 对齐固定上游的 Windows 依赖范围。[固定 pyproject](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/pyproject.toml) |
| openai | `2.28.0` | 上游客户端与 Realtime 数据结构；本项目不要求 OpenAI 云账户。[固定上游依赖](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/pyproject.toml) |
| faster-whisper / CTranslate2 | `1.2.1` / `4.8.3` | 多语 ASR，CUDA float16。[faster-whisper](https://pypi.org/project/faster-whisper/1.2.1/)、[CTranslate2](https://pypi.org/project/ctranslate2/4.8.3/) |
| faster-qwen3-tts | `0.5.4` | 备用 TTS 的 torch 路径；Windows 不安装它的 ggml extra。[版本页](https://pypi.org/project/faster-qwen3-tts/0.5.4/) |
| qwen-tts-hf | `0.1.1.post1` | 适配 Transformers 5 的非官方兼容发行包，与 Qwen 官方 `qwen-tts` 发行包不同。[发行说明](https://pypi.org/project/qwen-tts-hf/0.1.1.post1/) |
| aiohttp | `3.14.5` | 网页服务、本地桥接和 API 探针。[版本页](https://pypi.org/project/aiohttp/3.14.5/) |
| Python-SoXR | `soxr==1.0.0` | Drama 的有状态 44.1 kHz → 16 kHz 流式重采样；安装器显式安装。[版本及流式接口](https://pypi.org/project/soxr/1.0.0/) |

这些是关键组件的精确 constraints，**并非已在 Windows 解算完成的全部传递依赖锁文件**。安装器保留 pip resolver，执行 `pip check`，并保存实际 `.runtime/pip-freeze-windows.txt`。升级组件后应重新安装和验收。

固定上游没有用于此流程的 `all` extra。其 Windows 依赖已包含 Qwen 运行包，因此即使先使用 Drama，基础安装仍安装 Qwen 的 Python 依赖；是否下载 Qwen 权重由后端选择决定。不要另行安装 `qwen-tts`：它与 `qwen-tts-hf` 使用相同的 `qwen_tts` 导入名，且依赖路线不同。[固定上游依赖](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/pyproject.toml)、[兼容包说明](https://pypi.org/project/qwen-tts-hf/0.1.1.post1/)

## 2. 安装顺序

准备 CPython 3.11 x64 和适用于显卡的 NVIDIA 驱动，在项目根目录运行：

```powershell
.\scripts\install-windows.ps1
```

没有 `py.exe` 时可指定解释器：

```powershell
.\scripts\install-windows.ps1 -PythonExe 'C:\Program Files\Python311\python.exe'
```

脚本建立或重用 `.venv-speech`，先安装 CUDA PyTorch wheels，再安装固定 S2S、aiohttp、SoXR 和本项目；随后应用 Silero 来源修补，检查依赖、保存 freeze 并运行 doctor。失败返回非零退出码并保留现有环境。无需先激活 venv。

基础脚本不下载模型权重，不调用 DeepSeek，不安装驱动，也不改变系统 PowerShell 执行策略。`-SkipGpuCheck` 仅跳过本次 GPU 检查，不能视为 GPU 已通过验收。

### 主用：Drama 3

Drama 额外需要 Git、Visual Studio 2022 C++ Build Tools、Windows SDK、CMake/Ninja、OpenMP，以及与 MSVC 兼容的 NVIDIA CUDA Toolkit。PyTorch 自带运行库不能代替编译器；此构建优先使用 CUDA 12.8，明确指定 4080 SUPER 的 `sm_89` 和 CPU AVX2。

```powershell
.\scripts\install-drama.ps1 -DryRun
.\scripts\install-drama.ps1
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend drama3 --download
```

第二条构建固定 `audio.cpp` 并下载、校验 **24,596,497,888 字节，约 22.91 GiB** 的 GGUF。第三条仅预下载 Drama 模式共用的 ASR 与 SmartTurn，不下载 Qwen 权重，也不代替 GGUF 准备。已有模型复用、仅构建和自定义目录见 [DRAMA_SETUP.md](DRAMA_SETUP.md)。

主机使用锁页内存保存专家权重，GPU 使用每层 8 个专家缓存槽；`cpu_expert_layers=0`，专家矩阵运算仍在 GPU。64 GB RAM 是目标配置，不是经真机测得的峰值保证。[固定 Drama 实现说明](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/docs/models/fish_drama3.md)

### 备用：Qwen3-TTS 0.6B Base

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend qwen3
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend qwen3 --download
```

第一条只打印清单。第二条下载 ASR、完整 Qwen Base 快照及 SmartTurn，包含 `speech_tokenizer/` 子目录权重。`download-models.py` 不指定 `--backend` 时默认 `qwen3`；为避免误下载，本文件明确选择后端。

Qwen 使用 torch、CUDA、float16、eager attention 和参考音频克隆路径，不要求额外编译 FlashAttention 或 Triton。`qwen3_tts_non_streaming_mode: true` 指每个文本片段的预填充方式，输出波形仍通过流式接口返回。[固定 Qwen handler](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/qwen3_tts_handler.py)

## 3. 固定模型与实际传入方式

| 用途 | 固定内容 | revision |
|---|---|---|
| 共用 ASR | `Systran/faster-whisper-small` 完整多语快照 | [`536b0662742c02347bc0e980a01041f333bce120`](https://huggingface.co/Systran/faster-whisper-small/commit/536b0662742c02347bc0e980a01041f333bce120) |
| Drama 主用 | `Jmica/Drama-3-GGUF` 的 `drama-3-backbone-8bit-experts-Q4_K_S.gguf` | [`17a5b40c5dc2c2be986f5371a7e650108b828e29`](https://huggingface.co/Jmica/Drama-3-GGUF/tree/17a5b40c5dc2c2be986f5371a7e650108b828e29) |
| Qwen 备用 | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 完整快照 | [`5d83992436eae1d760afd27aff78a71d676296fc`](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base/commit/5d83992436eae1d760afd27aff78a71d676296fc) |
| 共用轮次判断 | `pipecat-ai/smart-turn-v3` 的 `smart-turn-v3.2-cpu.onnx` | [`f766f81d3cfdf7737ac64aad813d91bbfd56bf93`](https://huggingface.co/pipecat-ai/smart-turn-v3/commit/f766f81d3cfdf7737ac64aad813d91bbfd56bf93) |
| 共用 VAD | `snakers4/silero-vad`，含 `silero_vad.jit` | [`1e261b036686cd0017d500ee96acd1c4ba572a9d`](https://github.com/snakers4/silero-vad/commit/1e261b036686cd0017d500ee96acd1c4ba572a9d) |

ASR、Qwen、SmartTurn 由 `ensure_models(tts_backend=...)` 按完整 SHA 下载，再把真实缓存路径放入 worker 环境。Qwen 必须保留嵌套 tokenizer 权重；本地目录规则见[固定兼容 loader](https://github.com/andimarafioti/Qwen3-TTS/blob/af457d954d8ce797603c6f260c3f911a09b1d90a/qwen_tts/core/models/modeling_qwen3_tts.py)。Drama 的准备器校验完整 GGUF 大小和 SHA-256 后发布 `runtime.json`。公开 HF 下载都显式使用 `token=False`。

Silero 不在 HF 预下载范围内，其固定 GitHub 来源由 Torch Hub 在首次载入 VAD 时取得。安装修补仅替换已核对 `vad_handler.py` 中的一处移动分支引用，检查原始／修改后文件哈希及虚拟环境来源；启动 worker 只验证，不自动修改。见 [PATCHES.md](PATCHES.md)。

Drama 的 SoXR 适配是另一个明确披露的运行时钩子：只在 Drama worker 中校验原始 HTTP TTS handler 的 SHA-256，再在内存中替换重采样器类，不修改磁盘源码，不影响 Qwen。它保留上游 HTTP、取消和回复处理，见 `her_voice/drama_resampler.py`。

这些固定来源不保证 GPU 逐位一致、远端 DeepSeek 权重固定或手动改过的普通 HF 缓存被完整重新哈希。Drama 的完整 GGUF 校验与普通快照的必要文件检查是不同的范围。

## 4. 参考音色与首次启动

在网页音色面板导入 **1–30 秒 PCM16 WAV**，建议干净的 **5–15 秒** 单说话人录音，并填写准确文字。导入接受单／双声道、8–96 kHz，保存时重写为单声道 PCM16；两个后端各自处理后续采样率与特征编码。

选择后端及已保存音色，应用设置后开始。Drama 使用录音生成 native voice preset；Qwen Base 通过 `ref_audio` / `ref_text` 使用同一录音。共享的是 WAV、文字和音色 ID，**不共享 embedding 或模型缓存**，相似度需要分别听测。

生成的 S2S 配置使用 `.yaml` 扩展名，符合固定 `serve --file` 的检查。浏览器省略 `audio.output.voice`：Drama 使用 profile 音色 ID，Qwen 保留参考音频。默认值和路由已按固定源码核对，见 [UPSTREAM.md](UPSTREAM.md)。

## 5. CUDA 和 DLL 检查

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\doctor.py
.\.venv-speech\Scripts\python.exe .\scripts\doctor.py --json
```

doctor 不联网、不录音、不下载权重、不验证 API 密钥有效性。它检查平台、所有约束版本（含 SoXR）、固定 S2S 来源、Silero 修补和 Qwen 发行包冲突；在有时间上限的子进程中检查小 CUDA tensor 分配、CUDA 12.8、cuDNN 9、cuBLAS/cuDNN DLL 和 CTranslate2 CUDA float16 支持。

DLL 查找覆盖 venv 的 `torch/lib`、`CUDA_PATH/bin` 和可选 `HER_VOICE_CUDA_DLL_DIR`；Windows 下后者用分号分隔。`nvidia-smi` 的驱动兼容版本不能证明这些 DLL 已可加载。[faster-whisper GPU 要求](https://github.com/SYSTRAN/faster-whisper#gpu)、[CTranslate2 安装](https://opennmt.net/CTranslate2/installation.html)

doctor 的 PASS 不替代 native 编译、GGUF 加载、参考音色合成或共享显卡峰值测量。Drama 准备结果另保存在 `.runtime/drama3/runtime.json`；HTTP `/health` 与 `/v1/models` 可用也不等于已成功合成。

## 6. DeepSeek 与兼容 Agent

默认服务为 `https://api.deepseek.com/chat/completions`，模型标签为 `deepseek-flash`。网关发送 `thinking: {type: disabled}` 和 `stream: true`，读取 SSE，检查终结原因、usage 及 `[DONE]`。只有答案 `delta.content` 进入 TTS，推理内容和工具参数不朗读。API 契约见 [DeepSeek 官方参考](https://api-docs.deepseek.com/api/create-chat-completion/)。

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\probe-deepseek.py
.\.venv-speech\Scripts\python.exe .\scripts\probe-deepseek.py --live
```

第一条只显示计划；第二条发送一次实际请求，可能产生 API 费用。密钥从本地 `.env` 或进程环境读取，进程环境优先，不作为命令行参数。探针不输出密钥、回答正文或服务原始错误正文。本次交付没有运行真实付费探针。

实时引擎初始化时，上游 LLM handler 还会通过网关发出一次真实预热请求，因此先启动网关，再开始语音引擎。首次实时启动可能下载所选语音模型；安装包步骤和 dry-run 不产生该请求。[固定预热实现](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/LLM/chat_completions_language_model.py)

HER / Joyboi 等宿主可使用兼容 Agent 接口，见 [REUSE.md](REUSE.md)。DeepSeek 探针在 generic 模式会拒绝运行。语音波形留在本地，转录、历史及 system prompt 会发送给配置的文字服务；本地语音处理不代表整段对话离线。

## 7. 验证边界

已完成无权重的配置解析、安装准备逻辑、进程控制、参考音色导入、协议和取消测试。两个生成 profile 均通过固定 S2S `load_config → validate_config → resolve_config`；DSP 检查使用生成的音频和 HTTP fixture。

仍未执行：Windows PowerShell 安装、目标 Windows 完整依赖解算、MSVC/CUDA 编译、真实模型推理、麦克风／扬声器回路和 DeepSeek 实际请求。目标机器按 [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md) 分别验收 Drama 与 Qwen，再记录性能结果。
