# 0.4：在 HER、Joyboi 或其他应用中复用语音层

本项目提供独立 HER Voice、无 UI 依赖的浏览器 `VoiceClient`、本地语音服务和可替换的文字 gateway。**0.4 已接入 Drama 3 和 Qwen3-TTS 0.6B Base 两种 TTS、参考音色库及显式后端切换**。这些模块可以由 Joyboi 复用，但本交付没有修改 Joyboi／HER 真实产品的代码，也没有假定其角色、记忆或工具接口已经存在。

最小接法是由宿主保留自己的画面和 agent，复用本套件的收音、识别、字幕、播放、参考音色和打断。宿主通过 Chat Completions adapter 返回可朗读的文字；也可以先由宿主 launcher 打开现成的本地 HER Voice 页面。

## 1. 模块与责任

| 模块 | 本项目提供 | 宿主负责 |
| --- | --- | --- |
| `VoiceClient` | 连接与音频生命周期、字幕、暂停／打断、旧回复隔离、声音设置 API | 自己的 UI、操作入口、可见错误与浏览器音频权限 |
| `VoiceStore` | WAV 验证、单声道标准化、随机音色 ID、元数据及选择持久化 | 提供合适的参考素材，决定是否共享一个 workspace |
| `VoiceEngine` | Drama／Qwen profile、所拥有进程的启动、顺序停止、单后端选择 | 启动／退出策略，目标 Windows／GPU 设备验收 |
| 文字 gateway | DeepSeek／generic Chat Completions、SSE 完整性、认证与错误边界 | 自有 agent adapter、角色 prompt、会话身份、记忆和工具 |
| `web/app.js` | 独立 HER Voice 的薄 UI adapter | 可直接使用，也可替换成宿主自己的组件 |

原始麦克风音频留在浏览器与本地语音服务之间；识别文字和本轮上下文发给所选文字 provider。导入的参考音色保存在本地 workspace。宿主 adapter 如果继续转发其他服务，数据路径由宿主决定；使用本地 TTS 不等于整个系统离线。

一个 launcher 目前只允许一个 live session。多个应用可以复用同一套代码和下载缓存，但两个 launcher 会各自启动模型进程，可能重复占用显存。端口隔离不提供共享 GPU 调度、多租户授权或跨应用会话隔离。4080 SUPER 上建议先由一个语音 sidecar 服务当前使用的宿主，而不是同时加载两份服务。

## 2. 部署和 workspace

Live 应由目标 workspace 下的 `.venv-speech` Python 启动，使用 README 中固定的 speech-to-speech、依赖和补丁。仅安装 wheel 可以启动 web 服务／demo，不能代替模型、CUDA 环境及 Drama 原生构建。Drama 的准备步骤见 [DRAMA_SETUP.md](DRAMA_SETUP.md)。

```powershell
& "C:\Voice\Joyboi\.venv-speech\Scripts\python.exe" -m her_voice --workspace C:\Voice\Joyboi --port 8890 --engine-port 8891 --app-id joyboi-dev --app-name "Joyboi Voice" --open
```

这个命令只配置运行位置和应用标识，不会自动寻找 Joyboi 源码。Wheel 内含 web／config 资源，用户文件写入 workspace；不需要修改 site-packages 中的前端资源。Supervisor 使用启动它的 Python，不会自动寻找另一套语音虚拟环境。

