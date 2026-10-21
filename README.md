# Voice Runtime 0.4.0 — Drama 3 主后端 / Qwen3-TTS Base 备用

面向 **Windows x64、RTX 4080 SUPER 16GB、64GB RAM** 的实时语音助手原型。语音识别和语音合成留在本机，语言模型使用 **DeepSeek API**。包含实际服务端、动态浏览器界面、音色管理、模型准备脚本和自动化测试，可以继续作为 HER / Joyboi 的可复用语音模块开发。

本版本实现两种 TTS：**Drama 3 + audio.cpp 原生 CUDA + GGUF 量化及主机专家权重缓存**，以及 **Qwen3-TTS-12Hz-0.6B-Base 参考音色克隆**。两者共用导入的录音和转录文字；同一时刻只启动所选 TTS。

> 已完成代码、配置契约、HTTP/WS 流式传输和浏览器联调。开发环境是 Linux，**没有在真实 Windows / 4080 SUPER / 麦克风上执行模型推理**，也没有使用真实 DeepSeek key。具体证据和未测项目见 [VALIDATION.md](docs/VALIDATION.md)。模型权重不包含在 ZIP 中。

## 1. 已实现的功能

| 功能 | 当前行为 |
|---|---|
| 中文、英文、混合输入 | 多语 faster-whisper small 自动识别；语言模型跟随用户语言，中文默认简体；可直接要求使用英语 |
| 流式回复 | DeepSeek Chat Completions SSE，经本地网关处理 thinking 兼容性和完整事件边界 |
| Drama 3 | 原生 audio.cpp HTTP PCM 分块输出，44.1kHz 连续重采样；Q4 专家 / Q8 主干和 pinned-host 专家权重路径 |
| Qwen3-TTS Base | 0.6B Base CUDA 推理，传入所选 reference WAV 和文字，使用上游分块合成路径 |
| 指定音色 | 导入名称、录音、对应文字，保存到当前工作区；切换后端仍可选择同一个参考音色 |
| 打断 | 浏览器立即清空播放队列，取消当前回复，拒收旧回复的迟到音频；需要时重建会话 |
| 动态界面 | 监听、思考、回答、暂停、打断、实时字幕和输入/输出音量动画 |
| 后端切换 | 先关闭会话和当前模型进程，再保存选择；用户重新开始对话时加载所选后端 |
| 模块复用 | 独立 Python runtime、`VoiceClient`、HTTP/WS 接口、可配置 Agent endpoint 和可安装 wheel |

这套界面借鉴 JOSIE 公开的交互设计，代码为独立实现。JOSIE 的公开仓库不是可直接移植的完整应用源码。语音引擎固定为核实过的 speech-to-speech 提交，升级时可以对照同一组测试，不依赖浮动 `main`。

## 2. 两条实际运行路径

| 部分 | Drama 3 主后端 | Qwen3-TTS 备用 |
|---|---|---|
| 本机识别 | faster-whisper small 多语版，CUDA float16 | 相同 |
| 端点检测 | Silero VAD + Smart Turn v3.2 CPU ONNX | 相同 |
| 语言模型 | DeepSeek API，默认 `deepseek-flash`，stream + thinking disabled | 相同 |
| 合成模型 | `Jmica/Drama-3-GGUF` 的 `drama-3-backbone-8bit-experts-Q4_K_S.gguf` | `Qwen/Qwen3-TTS-12Hz-0.6B-Base` |
| 运行器 | 固定 audio.cpp 原生 Windows CUDA server | 固定 speech-to-speech 的 Qwen torch handler |
| 音色输入 | reference WAV + 转录文字，写入 native voice preset | 同一录音和文字，经 ref_audio / ref_text 传入 |
| 资源安排 | 主机内存固定专家权重，GPU 专家缓存，GPU 执行专家矩阵计算 | 仅加载 ASR、Qwen 和必要语音组件 |
| 额外准备 | 编译原生服务，准备 22.91GiB GGUF | 无需编译 audio.cpp，首次加载固定 HF 模型快照 |

