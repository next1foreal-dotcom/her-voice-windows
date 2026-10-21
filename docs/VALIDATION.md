# 0.4.0 实际验证记录

日期：**2026-10-20 UTC**。本版实现 Drama 3 原生量化主后端、Qwen3-TTS Base 备用、音色导入与切换。以下区分已经执行的代码/传输测试、固定源码契约检查和仍需 Windows 真机完成的验证。

**没有下载大模型权重，没有使用真实 DeepSeek key，没有执行 4080 SUPER 推理。** HER / Joyboi 的实际产品代码尚未接入。

## 1. 本版已经执行的检查

| 检查 | 结果 | 证据范围 |
|---|---|---|
| Python 全量回归 | **162 / 162 通过，0 失败、0 跳过** | 本地 HTTP/WS、子进程、mock 模型/云端接口、音色和模型切换 |
| JavaScript 用例 | **43 / 43 通过，0 失败、0 跳过** | 四个文件分别执行，并补跑整体 runner |
| 双后端上游配置 | 两条配置都通过 | 固定上游实际 `load_config` / `validate_config` / `resolve_config` |
| Drama 流式传输与 DSP | 已包含于 162 项中的 9 项 | 固定 handler 的实际方法、真实 loopback HTTP、SoXR、取消与 CRLF 兼容 |
| 浏览器原有 UI 演练 | 8 项通过 | 实际页面、字幕、暂停、混合文字、响应式布局；演练数据 |
| 浏览器原生音频路径 | 5 项通过 | 真实 getUserMedia / AudioWorklet / Web Audio、合成麦克风、本地 fake engine |
| 音色设置 UI fixture | 5 / 5 通过 | 导入/应用失败重试、表单与布局 |
| 音色设置真实 API 联调 | 8 / 8 通过 | 真实 create_app、上传/归一化、播放中切换、权限、刷新持久化 |
| 0.4.0 wheel | **通过**，见 `evidence/wheel.json` | 离线构建/安装、不同工作目录的内置资源与本地 HTTP；不代表语音依赖已安装 |

Python/JavaScript 机器可读记录：[implementation-tests-0.4.json](evidence/implementation-tests-0.4.json)。浏览器本版汇总：[voice-settings-browser-2026-10-20.json](evidence/voice-settings-browser-2026-10-20.json)。双后端配置记录：[tts-profile-contract-2026-10-20.json](evidence/tts-profile-contract-2026-10-20.json)。

开发环境是 **Linux x86_64、CPython 3.12.14、aiohttp 3.14.4、PyYAML 6.0.3、NumPy 2.3.5、SciPy 1.17.0**。Windows 安装器目标为 CPython 3.11，aiohttp 固定 3.14.5。测试环境和目标环境的这些区别仍需 Windows 验收，不能把 Linux 结果写成 Windows 安装成功。

## 2. Python 测试明细

```text
Ran 162 tests in 8.616s
OK
```

| 文件 | 数量 | 覆盖 |
|---|---:|---|
| `test_app.py` | 10 | 来源/token、静态资源、live slot、WS 桥接、取消保护 |
| `test_gateway.py` | 21 | DeepSeek thinking、SSE 完整事件、中英分段、错误/取消与凭证处理 |
| `test_settings.py` | 16 | 配置解析、环境优先级、provider/端口/workspace 隔离 |
| `test_deepseek_probe.py` | 11 | 显式 live 开关、SSE 结束/usage/error、错误不回显 key |
| `test_doctor.py` | 7 | 依赖/平台判定、CUDA 结果解析、未验证状态 |
| `test_engine.py` | 17 | 子进程、readiness、取消启动、旧进程隔离、凭证与 loopback 代理绕过 |
| `test_pin_silero.py` | 10 | 固定来源/hash、单行 ref 修补、幂等、未知代码拒绝 |
| `test_models.py` | 11 | 固定完整快照、嵌套 tokenizer、错误路径、Drama 不下载 Qwen |
| `test_providers.py` | 7 | DeepSeek/generic provider 契约和显式选择 |
| `test_reuse.py` | 7 | 两个 app 的配置/网关隔离、真实本地假 Agent adapter |
| `test_drama_runtime.py` | 9 | native 安装记录、所选音色、量化/缓存参数、owned process/readiness |
| `test_drama_setup.py` | 12 | 固定下载参数、完整 hash/size、原子 manifest、旧配置保留、dry-run |
| `test_drama_stream.py` | 9 | 分块 HTTP、PCM16 字节边界、44.1→16k、取消、JSON 错误、CRLF |
| `test_tts_selection.py` | 6 | 引用必填、双后端/同录音、取消暖机、失败清理和重启竞态 |
| `test_voices.py` | 9 | WAV 规范化、持久化、非法输入、上传/API、切换关闭会话 |

