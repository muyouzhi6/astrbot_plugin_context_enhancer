from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Plain, At, Image
from astrbot.api.platform import MessageType
import traceback
import json
import datetime
from collections import deque
import os
from typing import Dict, Any

# 导入工具模块
try:
    from .utils.image_caption import ImageCaptionUtils
    from .utils.message_utils import MessageUtils
except ImportError:
    try:
        # 备用导入方式
        from utils.image_caption import ImageCaptionUtils
        from utils.message_utils import MessageUtils
    except ImportError:
        # 如果导入失败，设置为 None，程序仍能正常运行
        ImageCaptionUtils = None
        MessageUtils = None
        logger.warning("utils 模块导入失败，将使用基础功能")


# 消息类型枚举 - 重命名以避免冲突
class ContextMessageType:
    LLM_TRIGGERED = "llm_triggered"  # 触发了LLM的消息（@机器人、命令等）
    NORMAL_CHAT = "normal_chat"  # 普通群聊消息
    IMAGE_MESSAGE = "image_message"  # 包含图片的消息
    BOT_REPLY = "bot_reply"  # 🤖 机器人自己的回复（补充数据库记录不足）


# 常量定义 - 避免硬编码
class ContextConstants:
    # 时间相关常量
    MESSAGE_MATCH_TIME_WINDOW = 3  # 消息匹配时间窗口（秒）
    INACTIVE_GROUP_CLEANUP_DAYS = 7  # 不活跃群组清理天数

    # 消息长度判断
    MIN_MESSAGE_LENGTH_FOR_TRIGGER = 10  # 触发词检测的最小消息长度

    # 命令前缀
    COMMAND_PREFIXES = ["/", "!", "！", "#", ".", "。"]

    # 触发关键词
    TRIGGER_KEYWORDS = [
        "bot",
        "机器人",
        "ai",
        "助手",
        "help",
        "帮助",
        "查询",
        "搜索",
        "翻译",
        "计算",
        "问答",
    ]

    # 机器人识别关键词
    BOT_KEYWORDS = ["bot", "机器人", "助手", "ai"]


class GroupMessage:
    """群聊消息包装类"""

    def __init__(self, event: AstrMessageEvent, message_type: str):
        self.event = event
        self.message_type = message_type
        self.timestamp = datetime.datetime.now()
        self.sender_name = (
            event.message_obj.sender.nickname if event.message_obj.sender else "用户"
        )
        self.sender_id = (
            event.message_obj.sender.user_id if event.message_obj.sender else "unknown"
        )
        self.group_id = (
            event.get_group_id()
            if hasattr(event, "get_group_id")
            else event.unified_msg_origin
        )
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

    async def format_for_display_async(
        self, include_images=True, message_utils=None
    ) -> str:
        """异步格式化消息用于显示，支持高级消息处理"""
        time_str = self.timestamp.strftime("%H:%M")

        # 如果提供了 MessageUtils，尝试使用高级格式化
        if message_utils and self.event.message_obj and self.event.message_obj.message:
            try:
                # 使用 MessageUtils 的高级格式化功能
                formatted_text = await message_utils.outline_message_list(
                    self.event.message_obj.message
                )
                if formatted_text:
                    result = f"[{time_str}] {self.sender_name}: {formatted_text}"
                else:
                    # 降级到简单格式化
                    result = f"[{time_str}] {self.sender_name}: {self.text_content}"
            except Exception:
                # 降级到简单格式化
                result = f"[{time_str}] {self.sender_name}: {self.text_content}"
        else:
            # 简单格式化
            result = f"[{time_str}] {self.sender_name}: {self.text_content}"

        if include_images and self.has_image:
            result += f" [包含{len(self.images)}张图片"
            if self.image_captions:
                result += f" - {'; '.join(self.image_captions)}"
            result += "]"

        return result

    def format_for_display(self, include_images=True, message_utils=None) -> str:
        """同步格式化消息用于显示（保持兼容性）"""
        time_str = self.timestamp.strftime("%H:%M")
        result = f"[{time_str}] {self.sender_name}: {self.text_content}"

        if include_images and self.has_image:
            result += f" [包含{len(self.images)}张图片"
            if self.image_captions:
                result += f" - {'; '.join(self.image_captions)}"
            result += "]"

        return result


