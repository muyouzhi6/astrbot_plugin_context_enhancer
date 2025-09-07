# astrbot_plugin_context_enhancer 优化建议文档

本文档旨在为 `astrbot_plugin_context_enhancer` 插件提供一套详细、可执行的优化方案，以解决现有问题并提升代码质量。

---

## 1. 核心优化：迁移至标准配置方案 (`_conf_schema.json`)

**问题现状**: 插件目前使用一个非标准的 `config.json` 文件来管理配置。这种方式绕过了 AstrBot 的标准配置加载、校验和UI管理机制，增加了用户手动配置的出错风险。

**解决方案**: 废弃 `config.json`，全面转向使用 `_conf_schema.json` 文件来定义配置项，并通过 `self.config.get()` 安全地在代码中读取。

### 1.1. 创建 `_conf_schema.json` 文件

在插件根目录下创建一个名为 `_conf_schema.json` 的新文件，内容如下：

```json
{
  "general_settings": {
    "type": "category",
    "label": "基础设置"
  },
  "enabled_groups": {
    "type": "list[string]",
    "label": "启用群组",
    "description": "配置插件生效的群组ID列表。如果为空，则默认对所有群组生效。",
    "default": [],
    "props": {
      "placeholder": "请输入群组ID"
    }
  },
  "context_management": {
    "type": "category",
    "label": "上下文管理"
  },
  "recent_chat_count": {
    "type": "int",
    "label": "最近聊天记录数量",
    "description": "在上下文中包含的最近用户聊天记录数量。",
    "default": 15,
    "props": {
      "min": 0,
      "max": 50
    }
  },
  "bot_reply_count": {
    "type": "int",
    "label": "机器人回复数量",
    "description": "在上下文中包含的机器人最近回复的数量。",
    "default": 5,
    "props": {
      "min": 0,
      "max": 20
    }
  },
  "image_processing": {
    "type": "category",
    "label": "图片处理"
  },
  "max_context_images": {
    "type": "int",
    "label": "上下文图片最大数量",
    "description": "在上下文中包含的最大图片数量。",
    "default": 4,
    "props": {
      "min": 0,
      "max": 10
    }
  },
  "enable_image_caption": {
    "type": "bool",
    "label": "启用图片描述",
    "description": "是否为上下文中的图片生成描述。关闭此项可节省资源。",
    "default": true
  },
  "image_caption_provider_id": {
    "type": "string",
    "label": "图片描述提供商ID",
    "description": "（可选）指定用于图片描述的LLM提供商ID。如果留空，将使用默认的视觉模型。",
    "default": ""
  },
  "image_caption_prompt": {
    "type": "string",
    "label": "图片描述提示词",
    "description": "用于指导图片描述生成的提示词。",
    "default": "请简洁地描述这张图片的主要内容，重点关注与聊天相关的信息"
  }
}
```

### 1.2. 修改 `main.py` 以读取标准配置

接下来，需要修改 `main.py` 中所有读取配置的地方，从 `self.config.get("key_in_snake_case")` 的方式读取。

**代码修改示例**：

将类似以下的旧代码：
```python
# 旧的读取方式
max_chats = self.config.get("最近聊天记录数量", 15)
enabled_groups = self.config.get("启用群组", [])
```

修改为符合 `_conf_schema.json` 中定义的 `key` 的新代码：
```python
# 新的、标准的读取方式
max_chats = self.config.get("recent_chat_count", 15)
enabled_groups = self.config.get("enabled_groups", [])
```

### 1.3. 删除 `config.json` 文件

完成以上代码修改并确认插件能通过 AstrBot 的 Web UI 正确配置和运行后，就可以安全地从插件目录中删除 `config.json` 文件了。

---

## 2. 健壮性与可维护性提升建议

除了解决核心的配置问题，以下建议可以进一步提升代码的质量。

### 2.1. 优化 `utils` 模块的导入逻辑

**问题现状**: `main.py` 中使用了 `try...except` 块来处理 `utils` 模块的导入，这虽然能防止程序崩溃，但不够优雅，且在打包或不同环境下可能出现路径问题。