Drama 的 `expert_cache_size=8` 是**每层缓存槽数，不是 8GB 显存**。`cpu_expert_layers=0` 表示这里不把专家计算转到 CPU；专家权重仍通过 pinned-host 和 GPU 缓存管理。64GB 系统内存并不会自动变成 64GB 显存。当前参数是待实机验证的起点，不能据此保证你的峰值显存、首音延迟或实时率。

原生 Drama 的连接取消会停止继续传输；首音前 GPU prefill 的立即抢占没有得到保证。切换后端、退出启动器，或调用 `POST /api/engine/stop` 会结束本应用拥有的进程。详细边界见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 3. Windows 首次安装

### 3.1 准备环境

将项目解压到例如 `D:\Voice\windows-voice-prototype`，在这个目录打开 **64 位 PowerShell**。需要：

- CPython **3.11 x64**，带 `py.exe` launcher；NVIDIA 驱动。
- Edge 或 Chrome，用于麦克风和 Web Audio。
- Drama 路径另需 Git for Windows、Visual Studio 2022 C++ Build Tools / Windows SDK / CMake / Ninja、CUDA Toolkit；具体组件见 [DRAMA_SETUP.md](docs/DRAMA_SETUP.md)。PyTorch 自带的 CUDA runtime 不能代替原生编译所需的 Toolkit。
- 足够的磁盘空间：Drama 单个 GGUF 为 **24,596,497,888 bytes / 22.91GiB**，此外还有 Python 环境、源码、编译产物与其他模型缓存。不要把这个文件大小当成总安装空间。

如果 PowerShell 提示脚本执行被阻止，可只对当前 PowerShell 会话设置：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

安装固定 Python 语音环境：

```powershell
.\scripts\install-windows.ps1
```

安装器创建独立 `.venv-speech`，安装固定 CUDA PyTorch、speech-to-speech、SoXR 等依赖，执行已披露的 Silero ref 修补、`pip check` 和本地 CUDA 诊断；把实际环境版本保存为 `.runtime/pip-freeze-windows.txt`。这个步骤不调用 DeepSeek，也不下载模型权重。具体参数和 DLL 说明见 [DEPENDENCIES.md](docs/DEPENDENCIES.md)。

### 3.2 准备 Drama 3

```powershell
# 可先查看固定版本、目标路径和构建参数；不联网、不写入
.\scripts\install-drama.ps1 -DryRun

# 实际构建原生 CUDA server，下载固定 GGUF 并完整校验 SHA256
.\scripts\install-drama.ps1

# 预取公共识别与 Smart Turn 模型；不会下载 Qwen 备用模型
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend drama3 --download
```

已有**完全相同文件**时，可复用其路径，不再复制 23GiB：

```powershell
.\scripts\install-drama.ps1 -ModelFile 'D:\Models\drama-3-backbone-8bit-experts-Q4_K_S.gguf'
```

脚本只有在可执行文件和模型通过检查后才发布有效的 runtime 记录。被中断的安装不会覆盖上一份可用记录。`-BuildOnly` 仅构建，不代表已经能推理。见 [DRAMA_SETUP.md](docs/DRAMA_SETUP.md)。

### 3.3 准备 Qwen 备用模型

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend qwen3 --download
```

下载 **Base** 完整固定快照，包括 `speech_tokenizer/` 的权重。若暂时想先体验 Qwen，可先完成 3.1 和这一步，在界面选择 Qwen；Drama 构建可以随后完成。两个模式都需要导入参考音色。未提前下载的公共模型会在首次启动对应后端时准备。

### 3.4 配置 DeepSeek

啟動 HER Voice 後，進入 **設定 → 回答接口**，填寫 API Key，點「拉取模型」並在下拉列表選擇模型，再點「儲存並套用」。已有 Key 時會自動拉取；模型列表使用同一接口的 GET /models（保留 /v1 等路徑前綴），不發送生成請求。接口不提供列表時可選「手動輸入模型」。需要驗證實際生成時可點「測試連線」。測試會發送一則短訊息；HTTP 401、402、429 會顯示對應的金鑰、帳務或限流提示。測試不會儲存設定，也不會載入語音模型。

儲存會結束目前對話，新設定立即套用，下次開始對話即可使用，不需要重啟程式。設定保存在本機 `.runtime/provider-settings.json`，優先於啟動環境與 `.env` 的語言模型設定；原有語音後端、音色、模型快取與連接埠設定會保留。已儲存的 Key 不會回傳到表單；同一接口留空可沿用，更換接口時需填寫該接口的 Key。

也可使用 `.env` 作為初始設定。仅在还没有 `.env` 时复制模板，然后填写自己的 key：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

```dotenv
DEEPSEEK_API_KEY=填写你的实际密钥
DEEPSEEK_MODEL=deepseek-flash
VOICE_TTS_BACKEND=drama3
VOICE_PORT=8790
VOICE_ENGINE_PORT=8791
VOICE_DRAMA_PORT=8792
```

`VOICE_TTS_BACKEND` 是初始默认值；之后界面保存的选择优先。DeepSeek 只提供语言模型，录音由本机语音组件处理；识别出的文字和对话上下文会发送到所配置的语言模型服务。网页不会读取已储存的 Key，也不将其写入浏览器存储或模型子进程。

可以单独检查真实 API 流式兼容性。此命令**会使用 key 发送一次请求，并可能产生用量**：

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\probe-deepseek.py --live
```

