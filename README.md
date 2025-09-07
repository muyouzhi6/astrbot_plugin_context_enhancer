# AstrBot 上下文增强器 v2.0 🧠

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![AstrBot](https://img.shields.io/badge/AstrBot-v4.0.0+-green.svg)](https://github.com/Soulter/AstrBot)

一个为 AstrBot 设计的简单直接的上下文增强插件，为 LLM 提供群聊历史和机器人回复记录，帮助 AI 更好地理解对话语境，实现“读空气”般的智能交互。

## ✨ 特性

- **🎯 简单直接的上下文增强** - 采用类似 SpectreCore 的简洁方式，不干扰人设系统。
- **📝 自动记录** - 自动收集群聊历史和机器人自身的回复记录。
- **🖼️ 图像理解** - 支持图片描述功能，让 AI 能够“看到”图片内容。（可选）
- **🛡️ 安全兼容** - 不覆盖 `system_prompt`，不干扰其他插件的正常工作。
- **🔧 灵活配置** - 提供丰富的配置选项，可为不同群组启用或禁用功能。

## 📦 安装

1.  将 `astrbot_plugin_context_enhancer` 文件夹放入 AstrBot 的 `data/plugins/` 目录。
2.  重启 AstrBot。

首次加载后，插件会在 `data/astrbot_plugin_context_enhancer/` 目录下自动生成 `config.json` 配置文件。

## ⚙️ 配置

配置文件 `config.json` 位于 `data/astrbot_plugin_context_enhancer/` 目录下。

```json
{
  "启用群组": [],
  "最近聊天记录数量": 15,
  "机器人回复数量": 5,
  "上下文图片最大数量": 4,
  "启用图片描述": true,
  "图片描述提供商ID": "",
  "图片描述提示词": "请简洁地描述这张图片的主要内容，重点关注与聊天相关的信息",
  "处理@信息": true,
  "收集机器人回复": true
}
```

### 配置说明

-   `启用群组` (list): 指定启用插件的群组 ID 列表。如果列表为空 `[]`，则对所有群组生效。
-   `最近聊天记录数量` (int): 在上下文中包含的最近用户聊天记录数量。
-   `机器人回复数量` (int): 在上下文中包含的机器人最近回复数量。
-   `上下文图片最大数量` (int): 在一次请求中最多包含的图片数量。
-   `启用图片描述` (bool): 是否启用图片内容的自动描述功能。
-   `图片描述提供商ID` (str): 用于生成图片描述的特定 LLM 提供商 ID。如果留空，则使用 AstrBot 当前默认的提供商。
-   `图片描述提示词` (str): 生成图片描述时使用的提示词。
-   `处理@信息` (bool): 是否在收集中处理 `@` 相关的消息。
-   `收集机器人回复` (bool): 是否记录并使用机器人自己的回复来构建上下文。

## 🚀 使用

插件安装并（可选地）配置后即可自动运行。它会在后台：

1.  **自动收集** 群聊中的文本和图片消息。
2.  **记录机器人回复** 以便在后续对话中参考。
3.  在 LLM 被触发时，**智能地构建** 包含历史记录的上下文，并将其注入到 Prompt 中。
4.  **避免重复增强**，如果检测到上下文已被其他方式处理，则跳过本次增强。

## 🤝 贡献

欢迎通过提交 Issue 和 Pull Request 来为本项目做出贡献。

## 📄 许可证

本项目采用 MIT 许可证。详情请参阅 [LICENSE](LICENSE) 文件。