模型类、模型权重下载和云端服务使用测试替身。实际启动的子进程是受控 fixture。安装器的 Python 准备逻辑有测试；PowerShell 原生构建尚未执行。

回归测试重现并修复了一个真实生命周期问题：旧启动失败任务的清理操作可能在用户停止并重新开始后杀掉新 Drama 进程。现在失败清理和显式停止使用互斥锁，只有前一代清理完成后才发布可重新启动状态。

## 3. Drama 的源码契约与连续音频

测试校验固定 `openai_compatible_handler.py` 的 SHA256，再从其原始 AST 中直接执行 HTTP/PCM 方法，排除与这项检查无关的 GPU 推理 imports。这是实际传输代码测试，**不是完整 speech-to-speech 模型启动**。

已确认：

- `response_format=pcm` 的路径发送 `stream_format=audio`，在 HTTP 响应结束前交出首个音频块；`openai_tts_stream=False` 不会把 PCM 改成整段等待。
- audio.cpp 固定路径返回 44.1kHz PCM16；应用使用连续 SoXR 状态转为上游 16kHz 音频块，Realtime 层再输出其协议要求的 24kHz。
- HTTP 将一个 PCM16 样本拆成奇数字节块时，样本仍正确重组；跨块连续性、音高、长度和末尾 flush/补齐经过测试。
- 首音前取消和流中取消均停止继续产出旧音频；新的响应不会复用上一个响应的重采样状态；JSON 错误不被当成音频。
- 仅 Drama worker 安装内存中的重采样兼容层。未知源码会被拒绝；Windows CRLF 只在 hash 前归一为 LF，其他字节变化仍拒绝。

局部 CPU 微测发现上游逐点零插值 FIR 在 160/441 比率下开销很大，因此改用 `soxr==1.0.0`。微测仅表示重采样成本，不是 Windows 首音/端到端延迟。详见 [PATCHES.md](PATCHES.md)。

**传输取消不等于 GPU 立即抢占。** 固定 native runtime 在下一个音频事件/写出错误时响应断开；首音前长 prefill 的即时停止没有验证。显式停止模型会结束本应用自己的原生进程。

## 4. JavaScript 与浏览器验证

四个测试文件分别执行的结果是：audio 10、client 12、protocol 12、voice-settings 9，总计 43。整体 `node --test tests/*.test.mjs` 同样通过；本环境的聚合输出以 4 个测试文件计数，所以精确用例数来自逐文件原生 test 输出。

```powershell
node tests/audio.test.mjs
node tests/client.test.mjs
node tests/protocol.test.mjs
node tests/voice-settings.test.mjs
```

新增测试确认：读取音色配置、实际 multipart 字段和 token、应用前断开/清播放、保留已完成字幕、禁止重复切换、失败保持服务端确认状态、晚到结果不污染已销毁 client。所有 session 更新省略 `audio.output.voice`，以保留服务端选中的参考音色；不会再以旧固定声线覆盖 Drama 或 Qwen Base。

