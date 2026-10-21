# 0.4：Drama 3 的 Windows 本地准备与验收

本项目将 **Drama 3 作为主用语音合成后端**，运行方式为原生 Windows CUDA `audio.cpp`。默认采用 `Q4_K_S` 专家／Q8 主干权重，将专家权重保存在锁页主机内存，通过 GPU 专家缓存执行运算，目标电脑为 RTX 4080 SUPER 16 GB、64 GB 内存。备用后端是固定版本的 Qwen3-TTS 0.6B Base，由界面手动选择。

这是代码和安装流程的实现；当前环境没有 Windows、NVIDIA GPU 或真实音频设备，尚未测得目标电脑的首音延迟、实时率、显存峰值或中文音质。首次构建、约 22.91 GiB 权重下载与真正的 Windows/GPU 推理需要在目标电脑完成。

## 最短安装流程

先在完整项目目录执行基础 Python 环境安装：

```powershell
.\scripts\install-windows.ps1
```

Drama 的 C++ 编译额外需要：

- Git for Windows。
- Visual Studio 2022 C++ Build Tools，包含 MSVC、Windows SDK、CMake、Ninja 和 OpenMP。
- NVIDIA CUDA Toolkit；优先使用与本项目 Python CUDA 基线一致的 CUDA 12.8，并确保该 Toolkit 支持安装的 MSVC 版本。只有显卡驱动及 PyTorch 自带 CUDA DLL，不能代替用于编译的 Toolkit。
- 支持 AVX2 的 x64 CPU。安装器针对 4080 SUPER 明确使用 CUDA 架构 `89`，没有根据另一张显卡的配置猜测。
- 足够的磁盘空间：GGUF 文件约 22.91GiB，另需源码、C++ 构建产物及 Python 环境空间。建议至少预留 35GiB 额外空间供 Drama 准备；这不是整套项目的总空间上限。

然后执行：

```powershell
.\scripts\install-drama.ps1
```

该命令会构建固定版本的 `audiocpp_server.exe`，下载指定的 Q4/Q8 权重并核对文件大小及完整 SHA-256。它不会启动推理，也不会调用 DeepSeek。下载由 Hugging Face 缓存管理，网络中断后可以重跑同一命令。

安装器不修改全局 Git 配置，不重置已有源码改动，不删除旧权重，也不接管已占用端口的服务。它在独立子 PowerShell 中调用上游构建脚本，MSVC/CUDA 的临时环境仅影响该构建子进程。

### 先看操作清单

```powershell
.\scripts\install-drama.ps1 -DryRun
.\.venv-speech\Scripts\python.exe .\scripts\prepare-drama.py
```

两个默认预览都不下载、不创建目录、不加载模型。Python 预览可在没有 GPU、没有 `huggingface_hub` 的环境执行。

### 已经有同一个 GGUF

```powershell
.\scripts\install-drama.ps1 -ModelFile 'D:\Models\drama-3-backbone-8bit-experts-Q4_K_S.gguf'
```

该文件必须与下面固定包的字节完全一致。准备器直接引用它，不额外复制 23GiB。重新移动或修改文件后，需要再次运行准备命令。

### 把构建和下载分开

```powershell
.\scripts\install-drama.ps1 -BuildOnly
.\.venv-speech\Scripts\python.exe .\scripts\prepare-drama.py --download
```

`-BuildOnly` 只生成 `build.json`，不会把尚未下载的模型标记为可用。第二条命令完成大小和 SHA-256 检查后才发布 `runtime.json`。

若需要不同端口或目录：

```powershell
.\scripts\install-drama.ps1 -Workspace 'D:\AI\voice-workspace' -Port 8892
```

后续启动助手必须使用同一个 workspace，并将 Drama 端口设为 8892；单独改变安装目录不会自动修改助手的环境配置。`-PythonExe` 可指定基础安装器创建的 Python，`-Jobs` 可调构建并行度，默认 4。

## 固定版本与启动配置

