"""
智能群聊上下文增强插件
通过多维度信息收集和分层架构，为 LLM 提供丰富的群聊语境，支持角色扮演，完全兼容人设系统。
"""
import traceback
import json
import re
import datetime
from collections import deque
import os
from typing import Dict, Optional
import time
import uuid
from dataclasses import dataclass
import asyncio
import aiofiles
import aiofiles.os as aio_os
from aiofiles.os import remove as aio_remove, rename as aio_rename

from astrbot.api.event import filter as event_filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Plain, At, Image
from astrbot.api.platform import MessageType

# 导入工具模块
try:
    from .utils.image_caption import ImageCaptionUtils
except ImportError:
    ImageCaptionUtils = None
    # _initialize_utils 方法中会记录详细日志


# 消息类型枚举 - 重命名以避免冲突
class ContextMessageType:
    """消息类型枚举"""
    LLM_TRIGGERED = "llm_triggered"
    NORMAL_CHAT = "normal_chat"
    IMAGE_MESSAGE = "image_message"
    BOT_REPLY = "bot_reply"


# 常量定义 - 避免硬编码
class ContextConstants:
    """插件中使用的常量"""
    MESSAGE_MATCH_TIME_WINDOW = 3
    PROMPT_HEADER = "你正在浏览聊天软件，查看群聊消息。"
    RECENT_CHATS_HEADER = "\n最近的聊天记录:"
    BOT_REPLIES_HEADER = "\n你最近的回复:"
    PROMPT_FOOTER = "请基于以上信息，并严格按照你的角色设定，做出自然且符合当前对话氛围的回复。"


@dataclass
class PluginConfig:
    """统一管理插件配置项"""
    enabled_groups: list
    recent_chats_count: int
    bot_replies_count: int
    max_images_in_context: int
    enable_image_caption: bool
    image_caption_provider_id: str
    image_caption_prompt: str
    cleanup_interval_seconds: int
    inactive_cleanup_days: int
    command_prefixes: list
    duplicate_check_window_messages: int
    duplicate_check_time_seconds: int
    passive_reply_instruction: str  # 被动回复指令
    active_speech_instruction: str  # 主动发言指令


