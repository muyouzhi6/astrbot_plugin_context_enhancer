# 贡献指南

感谢你对 AstrBot 上下文增强器的关注！我们欢迎所有形式的贡献。

## 🤝 贡献方式

### 🐛 报告问题
如果你发现了 bug 或有功能建议：
1. 先搜索 [Issues](https://github.com/muyouzhi6/astrbot_plugin_context_enhancer/issues) 确认问题未被报告
2. 创建新 Issue，使用合适的模板
3. 提供详细的描述和复现步骤

### 💡 功能建议
1. 在 Issues 中描述你的想法
2. 说明功能的使用场景和价值
3. 等待维护者反馈

### 🔧 代码贡献
1. Fork 仓库
2. 创建功能分支
3. 开发和测试
4. 提交 Pull Request

## 🛠️ 开发环境

### 环境要求
- Python 3.10+
- AstrBot v4.0.0+
- Git

### 设置开发环境
```bash
# 克隆你的 fork
git clone https://github.com/你的用户名/astrbot_plugin_context_enhancer.git
cd astrbot_plugin_context_enhancer

# 添加上游仓库
git remote add upstream https://github.com/muyouzhi6/astrbot_plugin_context_enhancer.git

# 安装到 AstrBot
ln -s $(pwd) /path/to/AstrBot/data/plugins/astrbot_plugin_context_enhancer
```

### 开发流程
```bash
# 1. 同步最新代码
git checkout main
git pull upstream main

# 2. 创建功能分支
git checkout -b feature/你的功能名

# 3. 开发...
# 编辑代码

# 4. 测试
# 在 AstrBot 中测试插件

# 5. 提交
git add .
git commit -m "feat: 添加新功能"

# 6. 推送到你的 fork
git push origin feature/你的功能名

# 7. 创建 Pull Request
# 在 GitHub 上创建 PR
```

## 📝 代码规范

### Python 代码风格
- 遵循 PEP 8
- 使用 4 空格缩进
- 行长度不超过 100 字符
- 函数和类使用文档字符串

### 注释规范
```python
def example_function(param1: str, param2: int) -> bool:
    """
    功能简述
    
    Args:
        param1: 参数1的说明
        param2: 参数2的说明
        
    Returns:
        返回值说明
        
    Raises:
        ExceptionType: 异常说明
    """
    pass
```

### 提交信息规范
使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<类型>[可选的作用域]: <描述>

[可选的正文]

[可选的脚注]
```

类型：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档更新
- `style`: 代码格式化
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 维护任务

示例：
```
feat: 添加角色扮演支持

- 新增 bot_self_reference 配置项
- 支持自定义机器人称呼
- 在上下文中保持称呼一致性

Closes #123
```

## 🧪 测试

### 测试要求
- 所有新功能必须经过测试
- 修复的 bug 必须包含回归测试
- 确保向后兼容性

### 测试方法
1. **单元测试**：测试独立的函数和方法
2. **集成测试**：在真实 AstrBot 环境中测试
3. **用户测试**：在实际群聊中验证功能

### 测试清单
- [ ] 插件正常加载
- [ ] 配置项生效
- [ ] 消息收集正常
- [ ] 上下文增强有效
- [ ] 错误处理正确
- [ ] 不影响其他插件
- [ ] 性能表现良好

## 📚 文档

### 文档更新
代码更改可能需要更新：
- [ ] README.md
- [ ] CHANGELOG.md
- [ ] 代码注释
- [ ] 配置说明

### 文档风格
- 使用清晰简洁的语言
- 提供实际的使用示例
- 包含配置参数说明
- 添加必要的警告和提示

## 🔍 代码审查

### Pull Request 要求
- [ ] 描述清楚更改内容
- [ ] 关联相关 Issue
- [ ] 包含测试结果
- [ ] 更新相关文档
- [ ] 通过所有检查

### 审查标准
- 代码质量和可读性
- 功能正确性
- 性能影响
- 安全性考虑
- 文档完整性

## 🏷️ 版本发布

### 版本规划
- **Major**: 重大功能更新或 API 变更
- **Minor**: 新功能添加
- **Patch**: Bug 修复

### 发布流程
参见 [RELEASE.md](RELEASE.md)

## 💬 沟通

### 获取帮助
- 查看 [Issues](https://github.com/muyouzhi6/astrbot_plugin_context_enhancer/issues)
- 创建新 Issue 提问
- 在 Pull Request 中讨论

### 保持友善
- 尊重所有贡献者
- 提供建设性的反馈
- 保持开放的心态

## 🙏 致谢

感谢所有贡献者的努力！你们的贡献让这个项目变得更好。

### 贡献者
- [木有知](https://github.com/muyouzhi6) - 项目维护者

---

再次感谢你的贡献！如果有任何问题，请随时联系我们。