| 项目 | 固定值 |
|---|---|
| Runtime | `0xShug0/audio.cpp` |
| Runtime commit | `0bf4d155f2af5f85853415170622dc145e3d62c3` |
| HF 仓库 | `Jmica/Drama-3-GGUF` |
| HF revision | `17a5b40c5dc2c2be986f5371a7e650108b828e29` |
| 文件 | `drama-3-backbone-8bit-experts-Q4_K_S.gguf` |
| 文件字节数 | `24596497888` |
| SHA-256 | `05af716baf78e7c20863b9ccebb3ebdeca9607dd3ea7d0b72c35079e675b212b` |
| Model ID | `drama-3` |
| 默认地址 | `127.0.0.1:8792` |
| 默认模式 | CUDA、streaming、一个模型、关闭上游 WebUI |

关键会话参数：

```json
{
  "fish_drama3.disk_experts": true,
  "fish_drama3.pin_experts": true,
  "fish_drama3.expert_cache_size": 8,
  "fish_drama3.cpu_expert_layers": 0,
  "fish_drama3.codec_backend": "same",
  "fish_drama3.context_length": 2048
}
```

这里的缓存 `8` 是**每层专家槽位数**，不是 8 GB。锁页主机内存存放完整专家权重，缓存未命中时传入 GPU，专家矩阵运算仍在 GPU 执行。`cpu_expert_layers=0` 明确关闭 CPU 专家计算，并不意味着所有权重常驻显存。锁页内存分配失败会报错；程序不会悄悄切换到另一种策略。

默认生成预算为每次请求 `max_tokens=500`。上下文包括文字、参考音频和生成预算；过长录音可能挤占上下文。导入接受 **1–30 秒 PCM16 WAV**，建议使用干净、单说话人的 **5–15 秒** 录音，并填写准确的录音文字。接受 30 秒文件不代表每个 30 秒参考都能在此上下文预算下完成长回答。

## 导入音色并开始对话

基础安装完成后可单独预下载共用 ASR 与 SmartTurn：

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend drama3 --download
```

启动网页，在音色面板导入 WAV 和对应文字，选择 Drama 3 及已保存音色，点击应用后开始。控制器会将参考文件写入派生的 native `voice_presets`，然后启动本应用拥有的原生进程和语音引擎。参考录音留在本机；DeepSeek 负责文字回答。

若切换备用，先准备完整 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 快照，revision 为 `5d83992436eae1d760afd27aff78a71d676296fc`：

```powershell
.\.venv-speech\Scripts\python.exe .\scripts\download-models.py --backend qwen3 --download
```

再在界面选择 Qwen 和同一个已保存音色。两个后端共享 WAV 与文字，各自计算声学特征，不共享 embedding。应用设置会结束当前会话并停止旧后端；失败时由用户手动选择备用，不会暗中同时加载两个 TTS。

## 生成的文件与可复用接口

在所选 workspace 下：

```text
.runtime/drama3/
  audio.cpp-0bf4d155f2af/          固定源码和 build/windows-cuda-release/bin
  hf-cache/                       固定模型的专用 HF 缓存
  source-owner.json               安装器创建源码目录的记录
  build.json                      仅 BuildOnly 时生成的构建记录
  server-<内容摘要>.json          固定基准 server config
  runtime.json                    校验后发布的入口 manifest
  session-server.json             控制器生成的当前音色配置