| 配置 | 含义 |
| --- | --- |
| `VOICE_PROVIDER` | `deepseek` 或 `openai-compatible` |
| `VOICE_PROVIDER_URL` | generic 的完整 POST URL，例如 `http://127.0.0.1:9001/v1/chat/completions` |
| `VOICE_PROVIDER_MODEL` | generic 的明确模型标识 |
| `VOICE_PROVIDER_API_KEY` | generic key，只由本地 gateway 持有 |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` | DeepSeek 模式配置；显式 generic 命名的 key／model 可在该模式覆盖它们 |
| `VOICE_APP_ID`、`VOICE_APP_NAME` | launcher 的应用标识与显示名称 |
| `VOICE_PORT`、`VOICE_ENGINE_PORT` | UI／gateway 和 speech worker 的 loopback 端口 |
| `VOICE_DRAMA_PORT` | native Drama 的独立端口，默认 8792，须与前两者不同 |
| `VOICE_TTS_BACKEND` | 没有已保存选择时的默认后端，`drama3` 或 `qwen3`；已保存选择优先 |
| `VOICE_PROFILE`／`--profile` | 语音基础 profile；相对路径以 workspace 为准，当前 pipeline 名称须为 `windows4080` |

宿主 adapter 的 `.env` 示例：

```dotenv
VOICE_PROVIDER=openai-compatible
VOICE_PROVIDER_URL=http://127.0.0.1:9001/v1/chat/completions
VOICE_PROVIDER_MODEL=host-adapter
VOICE_APP_ID=joyboi-dev
VOICE_APP_NAME=Joyboi Voice
VOICE_PORT=8890
VOICE_ENGINE_PORT=8891
VOICE_DRAMA_PORT=8892
VOICE_TTS_BACKEND=drama3
```

generic 模式不会回退到旧 `DEEPSEEK_*`。无 key 只允许 loopback adapter；远端 endpoint 需要 HTTPS 和 key。Provider 出错不会切换到另一家 provider 或 demo。

Python 宿主可以构造 `Settings` 并调用 `create_app(settings)` 得到 `aiohttp.Application`。相关字段为 `root`、`provider`、`provider_url`、`provider_api_key`、`provider_model`、`app_id`、`app_name`、`port`、`engine_port`、`drama_port`、`tts_backend` 和 `profile_path`。资源默认来自 wheel；仅需自定义资源时覆盖 `asset_root`／`web_root`。嵌入式运行同样需要语音环境，并保留 CLI 使用的 `handler_cancellation=True`，使断线可以取消 gateway handler。

### 当前同源契约

`VoiceClient({baseUrl:'/voice/'})` 表示同源路径前缀。跨端口也是跨 origin；不能把 `baseUrl` 指向另一个地址就假定跨域可用。

可直接在 Native launcher／WebView 中打开本机页面，或由宿主实现同源 reverse proxy。Proxy 必须成套转发 API、WS upgrade、JS／worklet，处理路径前缀，并满足本地服务的 loopback Host 和同源 Origin 检查。此版不自带宿主 proxy，也没有放开远端 HTTPS 页面直连 HTTP loopback 的浏览器限制。

## 3. 浏览器 JS API

从 [`web/client.js`](../web/client.js) 导入 `VoiceClient`。它不依赖 HER 的 DOM、`createUI` 或 localStorage，但仍需要浏览器的 Web Audio、AudioWorklet、WebSocket 和麦克风 API，不是 Node 音频 SDK。

```js
import {VoiceClient} from './client.js';

const voice = new VoiceClient({baseUrl: '/', mode: 'live'});
const offState = voice.subscribe(snapshot => renderHostUI(snapshot));
const offLevels = voice.onLevels(({input, output}) => renderHostMeters(input, output));
const offError = voice.onError(error => showHostError(error.message));

await voice.initialize();
await voice.loadVoiceSettings(); // 不启动模型，也不调用文字 provider。

// file 来自用户选择的 .wav File；录音文字必须与素材一致。
async function importAndSelect(file, name, transcript, backend = 'drama3') {
  const imported = await voice.importVoice({audio: file, name, transcript});
  if (!imported) return false;
  return voice.applyVoiceSettings({backend, voice_id: imported.id});
}

// 在用户点击事件中调用，满足浏览器音频启用要求。
async function startFromUserClick() {
  try { await voice.connect(); }
  catch { /* onError 已通知；宿主还可更新按钮状态。 */ }
}

