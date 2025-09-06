# AstrBot 上下文增强器 v2.0 🧠

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![AstrBot](https://img.shields.io/badge/AstrBot-v4.0.0+-green.svg)](https://github.com/Soulter/AstrBot)

一个为 AstrBot 设计的简单直接的上下文增强插件，为 LLM 提供群聊历史和机器人回复记录，帮助 AI 更好地理解对话语境。

## ✨ 特性

### 🎯 核心功能
- **简单直接的上下文增强** - 类似SpectreCore的简洁方式
- **群聊历史收集** - 自动收集最近的群聊消息
- **机器人回复记录** - 记录机器人的历史回复内容（包括TTS等情况）
- **智能消息分类** - 区分普通消息、触发消息和机器人回复

### 📝 上下文格式
插件提供的上下文格式简洁明了：
```
你正在浏览聊天软件，查看群聊消息。

最近的聊天记录:
用户A: 今天天气真好
用户B: 是啊，适合出去玩

你最近的回复:
你回复了: 确实是个好天气呢
你回复了: 大家可以去公园走走

现在 用户C（ID: 123456）发了一个消息: 有人想一起去爬山吗
需要你根据你的设定和当前形势做出最自然的回复。
```

### 🔧 技术特点
- **系统安全** - 不覆盖 system_prompt，完全兼容人设系统
- **插件兼容** - 使用合理的优先级，不干扰其他插件
- **智能检测** - 避免重复增强，防止内容叠加
- **高级功能** - 支持图片描述和高级消息格式化（可选）

## 📦 安装

### 方法一：直接下载
1. 下载本仓库到 AstrBot 的插件目录
```bash
cd /path/to/AstrBot/data/plugins/
git clone https://github.com/muyouzhi6/astrbot_plugin_context_enhancer.git
```

2. 重启 AstrBot

### 方法二：手动安装
1. 在 `data/plugins/` 目录下创建 `astrbot_plugin_context_enhancer` 文件夹
2. 将所有文件复制到该文件夹中
3. 重启 AstrBot

## ⚙️ 配置

插件提供简洁的配置选项：

```json
{
  "enabled_groups": [],  // 启用的群组列表（空数组表示所有群组）
  "max_normal_messages": 12,  // 最大普通消息数量
  "max_triggered_messages": 8,  // 最大触发消息数量
  "max_image_messages": 4,  // 最大图片消息数量
  "enable_image_caption": true,  // 启用图片描述（需要高级功能）
  "image_caption_provider_id": "",  // 图片描述专用LLM提供商ID（可选）
  "image_caption_prompt": "请简洁地描述这张图片的主要内容，重点关注与聊天相关的信息",  // 图片描述提示词
  "enable_at_processing": true   // 启用@信息处理
}
```

### 配置说明
- **enabled_groups**: 指定在哪些群组中启用插件，空数组表示所有群组
- **max_normal_messages**: 包含在上下文中的最大普通聊天消息数量
- **max_triggered_messages**: 包含在上下文中的最大触发消息数量  
- **max_image_messages**: 包含在上下文中的最大图片消息数量
- **enable_image_caption**: 是否启用图片智能描述功能
- **image_caption_provider_id**: 专用于图片描述的LLM提供商ID
  - 如为空，则使用当前选择的主提供商
  - 建议使用支持图像模态的模型（如GPT-4V、Claude-3等）
  - 适用于主要LLM不支持图像处理的场景
- **image_caption_prompt**: 自定义图片描述的提示词模板
- **enable_at_processing**: 是否处理@用户的信息

## 🚀 使用

插件安装后会自动工作，无需手动配置。它会：

1. **自动收集群聊消息** - 包括普通聊天、@机器人的消息、图片等
2. **记录机器人回复** - 自动记录机器人的回复内容供下次参考
3. **智能增强上下文** - 在LLM请求时自动添加相关的群聊上下文
4. **避免重复增强** - 智能检测已增强的内容，避免重复处理

## 🔧 高级功能

### 图片描述功能
当 `enable_image_caption` 为 true 时，插件会：
- 自动为群聊中的图片生成智能描述
- 将图片内容加入上下文，帮助AI理解视觉信息
- 使用缓存机制避免重复处理相同图片

### 图片处理分离
对于使用不支持图像模态的主要LLM（如某些开源模型）的用户：
1. **设置主要LLM** - 配置为文本处理模型（如Qwen、ChatGLM等）
2. **配置图片描述提供商** - 在`image_caption_provider_id`中指定支持图像的模型ID（如GPT-4V、Claude-3等）
3. **自动智能分工** - 插件会自动使用专用模型处理图片，主模型处理对话

这样可以：
- 💰 **节省成本** - 仅在需要时使用昂贵的多模态模型
- 🎯 **优化性能** - 主要对话使用快速的文本模型
- 🔄 **灵活切换** - 可以随时调整图片处理策略

### 高级消息格式化
插件支持多种消息组件的高级格式化：
- @用户信息
- 表情和贴纸
- 语音和视频
- 回复和转发消息

## 🐛 故障排除

### 常见问题

**Q: 为什么看到"utils 模块导入失败"的警告？**
A: 这是正常的，表示高级功能（图片描述等）不可用，但核心功能仍然正常工作。

**Q: 如何启用高级功能？**
A: 确保utils目录下的文件完整，并重启AstrBot。正常情况下会自动启用。

**Q: 插件会影响其他插件吗？**
A: 不会。插件使用合理的优先级设计，并且不修改system_prompt，完全兼容其他插件。

**Q: 如何禁用某个群组的上下文增强？**
A: 在配置中的 `enabled_groups` 中指定要启用的群组ID，不在列表中的群组将被跳过。

## 📝 更新日志

### v2.0.0 (2025-09-06)
- 🔄 **重大重构**: 简化架构，参考SpectreCore的简洁方式
- ✨ **新增功能**: 机器人回复记录功能
- 🔧 **优化**: 移除复杂的分层检测，使用简单直接的上下文格式
- 🐛 **修复**: 解决utils模块导入问题，启用高级功能
- 📖 **改进**: 更新文档和配置说明

### v1.x.x
- 初始版本功能

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- [AstrBot](https://github.com/Soulter/AstrBot) - 优秀的多平台聊天机器人框架
- [SpectreCore](https://github.com/23q3/astrbot-plugin-spectrecore) - 简洁设计的参考来源

---

如果这个插件对你有帮助，请给个 ⭐ Star 支持一下！
