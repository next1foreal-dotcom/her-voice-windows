# 0.4 上游兼容修改清单

当前固定 speech-to-speech 为 [`0ed1bb95bd335aa89bb7c172b6a347b6dae596f8`](https://github.com/huggingface/speech-to-speech/commit/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8)。本项目有三项明确的兼容处理，作用和校验规则不同：

| 项目 | 发生时间 | 修改范围 | 未识别源码时 |
| --- | --- | --- | --- |
| Silero 固定 revision | Windows 安装阶段 | 只改隔离环境中的一个源码字符串；live 启动只验证 | 拒绝修改／启动，要求重装固定依赖 |
| Drama 连续 SoXR 重采样 | Drama speech worker 启动阶段 | 校验已安装源码后，只替换该进程内的一个类；不改磁盘文件 | 拒绝启用，不静默使用旧的慢实现 |
| Drama 短音频预热 | Drama speech worker 启动阶段 | 校验相同固定源码，只限制启动探测，不改正式生成和磁盘文件 | 拒绝安装 hook |

Qwen 后端不安装 Drama hook。两种 TTS 的切换通过结束旧 worker 再启动新 worker 实现，内存替换不会跨后端继续存在。

## 1. Silero：固定 Torch Hub revision

### 原因与唯一修改

固定上游的 [`VAD/vad_handler.py`](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/VAD/vad_handler.py) 硬编码 `snakers4/silero-vad:master`。公开 VAD 配置不提供模型路径或 revision；直接保留浮动 master 会破坏固定模型来源。

本项目只替换下面这个字符串：

```diff
-snakers4/silero-vad:master
+snakers4/silero-vad:1e261b036686cd0017d500ee96acd1c4ba572a9d
```

目标安装路径是 `speech_to_speech/VAD/vad_handler.py`。没有全局 Torch Hub monkeypatch，上游的 `trust_repo=True`、`skip_validation=True` 和正常缓存行为不变。

所选 [Silero commit](https://github.com/snakers4/silero-vad/commit/1e261b036686cd0017d500ee96acd1c4ba572a9d) 中的 [`hubconf.py`](https://github.com/snakers4/silero-vad/blob/1e261b036686cd0017d500ee96acd1c4ba572a9d/hubconf.py) 从同一仓库快照的 `src/silero_vad/data/silero_vad.jit` 加载权重，没有再使用另一条浮动权重 URL。PyTorch 2.11.0 的 [`torch/hub.py`](https://github.com/pytorch/pytorch/blob/v2.11.0/torch/hub.py) 把指定 ref 放入 GitHub archive URL，支持完整 commit。

### 精确字节清单

Silero 检查原始文件字节，要求对应 LF 文件，不执行空白或换行标准化：

| 源码状态 | SHA-256 |
| --- | --- |
| 固定 upstream 原始文件 | `d6feeb9f4442240c9a493ac86ec076033882c8efece6020d2ec7cfbfebd70b7c` |
| 完成唯一字符串替换后 | `2613d149b238b4c50b9ff2ae9da3f22ff6db527d9a2efac40fb9dfcc764db7f1` |

还会检查 `speech-to-speech==1.0.0` 与 pip `direct_url.json` 来源。只接受固定 GitHub archive URL，或来自同一仓库且 resolved commit 完全相同的 Git 安装。版本号相同但来源不明时仍会拒绝。

### 应用与验证

基础 Windows 安装器会在固定上游和本套件安装完后执行补丁。如需重试，先停止语音引擎，再在项目 workspace 运行：

```powershell
.\.venv-speech\Scripts\python.exe scripts\pin-silero.py
```

只检查，不改文件：

```powershell
.\.venv-speech\Scripts\python.exe scripts\pin-silero.py --check
```

实现位于 [`her_voice/upstream_patch.py`](../her_voice/upstream_patch.py)。Python 的 resolved `sys.prefix` 必须是当前 workspace 的 `.venv-speech`，目标路径也必须在该环境中。其他 Python 环境、环境外 editable checkout、未知文件 hash、不同来源都会拒绝。

已识别的原文件必须恰好含一次旧字符串；替换后的 SHA 必须符合清单，再以原子替换写入。已经打补丁的文件直接接受，不重复写入；前置检查失败不改源文件。每个 live worker 在下载固定模型或导入上游 CLI 前调用只读 `verify_installed_silero_patch()`，不会在对话启动阶段偷偷修复安装。

重新安装／升级 speech-to-speech 可能覆盖此文件，应重跑文档中的安装流程。该环境已经有一处刻意的 installed-file 修改，不能描述成完全未改动的 upstream wheel。

## 2. Drama：限定后端的连续 SoXR hook

### 实际问题与保留范围

固定上游的 [`OpenAICompatibleTTSHandler`](https://github.com/huggingface/speech-to-speech/blob/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8/src/speech_to_speech/TTS/openai_compatible_handler.py) 已实现可取消 HTTP 流、PCM16 字节余数处理和连续重采样。它的 `_StreamingFIRResampler` 对 44.1→16 kHz 使用 160／441 比率，先插入零，再对长 FIR 做普通滤波；本地 CPU 实验确认这部分可以慢于实时。

Drama 的原始输出是 44.1 kHz 单声道 PCM16。不能把源采样率写成 24 kHz 来回避计算开销，那会改变音高和时长。当前实现保留 upstream HTTP、请求构造、解码、异常和取消方法，只用 [`StreamingSoxrResampler`](../her_voice/drama_resampler.py) 替换它在当前 worker 内引用的重采样类。

SoXR 使用固定 `soxr==1.0.0`、`ResampleStream(..., quality="HQ", dtype="float32")`。内部数值保持 PCM16 幅度单位，连续状态跨块保留，最后一次 `push(..., final=True)` flush 尾部，再四舍五入／截断为 int16。每个响应创建独立状态；取消不会把上一轮滤波尾部带进新回复。重采样后仍由原 handler 输出 512 样本块，并补齐最后一块。

### 固定源码 SHA 与 Windows 换行

| 项目 | 固定值 |
| --- | --- |
| 模块 | `speech_to_speech.TTS.openai_compatible_handler` |
| 文件 | `speech_to_speech/TTS/openai_compatible_handler.py` |
| SHA-256 | `8723e77e9ba7ebe6e0ae9a3b746cdf87b1faefeeb0c7114aed24986c98c3d3a8` |
| 唯一允许的规范化 | 读取字节后 `CRLF → LF`；不改原文件 |
| 待替换 API | `_StreamingFIRResampler`，必须有可调用的 `push` |
| 目标实现 | `her_voice.drama_resampler.StreamingSoxrResampler` |
| 依赖 | `soxr==1.0.0` |

Git for Windows 的 `core.autocrlf` 可能改变 checkout 的换行。Drama SHA 校验因此只接受这种等价换行转换；添加注释、改变代码、空格或任何其他内容依然会被拒绝。**这条规则只属于 Drama hook，不能套用到前面的 Silero 精确字节补丁。**

`install_drama_resampler()` 仅在 `VOICE_TTS_BACKEND=drama3` 时导入该模块，校验文件及所需接口，提前验证 SoXR 版本后替换类。其他后端返回 `False`，不导入推理依赖、不修改上游。若源码未知，会明确报错并要求重新安装固定引擎；不会绕过校验、猜测新版本接口或修改上游磁盘文件。

### 为什么配置中 stream=false 仍然流式

Drama 的 profile 使用 `response_format=pcm` 和 `sample_rate=44100`。固定 upstream PCM 路径会自动在请求中加入 `stream_format:"audio"`，原生 audio.cpp 按合成音频事件写出 HTTP chunks。

`openai_tts_stream:false` 只是不发送面向其他服务的 vLLM `stream:true` 扩展。HTTP reader 仍逐块处理响应，输出首个 512 样本块时服务器可以尚未完成请求。参考录音通过 native `voice_presets` 绑定到 `voice_id`，而不是虚构上游参数 schema 中不存在的生成参数。

### 中断的真实边界

原 HTTP reader 的取消机制保留；首音前等待和音频流中取消都已用 loopback fixtures 测试。浏览器的播放队列清理、旧 response／epoch 音频隔离由前端测试覆盖。这里没有把客户端取消解释成 CUDA 在任意位置都能抢占。

固定 [audio.cpp streaming 实现](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/app/server/runtime.cpp) 在后续事件／写入发现连接断开时结束请求；原生首音前长 prefill 可能继续到下一次检查。显式停止引擎会结束本应用拥有的 native 进程，释放其资源；普通插话不承诺立即停止全部 GPU 计算。

## 3. 测试与证据

| 覆盖 | 自动化入口／范围 |
| --- | --- |
| Silero | [`test_pin_silero.py`](../tests/test_pin_silero.py)：临时源码、来源、环境边界、未知内容拒绝、重复应用、原子写入失败、只读启动校验 |
| Drama runtime | [`test_drama_runtime.py`](../tests/test_drama_runtime.py)：安装凭据和修改检测、派生音色配置、真实子进程就绪／停止、外部占用端口 |
| SoXR DSP | [`test_drama_stream.py`](../tests/test_drama_stream.py)：不同块边界、尾部 flush、采样数、抗混叠、新响应状态隔离 |
| 实际 upstream HTTP 方法 | 同一测试先检查整文件 SHA，再以 AST 提取原方法并剔除无关的重型推理 imports；用真实 loopback HTTP chunks 验证首块早于响应完成、PCM 字节拆分、音高／时长、取消、JSON 错误与 CRLF |
| 浏览器取消 | [`audio.test.mjs`](../tests/audio.test.mjs)、[`client.test.mjs`](../tests/client.test.mjs)、[`protocol.test.mjs`](../tests/protocol.test.mjs)：播放清理和晚到事件隔离 |

HTTP 合约测试会优先使用 `VOICE_UPSTREAM_HTTP_SOURCE` 指向的已检查源码，也可以读取已安装的固定 speech-to-speech 包。没有相应依赖／源码时测试会明确 skip；不能把 skip 写成已经完成 HTTP 验证。当前开发环境已提供固定源码及隔离的依赖，实际跑过该路径，无需模型权重或 GPU。

[微测 JSON](evidence/drama-resampler-benchmark.json) 记录一次 Linux x86_64 CPU 的局部实验：50 ms、2,205 个输入样本，从 44.1 转 16 kHz，包含最终 flush，输出 800 样本，重复 5 次。

| 实现 | 5 次中位数 |
| --- | --- |
| 固定 upstream 的直接 FIR | 596.4 ms |
| Drama 连续 SoXR | 0.0514 ms |

这个实验只证明替换所针对的 CPU 瓶颈，不是 Windows 性能、GPU 推理速度、首音延迟或完整系统实时率；也不拿不同滤波器的逐样本完全相同作为音质结论。连续分块一致性、尾部和抗混叠另由测试验证。

固定源码、模型 revision 和内容校验不等于完全确定性推理，也不会固定远端 DeepSeek 权重、驱动或操作系统音效。Torch Hub／Hugging Face 的本地缓存如果被人工修改，仍需另行重新验证。最终 Windows 原生编译、GPU／内存、真实参考音色质量、麦克风、扬声器、AEC 与端到端体验见 [WINDOWS_ACCEPTANCE.md](WINDOWS_ACCEPTANCE.md)。

## 4. Drama 启动预热与失败回报

`her_voice/drama_warmup.py` 仅在 Drama worker 中安装内存 hook，校验上表相同的固定源码 SHA，保留上游文件。预热仍经过原 HTTP reader、300 秒请求时限、完整 PCM 解码及非空音频检查，只有音频流结束才接受就绪。启动探测使用 `Hello.`、`max_tokens=8`、`seed=0`；正式请求保留原始文本、音色和生成参数，预热失败也会清除探测状态。

此前预热沿用正式生成的最多 500 帧预算，且 worker 输出 `Configured startup failed` 后可能等待线程结束，supervisor 仍轮询就绪。现在识别固定失败标记并停止本应用拥有的进程，向页面返回预热失败；不会等到总体 1,200 秒超时。页面显示真实准备状态与本次连接已等待秒数，不再一律提示首次下载。

回归覆盖短预算与正式请求隔离、预热失败恢复、未退出 worker 的失败识别、浏览器错误状态及取消等待。冷启动耗时、原生生成和麦克风体验仍以当前 Windows 运行证据为准。

### Windows 16 GB 显存与磁盘缓存

2026-10-21 的实机日志确认：完整约 29 秒参考音色编码发生 `cudaMalloc failed: out of memory`。派生 Drama 配置现在设置 `fish_drama3.codec_encoder_chunk_samples=65536`，使用 runtime 已有的重叠窗口处理完整参考录音；原始音频和 transcript 保留。该模式可能产生后端相关的数值差异，不宣称逐样本等价。

`VoiceEngine` 在启动识别/会话 worker 前，先调用本应用拥有的 Drama `/v1/tasks/run`，设置 `encode_reference_only=true`。必须收到非空 acoustic-tokens artifact 才进入下一阶段；该操作会缓存参考编码并初始化串流 codec，失败不启动云端 worker，停止会结束本应用拥有的 native 进程。最终就绪仍要求正常 TTS warmup 返回并结束非空 PCM 流。

本机 D 盘为 WDC SATA HDD。`disk_experts=true` 会随机访问 GGUF；实测磁盘排队且原生首音超过 300 秒，Python 冷导入也受到影响。曾尝试建立 C 盘 NVMe 副本，但因磁盘空间不足已撤回；原始 D 盘模型和运行时路径保持不变。固定主机内存专家方案仍在验证，尚未通过真实连接验收。
本轮另有未安装的原生候选修正：Windows 固定专家银行直接从文件读入 pinned host buffer，避免再触碰同一段映射源；保留其他平台原有读取路径。CUDA 小型专家测试已通过 F32/Q4、固定/非固定内存、数值和错误边界检查。该测试在 MSVC 下需要单目标 `/EHc-`，因为它明确验证从 ggml extern-C 调度器回传的 C++ callback 异常。完整模型 300 秒内仅载入 5616 MiB 专家权重，仍未通过启动/首段 PCM 验收；当前安装的原生二进制与运行时凭据均未替换。候选记录为 `.runtime/native-pinned-candidate-20261021.json`。
### 2026-10-21 固定内存实机验证与启动阶段修正

后续实测确认 Windows 直接读入 pinned host buffer 可完成整包：完整所选参考音色编码 3.109 秒，首段 PCM 2.516 秒，总音频 32768 bytes，原生完整过程 610.062 秒。该验证使用 8-frame 启动探测，不代表长句音质或实时对话验收，云端调用为零。记录在 `.runtime/pinned-eager.stdout.log`，二进制 SHA256 `27ea29a32fd6466b5e90f90f0ffddf55167ab06fe47e419cdfe66764c860fe35`。

已备份并替换本地安装二进制，runtime/build 凭据记录基础 revision、实际 exe SHA 和 local_patches；派生配置恢复 pin_experts=true。模型改为 eager load，1200 秒冷载入预算与 300 秒参考音色请求分离；HTTP model.loaded=true 才允许进入参考编码。页面显示真实专家读取量，全部专家读入后显示主干/编解码器阶段，保留取消与进程归属检查。UI 总预算 2800 秒覆盖各阶段，实际会话及 TTS 单次请求时限保持原值。

本次 19 项 Python 生命周期检查与 18 项 Node 界面/连接检查通过；真实页面连接正在单独验收，尚不以原生短音频证明麦克风与会话链全部通过。
真实页面后续验收：后端达到ready，浏览器WebSocket完成配置，页面显示“已连线／我在听”。用户选择Nuroum Webcam V32AF麦克风后，实际语音识别及对应HER回复已在会话记录出现；一轮事件到播放指标14.55秒。模型启动、输入捕获与会话输出路径通过观察，长句音质与响应延迟仍需分别评价。浏览器getUserMedia若不完成，30秒后明确返回麦克风启用超时，保留已载入模型；迟到的stream会释放tracks。该提示不推断用户拒绝权限。
## 5. 中文回复流式断句

`her_voice/sentence_stream.py` 仅在本地语音worker安装分句绑定，校验固定LLM基础模块和Markdown工具源码SHA。实际NLTK默认断句把连续中文句子整体保留；因此上游消费器即使配置stream_batch_sentences=1，也可能等整段生成结束才向TTS产出。新绑定识别。！？的完整句末，在末尾加入上游消费器的未发声pending-tail，让第一句立即进入TTS。英文仍调用原分句器，Markdown保护、text-only输出、history、取消和供应商请求均保留。

7项新增检查直接AST执行完整固定上游消费方法及工具：原路径复现整段等待，修正后第一句在provider迭代后续内容前yield；同时覆盖分裂delta、中英混合、英文缩写/小数、代码保护、text-only逐字保留、取消与未知源码拒绝。连同引擎和后端生命周期共35项通过。端到端速度提升必须另以新版真实会话验证，不能由此断言固定节省秒数。模型、完整参考音色与Prompt未调整。
### 就绪检测的瞬时 HTTP 协议异常

本轮真实重载时，Drama 已完成加载，但就绪检测线程此前因 `http.client.BadStatusLine` 退出，页面仍等待。`EngineSupervisor` 和 `DramaSupervisor` 的 HTTP 健康探测现在捕获 `HTTPException`，将不完整响应和异常状态行视为暂未就绪，保留既有总超时与取消边界；不会因一次协议异常放行，也不重试任何付费生成请求。调用方包括本地语音 worker 的 pool 检查、Drama health/models 检查和运行状态检查。

两项新增回归在修正前复现失败；修正后覆盖 BadStatusLine、IncompleteRead、RemoteDisconnected，以及随后有效 HTTP 响应恢复就绪。中文断句、引擎生命周期、声音选择与 Drama runtime 共 47 项通过，记录 `.runtime/latency-regression.log`。真实连接验收继续单独记录。
## 6. Windows 专家权重冷读取优化（2026-10-21）

已安装本地原生修正：先分配各专家银行的固定主机内存，再按 GGUF 文件偏移顺序读取，Windows 使用 16 MiB 的 ReadFile 直接填入既有 pinned buffer。保留 Q4 专家／Q8 主干、GPU 专家缓存 8、完整参考音色和模型文件路径；未复制模型或扩大内存缓存。旧版本在 MSVC ifstream 路径产生约 4 KiB 系统读取，并按层执行造成回跳。小型 pinned-buffer 对照验证读取字节一致；其冷热缓存和并行磁盘负载未控制，不作为整机加速倍数。

CUDA 专家测试覆盖 F32/Q4、非固定/立即固定/延迟固定六种模式，数值误差 0，包含截断输入和重复 load 边界，构建退出 0。实际安装 exe SHA256 为 fa40461aab30db5b4dd191a3c94b876ad42260f333edbcb491ba928d8c9c8a7a；旧二进制与源码在 .runtime/cold-load-backup-20261021 保存。

本机本次启动从 13:06:55 到模型 loaded 13:13:03（约 368 秒）；16.9 GiB 专家区实际读取 298.304 秒，1080 次 ReadFile，约 57.9 MiB/s。完整所选 Samantha 27.9 秒参考音色随后预备成功。此为当前 HDD、系统缓存和并行负载下的一次启动，不是清空缓存的严格冷启动对照，也不等于完整会话启动时间。

本地正式中文两句、原始默认生成预算、seed=0 的流式测量：首个非零 PCM 1.801 秒，512000 bytes／5.805 秒音频，完整请求 7.456 秒，包含首音的 RTF 1.284，最大相邻读取间隔 70.1 ms。首音后的总体生成速度接近播放速度，但余量较小；不据此宣称全链路实时。云端调用为零。第一次同文本测量生成成功但报告受终端 CP950 编码失败影响，改用 ASCII JSON 后重测一次；未把失败报告算作有效测量。记录 .runtime/local-sentence-metrics.json。

原生模型与完整参考及正式 PCM 已验证；新版麦克风 → DeepSeek → 播放的完整对话仍待会话引擎启动和用户实说验收。现有 47 项 Python 与原生 CUDA 检查不替代该层验收。
本次新版页面随后于 13:29:17 完成 WebSocket 会话配置，实际显示“已连线／我在听，请说”。从本次启动到可交互约 22 分 22 秒；其中原生模型约 6 分 8 秒，后续 Python 依赖导入、VAD/ASR 和 warmup 约 16 分 12 秒。D 盘观察到明显并发 I/O 排队，worker 的模块列表持续从 pandas/sklearn 到 Torch/CUDA 推进；已下载模型缓存复用。该结果说明专家读取优化已生效，但完整冷启动仍不理想，不能宣称全系统冷启动已优化完成。正常挂断仅关闭会话和音频，不卸载引擎。新版真实用户发言后的全链路延迟仍待用户操作。
## 7. 相同设置不再触发整套冷启动（2026-10-21）

调用链为设置面板 → VoiceClient／provider-settings → 本地 PUT /api/tts、/api/provider → VoiceEngine.stop。此前即使当前设置完全相同，也会关闭会话并停止语音与 Drama 两个进程。两项新增 API 检查在修改前均复现 stops=1，修改后 stops=0。

API 在现有 control_lock 内比较有效供应商、URL、模型、Key 或已保存音色选择。相同供应商设置仍正常持久化；相同音色无需重写选择文件。两条路径均保留现有 engine 对象与 sockets，并返回 unchanged。前端先读取服务器当前选择，避免按过时面板值判断；相同音色直接保留麦克风与播放，实际变更仍按原顺序清理会话。Key 不回传或保存在浏览器，新 Key 不在前端推断相同，后端继续完整校验。

当前版本 34 项 Python 设置、声音、应用检查和 15 项 Node 设置交互检查通过，日志 .runtime/noop-settings-python.log、.runtime/noop-settings-node.log。实机相同设置请求前后 native PID61992 保持，读取进度继续；网页实际点击“应用声音设置”“储存并套用”也返回未变提示并保留连接中状态。证据 .runtime/noop-settings-live.json。该优化减少重复设置造成的冷启动次数，不声称提高首次磁盘读取速度。原模型、完整参考音色、Prompt 和精度保留。
### 用户授权的 SSD 运行库缓存

用户明确允许占用约 4 GB C 盘验证运行库缓存。已复制 torch/lib 的 58 个文件（53 个顶层文件及 5 个头文件），总计 4,280,414,108 bytes／约 3.986 GiB，逐文件 SHA256 相同。原目录保留为 .venv-speech/Lib/site-packages/torch/lib.hdd-original-20261021；原 lib 路径改为 Junction。模型及其他依赖仍在 D 盘。

最终 Junction 直接指向 C:/Users/Admin/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/HERVoice/runtime-cache/torch-lib-20261021，避免 Store 应用目录虚拟化令普通启动方式找不到缓存。最初的虚拟路径 Junction 已改名保留为 lib.ssd-virtual-alias-20261021，它本身不占用另一份运行库数据。完整文件清单、校验值和回退目录见 .runtime/ssd-runtime-cache-20261021.json。

独立新 Python 进程成功 import torch 2.11.0+cu128，耗时 12.352 秒，解析库目录明确位于 C 盘；记录 .runtime/ssd-torch-import.json。该测量发生在复制/校验之后，OS 缓存未控制，不是清空缓存后的冷启动，也不能据此给出全系统加速倍数。当前语音 worker 在进入 Torch DLL 加载之前完成映射切换，完整就绪另行记录。C 盘切换后约剩 21.72 GiB；本次新增数据只有上述缓存，不清理其他会话文件。

回退须先停止本工作区语音引擎：核对 lib 为该缓存的 Junction，将链接改名保留，再将 lib.hdd-original-20261021 改回 lib。缓存可以保留以便复核，无需删除任何模型或文件。
2026-10-21 14:03 PDT 完成现场验收：当前引擎于 14:00:52 建立实时 WebSocket，页面显示「已连线／我在听，请说」，Nuroum 麦克风、Drama 3、Samantha 与 DeepSeek 保持原选择。Torch 之后的语音识别与首段语音预热均成功。连接中分别点击相同音色应用和相同接口保存，页面返回设置未变；native 61992、speech wrapper 10968／child 46392 保持原进程，WebSocket 连接计数不变。证据 .runtime/noop-settings-ready-live.json；SSD receipt 已补入 live_validation。C 盘当时剩余 22.17 GiB。

该次 SSD 映射在启动途中启用，包含复制、哈希验证与并发磁盘负载，不作为完整冷启动 A/B 对照；12.352 秒仅证明当前缓存条件下独立 Torch 导入成功。运行库缓存约 3.986 GiB，不包含 D 盘模型本身。完成本机运行与 UI 验证；未执行 Git 提交、push 或部署。
## 8. 分段计时、首句提前与播放通知开销（2026-10-21）

调用方：浏览器 app → VoiceClient → ConversationState / VoiceAudio → UI；引擎 worker → 固定上游 LLM 串流 consumer / TurnLatencyTracker。精确 Prompt、Drama 3、完整 Samantha 音色、DeepSeek、Nuroum 和模型路径均保留。

复用上游 response.done 中的 speech_to_speech.turn_latency，浏览器只保留有限数值字段与最多20轮记录，VoiceClient.diagnostics 返回不可变副本，不保存字幕、音频、密钥或接口地址。增加事件至首包、事件至播放、包至播放队列延时、排队音频时长和调度间隙统计。现有延迟文字的 title 可查看服务端辨识、回答首字/全程、合成首段和停顿数据；各阶段存在重叠，不能相加，事件至播放也不能当成物理语音结束至播放。

her_voice.latency 只在 worker 内检查固定 turn_latency.py SHA256 8d599a2df2b4e99ba0b213af2b7eecb6180f51b65d93009da7e9099bb1d7aef0 后，将已经测量的 llm_ttft_s 加入原有 metadata_payload；超过上游512字符上限则保留原记录，不发起额外请求。网页辅助代码合在已有 protocol.js 内，兼容现有静态资源白名单。

中文长首句可在自然逗号/分号处先送出一段，要求至少24个字符且包含至少12个汉字；仅首段有效，后续完整句保持原逻辑。排除引号/括号内部、代码反引号和 URL，不改英文分句或纯文本输出。wrapper 只在推进原 consumer 的瞬间绑定 ContextVar，保留取消、历史及 Markdown 流程，不修改已安装上游文件。真实 consumer 测试先复现长首句等待，再验证早出、跨轮隔离及原有边界。

播放性能回归曾复现100个PCM片段触发200次完整状态通知；修正后仅1次，仍完整排入100段音频。音量走独立通道，首音、字幕、完成、插话和错误状态仍及时发布。该结果是通知数量优化，不宣称相同倍数的整机性能或端到端提速。

验证：32项 Python（计时、分句、引擎）与60项 Node（全部现有前端测试）通过，日志分别为 .runtime/latency-worker-python.log、.runtime/latency-final-node.log。最初分句测试的引号断言未考虑上游既有标点正规化，已改为与原 consumer 输出比较；未放宽内容保留断言。整合实际语音验证单独补记。

历史本机日志经 scripts/summarize-latency.py 提取20轮已完成语音（跨此前启动与版本）：STT中位70ms、回答全程750ms、TTS首段1755ms、服务端E2E中位2735ms/P90 3900ms；VAD判定中位100ms。记录 .runtime/conversation-latency-baseline.json，不视为本次修改后的结果，也不把取消轮次算进已完成统计。因此本轮保持64ms最小静音与Smart Turn设置，不靠降低阈值换取误截断。

GPU空闲观察约11.2GiB已用/4.9GiB可用，不能替代完整参考编码和生成峰值。保持expert_cache_size=8；本轮没有用空闲截图推断可以扩大缓存。冷启动继续使用已批准的C盘运行库映射和D盘模型，不复制权重；完整参考预备先于ASR加载的顺序保留，避免重叠GPU峰值。

原生性能日志进一步定位到已完成请求的 backbone_prefill_ms 约1601–1624ms，其中 offload_prefill_experts_ms 约1503ms；这是现有记录，不是本轮重新测量。上游每个独立请求重置 decoder；仅 continuation 复用已有前缀。该模型含线性注意力递归状态，不能只复用KV或改position而假定输出等价。后续若做参考前缀状态缓存，需要独立验证全部递归状态、取消/换音色失效与输出一致性，本轮未改原生推理状态。
现场整合结果：唯一一次重启于14:38:43 PDT启动，14:43:57.599建立实时连接，约314秒；专家区读取240.614秒/1080次。当前系统缓存未清空，不能与此前22分钟启动直接作为加速倍数。Tab5实际显示「已连线／我在听」，确认Drama 3、Samantha完整27.9秒、Nuroum与DeepSeek保持原选择，浏览器错误为零。运行worker已通过固定源校验并完成语音预热。测试端口8796完成独立演练的字幕/音频/回到listening验证，演练进程与页面已关闭；真实服务继续运行。新版本真实用户发言与主观听感对比仍待用户操作，未自动发起额外付费基准请求。证据 .runtime/latency-integration-startup.json、.runtime/latency-round2-browser.json。

后续用户澄清：本轮并未成功连线发言，之前「仍然慢或断续」不能记作真实延迟/听感验收。发现原有Tab4停留在断线错误页，而额外验证Tab5占用唯一pipeline；已结束并关闭验证页，回到Tab4刷新连接，后端模型进程保持常驻。本轮没有一条新的完整语音响应可用于性能比较。

另行离线复现播放排程缺陷：32ms音频包在0/37/69/101ms到达时，旧逻辑每包重加35ms启动余量，在仍有音频排队的情况下也插入5ms空隙。修改为有未来排队边界时直接续接，仅初始或真正欠载时恢复35ms余量，保留所有样本与取消语义。新增回归先失败后通过；全套61项Node通过，补充欠载/clear测试后audio专项13项通过（共62项覆盖，未重复其余已通过用例）。该离线缺陷不视作用户本轮连线失败的原因。
## 9. Drama3 expert-prefill row A/B (2026-10-21, rejected)

The trial changed only the offloaded expert-prefill block size from32 to16; expert-cache slots8, Q4 model, context2048, full27.9s Samantha reference and sampling stayed fixed. Its optional runtime control, loader compatibility and CUDA fixture were evaluated locally. The rejected patch is archived under ignored work/rejected-prefill16.patch, not shipped or applied by the installer. The loopback-only scripts/benchmark-drama-local.py remains available for bounded measurements against the already running local native model.

Two seeded baseline requests returned TTFA1.63644/1.62935sec, total7.34153/7.29614sec,5.80499sec audio/512000bytes, with identical PCM SHA25652e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb. The original harness initially rejected the native application/octet-stream header and cancelled; the corrected baseline measurements were fully drained. No cloud benchmark calls were made.

The candidate's first request built CUDA graphs: TTFA12.05257sec and total18.01881sec, excluded from the warm comparison. Its next two requests returned TTFA1.71274/1.75934sec (mean1.73604 versus baseline1.63289sec, about6.3% slower), total6.14460/6.16899sec,4.92263sec audio/434176bytes. All three candidate outputs matched each other at SHA25607b241f22817058b94c5d4b35f137c5ffd52c5fd9faa8aa88950a1ee87285c02, but differed from the baseline output and duration. A shorter total request therefore does not establish faster synthesis or preserved output. The candidate was rejected on both first-audio and exact-output gates. This is a small sequential sample with uncontrolled system load, not a controlled benchmark or listening-quality judgment. The speech-worker dependency import was still running; no speech-worker TTS warmup overlapped these native requests.

The actual CUDA fixture .runtime/prefill16-cuda-fixture.log passed32/16 rows at7/129/513/7 steps, resident/grouped comparison, continuation and allocation recovery. The copied build runner initially invoked the old disk-store fixture; the intended target was subsequently invoked explicitly. An initial startup rejected the new option in the old embedded contract. A second startup exited3221226356 after a config-layout change left stale assets/session objects: the incremental build rebuilt only backbone/generator, and the old NMake directory had empty dependency files from an invalid localized MSVC include prefix. Explicitly rebuilding all8 Drama3 source modules resolved that startup fault. Set VSLANG=1033 before initial CMake configuration in a new build directory, or explicitly rebuild all affected modules after header changes; changing locale after configuration does not repair cached dependency detection.

Trial executable SHA256d209b68bfaa44649cb8e12174c454821a1479b2f3814a50150b36d0d940b4a41 loaded the model and encoded the complete reference successfully. Its startup expert-bank read alone took347.851sec; D-drive I/O queueing continued through backbone/codec loading. This is not evidence of a cold-start improvement. Original executable SHA256fa40461aab30db5b4dd191a3c94b876ad42260f333edbcb491ba928d8c9c8a7a, runtime/build receipts and all5 modified native source files were restored from verified .runtime/prefill16-original backups at15:23:53 PDT. The existing cold-read patch and prior browser/segmentation improvements remain. Restored local synthesis passed: both fully drained outputs returned the baseline512000bytes/5.80499sec and exact PCM SHA25652e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb. First request TTFA2.10961sec included graph preparation; the second returned1.62980sec, total6.76939sec, max read gap51.94ms. These establish restoration, not a new speedup. Evidence: .runtime/prefill32-restored.jsonl. At15:45:35 PDT the restored speech engine accepted the original page WebSocket and session configuration. The page showed connected/listening; settings confirmed Nuroum Webcam V32AF, Drama3, full27.9sec Samantha and DeepSeek deepseek-flash. Browser warning/error log was empty. Screenshot: .runtime/prefill32-restored-ready.png. This confirms connection and selected devices, not a fresh user-spoken end-to-end latency or listening acceptance. From rollback start15:23:53 to connected15:45:35 was about21min42sec under uncontrolled shared HDD load; cold startup remains unresolved. No commit, push or deployment was performed in this round.
## 10. Microphone resampling allocation (2026-10-21)

Callers: capture-worklet.js invokes StreamingResampler.push for each browser audio quantum; audio.test.mjs also consumes this API. audio-dsp.js now writes samples directly into a bounded Float32Array rather than growing a JavaScript Array and copying it to Float32Array afterward. It preserves the weighted integration arithmetic, fractional carry, independent ownership of each returned chunk, all PCM samples, 40 ms transmission frames and silent microphone monitoring. No VAD threshold, model, reference recording or Prompt changed.

The local Node v24.17.0 microbenchmark used 128-sample input quanta, 60,000 calls per sample, five interleaved baseline/candidate pairs after warmup. Median baseline/candidate: 44.1 kHz 57.2868/45.6592 ms (20.30% less processing time); 48 kHz 49.1380/38.4159 ms (21.82%); 16 kHz 73.9817/46.6346 ms (36.96%). All 1,000 successive chunks per rate and benchmark checksums matched exactly. Evidence: .runtime/capture-resampler-benchmark.json; harness: ignored work/benchmark-capture-resampler.mjs. This isolates a small CPU operation in Node, not browser GC pauses or end-to-end conversation speed. At 48 kHz the saving is about 0.067 ms CPU per second of audio; it cannot explain or remove the roughly 1.6 s native first-audio delay.

18 focused Node checks passed: conversion/playback/capture lifecycle plus actual capture-worklet source at 16/24/44.1/48 kHz, PCM/frame/level equivalence, no speaker monitoring, empty input and retained-output ownership. No additional cloud requests or model reloads were made. The live frontend serves the updated DSP file, while the already active AudioWorklet retains its loaded version until the next normal reconnect; no audible latency acceptance is claimed.

Cold-start inspection confirmed direct scipy.signal use in the upstream realtime audio path and eager NLTK import/resource checks in s2s_pipeline.py. Removing these libraries wholesale would break supported paths; this round made no dependency deletion or speculative import hook. Current five-part status: timing instrumentation implemented; early Chinese first clause implemented with real comparative acceptance pending; end-of-speech/barge-in thresholds unchanged; native synthesis speed unresolved (row16 experiment rejected); cold-start partially improved by sequential expert reads, SSD Torch libraries and avoiding unchanged-settings reloads, but shared HDD dependency loading remains unresolved. All changes remain local and uncommitted.
## 11. Experimental backbone state checkpoint (2026-10-21)

The main remaining warm first-audio cost is fresh backbone prefix computation, not reference waveform encoding, which is already cached. A separate unenabled patch now establishes a tested state-checkpoint primitive for that path. A correct checkpoint includes both full-attention KV and linear-attention convolution/recurrent state. It captures all persistent state tensor bytes, binds the snapshot to the exact decoder instance, validates ownership and all tensor sizes before writes, and restores valid_steps. No generator or public API invokes this primitive.

The actual rebuilt CUDA fixture passed7/129/513/7-token prefixes,16 bit-exact continuation comparisons after other prompts/reset/expert invalidation/graph release,4 foreign checkpoint rejections without damaging the active continuation, and the existing12 allocation-failure recoveries with0 tracked buffer leaks. A KV-only negative implementation failed on continuation equality; the complete implementation passed again after restoration. The first negative run had not recompiled the fixture's directly included backbone.cpp due the known old NMake dependency-cache defect; that stale result is excluded. The runner was corrected to force the fixture translation unit to recompile. Relevant logs: .runtime/prefix-state-complete-rebuilt.log and .runtime/prefix-state-kv-only-negative-rebuilt.log.

Artifact: patches/audio.cpp/experimental-backbone-state-checkpoint.patch; both forward and reverse applicability checks passed. Both edited native source files were restored byte-for-byte afterward. The installed server retained SHA256fa40461aab30db5b4dd191a3c94b876ad42260f333edbcb491ba928d8c9c8a7a, nativePID32456 and speech wrapperPID61956 remained ready. No model restart, second full-model load, added model copy or cloud request occurred. Receipt: .runtime/prefix-state-checkpoint-result.json.

This is an experimental foundation, not a live latency improvement. Different text requires safe reference/history-prefix boundaries, complete hidden-sequence assembly and either complete tower state handling or original tower prefill recomputation. Split-prefill arithmetic and full27.9s Samantha PCM/quality/speed must still pass before promotion. KV-only caching was empirically rejected; no first-audio reduction is claimed. Existing realtime, model, reference, microphone and Prompt behavior remains unchanged; no commit/push/deployment this round.
## 12. Prefix-cache trials, rollback and startup bootstrap (2026-10-21)

All full-model prefix-cache candidates were rejected. Small CUDA fixtures were insufficient evidence for this quantized model. The complete27.9s selected Samantha reference and fixed seed0 were retained throughout; the known baseline is512000-byte PCM, SHA25652e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb.

| Trial | Complete PCM bytes | First-audio result | Acceptance |
| --- | --- | --- | --- |
| Split prefill | 475136 first,430080 repeated | approximately0.52s repeated | Rejected: baseline PCM changed |
| Reduced-expert memo | 397312 | 0.65–0.80s | Rejected: baseline PCM changed |
| Original fused finish graph | 397312 | 0.82–0.83s | Rejected: baseline PCM changed |
| Original finish plus expert grouping/transfer order | 491520 | 1.61–1.66s | Rejected: changed PCM and no stable speedup |

The last trial's paired baseline returned512000bytes twice, TTFA1.7163/1.5598s. It preserved all whole-prompt attention/GDN/KV, original32-row expert batches, original fused finish, original expert grouping/row positions/transfer schedule, and exact normalized-input/residual/routing cache keys. F32 and Q8_0/Q4_K CUDA fixtures still passed exact prefill/continuation, changed-input, budget/reset and allocation-failure checks. The full-model discrepancy remains unresolved; finish-graph fusion or expert placement alone is not a demonstrated root cause. None of the faster rejected figures represents an accepted application speedup.

The final research artifact is patches/audio.cpp/experimental-reference-prefix-cache.patch, unapplied. The original native executable SHA256fa40461aab30db5b4dd191a3c94b876ad42260f333edbcb491ba928d8c9c8a7a and all9 native source files were restored against verified backups. Automatic Python cache activation and its extra payload test were removed. Production warmup and HTTP payload behavior remain unchanged. No speculative native cache remains enabled. Local evidence is retained under .runtime/expert-memo-*; it contains no paid benchmark requests.

A separate narrow startup optimization is retained: the pinned upstream s2s_pipeline.py eagerly imported NLTK and checked/downloaded two NLTK resources that this reviewed package never used afterward. her_voice/startup_patch.py removes only that import/bootstrap, checks original/patched source SHA256, atomically applies during installation, and verifies read-only before worker launch. scipy.signal and every used audio dependency remain. Normalized original/patched pipeline SHA256:2b3fed85d07a2efbb4b705c6604f6baa1d8ddd4d643cb0e4e1e6e79814d10c47 /77735f305eb134726252eb7f3df704f0e1e33fbe703e5b0b57958de17374a591. The original installed source is retained locally for recovery. This removes unnecessary startup work; repeated shared-HDD loads are not a controlled cold-start comparison.

Restored full-model synthesis passed two fully drained runs:512000bytes and original PCM SHA256 in both; TTFA1.57388/1.51063s, wall7.05516/7.14669s, max read gap76.22/54.06ms. This establishes restoration, not a new native speedup; total RTF1.215/1.231 still does not establish sustained real-time generation. The restored startup was observed ready at22:24:40 PDT after22:19:27 restart, about313s under uncontrolled shared-HDD load. Current selected Python verification totals57 passing checks: startup3, supervisor20, native-runtime11, stream/resampler11, latency2, sentence10. The research benchmark now refuses cache flags unless the runtime actually advertises the experimental capability, preventing unsupported options from producing a false success. At22:25 PDT the original IAB Tab4 reconnected and showed connected/listening. SpeechPID57424 and nativePID9436 remained unchanged through connection, confirming reuse without another model load. Settings retained Nuroum Webcam V32AF, Drama3, full27.9s Samantha and DeepSeek deepseek-flash. Screenshot:.runtime/optimization-restored-ready.png. No fresh user-spoken round was generated for this verification, so no new end-to-end latency or listening acceptance is claimed.

| Optimization area | Actual status |
| --- | --- |
| Stage timing | Implemented and regression checked; distinguishes browser playback and server stages |
| Earlier first clause | Implemented without changing complete text/Prompt; fresh user-spoken comparative timing remains unmeasured |
| End-of-speech / barge-in | Existing approximately100ms decision and immediate cancellation retained; no threshold reduction that cuts speech |
| Native first audio | Experimental shortcuts rejected and rolled back; approximately1.6s local baseline remains unresolved |
| Cold startup | Sequential pinned reads, approved Torch DLL SSD cache, unchanged-settings reuse and unused NLTK bootstrap removal retained; D-drive24GB model read remains the bottleneck |

Existing18 focused Node capture/playback checks remain valid because those files have not changed since their passing run. This does not establish fresh user listening or end-to-end real-time acceptance. All changes remain local; no commit, push, deployment, new model download or new SSD model copy was performed.
## 13. Write-combined CUDA expert banks (2026-10-21)

A separate narrow native change is now active: only CUDA pinned expert banks use write-combined host allocation. These banks are written by the loader and read by GPU DMA; ordinary staging/readback and CPU expert paths retain their existing buffer allocation. The private ggml extension leaves the public header unchanged. Tensor shapes, arithmetic, expert grouping/cache size, full27.9s Samantha reference, model, Prompt and microphone settings remain unchanged. Artifact: patches/audio.cpp/cuda-write-combined-expert-banks.patch, applied after the cold-read patch; installer application remains manual.

The final native build succeeded. All six F32/Q4 expert fixture modes (unpinned, pinned and deferred pinned) passed with max_abs=0, including cache/bounds/truncation coverage; logs explicitly identify CUDA_Host_WriteCombined. The standalone transfer microbenchmark returned16.69/16.85GiB/s with ordinary pinned allocation versus18.14/18.62GiB/s write-combined, with exact transferred bytes. This transfer result does not establish the same improvement for complete inference.

Five complete fixed-seed/full-reference runs (two baseline, three candidate) each returned512000-byte PCM, SHA25652e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb. Excluding each version's first graph-preparation run, current baseline TTFA was1.57412s and candidate1.52179/1.49768s; candidate wall time6.75565/6.85832s for5.80499s audio (RTF1.164/1.181). Historical baseline1.51063-1.57388s overlaps these values, so the small sample supports a modest observed difference, not a robust overall percentage speedup. Sustained real-time synthesis and fresh microphone-to-speaker acceptance remain unachieved/unmeasured respectively. No additional cloud benchmark calls occurred.

Cold startup remains the main unresolved issue: the new runtime logged1080 expert reads taking295.1828332s for16.875GiB. This was a shared-HDD run, not a controlled cold A/B; it does not show a cold-start improvement. D is a SATA HDD; the approved approximately4GiB C-drive Torch DLL cache does not contain the approximately22.91GiB model. CPU-only module import measurement was9.404s with the current warm cache; no speculative parallel-import patch was applied. No new model/SSD copy or cleanup was performed.

Installed native SHA256:118a9d9dba9991ff29163b8edb8d90d2ddebf70ab13e2cfd9610e78ba1c84129. Rollback binary, runtime/build receipts and source originals are retained in.runtime/writecombined-original (previous executable SHA256fa40461aab30db5b4dd191a3c94b876ad42260f333edbcb491ba928d8c9c8a7a). The public ggml header was restored unchanged. Numeric/PCM acceptance is recorded separately from user listening and full-chain latency. Current services are ready; the browser was already disconnected and was left that way. Existing Python57 and focused Node18 checks remain valid for unchanged application files. No commit, push or deployment was performed.
## 14. Larger expert-cache trial rejected (2026-10-22)

The cache capacity16 candidate was measured once against the accepted capacity8 native runtime, using the same model, complete27.9s Samantha preset, fixed text and seed0. Current capacity8 baseline returned512000-byte PCM with SHA25652e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb twice; TTFA1.91278/1.42515s, wall6.79109/6.29220s. Capacity16 returned462848 bytes (5.24771s instead of5.80499s), SHA256624fe4d47e0d82f73b0087f15df7d2adc83f248544e656fb0478c2db608ac0e0, TTFA1.37674s. The shorter total duration cannot establish an equivalent-work speedup. Cache size also changes prefill expert grouping and GEMM shapes; the precise numerical cause of the changed generation was not traced. The trial was stopped at this failed consistency gate. No listening-quality or stable-speedup claim is made.

The two temporary application/test edits were restored from SHA-verified originals, and the original frontend restarted its capacity8 engine through the app control API. The frontend itself was preserved after automatic review rejected the compound frontend process-stop operation without further reason. Candidate process ownership and cleanup used DramaSupervisor. Evidence remains private in.runtime/cache16-baseline.jsonl, cache16-trial.jsonl and cache16-result.json. The candidate native HTTP startup took420.53s under uncontrolled shared-HDD/cache conditions; total sampled desktop-plus-candidate GPU use peaked10848MiB. There were no cloud benchmark calls, new model copies or new SSD allocations.

Read-only inspection found830 extents in the existing model file. The signed Microsoft Contig analysis confirmed that count, but volume bitmap access was denied; no defragmentation was attempted. Fragment count alone does not quantify its share of the cold-start delay. The measured cold-start bottleneck remains unresolved. The accepted write-combined runtime and earlier latency improvements remain in place; this trial adds no production speed change.

Restoration acceptance: both original app-managed services returned ready with pool1 idle; native PID43316, original frontend PID39132 retained. A complete post-restoration fixed-seed request again returned512000-byte PCM with the original SHA256; TTFA1.75843s, wall6.70907s. This single post-restart measurement is not a warm comparative speedup or an end-to-end conversation latency. No fresh microphone or listening acceptance was performed.

## 15. Local SSD model/package migration and normal restart (2026-10-22)

The accepted runtime now reads its unchanged 24,596,497,888-byte Drama GGUF from the local C-drive SSD. The original D-drive model remains available for rollback. Both complete files matched SHA256 `05af716baf78e7c20863b9ccebb3ebdeca9607dd3ea7d0b72c35079e675b212b`; source modification time was retained. Only the local model-path receipts changed. The full Samantha reference, capacity8 expert cache, native arithmetic, DeepSeek settings, Prompt and Nuroum selection were preserved.

The speech virtual environment also redirects15 package directories to verified SSD copies (21,479 files, approximately0.535GiB), reusing the previously approved Torch DLL cache. Every copied file was verified against its source. Originals were moved within D to a retained rollback directory before the directory junctions were activated. This is machine-local setup: model/cache contents, absolute user paths, credentials, migration scripts and runtime receipts are deliberately excluded from Git. A fresh clone does not automatically reproduce these SSD mappings; the ordinary installer remains usable without them. Future package updates affect the SSD targets, while the retained originals are historical rollback copies.

One managed normal restart stopped both prior owned, idle workers before starting replacements. The frontend remained running; no second model instance was loaded alongside the old one. Observed times from the start request:

| Stage | Elapsed time |
| --- | --- |
| 16.875GiB expert banks read | 22.558s |
| Native model HTTP ready | 32.710s |
| Full reference prepared; speech process started | 33.844s |
| Complete speech pool ready | 64.690s |

The previous SSD-model-only full startup was144.763s; this later observed startup was55.3% shorter. These were normal restarts with uncontrolled Windows file-cache state and concurrent load, not a controlled cold A/B or a reboot-cold guarantee. The difference cannot be attributed solely to the package migration. A separate read-only, unbuffered256-file sample measured13.113s on D versus0.407s on C with identical sampled bytes; that storage microbenchmark is not a whole-startup speedup ratio. Warm import comparisons did not demonstrate a stable speedup.

Normal startup retained its existing real DeepSeek warmup, which completed through the gateway with HTTP200; it was not a zero-cloud startup. No additional cloud benchmark question was submitted. The earlier isolated duplicate-worker diagnostic was aborted under system commit-memory pressure and is not counted as a pass; the separate single-instance managed restart completed successfully.

Post-restart local synthesis retained all512000 PCM bytes and the original SHA256 `52e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb`. First audio was1.7784s; generation took7.0164s for5.8050s audio (RTF1.2087). This validates unchanged deterministic output, not sustained real-time synthesis, new listening acceptance or microphone-to-speaker latency. Both services were ready and idle after verification. This storage verification made no further production source changes. Private measurement and rollback evidence remains under `work/speech-startup-20261022/managed-restart`, `work/ssd-model-20261022` and the retained local runtime backups.