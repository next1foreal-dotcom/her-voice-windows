# 0.4：第三方来源与授权入口

本项目新增的网页、桥接、音色管理和启动代码按根目录 MIT 许可证交付。外部引擎、Python 包及模型保留各自许可证；本项目的 MIT 许可证不替代它们。源码交付不包含模型权重、编译好的 CUDA 原生服务或 JOSIE 私有应用源码，安装脚本分别取得相应依赖。

| 来源 | 本项目用途 | 固定来源／授权入口 |
|---|---|---|
| Hugging Face speech-to-speech 1.0.0 | VAD、ASR、LLM/TTS 编排与 Realtime 协议 | [固定提交](https://github.com/huggingface/speech-to-speech/tree/0ed1bb95bd335aa89bb7c172b6a347b6dae596f8) 的 LICENSE / pyproject；上游标记 Apache-2.0。 |
| 0xShug0/audio.cpp | Windows CUDA Drama 3 原生运行时 | [固定 LICENSE](https://github.com/0xShug0/audio.cpp/blob/0bf4d155f2af5f85853415170622dc145e3d62c3/LICENSE)，Apache-2.0；其依赖和子模块保留各自说明。 |
| Jmica/Drama-3-GGUF | Drama 主用 GGUF：Q8 主干、Q4_K_S 专家 | [固定仓库文件](https://huggingface.co/Jmica/Drama-3-GGUF/tree/17a5b40c5dc2c2be986f5371a7e650108b828e29)。模型卡和模型许可证独立于 audio.cpp；本文不从运行时许可证推断权重授权。 |
| Qwen/Qwen3-TTS-12Hz-0.6B-Base | 备用参考音色合成权重 | [固定模型卡和许可证文件入口](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base/tree/5d83992436eae1d760afd27aff78a71d676296fc)。 |
| faster-qwen3-tts / qwen-tts-hf | Qwen 的流式 torch 路径与 Transformers 5 兼容包 | [faster-qwen3-tts 0.5.4](https://pypi.org/project/faster-qwen3-tts/0.5.4/)、[qwen-tts-hf 0.1.1.post1](https://pypi.org/project/qwen-tts-hf/0.1.1.post1/)。后者为兼容发行包，区别于 Qwen 官方发行包。 |
| Python-SoXR / libsoxr | Drama 44.1 kHz → 16 kHz 有状态流式重采样 | [Python-SoXR 1.0.0](https://pypi.org/project/soxr/1.0.0/)，标记 LGPL-2.1-or-later；包含的 libsoxr / PFFFT 说明见该发行包。 |
| faster-whisper / CTranslate2 | 本地多语识别 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[CTranslate2](https://github.com/OpenNMT/CTranslate2)，保留所安装发行包的 LICENSE。 |
| Systran/faster-whisper-small | 共用 ASR 权重 | [固定模型仓库](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120)。 |
| Silero / SmartTurn | 语音活动和轮次判断 | [固定 Silero](https://github.com/snakers4/silero-vad/tree/1e261b036686cd0017d500ee96acd1c4ba572a9d)、[固定 SmartTurn](https://huggingface.co/pipecat-ai/smart-turn-v3/tree/f766f81d3cfdf7737ac64aad813d91bbfd56bf93)。 |
| JOSIE-Voice | 公开交互设计参考 | [作者 README](https://github.com/Goekdeniz-Guelmez/JOSIE-Voice/blob/main/README.md)。界面为独立实现，没有复制未公开应用源码，也不代表官方合作。 |
| DeepSeek | 默认文字模型 API | [官方文档](https://api-docs.deepseek.com/)及用户账户对应服务条款；也可配置兼容 Agent 接口。 |

两个公开适配点均有记录：安装器对固定 S2S 的 Silero 来源做一处修改；Drama worker 对固定 HTTP handler 的重采样类做内存内替换。原始文件哈希、修改范围、SoXR 版本和 native pins 记录在 [upstream-lock.json](../config/upstream-lock.json)，并见 [PATCHES.md](PATCHES.md)、[UPSTREAM.md](UPSTREAM.md)。没有修改第三方模型权重。

用户导入的 WAV 和对应文字单独保存在本地工作目录，由用户提供；不包含在项目示例包。两个 TTS 后端复用这些输入，不共享 embedding。

界面使用系统字体和程序绘制的 Canvas 动画，不加载外部字体 CDN 或图片。开发验收使用的浏览器和字体不属于正式 Windows 推理依赖。实际安装的完整 Python 依赖记录由 `.runtime/pip-freeze-windows.txt` 保存；此表是主要集成来源索引，不是所有传递依赖的清单。
