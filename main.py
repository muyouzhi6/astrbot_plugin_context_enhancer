from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.provider import ProviderRequest
import traceback
import json
import datetime
from collections import deque
import asyncio

# 消息类型枚举 - 重命名以避免冲突
class ContextMessageType:
    LLM_TRIGGERED = "llm_triggered"  # 触发了LLM的消息（@机器人、命令等）
    NORMAL_CHAT = "normal_chat"      # 普通群聊消息
    IMAGE_MESSAGE = "image_message"  # 包含图片的消息
    BOT_REPLY = "bot_reply"         # 🤖 机器人自己的回复（补充数据库记录不足）

class GroupMessage:
    """群聊消息包装类"""
    def __init__(self, event: AstrMessageEvent, message_type: str):
        self.event = event
        self.message_type = message_type
        self.timestamp = datetime.datetime.now()
        self.sender_name = event.message_obj.sender.nickname if event.message_obj.sender else "用户"
        self.sender_id = event.message_obj.sender.user_id if event.message_obj.sender else "unknown"
        self.group_id = event.get_group_id() if hasattr(event, 'get_group_id') else event.unified_msg_origin
        self.text_content = self._extract_text()
        self.images = self._extract_images()
        self.has_image = len(self.images) > 0
        self.image_captions = []  # 存储图片描述
        
    def _extract_text(self) -> str:
        """提取消息中的文本内容"""
        text = ""
        if self.event.message_obj and self.event.message_obj.message:
            for comp in self.event.message_obj.message:
                if isinstance(comp, Plain):
                    text += comp.text
                elif isinstance(comp, At):
                    text += f"@{comp.qq}"
        return text.strip()
    
    def _extract_images(self) -> list:
        """提取消息中的图片"""
        images = []
        if self.event.message_obj and self.event.message_obj.message:
            for comp in self.event.message_obj.message:
                if isinstance(comp, Image):
                    images.append(comp)
        return images
    
    def format_for_display(self, include_images=True) -> str:
        """格式化消息用于显示"""
        time_str = self.timestamp.strftime("%H:%M")
        result = f"[{time_str}] {self.sender_name}: {self.text_content}"
        
        if include_images and self.has_image:
            result += f" [包含{len(self.images)}张图片"
            if self.image_captions:
                result += f" - {'; '.join(self.image_captions)}"
            result += "]"
        
        return result