四个浏览器脚本合计 **26 项检查通过**，没有意外页面错误。浏览器测试沿项目隔离 Playwright 路径执行，使用 Chromium headless 155.0.8059.84。原 UI 演练和真实浏览器音频 API 检查都通过，未发出外部请求。音频检查让合成麦克风穿过真正的 AudioWorklet 和本地 WS bridge，确认 24kHz PCM、旧 response ID 取消、迟到音频丢弃、播放耗尽后状态转换以及设备清理。

`speech_started` 在音频测试中由 fixture 产生，因此并未测试实际 Silero/Smart Turn 准确率。合成设备也不代表 Windows 实体麦克风、扬声器或回声消除效果。

新音色面板另外验证上传/应用失败与重试、名称和转录文本、安全文本渲染以及 1536/390/320 像素布局；真实 Python API 的联调还确认了双声道 WAV 归一为单声道、播放中应用另一个后端时停止音频并保留已完成历史、无 token 返回 403、非法后端返回 400，以及刷新后恢复已保存选择。两种后端仍指向同一音色；此流程没有启动模型。真实 API 和 fixture 结果在报告中分别标记。

## 5. 如何重跑

完成 Windows 依赖安装后：

```powershell
.\.venv-speech\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.test.mjs
```

Drama HTTP 契约测试会从已安装的固定 speech-to-speech 定位源文件；也可通过 `VOICE_UPSTREAM_HTTP_SOURCE` 指向该固定 handler 原文。需要 NumPy、SciPy、HTTPX 和固定 SoXR；缺少条件时明确报告 skip。本次使用隔离测试依赖及已检查的固定 handler，**全部 162 项均执行，没有 skip**。

浏览器测试额外需要 Node 和 Playwright；它们不是正式运行依赖：

```powershell
npm install --no-save playwright@1.62.1
npx playwright install chromium
$env:VOICE_PYTHON = (Resolve-Path '.\.venv-speech\Scripts\python.exe').Path
node scripts/browser-audio-check.mjs
node scripts/browser-voices-check.mjs
```

`browser-check.mjs` 连接单独启动的 `python -m her_voice --demo`；音频/音色 fixture 脚本自行建立隔离服务。新增 `browser-voices-api-check.mjs` 需要独立的空 demo 工作区、没有 provider 凭证，并通过 `VOICE_QA_URL` 指向它；脚本会写入合成参考音色，拒绝使用已有音色的工作区。其精确启动命令见浏览器证据 JSON。可以设置 `VOICE_CHROMIUM` 指定浏览器可执行文件，报告中的浏览器版本才是该次结果对应的版本。

离线 wheel 检查使用现有构建和 aiohttp 依赖，不解析完整语音环境：

```powershell
.\.venv-speech\Scripts\python.exe scripts\check-wheel.py
```

## 6. 必须在目标电脑验收的部分

| 未测项目 | 需要记录 |
|---|---|
| Windows 原生安装 | Python 3.11、固定 pip resolver、PowerShell、VS/CUDA 编译结果 |
| 模型实际载入 | GGUF/ASR/Qwen、cuDNN/CTranslate2 DLL、首轮预热 |
| 资源使用 | 每种模式的峰值显存、系统内存、pinned-host 内存与切换后的释放 |
| 端到端速度 | 句末判断、ASR 完成、DeepSeek 首 token、TTS 首音及持续实时率 |
| 中英质量 | 简体回答遵循、识别率、混合句读音、音色相似度与自然度 |
| 实际打断 | 耳机和外放分别测试、误触率、旧音频停止时间、native 长 prefill |
| 持续运行 | 多轮、快速停止/重启、后台异常、设备断开与 20 分钟资源变化 |

可直接填写 [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md)。本次测试不提供未经测量的延迟、吞吐或显存承诺。

## 7. 历史证据

`bilingual-routing.json`、`bilingual-sources.json` 等旧证据保留其原始测试对象和日期；其中 0.3/0.3.1 的 CustomVoice 声线与 profile hash 属于历史版本，不作为 0.4 Base/Drama 的证明。本版以本文件、0.4 测试报告和双后端配置报告为准。