class GroupMessage:
    """群聊消息的独立数据类，与框架解耦"""
    def __init__(self,
                 message_type: str,
                 sender_id: str,
                 sender_name: str,
                 group_id: str,
                 text_content: str = "",
                 images: Optional[list] = None,
                 message_id: Optional[str] = None,
                 nonce: Optional[str] = None):
        self.id = message_id
        self.nonce = nonce
        self.message_type = message_type
        self.timestamp = datetime.datetime.now()
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.group_id = group_id
        self.text_content = text_content
        self.images = images or []
        self.has_image = len(self.images) > 0
        self.image_captions: list[str] = []

    def to_dict(self) -> dict:
        """将消息对象转换为可序列化为 JSON 的字典"""
        return {
            "id": self.id,
            "nonce": self.nonce,
            "message_type": self.message_type,
            "timestamp": self.timestamp.isoformat(),
            "sender_name": self.sender_name,
            "sender_id": self.sender_id,
            "group_id": self.group_id,
            "text_content": self.text_content,
            "has_image": self.has_image,
            "image_captions": self.image_captions,
            "image_urls": [getattr(img, "url", None) or getattr(img, "file", None) for img in self.images]
        }

    @classmethod
    def from_dict(cls, data: dict):
        """从字典创建 GroupMessage 对象"""
        instance = cls(
            message_type=data["message_type"],
            sender_id=data.get("sender_id", "unknown"),
            sender_name=data.get("sender_name", "用户"),
            group_id=data.get("group_id", ""),
            text_content=data.get("text_content", ""),
            images=[Image.fromURL(url=url) for url in data.get("image_urls", []) if url],
            message_id=data.get("id"),
            nonce=data.get("nonce")
        )
        instance.timestamp = datetime.datetime.fromisoformat(data["timestamp"])
        instance.image_captions = data.get("image_captions", [])
        return instance




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
    - 🎯 简单直接的上下文增强，参考SpectreCore的简洁方式
    - 📝 自动收集群聊历史和机器人回复记录
    - 🖼️ 支持图片描述和高级消息格式化（可选）
    - 🛡️ 安全兼容，不覆盖system_prompt，不干扰其他插件

    技术保证:
    - 不影响 system_prompt，完全兼容人设系统
    - 使用合理优先级，不干扰其他插件
    - 异步处理，不阻塞主流程
    - 完善的错误处理和功能降级
    """
    # 缓冲区大小乘数，用于为 deque 提供额外空间，避免在消息快速增长时频繁丢弃旧消息
    CACHE_LOAD_BUFFER_MULTIPLIER = 2

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.raw_config = config
        self.config = self._load_plugin_config()
        self._global_lock = asyncio.Lock()
        logger.info("上下文增强器v2.0已初始化")

        # 初始化工具类
        self._initialize_utils()

        # 群聊消息缓存 - 每个群独立存储
        self.group_messages: Dict[str, deque[GroupMessage]] = {}
        self.group_last_activity: Dict[str, datetime.datetime] = {}
        self.last_cleanup_time = time.time()

        # 异步加载持久化的上下文
        self.data_dir = os.path.join(
            StarTools.get_data_dir(), "astrbot_plugin_context_enhancer"
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.cache_path = os.path.join(self.data_dir, "context_cache.json")
        
        # 显示当前配置
        logger.info(f"上下文增强器配置加载完成: {self.config}")

    async def _async_init(self):
        """异步初始化部分，例如加载缓存"""
        await self._load_cache_from_file()

    async def terminate(self):
        """插件终止时，异步持久化上下文并关闭会话"""
        # 异步持久化上下文
        temp_path = self.cache_path + ".tmp"
        try:
            serializable_messages = {}
            for group_id, messages in self.group_messages.items():
                serializable_messages[group_id] = [msg.to_dict() for msg in messages]

            # 1. 写入临时文件
            async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(serializable_messages, ensure_ascii=False, indent=4))

            # 2. 原子性重命名
            await aio_rename(temp_path, self.cache_path)
            logger.info(f"上下文缓存已成功原子化保存到 {self.cache_path}")

        except Exception as e:
            logger.error(f"异步保存上下文缓存失败: {e}")
        finally:
            # 3. 确保清理临时文件
            if await aio_os.path.exists(temp_path):
                try:
                    await aio_remove(temp_path)
                except Exception as e:
                    logger.error(f"清理临时缓存文件 {temp_path} 失败: {e}")

        # 关闭 aiohttp session
        if self.image_caption_utils and hasattr(self.image_caption_utils, 'close'):
            await self.image_caption_utils.close()
            logger.info("ImageCaptionUtils 的 aiohttp session 已关闭。")

    def _load_plugin_config(self) -> PluginConfig:
        """从原始配置加载并填充插件配置类"""
        return PluginConfig(
            enabled_groups=self.raw_config.get("启用群组", []),
            recent_chats_count=self.raw_config.get("最近聊天记录数量", 15),
            bot_replies_count=self.raw_config.get("机器人回复数量", 5),
            max_images_in_context=self.raw_config.get("上下文图片最大数量", 4),
            enable_image_caption=self.raw_config.get("启用图片描述", True),
            image_caption_provider_id=self.raw_config.get("图片描述提供商ID", ""),
            image_caption_prompt=self.raw_config.get(
                "图片描述提示词", "请简洁地描述这张图片的主要内容，重点关注与聊天相关的信息"
            ),
            cleanup_interval_seconds=self.raw_config.get("cleanup_interval_seconds", 600),
            inactive_cleanup_days=self.raw_config.get("inactive_cleanup_days", 7),
            command_prefixes=self.raw_config.get("command_prefixes", ["/", "!", "！", "#", ".", "。"]),
            duplicate_check_window_messages=self.raw_config.get("duplicate_check_window_messages", 5),
            duplicate_check_time_seconds=self.raw_config.get("duplicate_check_time_seconds", 30),
            passive_reply_instruction=self.raw_config.get("passive_reply_instruction", '现在，群成员 {sender_name} (ID: {sender_id}) 正在对你说话，或者提到了你，TA说："{original_prompt}"\n你需要根据以上聊天记录和你的角色设定，直接回复该用户。'),
            active_speech_instruction=self.raw_config.get("active_speech_instruction", '以上是最近的聊天记录。现在，你决定主动参与讨论，并想就以下内容发表你的看法："{original_prompt}"\n你需要根据以上聊天记录和你的角色设定，自然地切入对话。'),
        )

    def _initialize_utils(self):
        """初始化工具模块"""
        try:
            if ImageCaptionUtils is not None:
                self.image_caption_utils = ImageCaptionUtils(
                    self.context, self.raw_config
                )
                logger.debug("ImageCaptionUtils 初始化成功")
            else:
                self.image_caption_utils = None
                logger.warning("ImageCaptionUtils 不可用，将使用基础图片处理")
        except Exception as e:
            logger.error(f"工具类初始化失败: {e}")
            self.image_caption_utils = None

    async def _load_cache_from_file(self):
        """从文件异步加载缓存"""
        if not await aio_os.path.exists(self.cache_path):
            return
        try:
            async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                content = await f.read()
                if content: # 确保文件内容不为空
                    data = json.loads(content)
                    self.group_messages = self._load_group_messages_from_dict(data)
                    logger.info(f"成功从 {self.cache_path} 异步加载上下文缓存。")
                else:
                    logger.info(f"缓存文件 {self.cache_path} 为空，跳过加载。")
        except Exception as e:
            logger.error(f"异步加载上下文缓存失败: {e}")

    def _load_group_messages_from_dict(
        self, data: Dict[str, list]
    ) -> Dict[str, deque]:
        """从字典加载群组消息"""
        group_messages = {}

        # 计算 maxlen
        base_max_len = self.config.recent_chats_count + self.config.bot_replies_count
        max_len = base_max_len * self.CACHE_LOAD_BUFFER_MULTIPLIER

        for group_id, msg_list in data.items():
            # 为每个群组创建一个有最大长度限制的 deque
            message_deque = deque(maxlen=max_len)
            for msg_data in msg_list:
                try:
                    # 从字典重建 GroupMessage 对象
                    message_deque.append(GroupMessage.from_dict(msg_data))
                except Exception as e:
                    logger.warning(f"从字典转换消息失败 (群 {group_id}): {e}")
            group_messages[group_id] = message_deque
        return group_messages

    async def _get_group_buffer(self, group_id: str) -> deque:
        """获取群聊的消息缓冲区，并管理内存"""
        current_dt = datetime.datetime.now()

        # 更新活动时间
        self.group_last_activity[group_id] = current_dt

        # 基于时间的缓存清理
        now = time.time()
        if now - self.last_cleanup_time > self.config.cleanup_interval_seconds:
            self._cleanup_inactive_groups(current_dt)
            self.last_cleanup_time = now

        if group_id not in self.group_messages:
            async with self._global_lock:
                # 双重检查，防止在等待锁期间其他协程已创建
                if group_id not in self.group_messages:
                    max_len = (self.config.recent_chats_count + self.config.bot_replies_count) * self.CACHE_LOAD_BUFFER_MULTIPLIER
                    self.group_messages[group_id] = deque(maxlen=max_len)
        return self.group_messages[group_id]

    def _cleanup_inactive_groups(self, current_time: datetime.datetime):
        """清理超过配置天数未活跃的群组缓存"""
        inactive_threshold = datetime.timedelta(
            days=self.config.inactive_cleanup_days
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
            group_id = event.get_group_id()
            logger.debug(f"群聊启用检查: 群ID={group_id}, 启用列表={self.config.enabled_groups}")

            if not self.config.enabled_groups:  # 空列表表示对所有群生效
                logger.debug("空的启用列表，对所有群生效")
                return True

            result = group_id in self.config.enabled_groups
            logger.debug(f"群聊启用结果: {result}")
            return result

    @event_filter.platform_adapter_type(event_filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，进行分类和存储"""
        if event.get_message_type() == MessageType.GROUP_MESSAGE and not event.get_group_id():
            logger.warning("无法获取群组ID，已跳过上下文处理。")
            return
        try:
            if not self.is_chat_enabled(event):
                return

            # 检查是否是 reset 命令
            message_text = (event.message_str or "").strip()
            if message_text.lower() in ["reset", "new"]:
                await self.handle_clear_context_command(event)
                return

            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                await self._handle_group_message(event)

        except Exception as e:
            logger.error(f"处理消息时发生错误: {e}")
            logger.error(traceback.format_exc())

    def _create_group_message_from_event(self, event: AstrMessageEvent, message_type: str) -> GroupMessage:
        """从事件创建 GroupMessage 实例"""
        text_content_parts = []
        images = []
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, Plain):
                    text_content_parts.append(comp.text)
                elif isinstance(comp, At):
                    text_content_parts.append(f"@{comp.qq}")
                elif isinstance(comp, Image):
                    images.append(comp)

        # 1. 优先使用标准方法
        sender_name = event.get_sender_name()

        # 2. 如果标准方法失败，尝试从原始事件数据中获取 (兼容 aiocqhttp 等)
        raw_event = getattr(event, 'raw_event', None)
        if not sender_name and raw_event and isinstance(raw_event.get("sender"), dict):
            sender = raw_event.get("sender")
            # 优先使用群名片，其次是昵称
            sender_name = sender.get("card") or sender.get("nickname")

        # 3. 最后使用后备值 "用户"
        final_sender_name = sender_name or "用户"
        
        return GroupMessage(
            message_type=message_type,
            sender_id=event.get_sender_id() or "unknown",
            sender_name=final_sender_name,
            group_id=event.get_group_id(),
            text_content="".join(text_content_parts).strip(),
            images=images,
            # 尝试从不同事件结构中获取消息ID，兼容直接事件和包装后的事件对象
            message_id=getattr(event, 'id', None) or getattr(getattr(event, 'message_obj', None), 'id', None),
            nonce=getattr(event, '_context_enhancer_nonce', None)
        )

    async def _handle_group_message(self, event: AstrMessageEvent):
        """处理群聊消息"""
        group_msg = self._create_group_message_from_event(event, "")  # 临时创建以检查内容
        if not group_msg.text_content and not group_msg.images:
            logger.debug("消息为空（无文本无图片），跳过处理。")
            return

        try:
            if self._is_bot_message(event):
                logger.debug("收集到机器人自己的消息，用于保持上下文完整性。")

            message_type = self._classify_message(event)
            group_msg.message_type = message_type # 更新消息类型

            # 生成图片描述
            if group_msg.has_image and self.config.enable_image_caption:
                await self._generate_image_captions(group_msg)

            # 添加到缓冲区前进行去重检查
            buffer = await self._get_group_buffer(group_msg.group_id)

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

    def _is_duplicate_message(self, buffer: deque, new_msg: GroupMessage) -> bool:
        """检查消息是否已存在于缓冲区（防重复）"""
        # 如果新消息包含图片，则不视为重复，以确保图片总能被处理
        if new_msg.has_image:
            return False
            
        # 检查最近N条消息即可，避免性能问题
        recent_messages = list(buffer)[-self.config.duplicate_check_window_messages:] if buffer else []

        for existing_msg in recent_messages:
            # 重复判断条件：
            # 1. 相同发送者
            # 2. 相同文本内容
            # 3. 时间差在指定窗口内
            if (
                existing_msg.sender_id == new_msg.sender_id and
                existing_msg.text_content == new_msg.text_content and
                abs((new_msg.timestamp - existing_msg.timestamp).total_seconds()) < self.config.duplicate_check_time_seconds
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
            return bool(bot_id and sender_id and str(sender_id) == str(bot_id))
        except (AttributeError, KeyError) as e:
            logger.warning(f"检查机器人消息时出错（可能是不支持的事件类型或数据结构）: {e}")
            return False

    def _classify_message(self, event: AstrMessageEvent) -> str:
        """
        分类消息类型，区分直接触发和间接触发。
        新的逻辑流程:
        1. 直接触发 (用户@或指令) -> LLM_TRIGGERED (被动响应)
        2. 间接触发 (wakepro等) -> NORMAL_CHAT (主动发言)
        3. 其他按原逻辑处理
        """
        # 🤖 首先检查是否是机器人自己的消息
        if self._is_bot_message(event) and self.config.bot_replies_count > 0:
            return ContextMessageType.BOT_REPLY

        # 1. 检查是否为用户直接触发
        if self._is_directly_triggered(event):
            # 附加一个唯一标识符，用于后续精确匹配
            setattr(event, '_context_enhancer_nonce', uuid.uuid4().hex)
            return ContextMessageType.LLM_TRIGGERED

        # 2. 检查是否为间接触发（例如被 wakepro 唤醒）
        # 根据新逻辑，这种情况被视为普通聊天，以体现“主动发言”的角色扮演
        if self._is_indirectly_triggered(event):
            return ContextMessageType.NORMAL_CHAT

        # 3. 如果不是间接触发，也不是机器人自己的消息，那它就是一次需要LLM响应的普通消息
        return ContextMessageType.NORMAL_CHAT

    def _contains_image(self, event: AstrMessageEvent) -> bool:
        """检查消息是否包含图片"""
        if not (event.message_obj and event.message_obj.message):
            return False

        for comp in event.message_obj.message:
            if isinstance(comp, Image):
                return True
        return False

    def _is_at_triggered(self, event: AstrMessageEvent) -> bool:
        """检查消息是否通过@机器人触发"""
        bot_id = event.get_self_id()
        if not bot_id:
            return False

        # 检查消息组件
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, At) and (
                    str(comp.qq) == str(bot_id) or comp.qq == "all"
                ):
                    return True
        
        # 检查纯文本
        message_text = event.message_str or ""
        # 使用正则表达式确保 @<bot_id> 是一个独立的词
        pattern = rf'(^|\s)@{re.escape(str(bot_id))}($|\s)'
        if re.search(pattern, message_text):
            return True

        return False

    def _is_keyword_triggered(self, event: AstrMessageEvent) -> bool:
        """检查消息是否通过命令前缀触发"""
        message_text = (event.message_str or "").lower().strip()
        if not message_text:
            return False

        return any(
            message_text.startswith(prefix)
            for prefix in self.config.command_prefixes
        )

    def _is_directly_triggered(self, event: AstrMessageEvent) -> bool:
        """
        检查消息是否由用户直接触发（@机器人或使用命令词）。
        这代表了最明确的用户交互意图。
        """
        return self._is_at_triggered(event) or self._is_keyword_triggered(event)

    def _is_indirectly_triggered(self, event: AstrMessageEvent) -> bool:
        """
        检查消息是否由间接方式触发（如 wakepro 插件的智能唤醒）。
        这通常不被视为用户直接的对话意图。
        """
        return getattr(event, "is_wake", False) or getattr(
            event, "is_at_or_wake_command", False
        )

    async def _generate_image_captions(self, group_msg: GroupMessage):
        """为图片生成智能描述，使用高级图片分析功能，支持独立的图片描述提供商"""
        try:
            if not group_msg.images:
                return

            # 检查是否启用图片描述
            if not self.config.enable_image_caption:
                # 如果禁用，使用简单占位符
                for i, img in enumerate(group_msg.images):
                    group_msg.image_captions.append(f"图片{i + 1}")
                return

            # 使用高级图片描述功能
            captions = []
            # 从统一配置中获取
            image_caption_provider_id = self.config.image_caption_provider_id
            image_caption_prompt = self.config.image_caption_prompt

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
                            # 直接存储纯净的描述文本
                            captions.append(caption)
                        else:
                            # 如果没有生成描述，可以添加一个默认占位符或空字符串
                            captions.append("图片")
                    else:
                        captions.append("图片")
                except Exception as e:
                    logger.debug(f"生成图片{i + 1}描述失败: {e}")
                    captions.append("图片")

            group_msg.image_captions = captions

        except Exception as e:
            logger.warning(f"生成图片描述时发生错误: {e}")
            # 降级到简单占位符
            for i, img in enumerate(group_msg.images):
                group_msg.image_captions.append(f"图片{i + 1}")

    @event_filter.on_llm_request(priority=100)
    async def on_llm_request(self, event: AstrMessageEvent, request: ProviderRequest):
        """
        LLM请求时提供上下文增强。
        此方法作为总入口，协调上下文的构建和注入流程。
        """
        if event.get_message_type() == MessageType.GROUP_MESSAGE and not event.get_group_id():
            return
        try:
            # 1. 检查是否需要增强
            if not self._should_enhance_context(event, request):
                return

            # 2. 获取群聊历史记录
            group_id = event.get_group_id()
            buffer = await self._get_group_buffer(group_id)
            if not buffer:
                logger.debug("没有群聊历史，跳过增强")
                return

            # 3. 确定场景（被动回复 vs 主动发言）
            triggering_message, scene = self._find_triggering_message_from_event(buffer, event)

            # 4. 构建上下文增强内容
            context_enhancement, image_urls = self._build_context_enhancement(
                buffer, request.prompt, triggering_message, scene
            )

            # 5. 将上下文注入到请求中
            self._inject_context_into_request(request, context_enhancement, image_urls)

        except Exception as e:
            logger.error(f"上下文增强时发生错误: {e}")
            logger.error(traceback.format_exc())

    def _should_enhance_context(self, event: AstrMessageEvent, request: ProviderRequest) -> bool:
        """检查是否应执行上下文增强"""
        # 避免重复增强
        if hasattr(request, '_context_enhanced'):
            logger.debug("检测到已增强的请求，跳过重复处理")
            return False

        # 检查群聊是否启用
        if not self.is_chat_enabled(event):
            logger.debug("上下文增强器：当前聊天未启用，跳过增强")
            return False

        # 只处理群聊消息
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False

        return True

    def _extract_messages_for_context(self, buffer: deque) -> dict:
        """从消息缓冲区中提取和筛选数据"""
        recent_chats = []
        bot_replies = []
        image_urls = []

        # 读取配置
        max_chats = self.config.recent_chats_count
        max_bot_replies = self.config.bot_replies_count

        # 从后向前遍历 buffer 来收集所需数量的消息
        for msg in reversed(buffer):
            # 如果两个列表都已填满，则立即停止遍历
            if len(recent_chats) >= max_chats and len(bot_replies) >= max_bot_replies:
                break

            if msg.message_type == ContextMessageType.BOT_REPLY and len(bot_replies) < max_bot_replies:
                bot_replies.append(f"你回复了: {msg.text_content}")
            elif msg.message_type != ContextMessageType.BOT_REPLY and len(recent_chats) < max_chats:
                # 强化输入净化
                safe_sender_name = msg.sender_name.replace("\n", " ")
                safe_text_content = msg.text_content.replace("\n", " ")

                text_part = f"{safe_sender_name}: {safe_text_content}"
                caption_part = ""
                if msg.image_captions:
                    # 在格式化输出时动态添加前缀
                    caption_part = f" [图片: {'; '.join(msg.image_captions)}]"

                if msg.text_content or caption_part:
                    recent_chats.append(f"{text_part}{caption_part}")

                if msg.has_image:
                    for img in msg.images:
                        image_url = getattr(img, "url", None) or getattr(
                            img, "file", None
                        )
                        if image_url:
                            image_urls.append(image_url)
        
        # 反转列表以恢复正确的顺序
        recent_chats.reverse()
        bot_replies.reverse()
        image_urls.reverse()

        return {
            "recent_chats": recent_chats,
            "bot_replies": bot_replies,
            "image_urls": image_urls,
        }

    def _build_context_enhancement(
        self,
        buffer: deque,
        original_prompt: str,
        triggering_message: Optional[GroupMessage],
        scene: str,
    ) -> tuple[str, list[str]]:
        """
        构建要追加到原始提示词的增强内容。
        返回 (增强内容字符串, 图片URL列表)。
        """
        extracted_data = self._extract_messages_for_context(buffer)

        # 构建历史聊天记录部分
        history_parts = [ContextConstants.PROMPT_HEADER]
        history_parts.extend(self._format_recent_chats_section(extracted_data["recent_chats"]))
        history_parts.extend(self._format_bot_replies_section(extracted_data["bot_replies"]))
        context_str = "\n".join(part for part in history_parts if part)

        # 根据场景选择并格式化指令
        instruction_prompt = self._format_situation_instruction(
            original_prompt, triggering_message, scene
        )

        # 组合成最终的增强内容
        final_enhancement = f"{context_str}\n\n{instruction_prompt}"
        
        return final_enhancement, extracted_data["image_urls"]

    def _inject_context_into_request(
        self, request: ProviderRequest, context_enhancement: str, image_urls: list[str]
    ):
        """将生成的增强内容追加到 ProviderRequest 对象的末尾"""
        if context_enhancement:
            # 核心逻辑：直接使用增强后的内容覆盖原始 prompt
            request.prompt = context_enhancement
            setattr(request, '_context_enhanced', True) # 设置标志位
            logger.debug(f"上下文追加完成，新prompt长度: {len(request.prompt)}")

        if image_urls:
            max_images = self.config.max_images_in_context
            # 去重并限制数量
            final_image_urls = list(dict.fromkeys(image_urls))[-max_images:]

            if not request.image_urls:
                request.image_urls = []
            
            # 合并并去重，确保新图片在前
            request.image_urls = list(
                dict.fromkeys(final_image_urls + request.image_urls)
            )
            logger.debug(f"上下文中合并了 {len(final_image_urls)} 张图片")

    def _find_triggering_message_from_event(self, buffer: deque, llm_request_event: AstrMessageEvent) -> tuple[Optional[GroupMessage], str]:
        """
        在 on_llm_request 事件中，根据 nonce 精确查找触发 LLM 调用的消息，并判断场景。

        返回:
            一个元组 (触发消息对象, 场景字符串)
            - (message, "被动回复"): 如果找到了匹配的 nonce
            - (None, "主动发言"): 如果 llm_request_event 上没有 nonce，或没找到匹配
        """
        # 1. 从 llm_request_event 事件对象中直接获取之前设置的 nonce 值
        nonce = getattr(llm_request_event, '_context_enhancer_nonce', None)

        # 2. 如果 nonce 不存在，直接返回 "主动发言"
        if not nonce:
            logger.debug("事件中未找到 nonce，判定为'主动发言'")
            return None, "主动发言"

        # 3. 遍历 buffer 查找匹配的 nonce
        for message in reversed(buffer):
            if message.nonce == nonce:
                logger.debug(f"通过 nonce 成功匹配到触发消息，判定为'被动回复'")
                return message, "被动回复"

        # 4. 如果遍历完 buffer 仍未找到，返回 "主动发言"
        logger.warning(f"持有 nonce 但在缓冲区中未找到匹配消息，判定为'主动发言'")
        return None, "主动发言"

    def _format_recent_chats_section(self, recent_chats: list) -> list:
        """格式化最近的聊天记录部分"""
        if not recent_chats:
            return []
        return [ContextConstants.RECENT_CHATS_HEADER] + recent_chats

    def _format_bot_replies_section(self, bot_replies: list) -> list:
        """格式化机器人回复部分"""
        if not bot_replies:
            return []
        return [ContextConstants.BOT_REPLIES_HEADER] + bot_replies

    def _format_situation_instruction(
        self,
        original_prompt: str,
        triggering_message: Optional[GroupMessage],
        scenario: str,
    ) -> str:
        """根据场景格式化指令性提示词"""
        if scenario == "被动回复" and triggering_message:
            instruction = self.config.passive_reply_instruction
            return instruction.format(
                sender_name=triggering_message.sender_name,
                sender_id=triggering_message.sender_id,
                original_prompt=original_prompt,
            )
        else:
            # 默认为主动发言
            instruction = self.config.active_speech_instruction
            return instruction.format(
                original_prompt=original_prompt
            )

    @event_filter.on_llm_response(priority=100)
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """记录机器人的回复内容"""
        try:
            if event.get_message_type() == MessageType.GROUP_MESSAGE:
                group_id = event.get_group_id()

                # 获取回复文本
                response_text = ""
                if hasattr(resp, "completion_text"):
                    response_text = resp.completion_text
                elif hasattr(resp, "text"):
                    response_text = resp.text
                else:
                    response_text = str(resp)

                # 创建机器人回复记录
                bot_reply = GroupMessage(
                    message_type=ContextMessageType.BOT_REPLY,
                    sender_id=event.get_self_id(),
                    sender_name=self.raw_config.get("name", "助手"),
                    group_id=group_id,
                    text_content=response_text[:1000]
                )

                buffer = await self._get_group_buffer(group_id)
                buffer.append(bot_reply)

                logger.debug(f"记录机器人回复: {response_text[:50]}...")

        except Exception as e:
            logger.error(f"记录机器人回复时发生错误: {e}")

    async def clear_context_cache(self, group_id: Optional[str] = None):
        """
        清空上下文缓存。
        如果提供了 group_id，则只清空该群组的缓存。
        否则，清空所有群组的缓存。
        """
        try:
            if group_id:
                async with self._global_lock:
                    self.group_messages.pop(group_id, None)
                logger.info(f"已清空群组 {group_id} 的内存上下文缓存。")
                if group_id in self.group_last_activity:
                    del self.group_last_activity[group_id]
            else:
                async with self._global_lock:
                    self.group_messages.clear()
                self.group_last_activity.clear()
                logger.info("内存中的所有上下文缓存已清空。")
                if os.path.exists(self.cache_path):
                    await aio_remove(self.cache_path)
                    logger.info(f"持久化缓存文件 {self.cache_path} 已异步删除。")

        except Exception as e:
            logger.error(f"清空上下文缓存时发生错误: {e}")

    @event_filter.command("reset", "new", description="清空当前群聊的上下文缓存")
    async def handle_clear_context_command(self, event: AstrMessageEvent):
        """处理 reset 和 new 命令，清空特定群组的上下文缓存"""
        group_id = event.get_group_id()
        if group_id:
            logger.info(f"收到为群组 {group_id} 清空上下文的命令...")
            await self.clear_context_cache(group_id=group_id)
        else:
            logger.warning("无法获取 group_id，无法执行定向清空操作。")
