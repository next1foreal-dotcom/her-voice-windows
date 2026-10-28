# 参与贡献

欢迎通过 [Issues](https://github.com/next1foreal-dotcom/her-voice-windows/issues) 报告问题、讨论改进，或提交 Pull Request。较大的行为改变请先说明使用场景和预期结果。

## 报告问题

请提供 commit、Windows / Python / 浏览器版本、语音后端、复现步骤、预期与实际结果，以及去除个人信息的错误内容。涉及 GPU 时补充显卡、驱动与显存信息；注明是演示还是真实语音模式。

不要上传 API Key、`.env`、`.runtime/provider-settings.json`、私人录音、声纹缓存、完整聊天记录或模型权重。安全问题请按 [SECURITY.md](SECURITY.md) 私密报告。

## 开发与检查

Fork 并克隆仓库，在独立分支开发。按 [安装指南](docs/GETTING_STARTED.md) 准备 CPython 3.11 x64；只检查界面时可以先运行 `.\scripts\start-demo.ps1`。保留上游固定 revision，不要将自己的路径、设备、Key 或录音写成默认配置。

```powershell
# 完成语音依赖安装后
.\.venv-speech\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
pnpm test
git diff --check
```

JavaScript 使用 Node 自带测试运行器，不需要前端打包或 npm 依赖。部分 Python DSP / 上游源码检查缺少条件时会跳过，请在 PR 中明确报告。浏览器检查另见 [验证说明](docs/VALIDATION.md)。

## Pull Request

说明问题、修改后的行为、实际检查结果及未验证边界。界面修改附截图；语音修改区分协议、浏览器音频、本机 TTS 和真实麦克风 / 模型服务验收。不要默认发送付费请求或下载大模型。

修改 audio.cpp 派生代码时保留 Apache-2.0 许可，并记录基点、应用顺序、验证与回退。不要将未通过完整模型检查的实验补丁设为默认。

请仅贡献有权提交的内容，沿用对应文件的现有许可；第三方内容注明来源与许可。参考音色和模型权重不随 PR 提交。
