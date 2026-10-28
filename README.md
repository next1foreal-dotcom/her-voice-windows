# Open OS1

一个面向 Windows 的开源语音助手原型：本机语音识别与合成、流式对话、可打断播放，以及随声音变化的 WebGL 丝带界面。

[MIT License](LICENSE) · [安装指南](docs/GETTING_STARTED.md) · [架构](docs/ARCHITECTURE.md) · [贡献](CONTRIBUTING.md) · [安全](SECURITY.md)

Python 包名为 `her-windows-voice`，代码模块为 `her_voice`。这是可自行运行和继续开发的原型，真实语音模式需要准备本地模型、NVIDIA GPU 和文字模型接口。

## 功能

- 中文、英语及混合输入，流式字幕和语音回复，暂停、结束与插话打断。
- 本地 faster-whisper 识别，Silero VAD / Smart Turn 轮次判断，Drama 3 或 Qwen3-TTS Base 合成；同一时间运行一个 TTS 后端。
- 导入自己的 PCM16 WAV 和对应文字，在两种后端之间复用参考录音。源码不附带示例人声或预设声纹。
- 默认 DeepSeek，可显式配置兼容的 Chat Completions / Agent 接口，在设置中管理模型和连接。
- WebGL 动态丝带与 Canvas2D 回退、文字输入、字幕、通话记录、音频与音色设置。
- 独立 Python runtime、HTTP / WebSocket 接口和 JavaScript `VoiceClient`，见 [接入指南](docs/REUSE.md)。

## 先运行演示

需要 Git、CPython **3.11 x64**（含 `py.exe`）和 Edge / Chrome。在 PowerShell 中运行：

```powershell
git clone https://github.com/next1foreal-dotcom/her-voice-windows.git
cd her-voice-windows
.\scripts\start-demo.ps1
```

启动器会创建独立 Python 环境并安装轻量依赖，随后打开 `http://127.0.0.1:8790`。首次安装需要网络。

演示使用预设事件和测试音，不采集真实麦克风、不加载语音模型、不调用付费 API，不能用于评估真实音色或对话延迟。手动打开页面可使用 `-NoBrowser`；没有 `py.exe` 时可用 `-PythonExe 'C:\Path\To\Python311\python.exe'` 指定解释器。

脚本被执行策略阻止时，可仅在当前 PowerShell 会话执行 `Set-ExecutionPolicy -Scope Process Bypass`，再重试。

## 真实语音

目标开发配置是 **Windows x64、RTX 4080 SUPER 16GB、64GB RAM**；这是开发基线，不是所有硬件上的最低配置或性能承诺。Drama 还需要 Visual Studio C++ Build Tools、CMake / Ninja、CUDA Toolkit 和约 **22.91 GiB** 的 GGUF 文件；环境、其他模型与构建缓存另占空间。

1. 运行 `.\scripts\install-windows.ps1` 安装固定的 Python 语音依赖。
2. 准备所选后端：Drama 使用 `.\scripts\install-drama.ps1`，并按[安装指南](docs/GETTING_STARTED.md)预下载识别与轮次模型；Qwen 使用指南中的 Qwen 模型下载命令。
3. 运行 `.\scripts\start.ps1`，在 **设置 → 回答接口** 填入自己的 API Key 和模型。也可先将 `.env.example` 复制为 `.env`。
4. 导入有权使用的录音与准确文字，选择后端和音色，再开始通话。

完整命令、模型下载和排错见 [安装指南](docs/GETTING_STARTED.md)，原生构建见 [DRAMA_SETUP.md](docs/DRAMA_SETUP.md)。源码不含模型权重、CUDA 可执行文件或预构建安装包；安装脚本不会自动应用 `patches/audio.cpp/` 中的本机实验补丁。

## 数据与隐私

麦克风音频由本机语音组件处理；**识别文字和对话上下文会发送到你配置的文字模型服务**。真实对话、模型预热及设置中的“测试连线”可能产生 API 用量。

API Key 保存在本机 `.env` 或 `.runtime/provider-settings.json`；后者为明文文件。参考录音、文字与派生缓存保存在 `voices/`。这些路径、模型、虚拟环境和工作记录均被 Git 忽略。日志、截图和浏览器通话记录可能包含个人数据，分享前请检查。

服务默认绑定 `127.0.0.1`，用于单人本机运行。网络部署与多人服务不属于当前支持范围。详见 [SECURITY.md](SECURITY.md)。

## 开发与验证

运行助手无需前端构建。开发测试使用 Node.js 和 pnpm；Python 测试需可导入项目及相应依赖。

```powershell
# 完成语音依赖安装后
.\.venv-speech\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
pnpm test
```

部分上游源码契约测试依赖固定的 speech-to-speech 文件及 DSP 包，缺少条件会明确跳过。浏览器脚本另需 Playwright，见 [验证说明](docs/VALIDATION.md)。

初始自动化与浏览器验证见 [VALIDATION.md](docs/VALIDATION.md)，后续 Windows 原生 TTS、参考编码、启动与传输实验见 [PATCHES.md](docs/PATCHES.md) 和 [证据目录](docs/evidence/)。局部 PCM 一致性或本机计时不代表完整对话体验、新电脑安装成功或任意硬件实时运行；麦克风、扬声器、回声、音色与端到端延迟请按 [Windows 验收清单](docs/WINDOWS_ACCEPTANCE.md) 检查。

## 文档

| 文档 | 内容 |
|---|---|
| [安装与运行](docs/GETTING_STARTED.md) | 完整步骤、模型准备、操作与排错 |
| [架构](docs/ARCHITECTURE.md) | 进程、流式音频与会话生命周期 |
| [模块接入](docs/REUSE.md) | VoiceClient、Agent 接口与工作区隔离 |
| [依赖](docs/DEPENDENCIES.md) / [上游版本](docs/UPSTREAM.md) | 约束、来源与固定 revision |
| [补丁与实验](docs/PATCHES.md) | 已验证补丁、可选实验与被拒绝的方案 |
| [第三方声明](docs/THIRD_PARTY_NOTICES.md) | 代码、模型及服务的许可入口 |
| [贡献指南](CONTRIBUTING.md) | 问题报告、开发检查与 PR |

## 许可与致谢

本项目新增代码采用 [MIT License](LICENSE)。audio.cpp 派生补丁保留 [Apache-2.0 许可](patches/audio.cpp/LICENSE.upstream)；模型、第三方依赖与服务适用各自许可及条款，见 [第三方声明](docs/THIRD_PARTY_NOTICES.md)。

感谢 Hugging Face speech-to-speech、audio.cpp、Drama、Qwen3-TTS、faster-whisper、Silero、Smart Turn 和 SoXR 等上游项目。早期交互参考 JOSIE 的公开设计，当前界面采用 OS¹ 风格的程序绘制丝带；本项目为独立实现，不代表与相关作品、品牌或上游项目存在官方关联。不分发电影音轨、第三方人物声纹或原始参考媒体；请使用获得授权的录音。