@register(
    "astrbot_plugin_context_enhancer",
    "木有知",
    "智能群聊上下文增强插件v2.0，提供强大的'读空气'功能。通过多维度信息收集和分层架构，为 LLM 提供丰富的群聊语境，支持角色扮演，完全兼容人设系统。",
    "2.0.0",
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

        # 初始化工具类
        try:
            if ImageCaptionUtils is not None:
                self.image_caption_utils = ImageCaptionUtils(
                    context, context.get_config()
                )
                logger.debug("ImageCaptionUtils 初始化成功")
            else:
                self.image_caption_utils = None
                logger.warning("ImageCaptionUtils 不可用，将使用基础图片处理")

            if MessageUtils is not None:
                self.message_utils = MessageUtils(context.get_config(), context)
                logger.debug("MessageUtils 初始化成功")
            else:
                self.message_utils = None
                logger.warning("MessageUtils 不可用，将使用基础消息格式化")
        except Exception as e:
            logger.error(f"工具类初始化失败: {e}")
            self.image_caption_utils = None
            self.message_utils = None

        # 群聊消息缓存 - 每个群独立存储
        self.group_messages = {}  # group_id -> deque of GroupMessage
        self.group_last_activity = {}  # group_id -> last_activity_time (用于清理不活跃群组)

        # 显示当前配置
        logger.info(
            f"上下文增强器配置 - 触发消息: {self.config.get('触发消息数量', 8)}, "
            f"普通消息: {self.config.get('普通消息数量', 12)}, "
            f"图片消息: {self.config.get('图片消息数量', 4)}"
        )

    def load_config(self) -> Dict[str, Any]:
        """加载配置文件，使用动态路径解析"""
        try:
            # 获取插件目录的配置文件路径
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(plugin_dir, "config.json")

            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    logger.debug(f"配置文件加载成功: {config_path}")
                    return config
            else:
                logger.info("配置文件不存在，使用默认配置")
                return self.get_default_config()
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"配置文件加载失败，使用默认配置: {e}")
            return self.get_default_config()

    def get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "启用群组": [],  # 空列表表示对所有群生效
            "普通消息数量": 12,  # 最近普通聊天消息数量
            "触发消息数量": 8,  # 最近触发LLM的消息数量
            "图片消息数量": 4,  # 最近图片消息数量
            "启用图片描述": True,  # 是否启用图片描述
            "处理@信息": True,  # 是否处理@信息
            "收集机器人回复": True,  # 是否收集机器人回复
            "机器人回复数量": 5,  # 机器人回复收集数量
        }

    def _get_group_buffer(self, group_id: str) -> deque:
        """获取群聊的消息缓冲区，并管理内存"""
        current_time = datetime.datetime.now()

        # 更新活动时间
        self.group_last_activity[group_id] = current_time

        # 定期清理不活跃的群组缓存（每100次调用检查一次）
        if len(self.group_messages) % 100 == 0:
            self._cleanup_inactive_groups(current_time)

        if group_id not in self.group_messages:
            max_total = (
                self.config.get("触发消息数量", 8)
                + self.config.get("普通消息数量", 12)
                + self.config.get("图片消息数量", 4)
            ) * 2  # 预留空间
            self.group_messages[group_id] = deque(maxlen=max_total)
        return self.group_messages[group_id]

    def _cleanup_inactive_groups(self, current_time: datetime.datetime):
        """清理超过配置天数未活跃的群组缓存"""
        inactive_threshold = datetime.timedelta(
            days=ContextConstants.INACTIVE_GROUP_CLEANUP_DAYS
        )
        inactive_groups = []

        for group_id, last_activity in self.group_last_activity.items():
            if current_time - last_activity > inactive_threshold:
                inactive_groups.append(group_id)

        for group_id in inactive_groups:
            if group_id in self.group_messages:
                del self.group_messages[group_id]
            del self.group_last_activity[group_id]

        if inactive_groups:
            logger.debug(f"清理了 {len(inactive_groups)} 个不活跃群组的缓存")

    def is_chat_enabled(self, event: AstrMessageEvent) -> bool:
        """检查当前聊天是否启用增强功能"""
        if event.get_message_type() == MessageType.FRIEND_MESSAGE:
            return True  # 简化版本默认启用私聊
        else:
            enabled_groups = self.config.get("启用群组", [])
            group_id = event.get_group_id()
            logger.debug(f"群聊启用检查: 群ID={group_id}, 启用列表={enabled_groups}")

            if not enabled_groups:  # 空列表表示对所有群生效
                logger.debug("空的启用列表，对所有群生效")
                return True

            result = group_id in enabled_groups
            logger.debug(f"群聊启用结果: {result}")
            return result

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
            # 🤖 机器人消息处理：简化版本默认收集所有消息
            if self._is_bot_message(event):
                logger.debug("收集机器人自己的消息（保持上下文完整性）")

            # 判断消息类型
            message_type = self._classify_message(event)

            # 创建消息对象
            group_msg = GroupMessage(event, message_type)

            # 生成图片描述
            if group_msg.has_image and self.config.get("启用图片描述", True):
                await self._generate_image_captions(group_msg)

            # 添加到缓冲区前进行去重检查
            buffer = self._get_group_buffer(group_msg.group_id)

            # 🚨 防重复机制：检查是否已存在相同消息
            if not self._is_duplicate_message(buffer, group_msg):
                buffer.append(group_msg)
                logger.debug(
                    f"收集群聊消息 [{message_type}]: {group_msg.sender_name} - {group_msg.text_content[:50]}..."
                )
            else:
                logger.debug(
                    f"跳过重复消息: {group_msg.sender_name} - {group_msg.text_content[:30]}..."
                )

        except Exception as e:
            logger.error(f"处理群聊消息时发生错误: {e}")

    def _is_duplicate_message(self, buffer: deque, new_msg) -> bool:
        """检查消息是否已存在于缓冲区（防重复）"""
        # 检查最近5条消息即可，避免性能问题
        recent_messages = list(buffer)[-5:] if buffer else []

        for existing_msg in recent_messages:
            # 重复判断条件：
            # 1. 相同发送者
            # 2. 相同内容（或内容高度相似）
            # 3. 时间差在30秒内
            if (
                existing_msg.sender_id == new_msg.sender_id
                and existing_msg.text_content == new_msg.text_content
                and abs((new_msg.timestamp - existing_msg.timestamp).total_seconds())
                < 30
            ):
                return True

        return False

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
            sender_name = (
                event.get_sender_name().lower() if event.get_sender_name() else ""
            )
            if any(keyword in sender_name for keyword in ContextConstants.BOT_KEYWORDS):
                # 这个额外检查只是模糊匹配，不能确定是否是当前机器人
                logger.debug(f"检测到疑似机器人消息，发送者名称: {sender_name}")

            return False
        except Exception as e:
            logger.debug(f"检查机器人消息时出错: {e}")
            return False

    def _classify_message(self, event: AstrMessageEvent) -> str:
        """分类消息类型"""

        # 🤖 首先检查是否是机器人消息
        if self._is_bot_message(event) and self.config.get("收集机器人回复", True):
            return ContextMessageType.BOT_REPLY

        # 检查是否包含图片
        if self._contains_image(event):
            return ContextMessageType.IMAGE_MESSAGE

        # 检查是否触发LLM
        if self._is_llm_triggered(event):
            return ContextMessageType.LLM_TRIGGERED

        # 默认为普通聊天消息
        return ContextMessageType.NORMAL_CHAT

    def _contains_image(self, event: AstrMessageEvent) -> bool:
        """检查消息是否包含图片"""
        if not (event.message_obj and event.message_obj.message):
            return False

        for comp in event.message_obj.message:
            if isinstance(comp, Image):
                return True
        return False

    def _is_llm_triggered(self, event: AstrMessageEvent) -> bool:
        """判断消息是否会触发LLM回复"""
        return (
            self._contains_at_bot(event)
            or self._is_command_message(event)
            or self._is_wake_message(event)
            or self._contains_trigger_words(event)
        )

    def _contains_at_bot(self, event: AstrMessageEvent) -> bool:
        """检查消息是否@了机器人"""
        if not (event.message_obj and event.message_obj.message):
            return False

        bot_id = event.get_self_id()
        for comp in event.message_obj.message:
            if isinstance(comp, At):
                if str(comp.qq) == str(bot_id) or comp.qq == "all":
                    return True
        return False

    def _is_command_message(self, event: AstrMessageEvent) -> bool:
        """检查消息是否是命令"""
        if not event.message_str:
            return False

        message_text = event.message_str.lower().strip()

        return any(
            message_text.startswith(prefix)
            for prefix in ContextConstants.COMMAND_PREFIXES
        )

    def _is_wake_message(self, event: AstrMessageEvent) -> bool:
        """检查是否是唤醒状态的消息"""
        return getattr(event, "is_wake", False) or getattr(
            event, "is_at_or_wake_command", False
        )

    def _contains_trigger_words(self, event: AstrMessageEvent) -> bool:
        """检查消息是否包含触发词"""
        if not event.message_str:
            return False

        message_text = event.message_str.lower()

        # 避免误判短消息
        if len(message_text) <= ContextConstants.MIN_MESSAGE_LENGTH_FOR_TRIGGER:
            return False

        return any(
            keyword in message_text for keyword in ContextConstants.TRIGGER_KEYWORDS
        )

    async def _generate_image_captions(self, group_msg: GroupMessage):
        """为图片生成智能描述，使用高级图片分析功能，支持独立的图片描述提供商"""
        try:
            if not group_msg.images:
                return

            # 检查是否启用图片描述
            if not self.config.get("启用图片描述", True):
                # 如果禁用，使用简单占位符
                for i, img in enumerate(group_msg.images):
                    group_msg.image_captions.append(f"图片{i + 1}")
                return

            # 使用高级图片描述功能
            captions = []
            # 获取图片描述的特定配置
            image_caption_provider_id = self.config.get("图片描述提供商ID", "")
            image_caption_prompt = self.config.get(
                "图片描述提示词",
                "请简洁地描述这张图片的主要内容，重点关注与聊天相关的信息",
            )

            for i, img in enumerate(group_msg.images):
                try:
                    # 获取图片的URL或路径
                    image_data = getattr(img, "url", None) or getattr(img, "file", None)
                    if image_data and self.image_caption_utils is not None:
                        # 调用图片描述工具，传入特定的提供商ID和提示词
                        caption = await self.image_caption_utils.generate_image_caption(
                            image_data,
                            timeout=10,
                            provider_id=image_caption_provider_id
                            if image_caption_provider_id
                            else None,
                            custom_prompt=image_caption_prompt,
                        )
                        if caption:
                            captions.append(f"图片{i + 1}: {caption}")
                        else:
                            captions.append(f"图片{i + 1}")
                    else:
                        captions.append(f"图片{i + 1}")
                except Exception as e:
                    logger.debug(f"生成图片{i + 1}描述失败: {e}")
                    captions.append(f"图片{i + 1}")

            group_msg.image_captions = captions

        except Exception as e:
            logger.warning(f"生成图片描述时发生错误: {e}")
            # 降级到简单占位符
            for i, img in enumerate(group_msg.images):
                group_msg.image_captions.append(f"图片{i + 1}")

    @filter.on_llm_request(priority=100)  # 🔧 使用较低优先级，避免干扰其他插件
    async def on_llm_request(self, event: AstrMessageEvent, request: ProviderRequest):
        """LLM请求时提供简单直接的上下文增强"""
        try:
            # 简单检测：避免重复增强
            if request.prompt and "你正在浏览聊天软件" in request.prompt:
                logger.debug("检测到已增强的内容，跳过重复处理")
                return

            if not self.is_chat_enabled(event):
                logger.debug("上下文增强器：当前聊天未启用，跳过增强")
                return

            # 只处理群聊消息
            if event.get_message_type() != MessageType.GROUP_MESSAGE:
                return

            logger.debug("开始构建简单上下文...")

            # 标记当前消息为LLM触发类型
            await self._mark_current_as_llm_triggered(event)

            # 获取群聊历史
            group_id = (
                event.get_group_id()
                if hasattr(event, "get_group_id")
                else event.unified_msg_origin
            )
            buffer = self._get_group_buffer(group_id)

            if not buffer:
                logger.debug("没有群聊历史，跳过增强")
                return

            # 构建简单上下文
            enhanced_prompt = await self._build_simple_context(
                buffer, request.prompt, event
            )

            if enhanced_prompt and enhanced_prompt != request.prompt:
                request.prompt = enhanced_prompt
                logger.debug(
                    f"简单上下文增强完成，新prompt长度: {len(enhanced_prompt)}"
                )

        except Exception as e:
            logger.error(f"上下文增强时发生错误: {e}")
            # 出错时不影响正常流程

    async def _build_simple_context(
        self, buffer: deque, original_prompt: str, event: AstrMessageEvent
    ) -> str:
        """构建简单直接的上下文 - 类似SpectreCore的方式"""
        # 获取用户信息
        sender_name = (
            event.message_obj.sender.nickname if event.message_obj.sender else "用户"
        )
        sender_id = (
            event.message_obj.sender.user_id if event.message_obj.sender else "unknown"
        )

        # 收集最近的聊天记录（普通消息）
        recent_chats = []
        bot_replies = []

        messages = list(buffer)[-20:]  # 最近20条消息

        for msg in messages:
            if msg.message_type == ContextMessageType.NORMAL_CHAT:
                recent_chats.append(f"{msg.sender_name}: {msg.text_content}")
            elif msg.message_type == ContextMessageType.BOT_REPLY:
                bot_replies.append(f"你回复了: {msg.text_content}")

        # 构建简单的上下文
        context_parts = ["你正在浏览聊天软件，查看群聊消息。"]

        # 添加最近聊天记录
        if recent_chats:
            context_parts.append("\n最近的聊天记录:")
            context_parts.extend(recent_chats[-8:])  # 最近8条

        # 添加你最近的回复
        if bot_replies:
            context_parts.append("\n你最近的回复:")
            context_parts.extend(bot_replies[-3:])  # 最近3条回复

        # 当前情况
        context_parts.append(
            f"\n现在 {sender_name}（ID: {sender_id}）发了一个消息: {original_prompt}"
        )
        context_parts.append("需要你在心里理清当前到底讨论的什么，搞清楚形势，谁在跟谁说话，你是在插话还是回复，然后根据你的设定和当前形势做出最自然的回复。")

        return "\n".join(context_parts)

    # 添加记录机器人回复的功能
    @filter.on_llm_response(priority=100)
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """记录机器人的回复内容"""
        try:
            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                group_id = (
                    event.get_group_id()
                    if hasattr(event, "get_group_id")
                    else event.unified_msg_origin
                )

                # 获取回复文本
                response_text = ""
                if hasattr(resp, "completion_text"):
                    response_text = resp.completion_text
                elif hasattr(resp, "text"):
                    response_text = resp.text
                else:
                    response_text = str(resp)

                # 创建机器人回复记录
                bot_reply = GroupMessage(event, ContextMessageType.BOT_REPLY)
                bot_reply.text_content = response_text  # 记录原始回复文本
                bot_reply.sender_name = "助手"  # 机器人名称
                bot_reply.sender_id = "bot"

                buffer = self._get_group_buffer(group_id)
                buffer.append(bot_reply)

                logger.debug(f"记录机器人回复: {response_text[:50]}...")

        except Exception as e:
            logger.error(f"记录机器人回复时发生错误: {e}")

    async def _mark_current_as_llm_triggered(self, event: AstrMessageEvent):
        """将当前消息标记为LLM触发类型"""
        if event.get_message_type() == MessageType.GROUP_MESSAGE:
            group_id = (
                event.get_group_id()
                if hasattr(event, "get_group_id")
                else event.unified_msg_origin
            )
            buffer = self._get_group_buffer(group_id)

            # 使用更健壮的匹配逻辑：发送者ID + 时间窗口
            current_time = datetime.datetime.now()
            sender_id = (
                event.message_obj.sender.user_id if event.message_obj.sender else None
            )

            # 查找最近指定时间窗口内的匹配消息
            for msg in reversed(buffer):
                time_diff = (current_time - msg.timestamp).total_seconds()
                if (
                    time_diff <= ContextConstants.MESSAGE_MATCH_TIME_WINDOW
                    and msg.sender_id == sender_id
                    and msg.message_type
                    != ContextMessageType.LLM_TRIGGERED  # 避免重复标记
                ):
                    msg.message_type = ContextMessageType.LLM_TRIGGERED
                    logger.debug(f"标记消息为LLM触发: {msg.text_content[:50]}...")
                    break

    async def _build_structured_context(
        self, event: AstrMessageEvent, request: ProviderRequest
    ) -> dict:
        """构建结构化的上下文信息"""
        context_info = {
            "triggered_messages": [],
            "normal_messages": [],
            "image_messages": [],
            "bot_replies": [],  # 🤖 机器人回复消息
            "atmosphere_summary": "",
        }

        # 🎯 参考SpectreCore方式：完全不使用request.conversation.history
        # 避免套娃问题，只使用我们自己控制的群聊消息缓存

        # 获取群聊消息缓存
        if event.get_message_type() == MessageType.GROUP_MESSAGE:
            group_id = (
                event.get_group_id()
                if hasattr(event, "get_group_id")
                else event.unified_msg_origin
            )
            buffer = self._get_group_buffer(group_id)
            logger.debug(f"群聊消息缓存大小: {len(buffer)}")

            await self._collect_recent_messages(buffer, context_info)

            logger.debug(
                f"收集到的消息数量: 普通={len(context_info['normal_messages'])}, 触发={len(context_info['triggered_messages'])}, 图片={len(context_info['image_messages'])}, 机器人回复={len(context_info['bot_replies'])}"
            )

        return context_info

    async def _collect_recent_messages(self, buffer: deque, context_info: dict):
        """从缓冲区收集最近的各类消息"""
        max_triggered = self.config.get("触发消息数量", 8)
        max_normal = self.config.get("普通消息数量", 12)
        max_image = self.config.get("图片消息数量", 4)
        max_bot_replies = self.config.get("机器人回复数量", 5)  # 🤖 机器人回复数量

        triggered_count = 0
        normal_count = 0
        image_count = 0
        bot_reply_count = 0

        # 从最新的消息开始收集
        for msg in reversed(buffer):
            if (
                msg.message_type == ContextMessageType.LLM_TRIGGERED
                and triggered_count < max_triggered
            ):
                context_info["triggered_messages"].insert(0, msg)
                triggered_count += 1
            elif (
                msg.message_type == ContextMessageType.NORMAL_CHAT
                and normal_count < max_normal
            ):
                context_info["normal_messages"].insert(0, msg)
                normal_count += 1
            elif (
                msg.message_type == ContextMessageType.IMAGE_MESSAGE
                and image_count < max_image
            ):
                context_info["image_messages"].insert(0, msg)
                image_count += 1
            elif (
                msg.message_type == ContextMessageType.BOT_REPLY
                and bot_reply_count < max_bot_replies
            ):  # 🤖
                context_info["bot_replies"].insert(0, msg)
                bot_reply_count += 1

        # 分析群聊氛围（排除机器人回复）
        if len(context_info["normal_messages"]) >= self.config.get(
            "min_normal_messages_for_context", 3
        ):
            context_info["atmosphere_summary"] = self._analyze_atmosphere(
                context_info["normal_messages"]
            )

    def _analyze_atmosphere(self, normal_messages: list) -> str:
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