@register(
    "context_enhancer_v2",
    "木有知", 
    "上下文增强插件，让bot更好的理解对话。通过多维度信息收集和分层架构，为 LLM 提供丰富的群聊语境。",
    "2.0.0"
)
class ContextEnhancerV2(Star):
    """
    AstrBot 上下文增强器 v2.0
    
    作者: 木有知 (https://github.com/muyouzhi6)
    
    功能特点:
    - 🎯 智能"读空气"功能，深度理解群聊语境
    - 🏗️ 分层信息架构，按重要性组织上下文
    - 🎭 角色扮演支持，完美兼容人设系统
    - 🤖 机器人回复收集，补充数据库记录不足
    - 🔧 高度可配置，灵活适应不同需求
    
    信息层次结构:
    1. 当前群聊状态 - 群聊氛围、活跃用户、话题分析
    2. 最近群聊内容 - 普通消息背景信息
    3. 与你相关的对话 - 触发 AI 回复的重要对话
    4. 最近图片信息 - 视觉上下文补充
    5. 当前请求详情 - 详细的请求信息和触发方式
    
    技术保证:
    - 不影响 system_prompt，完全兼容人设系统
    - 使用合理优先级，不干扰其他插件
    - 异步处理，不阻塞主流程
    - 完善的错误处理
    """
    
    def __init__(self, context: Context):
        self.context = context
        self.config = self.load_config()
        logger.info("上下文增强器v2.0已初始化")
        
        # 群聊消息缓存 - 每个群独立存储
        self.group_messages = {}  # group_id -> deque of GroupMessage
        
        # 初始化配置
        self._init_message_buffers()
        
        # 显示当前配置
        logger.info(f"上下文增强器配置 - 触发消息: {self.config.get('max_triggered_messages', 10)}, "
                   f"普通消息: {self.config.get('max_normal_messages', 15)}, "
                   f"图片消息: {self.config.get('max_image_messages', 5)}")

    def load_config(self):
        """加载配置文件"""
        try:
            with open("data/plugins/astrbot_plugin_context_enhancer/config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
                logger.debug("配置文件加载成功")
                return config
        except FileNotFoundError:
            logger.info("配置文件不存在，使用默认配置")
            return self.get_default_config()
        except Exception as e:
            logger.error(f"配置文件加载失败，使用默认配置: {e}")
            return self.get_default_config()

    def get_default_config(self):
        """获取默认配置"""
        return {
            "enabled_groups": [],  # 空列表表示对所有群生效
            "enabled_private": True,
            "max_triggered_messages": 10,  # 最近触发LLM的消息数量
            "max_normal_messages": 15,     # 最近普通聊天消息数量
            "max_image_messages": 5,       # 最近图片消息数量
            "enable_image_caption": True,  # 是否启用图片描述
            "enable_atmosphere_analysis": True,  # 是否分析群聊氛围
            "min_normal_messages_for_context": 3,  # 至少多少条普通消息才提供上下文
            "ignore_bot_messages": False,  # 🤖 是否忽略机器人消息（默认保留，保证上下文完整）
            "safe_mode": True,            # 🔧 安全模式：出错时不影响其他插件
            "collect_bot_replies": True,  # 🤖 是否收集机器人回复（补充数据库记录的不足）
            "max_bot_replies": 8,         # 🤖 收集的机器人回复数量
            "bot_self_reference": "你",   # 🎭 机器人自称（支持人设角色扮演）
        }

    def _init_message_buffers(self):
        """初始化消息缓冲区"""
        # 不需要预初始化，动态创建
        pass

    def _get_group_buffer(self, group_id: str) -> deque:
        """获取群聊的消息缓冲区"""
        if group_id not in self.group_messages:
            max_total = (self.config.get('max_triggered_messages', 10) + 
                        self.config.get('max_normal_messages', 15) + 
                        self.config.get('max_image_messages', 5)) * 2  # 预留空间
            self.group_messages[group_id] = deque(maxlen=max_total)
        return self.group_messages[group_id]

    def is_chat_enabled(self, event: AstrMessageEvent) -> bool:
        """检查当前聊天是否启用增强功能"""
        if event.get_message_type() == MessageType.FRIEND_MESSAGE:
            return self.config.get("enabled_private", True)
        else:
            enabled_groups = self.config.get("enabled_groups", [])
            if not enabled_groups:  # 空列表表示对所有群生效
                return True
            return event.get_group_id() in enabled_groups

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，进行分类和存储"""
        try:
            if not self.is_chat_enabled(event):
                return
            
            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                await self._handle_group_message(event)
                
        except Exception as e:
            logger.error(f"处理消息时发生错误: {e}")
            logger.error(traceback.format_exc())

    async def _handle_group_message(self, event: AstrMessageEvent):
        """处理群聊消息"""
        try:
            # 🤖 机器人消息处理：根据配置决定是否收集
            if self._is_bot_message(event):
                if self.config.get('ignore_bot_messages', False):  # 默认不忽略
                    logger.debug("跳过机器人自己的消息（配置启用过滤）")
                    return
                else:
                    logger.debug("收集机器人自己的消息（保持上下文完整性）")
            
            # 判断消息类型
            message_type = self._classify_message(event)
            
            # 创建消息对象
            group_msg = GroupMessage(event, message_type)
            
            # 处理图片描述
            if group_msg.has_image and self.config.get('enable_image_caption', True):
                await self._process_image_captions(group_msg)
            
            # 添加到缓冲区
            buffer = self._get_group_buffer(group_msg.group_id)
            buffer.append(group_msg)
            
            logger.debug(f"收集群聊消息 [{message_type}]: {group_msg.sender_name} - {group_msg.text_content[:50]}...")
            
        except Exception as e:
            logger.error(f"处理群聊消息时发生错误: {e}")

    def _is_bot_message(self, event: AstrMessageEvent) -> bool:
        """检查是否是机器人自己发送的消息"""
        try:
            # 获取机器人自身ID
            bot_id = event.get_self_id()
            sender_id = event.get_sender_id()
            
            # 如果发送者ID等于机器人ID，则是机器人自己的消息
            if bot_id and sender_id and str(sender_id) == str(bot_id):
                return True
                
            # 额外检查：某些平台可能有特殊标识
            sender_name = event.get_sender_name().lower() if event.get_sender_name() else ""
            if any(keyword in sender_name for keyword in ["bot", "机器人", "助手", "ai"]):
                # 进一步验证：检查是否真的是当前机器人
                if bot_id and sender_id and str(sender_id) == str(bot_id):
                    return True
            
            return False
        except Exception as e:
            logger.debug(f"检查机器人消息时出错: {e}")
            return False

    def _classify_message(self, event: AstrMessageEvent) -> str:
        """分类消息类型"""
        
        # 🤖 首先检查是否是机器人消息
        if self._is_bot_message(event) and self.config.get('collect_bot_replies', True):
            return ContextMessageType.BOT_REPLY
        
        # 检查是否包含图片
        has_image = False
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, Image):
                    has_image = True
                    break
        
        if has_image:
            return ContextMessageType.IMAGE_MESSAGE
        
        # 🔍 改进的LLM触发判断逻辑
        # 1. 检查是否有@机器人
        message_text = event.message_str.lower() if event.message_str else ""
        is_at_bot = False
        
        # 检查消息中是否有@机器人的行为
        if event.message_obj and event.message_obj.message:
            bot_id = event.get_self_id()
            for comp in event.message_obj.message:
                if isinstance(comp, At):
                    if comp.qq == bot_id or comp.qq == "all":
                        is_at_bot = True
                        break
        
        # 2. 检查是否是命令格式
        is_command = False
        command_prefixes = ["/", "!", "！", "#", ".", "。"]
        if any(message_text.startswith(prefix) for prefix in command_prefixes):
            is_command = True
        
        # 3. 检查是否包含常见的机器人触发词
        trigger_keywords = [
            "bot", "机器人", "ai", "助手", "help", "帮助", 
            "查询", "搜索", "翻译", "计算", "问答"
        ]
        has_trigger_word = any(keyword in message_text for keyword in trigger_keywords)
        
        # 4. 检查是否是唤醒状态的消息
        is_wake = getattr(event, 'is_wake', False)
        is_at_or_wake = getattr(event, 'is_at_or_wake_command', False)
        
        # 综合判断是否为LLM触发消息
        if is_at_bot or is_command or is_wake or is_at_or_wake:
            return ContextMessageType.LLM_TRIGGERED
        elif has_trigger_word and len(message_text) > 10:  # 避免误判短消息
            return ContextMessageType.LLM_TRIGGERED
        
        return ContextMessageType.NORMAL_CHAT

    async def _process_image_captions(self, group_msg: GroupMessage):
        """处理图片描述（简化版）"""
        try:
            # 这里可以集成图片描述功能
            # 暂时使用简单的占位符
            for i, img in enumerate(group_msg.images):
                group_msg.image_captions.append(f"图片{i+1}")
        except Exception as e:
            logger.warning(f"处理图片描述时发生错误: {e}")

    @filter.on_llm_request(priority=100)  # 🔧 使用较低优先级，避免干扰其他插件
    async def on_llm_request(self, event: AstrMessageEvent, request: ProviderRequest):
        """LLM请求时提供增强的上下文"""
        try:
            # 🔍 调试信息：记录接收到的请求状态
            logger.debug(f"Context Enhancer接收到LLM请求:")
            logger.debug(f"  - prompt长度: {len(request.prompt) if request.prompt else 0}")
            logger.debug(f"  - system_prompt长度: {len(request.system_prompt) if request.system_prompt else 0}")
            logger.debug(f"  - contexts数量: {len(request.contexts) if request.contexts else 0}")
            
            if not self.is_chat_enabled(event):
                logger.debug(f"上下文增强器：当前聊天未启用，跳过增强。")
                return

            logger.debug(f"上下文增强器v2：开始构建智能上下文...")

            # 🤖 机器人消息处理：在LLM请求时通常不需要再次处理自己的消息
            if self._is_bot_message(event):
                logger.debug("检测到机器人自己的LLM请求，这通常不应该发生")
                return

            # 标记当前消息为LLM触发类型
            await self._mark_current_as_llm_triggered(event)

            # 构建结构化上下文
            context_info = await self._build_structured_context(event, request)
            
            if not context_info:
                logger.debug("没有足够的上下文信息，跳过增强")
                return

            # 构建新的prompt
            enhanced_prompt = await self._build_enhanced_prompt(context_info, request.prompt)
            
            # 🔧 安全地增强用户prompt，不影响system_prompt和其他插件的修改
            if enhanced_prompt and enhanced_prompt != request.prompt:
                # 保留原始的用户prompt作为核心内容，将上下文作为辅助信息
                # 不覆盖system_prompt，确保人设、时间戳等信息不丢失
                request.prompt = enhanced_prompt
                logger.debug(f"上下文增强完成，新prompt长度: {len(enhanced_prompt)}")
                logger.debug(f"System prompt保持不变，长度: {len(request.system_prompt) if request.system_prompt else 0}")
            else:
                logger.debug("prompt未发生变化，跳过替换")

        except Exception as e:
            logger.error(f"上下文增强时发生错误: {e}")
            logger.error(traceback.format_exc())
            # 🔧 出错时不影响正常流程

    async def _mark_current_as_llm_triggered(self, event: AstrMessageEvent):
        """将当前消息标记为LLM触发类型"""
        if event.get_message_type() == MessageType.GROUP_MESSAGE:
            group_id = event.get_group_id() if hasattr(event, 'get_group_id') else event.unified_msg_origin
            buffer = self._get_group_buffer(group_id)
            
            # 查找最近的匹配消息并更新类型
            for msg in reversed(buffer):
                if (msg.sender_id == event.message_obj.sender.user_id and 
                    msg.text_content == event.message_str):
                    msg.message_type = ContextMessageType.LLM_TRIGGERED
                    break

    async def _build_structured_context(self, event: AstrMessageEvent, request: ProviderRequest) -> dict:
        """构建结构化的上下文信息"""
        context_info = {
            "triggered_messages": [],
            "normal_messages": [],
            "image_messages": [],
            "bot_replies": [],           # 🤖 机器人回复消息
            "conversation_history": [],
            "atmosphere_summary": "",
        }

        # 从数据库获取对话历史
        if request.conversation and request.conversation.history:
            try:
                history_raw = json.loads(request.conversation.history)
                context_info["conversation_history"] = history_raw
            except:
                pass

        # 获取群聊消息缓存
        if event.get_message_type() == MessageType.GROUP_MESSAGE:
            group_id = event.get_group_id() if hasattr(event, 'get_group_id') else event.unified_msg_origin
            buffer = self._get_group_buffer(group_id)
            
            await self._collect_recent_messages(buffer, context_info)

        return context_info

    async def _collect_recent_messages(self, buffer: deque, context_info: dict):
        """从缓冲区收集最近的各类消息"""
        max_triggered = self.config.get('max_triggered_messages', 10)
        max_normal = self.config.get('max_normal_messages', 15)
        max_image = self.config.get('max_image_messages', 5)
        max_bot_replies = self.config.get('max_bot_replies', 8)  # 🤖 机器人回复数量

        triggered_count = 0
        normal_count = 0
        image_count = 0
        bot_reply_count = 0

        # 从最新的消息开始收集
        for msg in reversed(buffer):
            if msg.message_type == ContextMessageType.LLM_TRIGGERED and triggered_count < max_triggered:
                context_info["triggered_messages"].insert(0, msg)
                triggered_count += 1
            elif msg.message_type == ContextMessageType.NORMAL_CHAT and normal_count < max_normal:
                context_info["normal_messages"].insert(0, msg)
                normal_count += 1
            elif msg.message_type == ContextMessageType.IMAGE_MESSAGE and image_count < max_image:
                context_info["image_messages"].insert(0, msg)
                image_count += 1
            elif msg.message_type == ContextMessageType.BOT_REPLY and bot_reply_count < max_bot_replies:  # 🤖
                context_info["bot_replies"].insert(0, msg)
                bot_reply_count += 1

        # 分析群聊氛围（排除机器人回复）
        if len(context_info["normal_messages"]) >= self.config.get('min_normal_messages_for_context', 3):
            context_info["atmosphere_summary"] = await self._analyze_atmosphere(context_info["normal_messages"])

    async def _analyze_atmosphere(self, normal_messages: list) -> str:
        """分析群聊氛围"""
        if not normal_messages:
            return ""

        # 简单的氛围分析
        recent_topics = []
        active_users = set()
        
        for msg in normal_messages[-10:]:  # 最近10条消息
            active_users.add(msg.sender_name)
            if len(msg.text_content) > 5:  # 过滤太短的消息
                recent_topics.append(f"{msg.sender_name}: {msg.text_content}")

        atmosphere = f"最近活跃用户: {', '.join(list(active_users)[:5])}"
        if recent_topics:
            atmosphere += f"\n最近话题: {'; '.join(recent_topics[-3:])}"

        return atmosphere

    async def _build_enhanced_prompt(self, context_info: dict, original_prompt: str) -> str:
        """构建增强的prompt - 按照清晰的信息层次结构"""
        sections = []
        bot_reference = self.config.get('bot_self_reference', '你')

        # 第一层：当前群聊状态
        if context_info.get("atmosphere_summary"):
            sections.append("=== 当前群聊状态 ===")
            sections.append(context_info["atmosphere_summary"])
            sections.append("")

        # 第二层：最近群聊内容（普通背景消息）
        if context_info.get("normal_messages"):
            sections.append("=== 最近群聊内容 ===")
            for msg in context_info["normal_messages"][-10:]:  # 增加普通消息数量
                sections.append(msg.format_for_display())
            sections.append("")

        # 第三层：最近和你相关的对话（触发了LLM回复的对话内容）
        sections.append(f"=== 最近和{bot_reference}相关的对话 ===")
        sections.append("# 以下是触发了AI回复的重要对话（@提及、唤醒词、主动回复等）")
        
        # 组织一问一答的形式
        if context_info.get("triggered_messages") or context_info.get("bot_replies"):
            # 合并触发消息和机器人回复，按时间排序
            all_interactions = []
            
            if context_info.get("triggered_messages"):
                for msg in context_info["triggered_messages"]:
                    all_interactions.append(("triggered", msg))
            
            if context_info.get("bot_replies"):
                for msg in context_info["bot_replies"]:
                    all_interactions.append(("bot_reply", msg))
            
            # 按时间戳排序
            all_interactions.sort(key=lambda x: x[1].timestamp if hasattr(x[1], 'timestamp') else 0)
            
            # 显示最近的互动
            for interaction_type, msg in all_interactions[-10:]:
                if interaction_type == "triggered":
                    sections.append(f"👤 {msg.format_for_display()}")
                elif interaction_type == "bot_reply":
                    sections.append(f"🤖 {msg.format_for_display()}")
            
            # 如果有对话历史数据库记录，也添加进来
            if context_info.get("conversation_history"):
                sections.append("# 从对话历史记录补充：")
                for record in context_info["conversation_history"][-8:]:
                    role = record.get("role", "unknown")
                    content = record.get("content", "")
                    timestamp = record.get("timestamp", "")
                    if role == "user":
                        sections.append(f"👤 [{timestamp}] 用户: {content}")
                    elif role == "assistant":
                        sections.append(f"🤖 [{timestamp}] {bot_reference}: {content}")
        
        if not any(context_info.get(key) for key in ["triggered_messages", "bot_replies", "conversation_history"]):
            sections.append("（暂无相关对话记录）")
        sections.append("")

        # 第四层：最近图片信息
        if context_info.get("image_messages"):
            sections.append("=== 最近图片 ===")
            for msg in context_info["image_messages"][-5:]:
                sections.append(f"📷 {msg.format_for_display()}")
            sections.append("")

        # 第五层：当前需要回复的请求（最详细）
        sections.append(f"=== 当前需要{bot_reference}回复的请求 ===")
        sections.append(f"📅 详细时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 从原始请求中提取更多信息（如果有的话）
        sections.append(f"💬 请求内容: {original_prompt}")
        
        # 检查是否有特殊触发标记
        if "@" in original_prompt:
            sections.append(f"🎯 触发方式: @提及")
        
        sections.append("")

        # 构建最终prompt
        if not sections:
            return original_prompt

        enhanced_context = "\n".join(sections)
        
        final_prompt = f"""{enhanced_context}请基于以上完整的群聊上下文信息，自然、智能地回复当前请求。注意理解群聊氛围和对话语境，保持对话的连续性和相关性。

当前用户请求: {original_prompt}"""

        return final_prompt