**解决方案**: 确保 `utils` 目录包含一个 `__init__.py` 文件（即使是空的），并始终使用相对路径 `from .utils.xxx import yyy` 进行导入。这可以保证 Python 将 `utils` 视为一个包，从而实现更可靠的模块解析。

**操作步骤**:
1.  确认 `data/plugins/astrbot_plugin_context_enhancer/utils/__init__.py` 文件存在。如果不存在，则创建一个空文件。
2.  将 `main.py` 中的导入部分修改为：

    ```python
    # main.py

    # ... (其他导入)
    try:
        from .utils.image_caption import ImageCaptionUtils
        from .utils.message_utils import MessageUtils
    except ImportError:
        ImageCaptionUtils = None
        MessageUtils = None
        logger.warning("utils 模块导入失败，相关功能将不可用。")

    ```
    *说明：保留 `try-except` 结构是为了实现优雅降级，当 `utils` 模块因缺少依赖（如 `Pillow`）而无法导入时，插件主体功能仍可运行。*


### 2.2. 将常量和 Prompt 模板移至独立文件

**问题现状**: `ContextConstants` 类中包含了大量的字符串常量和 Prompt 模板，这使得 `main.py` 文件显得臃肿，并且不利于模板的单独管理和修改。

**解决方案**: 创建一个 `constants.py` 文件，将这些静态数据移入其中，然后在 `main.py` 中导入。

**操作步骤**:
1.  在插件目录下创建 `constants.py` 文件：

    ```python
    # data/plugins/astrbot_plugin_context_enhancer/constants.py

    class ContextMessageType:
        LLM_TRIGGERED = "llm_triggered"
        NORMAL_CHAT = "normal_chat"
        IMAGE_MESSAGE = "image_message"
        BOT_REPLY = "bot_reply"

    class PromptTemplates:
        PROMPT_HEADER = "你正在浏览聊天软件，查看群聊消息。"
        RECENT_CHATS_HEADER = "\n最近的聊天记录:"
        BOT_REPLIES_HEADER = "\n你最近的回复:"
        USER_TRIGGER_TEMPLATE = "\n现在 {sender_name}（ID: {sender_id}）发了一个消息: {original_prompt}"
        PROACTIVE_TRIGGER_TEMPLATE = "\n你需要根据以上聊天记录，主动就以下内容发表观点: {original_prompt}"
        PROMPT_FOOTER = "需要你在心里理清当前到底讨论的什么，搞清楚形势，谁在跟谁说话，你是在插话还是回复，然后根据你的设定和当前形势做出最自然的回复。"

    # 其他应用级常量
    MESSAGE_MATCH_TIME_WINDOW = 3
    INACTIVE_GROUP_CLEANUP_DAYS = 7
    COMMAND_PREFIXES = ["/", "!", "！", "#", ".", "。"]
    ```

2.  在 `main.py` 中导入并使用这些常量。

### 2.3. 明确数据持久化说明

**问题现状**: 代码中已正确地将数据存储在 `data/` 目录下，但缺乏对用户的明确告知。

**解决方案**: 在 `README.md` 文件中增加一个“数据存储”章节，清晰地告知用户数据文件的位置、作用以及注意事项。

**建议操作**:
在 `README.md` 文件中增加以下内容：

```markdown
## 数据存储

本插件会将群聊的上下文历史记录缓存到本地，以便在重启后恢复。

- **存储路径**: `data/astrbot_plugin_context_enhancer/context_cache.json`
- **存储格式**: JSON

此文件包含了插件运行所需的所有历史消息，请勿手动修改，以免造成格式错误。如果需要重置上下文，删除此文件即可。
```
---

## 3. 总结与后续步骤

**总结**:
通过实施以上优化，`astrbot_plugin_context_enhancer` 插件将完全符合 AstrBot 的开发规范，并在配置灵活性、代码可维护性和用户体验上得到显著提升。

**后续操作建议**:
我已为你整理好所有必要的修改方案。接下来，我建议切换到 **"代码"** 模式，由我来为你直接执行这些修改。