async function disposeHostView() {
  offState(); offLevels(); offError();
  await voice.destroy();
}
```

`renderHostUI`、`renderHostMeters`、`showHostError` 是宿主自行实现的函数。初始化、导入和应用调用的 rejected Promise 也要由宿主处理，`onError` 只负责通知。

| API | 返回值与契约 |
| --- | --- |
| `new VoiceClient({baseUrl='/', mode='live', fetch, WebSocket, audioFactory})` | 可注入传输／音频依赖测试；默认使用浏览器实现 |
| `initialize()` | async，读取 bootstrap／health 和控制 token，返回 snapshot |
| `loadVoiceSettings()` | async，读取后端配置和音色列表，成功返回 `true`，结果写入 `state.voiceAdmin` |
| `importVoice({name, transcript, audio})` | async，`audio` 为带 `.wav` 名称的 File／Blob；成功返回音色元数据，不自动选择、不启动推理 |
| `applyVoiceSettings({backend, voice_id})` | async，关闭当前音频／session，要求服务端停止旧进程后保存设置；成功返回 `true`，不自动重连 |
| `connect()`／`disconnect()` | async，开始／结束语音连接；disconnect 保留已完成文字，不等同于停止整个模型进程 |
| `pause()`／`resume()` | async，显式暂停／恢复，不需要宿主猜测 toggle 状态 |
| `interrupt()` | async，清理本地播放并建立下一轮可用的 session 边界 |
| `sendText(text)` | async，连接且未暂停时提交；重建 session 隔离旧 ASR |
| `updateSettings(partial)` | async，仅更新 `micDeviceId`、`outputDeviceId`、`captions`、`bargeIn` |
| `clearHistory()` | async，断开并清空本实例的语音上下文；不删除参考音色或宿主数据库 |
| `setMode('live' \| 'demo')` | async，显式切换模式并重设语音会话 |
| `destroy()` | async，释放当前 client 的连接、音频和事件资源 |
| `state` | 只读的 detached／frozen snapshot；不直接修改 |
| `subscribe(listener)` | 同步订阅并立即收到 snapshot；返回 unsubscribe |
| `onLevels(listener)` | 同步订阅并立即收到 `{input, output}`；返回 unsubscribe |
| `onError(listener)` | 订阅后续 `Error`；返回 unsubscribe |

多数操作成功返回 boolean；忙碌、过期或无需变更时可能返回 `false`。`disconnect()`、`clearHistory()`、`destroy()` 完成后不返回数据。`importVoice()` 成功返回对象，不能与一般 boolean 操作混淆。订阅事件和 resolved Promise 都不是宿主持久任务的完成 ACK。

### 0.4 的声音设置状态

Snapshot 原有 `mode`、`connection`、`state`、`caption`、`messages`、`settings`、`metrics`、`devices`、`paused`、`error`、`health` 保留，新增 `voiceAdmin`：

```js
{
  loading: false,
  busy: false,
  config: {backend: 'drama3', voice_id: '...'},
  voices: [{id: '...', name: '我的声音', transcript: '...', /* 元数据 */}],
  error: '',
  message: '',
  importedVoiceId: '...' // 导入后出现，方便宿主选中刚保存的项。
}
```

`config` 和 `voices` 初始分别为 `null` 和空数组。声音管理 API 在同一 client 内串行处理；导入完成后可刷新列表，应用设置后保留已完成的文字上下文。改变后端需要重新点击开始；这会加载新模型，不是毫秒级热切换。

同一份参考音色可以显式改用备用后端：

```js
const voiceId = voice.state.voiceAdmin.config?.voice_id;
if (voiceId) {
  await voice.applyVoiceSettings({backend: 'qwen3', voice_id: voiceId});
  // 待用户确认开始，再在点击事件中调用 voice.connect()。
}
```

SDK 不自行读写 localStorage。现成 `app.js` 只保存设备／字幕等偏好；声音选择由本地服务保存，字幕不是跨应用的共享会话。

### 中文和英文

同一客户端支持中文、英文和混合文字。默认 prompt 遵从明确的回答语言要求；否则中文用简体、英文用英文，保留混合语句中的术语。宿主替换 system prompt 时，应明确保留自己的语言政策；本模块不会再次翻译 host 返回的文字。

客户端发送 `transcription.language:null` 清除会话单语覆盖，省略 `audio.output.voice`，让服务端选中的参考音色生效。不要添加旧内置 speaker 或任意语言字段覆盖当前 profile。Qwen 保留自动语言模式，Drama 请求不指定固定语言。真实发音和音色质量仍要验收，详见 [BILINGUAL.md](BILINGUAL.md)。

## 4. 本地 HTTP 和 WebSocket 契约

普通浏览器集成优先调用 `VoiceClient`；原生宿主或自己的 SDK 可以实现以下同源 HTTP 契约。`GET /api/bootstrap` 返回每次 launcher 启动的 token，写操作带 `X-Voice-Token`。WebSocket 使用 query token，因为浏览器 WebSocket 构造函数不能设置这个自定义 header。所有路由仍受 loopback Host／Origin 检查，不应把 token 当成远程多租户认证。

| 请求 | 请求体／返回值 | 行为 |
| --- | --- | --- |
| `GET /api/bootstrap` | `{token, health}` | 读取控制凭据和公开状态 |
| `GET /api/status` | health 对象 | 读取文字设置、engine 和 `tts` 状态，不返回 key／绝对素材路径／日志尾部 |
| `GET /api/voices` | `{voices: [...], selected_voice_id}` | 返回导入的音色元数据 |
| `POST /api/voices` | multipart：恰好 `name`、`transcript`、`audio` 三个字段；成功 HTTP 201 `{voice: {...}}` | 验证并保存素材，不选择音色、不启动模型 |
| `GET /api/tts` | 下方 TTS 设置对象 | 读取当前选择和素材限制 |
| `PUT /api/tts` | JSON：恰好 `{backend, voice_id}`；成功返回 TTS 设置对象 | 关闭本应用的 sessions，停止所拥有进程，再保存选择；不启动新模型 |
| `POST /api/engine/start` | 无需请求体；返回 engine 状态 | 后台准备所选模型；收到 200 不代表已经 ready |
| `POST /api/engine/stop` | 无需请求体；返回 engine 状态 | 关闭本应用 sessions，停止所拥有进程 |
| `GET /ws/realtime?mode=live&token=...` | WebSocket 协议升级 | 单 live session，连接到已准备好的 speech worker |
| `GET /ws/realtime?mode=demo&token=...` | WebSocket 协议升级 | 协议演练，绕过模型、ASR 和文字 provider |

TTS 设置对象示例：

```json
{
  "backend": "drama3",
  "voice_id": "0123456789abcdef0123456789abcdef",
  "backends": [
    {"id": "drama3", "label": "Drama 3 · 主用"},
    {"id": "qwen3", "label": "Qwen3-TTS Base · 备用"}
  ],
  "reference_required": true,
  "reference_ready": true,
  "error": null,
  "reference_limits": {
    "max_bytes": 12582912,
    "min_seconds": 1,
    "max_seconds": 30,
    "format": "PCM16 WAV",
    "recommended_seconds": "5–15"
  }
}
```

新 workspace 的 `voice_id` 为 `null`。`reference_ready` 只表示存在有效的保存选择，不证明模型已安装、原生 CUDA 可用或某段音色已通过合成试音。

音色元数据包含 `id`、`name`、`transcript`、`duration_seconds`、`sample_rate`、`channels`、`created_at`。允许 1–30 秒、最多 12 MiB 的 16 位 PCM WAV，8–96kHz，单／双声道；服务端标准化为单声道，保留采样率。名称最多 80 字符，文字最多 2,000 字符。上传文件名不会作为服务器路径，当前没有公开任意文件路径导入或参考 WAV 下载接口。

无效上传／选择通常返回 400 `{"error":"..."}`；音色目录或引擎状态冲突返回 409；token／Origin 不符合要求时为 403。WS 在已有 live session 或正在切换时拒绝新 live 连接，worker 未就绪／正在释放时可返回 503。SDK 负责解析和显示可用错误；自有客户端也应处理非 JSON 的框架拒绝响应。

公开 health 的 `provider_ready` 表示文字设置完整，无 key 的 loopback adapter 也可 ready；它不是网络连通性或 API key 有效性的证明。`engine.ready` 代表启动检查完成，`engine.tts_process` 是 Drama 子进程状态，不包含原生日志。原生 HTTP ready 不等于完成首次 GPU 推理。

Realtime 外部音频格式固定为 24 kHz PCM16LE；TTS 内部的 Drama 44.1 kHz 由 handler 转换，宿主不应按 native 速率播放 Realtime 数据。会话扩展和旧事件处理直接参考 [`web/protocol.js`](../web/protocol.js)，不要省略 voice／language 的上述规则。

## 5. 宿主文字 adapter 契约

`CompletionGateway` 调用完整 POST endpoint，使用 OpenAI Chat Completions 的 `model`、`messages` 和 `stream`。初次接入可让 adapter 无状态地处理完整 messages 快照，避免把每次请求中的相同历史重复追加到宿主记忆。

generic 模式附带两个提示 header：

| Header | 含义 |
| --- | --- |
| `X-Voice-App-ID` | launcher 配置的固定应用 routing hint |
| `X-Voice-Request-Purpose` | 当前固定管线按 non-stream／stream 请求区分 `warmup`／`conversation` |

这些字段都不是用户认证、租户隔离、工具授权或 conversation ID。暖机必须无副作用。DeepSeek 模式不发送上述自定义 headers。

`stream:true` 时返回 `Content-Type:text/event-stream`，可朗读内容放在 `choices[].delta.content`，最后发送带 `finish_reason` 的 choice 和独立 `[DONE]` 事件：

```text
data: {"id":"answer_1","object":"chat.completion.chunk","model":"host-adapter","choices":[{"index":0,"delta":{"content":"我在。"},"finish_reason":null}]}

