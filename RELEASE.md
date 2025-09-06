# 发版指南

本文档记录了如何为 AstrBot 上下文增强器创建新版本。

## 🏷️ 版本标签规范

### 标签格式
- 格式：`v主版本.次版本.修订版本`
- 示例：`v2.0.0`, `v2.1.0`, `v2.0.1`

### 版本号说明
- **主版本号（Major）**: 不兼容的 API 修改
- **次版本号（Minor）**: 向后兼容的新功能
- **修订版本号（Patch）**: 向后兼容的缺陷修复

## 🚀 发版流程

### 1. 准备发版
```bash
# 检查代码状态
git status

# 确保在 main 分支
git checkout main

# 拉取最新代码
git pull origin main
```

### 2. 更新版本信息
更新以下文件中的版本号：
- [ ] `metadata.yaml` - 插件元数据
- [ ] `CHANGELOG.md` - 更新日志
- [ ] `README.md` - 如果有版本相关信息

### 3. 提交版本更新
```bash
# 添加更改
git add .

# 提交更改
git commit -m "chore: 准备发布 v2.0.0"

# 推送到远程
git push origin main
```

### 4. 创建标签
```bash
# 创建带注释的标签
git tag -a v2.0.0 -m "Release v2.0.0

✨ 新功能:
- 全新的信息层次结构
- 角色扮演支持
- 机器人回复收集
- 一问一答对话整理

🔧 技术改进:
- 系统安全性增强
- 插件兼容性优化
- 性能优化
- 完善的错误处理

📚 文档:
- 完整的 README
- 详细的配置说明
- 技术文档"

# 推送标签
git push origin v2.0.0
```

### 5. 创建 GitHub Release
1. 访问 https://github.com/muyouzhi6/astrbot_plugin_context_enhancer/releases
2. 点击 "Create a new release"
3. 选择刚创建的标签 `v2.0.0`
4. 填写发布标题：`v2.0.0 - 智能上下文增强，支持"读空气"功能`
5. 填写发布说明（参考 CHANGELOG.md）
6. 勾选 "Set as the latest release"
7. 点击 "Publish release"

## 📦 发布内容模板

### Release Title
```
v2.0.0 - 智能上下文增强，支持"读空气"功能
```

### Release Description
```markdown
## 🎉 AstrBot 上下文增强器 v2.0.0 发布！

这是一个重大更新版本，带来了全新的"读空气"功能和智能上下文增强能力。

### ✨ 主要新功能

#### 🏗️ 全新信息层次结构
1. **当前群聊状态** - 群聊氛围、活跃用户、话题分析
2. **最近群聊内容** - 普通消息背景信息
3. **与你相关的对话** - 触发 AI 回复的重要对话
4. **最近图片信息** - 视觉上下文补充
5. **当前请求详情** - 详细的请求信息和触发方式

#### 🎭 角色扮演支持
- 通过 `bot_self_reference` 配置支持各种人设
- 完美兼容人设系统，不影响 system prompt
- 保持对话中的角色一致性

#### 🤖 智能消息收集
- 收集机器人回复，补充数据库记录不足
- 一问一答对话整理
- 智能分类不同类型的消息

### 🔧 技术改进
- 🛡️ **系统安全性** - 不影响 system_prompt
- 🔗 **插件兼容性** - 使用合理优先级
- ⚡ **性能优化** - 异步处理，内存管理
- 🔍 **调试增强** - 详细的日志系统

### 📦 安装方法

```bash
cd /path/to/AstrBot/data/plugins/
git clone https://github.com/muyouzhi6/astrbot_plugin_context_enhancer.git
```

### 📚 文档
- [详细使用指南](README.md)
- [配置说明](README.md#配置)
- [更新日志](CHANGELOG.md)

### 🙏 致谢
感谢所有测试和反馈的用户！

---
**完整更新内容请查看 [CHANGELOG.md](CHANGELOG.md)**
```

## 🔄 热修复发版

对于紧急修复（patch 版本）：

```bash
# 创建修复分支
git checkout -b hotfix/v2.0.1

# 进行修复
# ... 修改代码 ...

# 提交修复
git commit -m "fix: 修复关键问题"

# 合并到 main
git checkout main
git merge hotfix/v2.0.1

# 创建标签
git tag -a v2.0.1 -m "Hotfix v2.0.1: 修复关键问题"

# 推送
git push origin main v2.0.1

# 删除修复分支
git branch -d hotfix/v2.0.1
```

## 📋 发版检查清单

发版前确保：
- [ ] 所有测试通过
- [ ] 文档已更新
- [ ] 版本号已更新
- [ ] CHANGELOG.md 已更新
- [ ] 代码已推送到 main
- [ ] 标签已创建并推送
- [ ] GitHub Release 已创建
- [ ] 发布说明已填写完整
