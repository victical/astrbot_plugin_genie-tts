# AstrBot Genie-TTS 插件

本插件为 AstrBot 集成了 [Genie-TTS](https://github.com/High-Logic/Genie-TTS) 服务，能够将大语言模型（LLM）的文本回复实时合成为语音消息。

**项目来源与致谢:**
- **核心 TTS 服务**: 本插件依赖于 [Genie-TTS](https://github.com/High-Logic/Genie-TTS) 项目提供的语音合成服务。
- **开发参考**: 插件的开发过程参考了 [astrbot_plugin_tts_emotion_router](https://github.com/muyouzhi6/astrbot_plugin_tts_emotion_router) 项目的结构和实现。
- **此项目由 AI 开发**。

---

## ✨ 功能特性

- **实时语音合成**: 自动将 LLM 的文本回复转换为语音消息。
- **高质量语音**: 利用 Genie-TTS 模型的先进能力，生成自然流畅的语音。
- **高度可配置**: 提供丰富的配置项，如触发概率、文本长度限制、冷却时间等。
- **会话级控制**: 支持在不同会话中独立启用或禁用 TTS 功能。
- **便捷测试指令**: 内置指令，方便您快速测试 TTS 服务器的连通性和效果。

## ⚠️ 前置要求

本插件是一个客户端，它的正常运行**必须依赖**一个独立运行的 [Genie-TTS 服务器](https://github.com/High-Logic/Genie-TTS)实例。

目前Genie-TTS只能说日语，所以在使用前告知bot让其输出日语。

在使用本插件前，请务必根据 Genie-TTS 项目的官方文档完成服务器的安装和启动。

默认情况下，本插件会尝试连接位于 `127.0.0.1:9999` 的服务器。

## ⚙️ 安装与配置

1.  通过 AstrBot 的插件市场安装本插件，或将插件文件夹手动放置于 `AstrBot/data/plugins` 目录下。
2.  重启 AstrBot。
3.  在 AstrBot 的仪表盘中找到本插件的配置页面。配置项已按功能分组，清晰明了：
    - **服务器与模型**: 配置 Genie-TTS 服务器的连接地址、端口以及要使用的角色模型。
    - **生成控制**: 管理音频生成的具体行为，如失败重试次数、长文本自动切分等。
    - **触发规则**: 定义 TTS 功能的触发条件，如概率、文本长度限制和冷却时间。
    - **内容处理**: 控制文本的过滤规则、翻译选项以及是否在发送语音的同时附带原文。

## 🚀 使用方法

插件主要在后台自动运行。您也可以通过以下指令进行手动控制：

- `gentts test <要合成的文本>`: 根据您输入的文本生成一条语音，用于测试。
- `gentts on`: 在当前会话中启用 TTS 功能。
- `gentts off`: 在当前会话中禁用 TTS 功能。
- `gentts status`: 查看当前 TTS 插件的运行状态。

### 👑 管理员指令

- `gentts globalon`: 全局启用 TTS（黑名单模式）。
- `gentts globaloff`: 全局禁用 TTS（白名单模式）。