```

`runtime.json` 包含 `executable`、`source_dir`、`runtime_revision`、`executable_sha256`、`model_path`、`model_revision`、`model_sha256`、`model_size_bytes`、`model_mtime_ns` 和 `server_config_path`。运行控制器应使用 manifest 中的路径，而不是依赖固定目录名或猜测下载缓存位置。

模型验证失败不会修改现有 manifest 或其配置，也不会删除用户文件。配置使用内容摘要命名，最后以原子替换发布 manifest；若最终写入失败，之前的配置仍保持可访问。重复准备相同输入会得到相同配置路径。

直接启动上游服务可用于定位问题；正常使用应由助手的进程控制器启动，以便后端切换时释放显存：

```powershell
$Drama = Get-Content .runtime\drama3\runtime.json -Raw | ConvertFrom-Json
& $Drama.executable --config $Drama.server_config_path --no-ui
```

上面的直接命令仅用于服务诊断，基准配置不包含界面选择的参考音色。正常对话由控制器生成 `session-server.json` 并启动，保留安装器的基准配置。

`/health` 只说明 HTTP 服务可用。因为采用延迟加载，首个真实合成请求才会加载模型，所以 health 成功不能等同于已完成 GPU 推理。控制器还校验 `/v1/models` 中的 `drama-3` 注册信息；第一次实际参考音色合成仍需听测。

## 已验证与需要真机验证

自动化测试覆盖：只读预览、不复制现有模型、固定 revision 及 `token=False` 下载、大小与 SHA-256 校验、失败时保留旧配置、最终发布失败的回滚边界、幂等准备、源码 commit/改动检查、无凭据泄漏的下载错误。

```powershell
.\.venv-speech\Scripts\python.exe -m unittest discover -s tests -p 'test_drama_setup.py' -v
```

这些测试使用小文件和模拟下载器，不替代 23GiB 真权重或 MSVC/CUDA 编译。

还需在目标 Windows 电脑验证：

1. 构建脚本成功生成包含 `fish_drama3` 的 CUDA server，完整模型通过 SHA-256。
2. 参考音色的中文、英文和中英混读无持续漏词、异常静音或截断。
3. 首音延迟、持续生成实时率、峰值显存及主机内存；首音快不等于持续生成能跟上播放。
4. 真实麦克风插话、停止播放、取消旧请求，以及下一轮不会播放过期音频。
5. Drama 和 Qwen 切换后，旧模型资源已释放；同一显卡同时运行其他软件时的余量。

上游原生音频为 **44.1 kHz 单声道 PCM16**。本项目的 `soxr==1.0.0` 运行时适配将它有状态地转换成上游内部 16 kHz，再由现有 Realtime 输出层转成浏览器的 24 kHz；不能直接修改采样率标签。适配只在 Drama worker 中启用，校验固定 HTTP handler 的源码哈希，不修改安装文件，也不影响 Qwen。见 [UPSTREAM.md](UPSTREAM.md) 和 [DEPENDENCIES.md](DEPENDENCIES.md)。

## 故障排查

| 现象 | 处理 |
|---|---|
| `Official CUDA Toolkit was not found` | 安装 Toolkit；确认 `CUDA_PATH` 指向包含 `bin/nvcc.exe` 的目录 |
| 缺少 `cl.exe` / CMake / Ninja / SDK 工具 | 在 Visual Studio Installer 补齐 C++ 桌面开发及相关构建组件 |
| `unsupported Microsoft Visual Studio version` | 根据当前 CUDA Toolkit 的官方支持表选兼容 MSVC，不要直接加忽略版本检查的参数 |
| 源码目录已有其他内容或被修改 | 保留该目录，改用一个新的 workspace；安装器不会 reset/clean |
| SHA-256 不匹配 | 检查是否选错量化包；重新下载固定 revision。准备器保留旧配置和错误文件供诊断 |
| 锁页内存分配失败 / CUDA OOM | 关闭其他占用内存或显存的程序；先保持短参考、context 2048、cache 8。不要把模型文件大小当成峰值显存 |
| 端口被占用 | 关闭已确认属于本助手的旧实例或改端口；不会接管未知服务 |
| health 正常但首次合成很慢 | 检查首次模型加载和锁页内存初始化；以第一轮成功合成后的连续请求重新计时 |

## 上游依据

- [固定提交的 Windows 构建器](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/scripts/build_windows.ps1)
- [固定提交的 Drama 3 模型说明](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/docs/models/fish_drama3.md)
- [固定提交的 Server 配置说明](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/app/server/README.md)
- [GGUF 仓库](https://huggingface.co/Jmica/Drama-3-GGUF)

模型和原生运行时仍保留各自许可证及说明文件；本准备器没有修改第三方权重。