data: {"id":"answer_1","object":"chat.completion.chunk","model":"host-adapter","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

```

中文 UTF-8 可以跨网络 chunk，但 SSE 事件必须正确结束。缺少完整 `[DONE]` 的响应会被判为未完成。`stream:false` 暖机返回标准 JSON `chat.completion`，包含 `choices[0].message` 的 assistant 文字和 `finish_reason`，不建立后台持久任务。

宿主可以在 adapter 内调用自己的角色、记忆或工具，再返回最终可朗读文字。本语音管线不执行 `tool_calls`；gateway 保留部分工具字段，不等同于存在完整工具循环。角色 prompt 与语音长度／语言政策如何合并，由宿主明确决定。

## 6. 会话、历史和取消

当前没有端到端 host conversation ID 或 task ID。`app_id` 标识 launcher，`response_id` 标识语音生成，client epoch 隔离晚到事件。正式宿主如果需要持久记忆、恢复旧任务或工具调用，须自己增加 session 绑定、turn identity、幂等与提交规则。

暂停、手动打断和文字输入会重建 session，只恢复最近最多 30 条 `complete` 且非空的文字。未完成、失败或已中断回复不恢复。自然插话在当前 session 中取消，目前没有精确的 heard-history truncation；完整字幕不能直接作为用户确实听到的内容保存。

浏览器会清理本地播放队列，取消对应生成和 HTTP 连接，并隔离旧音频。这些边界已有自动化测试。Drama 原生 GPU 在首音前长 prefill 中未必立即检查断线，不能承诺插话一发生就停止全部 CUDA 计算；显式停止引擎／切换后端会结束本应用拥有的进程。详情见 [ARCHITECTURE.md](ARCHITECTURE.md)。

Voice 层的取消不构成宿主持久任务的取消授权、命令或成功回执。宿主已提交的工具操作、后台工作与不可撤回结果仍由宿主自己的生命周期接口管理。`clearHistory()` 也不删除宿主记忆或工作。

## 7. 建议整合顺序与验证

1. 先使用 `examples/agent_stub.py --port 9001` 的无副作用 mock，验证 non-stream 暖机、中文 SSE、完整 `[DONE]` 和 app／purpose headers。
2. 用 `VoiceClient({mode:'demo'})` 接宿主 UI，测试字幕、暂停、打断、恢复、销毁和错误显示。Demo 绕过模型，不证明 provider 已连通。
3. 使用声音管理 API 导入一段参考 WAV，显式选择 Drama 或 Qwen，检查 GET／PUT 契约；此时不必加载大模型。
4. 在完整 Windows 语音环境中先连接 stub，验证真实麦克风 → ASR → stub → 所选 TTS → 扬声器；分别测中文、英文、参考音色和打断。
5. 切换后端，确认旧进程停止、显存释放且新选择不会自动开始，再测第二套后端。
6. 最后接真实 host adapter，验证自己的会话、记忆和工具语义；按 [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md) 完成设备与整体性能验收。

当前自动化覆盖入口包括 [`tests/voice-settings.test.mjs`](../tests/voice-settings.test.mjs)、[`tests/test_voices.py`](../tests/test_voices.py)、[`tests/test_tts_selection.py`](../tests/test_tts_selection.py)、[`tests/test_app.py`](../tests/test_app.py)、[`tests/test_reuse.py`](../tests/test_reuse.py) 和 [`tests/test_drama_stream.py`](../tests/test_drama_stream.py)。它们验证模块和契约；真实 Joyboi 适配、模型音质和 Windows 音频设备仍须实机集成。