不加 `--live` 只显示检查计划，不发送请求。API key、余额和服务端延迟不能通过本地测试替你确认。

## 4. 开始实际对话

```powershell
.\scripts\start.ps1
```

打开 `http://127.0.0.1:8790`，进入设置面板：

1. 选择 Drama 3 或 Qwen3-TTS Base。
2. 导入一段干净的人声 **PCM16 WAV**，填写名称和录音中实际说出的完整文字。支持 8–96kHz、单/双声道、1–30 秒、最大 12MiB；建议 5–15 秒，服务端会归一为单声道。
3. 选择已导入的音色，点“应用”。导入和应用不会自动启动推理。
4. 点“开始对话”，等待所选后端和识别引擎就绪，并允许麦克风权限。
5. 测试中文、英语和混合句；助手朗读时插话，检查旧音频是否停止。

音色切换复用的是**原始录音和文字**，不是两个模型共用同一个声纹 embedding，也不是训练模型。音色相似度、跨语言发音和情绪表达需要实际试听。本版本不把任意情绪提示或同音色绝对一致作为已完成能力。

首次启动可能下载缓存并预热模型。只要是在真实模式，错误会显示出来，不会自动换成演练回答。切换后端会结束会话并释放模型；已完成字幕可保留，重新开始时恢复有限文字上下文。

“结束对话”只关闭该次会话和音频设备，模型保持暖机；切换后端、退出启动器，或调用 `POST /api/engine/stop` 会结束本应用拥有的语音进程。不会接管已有占用端口的其他服务。

## 5. 先看界面和交互

```powershell
.\scripts\start-demo.ps1
```

这是明确标记的**协议演练**：使用测试音和预设事件，不采集真实麦克风、不做 ASR/TTS、不调用 DeepSeek。它可以验证界面、字幕、暂停和打断状态；正式推理请使用上一节的 `start.ps1`。

## 6. 诊断与常见问题

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\doctor.py
nvidia-smi
```

| 问题 | 检查 |
|---|---|
| 提示未准备 Drama runtime | 完成 `install-drama.ps1`；确认 `.runtime/drama3/runtime.json` 有效，模型未被移动或改写 |
| 没有参考音色 | 在设置中导入 PCM16 WAV 和对应文字，然后选择并应用 |
| GGUF 校验失败 | 文件名相同不代表内容相同；必须与固定 revision / SHA256 一致 |
| CUDA DLL 缺失 | 查看 doctor，确认 CUDA/cuDNN/CTranslate2 的依赖；worker 加入 Torch lib、CUDA_PATH 和可选 HER_VOICE_CUDA_DLL_DIR |
| Drama 显存不足 | 先关闭其他 GPU 程序并记录实际显存；可以切换 Qwen 继续验收，不能把 64GB RAM 直接当显存 |
| 下载等待较久 | 根据后端运行上述预下载命令，检查 HF 缓存、网络和磁盘；安装/下载未结束时不要把状态视为 ready |
| 401 / 402 / 429 | 检查本机 key、账户余额或服务限额；用显式 `--live` probe 检查 |
| 后端切换后没有自动说话 | 这是显式生命周期：应用选择后重新点“开始对话” |
| 外放导致误打断 | 先用耳机验收，再测试扬声器和浏览器回声消除；必要时关闭自动插话进行定位 |
| 8790 / 8791 / 8792 已占用 | 查清占用服务，或在 `.env` 设置三个互不冲突的端口后重启 |

本地引擎详情可通过以下方式输出。日志会遮蔽凭证，但可能含对话文字：

```powershell
.\.venv-speech\Scripts\python.exe -m her_voice --engine-log
```

## 7. 可复用模块和项目结构

```text
her_voice/
  app.py                 本地 HTTP / Realtime WS、音色管理 API
  voices.py              参考录音验证、持久化和后端选择
  tts.py                 两种后端配置与进程生命周期
  drama.py               原生 audio.cpp 进程和 voice preset
  drama_resampler.py     固定上游的流式 SoXR 兼容层
  engine.py              speech-to-speech worker / supervisor
  gateway.py             DeepSeek 流式网关
  models.py              固定模型快照准备
web/
  client.js              可复用 VoiceClient
  protocol.js            会话、字幕、取消状态机
  audio.js               麦克风、PCM、Web Audio 播放
  capture-worklet.js      实时采集
  index.html / ui.js / styles.css
config/                  固定依赖、上游来源与语音模板
scripts/                 Windows 安装、模型准备、启动与诊断
examples/                HER / Joyboi 配置示例、测试 Agent
tests/                  Python 与 JavaScript 测试
docs/                   架构、来源、验收与证据
dist/                   her_windows_voice-0.4.0-py3-none-any.whl
```

HER / Joyboi 可以接入 `VoiceClient`，或调用同源 HTTP/WS 接口；角色、工具和记忆由各自应用提供。当前已经实现复用接口和隔离测试，**没有实际连接你的 Joyboi 项目**；单个 runtime 只有一个 live slot，也不提供两个产品同时共享一份 GPU 模型的调度服务。

```js
import { VoiceClient } from './client.js';
const voice = new VoiceClient({ baseUrl: '/', mode: 'live' });
voice.subscribe(state => renderYourUI(state));
await voice.loadVoiceSettings();
// 用户选择音色后：
await voice.applyVoiceSettings({ backend: 'drama3', voice_id: savedVoiceId });
// 在用户点击事件中执行 await voice.connect()；离开时 await voice.destroy()。
```

`renderYourUI`、`savedVoiceId` 由接入方提供。完整接口及 Agent adapter 例子见 [REUSE.md](docs/REUSE.md)。wheel 包含运行模块、web 和 config；Windows 原生安装脚本与文档在完整 ZIP 内。

## 8. 测试和固定来源

```powershell
.\.venv-speech\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.test.mjs
```

Node / Playwright 只用于开发测试，运行助手不要求前端构建。额外 HTTP 源码契约测试需要固定上游源码和测试依赖；没有这些条件时会明确跳过，见 [VALIDATION.md](docs/VALIDATION.md)。

当前测试覆盖真实 loopback HTTP/WS、子进程退出、双后端选择、上传/持久化、取消旧回复、连续重采样、实际浏览器 AudioWorklet 和界面联调。Windows 编译、模型推理、显存/内存峰值、真实 DeepSeek、声学回声与端到端延迟留给 [WINDOWS_ACCEPTANCE.md](docs/WINDOWS_ACCEPTANCE.md) 记录。

- speech-to-speech：`0ed1bb95bd335aa89bb7c172b6a347b6dae596f8`
- audio.cpp：`0bf4d155f2af5f85853415170622dc145e3d62c3`
- Drama GGUF：`17a5b40c5dc2c2be986f5371a7e650108b828e29`
- Qwen Base：`5d83992436eae1d760afd27aff78a71d676296fc`

链接和依赖细节见 [UPSTREAM.md](docs/UPSTREAM.md)、[DEPENDENCIES.md](docs/DEPENDENCIES.md)。兼容层见 [PATCHES.md](docs/PATCHES.md)，第三方许可入口见 [THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md)。本项目新增代码使用 MIT；第三方代码、模型和服务适用其自身许可与条款。
