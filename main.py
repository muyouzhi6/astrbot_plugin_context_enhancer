"""
智能群聊上下文增强插件
通过多维度信息收集和分层架构，为 LLM 提供丰富的群聊语境，支持角色扮演，完全兼容人设系统。
"""
import traceback
import json
import re
import datetime
import heapq
import itertools
from collections import deque, defaultdict
import os
from typing import Dict, Optional
from asyncio import Lock
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
from astrbot.api.message_components import Plain, At, Image, Face, Reply
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
    collect_bot_replies: bool
    max_images_in_context: int
    enable_image_caption: bool
    image_caption_provider_id: str
    image_caption_prompt: str
    image_caption_timeout: int
    cleanup_interval_seconds: int
    inactive_cleanup_days: int
    command_prefixes: list
    duplicate_check_window_messages: int
    duplicate_check_time_seconds: int
    passive_reply_instruction: str  # 被动回复指令
    active_speech_instruction: str  # 主动发言指令


@dataclass
class GroupMessageBuffers:
    """为每个群组管理独立的、按类型划分的消息缓冲区"""
    recent_chats: deque
    bot_replies: deque
    image_messages: deque


class GroupMessage:
    """群聊消息的独立数据类，与框架解耦"""
    def __init__(self,
                 message_type: str,
                 sender_id: str,
                 sender_name: str,
                 group_id: str,
                 text_content: str = "",
                 images: Optional[list[str]] = None,
                 message_id: Optional[str] = None,
                 nonce: Optional[str] = None,
                 raw_components: Optional[list] = None):
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
        self.raw_components = raw_components or []

    def to_dict(self) -> dict:
        """将消息对象转换为可序列化为 JSON 的字典"""
        # 序列化 raw_components
        serializable_components = []
        for comp in self.raw_components:
            if hasattr(comp, 'to_dict'):
                serializable_components.append(comp.to_dict())
            else:
                # 对于没有 to_dict 方法的组件，尝试转换为字符串
                try:
                    # 修复 #3: 改进对未知组件的序列化处理
                    serializable_components.append({"type": comp.__class__.__name__, "content": str(comp)})
                except Exception:
                    serializable_components.append({"type": "unknown", "content": str(comp)})

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
            "images": self.images,  # 直接存储 URL 列表
            "raw_components": serializable_components
        }

    @classmethod
    def from_dict(cls, data: dict):
        """从字典创建 GroupMessage 对象"""
        # 注意：从字典恢复 raw_components 较为复杂，
        # 这里我们只恢复其字典形式，因为原始对象类型信息已丢失。
        # 如果需要完全恢复，需要一个组件工厂函数。
        # 目前的实现对于数据存储和传输是足够的。
       # 修复 #1: 增强向后兼容性，使用 .get() 并提供默认值
        instance = cls(
           message_type=data.get("message_type", ContextMessageType.NORMAL_CHAT),
           sender_id=data.get("sender_id", "unknown"),
           sender_name=data.get("sender_name", "用户"),
           group_id=data.get("group_id", ""),
           text_content=data.get("text_content", ""),
           images=data.get("images", []),
           message_id=data.get("id"),
           nonce=data.get("nonce"),
           raw_components=data.get("raw_components", [])
        )
        # 时间戳是核心字段，如果缺少则可能无法处理，但仍尝试提供默认值
        timestamp_str = data.get("timestamp")
        instance.timestamp = datetime.datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.datetime.now()
        instance.image_captions = data.get("image_captions", [])
       # has_image 属性需要根据恢复的 images 列表重新计算
        instance.has_image = len(instance.images) > 0
        return instance


@register("context_enhancer_v2", "木有知", "智能群聊上下文增强插件 v2", "2.0.0", repo="https://github.com/muyouzhi6/astrbot_plugin_context_enhancer")
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
        self.group_messages: Dict[str, "GroupMessageBuffers"] = {}
        self.group_locks: defaultdict[str, Lock] = defaultdict(Lock)
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
            serializable_data = {}
            for group_id, buffers in self.group_messages.items():
                # 使用 heapq.merge 高效合并已排序的 deques，并立即转换为列表
                all_messages = list(heapq.merge(
                    buffers.recent_chats, buffers.bot_replies, buffers.image_messages, key=lambda msg: msg.timestamp
                ))

                # 在保存前，根据配置裁剪消息列表，防止缓存文件无限增长
                max_messages_to_save = self.config.recent_chats_count + self.config.bot_replies_count
                if len(all_messages) > max_messages_to_save:
                    all_messages = all_messages[-max_messages_to_save:]

                # 序列化
                serializable_data[group_id] = [msg.to_dict() for msg in all_messages]

            # 1. 写入临时文件
            async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(serializable_data, ensure_ascii=False, indent=4))

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
            enabled_groups=[str(g) for g in self.raw_config.get("enabled_groups", [])],
            recent_chats_count=self.raw_config.get("recent_chats_count", 15),
            bot_replies_count=self.raw_config.get("bot_replies_count", 5),
            max_images_in_context=self.raw_config.get("max_context_images", 4),
            collect_bot_replies=self.raw_config.get("collect_bot_replies", True),
            enable_image_caption=self.raw_config.get("enable_image_caption", True),
            image_caption_provider_id=self.raw_config.get("image_caption_provider_id", ""),
            image_caption_prompt=self.raw_config.get(
                "image_caption_prompt", "请简洁地描述这张图片的主要内容，重点关注与聊天相关的信息"
            ),
            image_caption_timeout=self.raw_config.get("image_caption_timeout", 30),
            cleanup_interval_seconds=self.raw_config.get("cleanup_interval_seconds", 600),
            inactive_cleanup_days=self.raw_config.get("inactive_cleanup_days", 7),
            command_prefixes=self.raw_config.get("command_prefixes", ["/", "!", "！", "#", ".", "。"]),
            duplicate_check_window_messages=self.raw_config.get("duplicate_check_window_messages", 5),
            duplicate_check_time_seconds=self.raw_config.get("duplicate_check_time_seconds", 30),
            passive_reply_instruction=self.raw_config.get("passive_reply_instruction", '现在，群成员 {sender_name} (ID: {sender_id}) 正在对你说话，或者提到了你，TA说："{original_prompt}"\n你需要根据以上聊天记录和你的角色设定，直接回复该用户。（不要回复本消息，这只是个提示）'),
            active_speech_instruction=self.raw_config.get("active_speech_instruction", '以上是最近的聊天记录。现在，你决定主动参与讨论，并想就以下内容发表你的看法："{original_prompt}"\n你需要根据以上聊天记录和你的角色设定，自然地切入对话。（不要回复本消息，这只是个提示）'),
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

    def _get_or_create_lock(self, group_id: str) -> Lock:
        return self.group_locks[group_id]

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
    ) -> Dict[str, "GroupMessageBuffers"]:
        """从字典加载群组消息到新的多缓冲区结构"""
        group_buffers_map = {}

        for group_id, msg_list in data.items():
            # 为每个群组创建独立的缓冲区
            buffers = self._create_new_group_buffers()

            for msg_data in msg_list:
                try:
                    msg = GroupMessage.from_dict(msg_data)
                    # 根据消息类型和内容分发到对应的 deque
                    if msg.message_type == ContextMessageType.BOT_REPLY:
                        buffers.bot_replies.append(msg)
                    elif msg.has_image:
                        buffers.image_messages.append(msg)
                    else:
                        buffers.recent_chats.append(msg)
                except Exception as e:
                    logger.warning(f"从字典转换并分发消息失败 (群 {group_id}): {e}")
            group_buffers_map[group_id] = buffers
        return group_buffers_map

    def _create_new_group_buffers(self) -> "GroupMessageBuffers":
        """创建一个新的 GroupMessageBuffers 实例，并根据配置初始化 deques"""
        # 为每个 deque 设置独立的 maxlen，并增加一定的缓冲空间
        return GroupMessageBuffers(
            recent_chats=deque(maxlen=self.config.recent_chats_count * self.CACHE_LOAD_BUFFER_MULTIPLIER),
            bot_replies=deque(maxlen=self.config.bot_replies_count * self.CACHE_LOAD_BUFFER_MULTIPLIER),
            image_messages=deque(maxlen=self.config.max_images_in_context * self.CACHE_LOAD_BUFFER_MULTIPLIER)
        )

    async def _get_or_create_group_buffers(self, group_id: str) -> "GroupMessageBuffers":
        """获取或创建群聊的消息缓冲区集合"""
        current_dt = datetime.datetime.now()

        # 更新活动时间
        self.group_last_activity[group_id] = current_dt

        # 基于时间的缓存清理
        now = time.time()
        if now - self.last_cleanup_time > self.config.cleanup_interval_seconds:
            await self._cleanup_inactive_groups(current_dt)
            self.last_cleanup_time = now

        if group_id not in self.group_messages:
            async with self._global_lock:
                # 双重检查，防止在等待锁期间其他协程已创建
                if group_id not in self.group_messages:
                    self.group_messages[group_id] = self._create_new_group_buffers()
        return self.group_messages[group_id]

    async def _cleanup_inactive_groups(self, current_time: datetime.datetime):
        """清理超过配置天数未活跃的群组缓存"""
        inactive_threshold = datetime.timedelta(
            days=self.config.inactive_cleanup_days
        )
        inactive_groups = []

        # 这个循环是安全的，因为它只读取 self.group_last_activity
        for group_id, last_activity in list(self.group_last_activity.items()):
            if current_time - last_activity > inactive_threshold:
                inactive_groups.append(group_id)

        if inactive_groups:
            logger.info(f"准备清理 {len(inactive_groups)} 个不活跃的群组上下文缓存...")
            async with self._global_lock:
                for group_id in inactive_groups:
                    self.group_messages.pop(group_id, None)
                    self.group_last_activity.pop(group_id, None)
                    self.group_locks.pop(group_id, None)
            logger.info("不活跃群组上下文缓存清理完毕。")

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
        start_time = time.monotonic()
        group_id = event.get_group_id()
        if event.get_message_type() == MessageType.GROUP_MESSAGE and not group_id:
            logger.warning("事件缺少 group_id，无法处理。")
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
        finally:
            duration = (time.monotonic() - start_time) * 1000
            logger.debug(f"[Profiler] on_message took: {duration:.2f} ms")

    def _extract_user_info_from_event(self, event: AstrMessageEvent) -> tuple[str, str]:
        """
        从事件中提取用户ID和昵称的统一方法
        返回: (sender_name, sender_id)
        """
        # 1. 优先使用标准方法
        sender_name = event.get_sender_name()
        sender_id = event.get_sender_id()

        # 2. 如果标准方法失败，尝试从 message_obj.sender 获取
        if not sender_name or not sender_id:
            message_obj = getattr(event, 'message_obj', None)
            if message_obj and hasattr(message_obj, 'sender') and message_obj.sender:
                sender = message_obj.sender
                if not sender_name and hasattr(sender, 'nickname'):
                    sender_name = sender.nickname
                if not sender_id and hasattr(sender, 'user_id'):
                    sender_id = sender.user_id

        # 3. 如果仍然失败，尝试从原始事件数据中获取 (兼容性)
        if not sender_name or not sender_id:
            raw_event = getattr(event, 'raw_event', None)
            if raw_event and isinstance(raw_event.get("sender"), dict):
                raw_sender = raw_event["sender"]
                if not sender_name:
                    sender_name = raw_sender.get("card") or raw_sender.get("nickname")
                if not sender_id:
                    sender_id = raw_sender.get("user_id") or raw_sender.get("id")

        # 4. 最后使用后备值
        return sender_name or "用户", sender_id or "unknown"

    async def _create_group_message_from_event(self, event: AstrMessageEvent, message_type: str) -> GroupMessage:
        """从事件创建 GroupMessage 实例，并在检测到图片时同步获取描述"""
        text_content_parts = []
        images = []
        has_image_component = False

        # 安全地获取 message_obj 和 raw_components
        message_obj = getattr(event, 'message_obj', None)
        raw_components = message_obj.message if message_obj and hasattr(message_obj, 'message') else []

        if raw_components:
            for comp in raw_components:
                if isinstance(comp, Plain):
                    text_content_parts.append(comp.text)
                elif isinstance(comp, At):
                    text_content_parts.append(f"@{comp.qq}")
                elif isinstance(comp, Face):
                    text_content_parts.append(f"[表情]")
                elif isinstance(comp, Reply):
                    text_content_parts.append(f"[引用了 {comp.sender_nickname} 的消息]")
                elif isinstance(comp, Image):
                    has_image_component = True
                    image_url = getattr(comp, "url", None) or getattr(comp, "file", None)
                    if image_url:
                        images.append(image_url)
                    # 如果禁用了图片描述，则添加一个简单的占位符
                    if not self.config.enable_image_caption or not self.image_caption_utils:
                        text_content_parts.append("[图片]")

        # 如果启用了图片描述，则在此处同步处理
        if has_image_component and self.config.enable_image_caption and self.image_caption_utils:
            image_captions = []
            for image_url in images:
                try:
                    if image_url:
                        caption = await self.image_caption_utils.generate_image_caption(
                            image_url,
                            timeout=self.config.image_caption_timeout,
                            provider_id=self.config.image_caption_provider_id or None,
                            custom_prompt=self.config.image_caption_prompt,
                        )
                        # 将描述格式化并添加到文本部分
                        image_captions.append(caption or "图片内容未知")
                    else:
                        image_captions.append("图片")
                except Exception as e:
                    logger.debug(f"在消息创建期间生成图片描述失败: {e}")
                    image_captions.append("图片")

            if image_captions:
                # 将所有图片的描述合并为一个字符串，并添加到文本内容的末尾
                text_content_parts.append(f"[Image: {'; '.join(image_captions)}]")


        # 使用统一的用户信息提取方法
        final_sender_name, final_sender_id = self._extract_user_info_from_event(event)

        return GroupMessage(
            message_type=message_type,
            sender_id=final_sender_id,
            sender_name=final_sender_name,
            group_id=event.get_group_id(),
            text_content="".join(text_content_parts).strip(),
            images=images,  # 存储收集到的图片 URL
            # 尝试从不同事件结构中获取消息ID，兼容直接事件和包装后的事件对象
            message_id=getattr(event, 'id', None) or (message_obj and getattr(message_obj, 'id', None)),
            nonce=getattr(event, '_context_enhancer_nonce', None),
            raw_components=raw_components
        )

    async def _handle_group_message(self, event: AstrMessageEvent):
        """处理群聊消息"""
        # 现在 create 方法是 async 的，需要 await
        group_msg = await self._create_group_message_from_event(event, "")  # 临时创建以检查内容
        if not group_msg.text_content and not group_msg.has_image: # 检查 has_image 以防万一
            logger.debug("消息为空（无文本无图片），跳过处理。")
            return

        try:
            if self._is_bot_message(event):
                logger.debug("收集到机器人自己的消息，用于保持上下文完整性。")

            message_type = self._classify_message(event)
            group_msg.message_type = message_type # 更新消息类型

            # 获取或创建该群组的缓冲区集合
            buffers = await self._get_or_create_group_buffers(group_msg.group_id)
            lock = self._get_or_create_lock(group_msg.group_id)

            async with lock:
                # 根据消息类型和内容，将其放入对应的 deque
                target_deque = None
                if message_type == ContextMessageType.BOT_REPLY:
                    target_deque = buffers.bot_replies
                # 图片消息现在作为普通聊天处理，因为内容已是文本
                else: # NORMAL_CHAT or LLM_TRIGGERED
                    target_deque = buffers.recent_chats

                # 🚨 防重复机制：检查是否已存在相同消息
                if not self._is_duplicate_message(target_deque, group_msg):
                    target_deque.append(group_msg)
                    logger.debug(
                        f"收集群聊消息 [{message_type}]: {group_msg.sender_name} - {group_msg.text_content[:50]}..."
                    )
                else:
                    logger.debug(
                        f"跳过重复消息: {group_msg.sender_name} - {group_msg.text_content[:30]}..."
                    )

        except Exception as e:
            logger.error(f"处理群聊消息时发生错误: {e}")

    def _is_duplicate_message(self, target_deque: deque, new_msg: GroupMessage) -> bool:
        """检查消息是否已存在于目标缓冲区（防重复）"""
        # 如果新消息包含图片，则不视为重复，以确保图片总能被处理
        if new_msg.has_image:
            return False
            
        # 检查最近N条消息即可，避免性能问题
        start_index = max(0, len(target_deque) - self.config.duplicate_check_window_messages)
        recent_messages = list(itertools.islice(target_deque, start_index, len(target_deque)))

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

    @event_filter.on_llm_request(priority=100)
    async def on_llm_request(self, event: AstrMessageEvent, request: ProviderRequest):
        """
        LLM请求时提供上下文增强。
        此方法作为总入口，协调上下文的构建和注入流程。
        """
        start_time = time.monotonic()
        group_id = event.get_group_id()
        if event.get_message_type() == MessageType.GROUP_MESSAGE and not group_id:
            logger.warning(f"LLM 请求事件缺少 group_id，无法增强上下文。")
            return
            
        try:
            # 1. 检查是否需要增强
            if not self._should_enhance_context(event, request):
                return

            # 2. 获取群聊历史记录
            group_id = event.get_group_id()
            buffers = await self._get_or_create_group_buffers(group_id)
            if not any([buffers.recent_chats, buffers.bot_replies, buffers.image_messages]):
                logger.debug("所有消息缓冲区都为空，跳过增强")
                return

            # 3. 确定场景（被动回复 vs 主动发言）
            lock = self._get_or_create_lock(group_id)
            async with lock:
                # 合并所有消息用于查找触发消息
                collect_start = time.monotonic()
                # deques are already sorted by timestamp implicitly
                all_messages = list(heapq.merge(buffers.recent_chats, buffers.bot_replies, buffers.image_messages, key=lambda x: x.timestamp))
                logger.debug(f"[Profiler] Merging messages from deques took: {(time.monotonic() - collect_start) * 1000:.2f} ms")

                triggering_message, scene = self._find_triggering_message_from_event(all_messages, event)

                # 4. 构建上下文增强内容
                build_start = time.monotonic()
                context_enhancement, image_urls_for_context = self._build_context_enhancement(
                    all_messages, request.prompt, triggering_message, scene, event
                )
                logger.debug(f"[Profiler] _build_context_enhancement took: {(time.monotonic() - build_start) * 1000:.2f} ms")

            # 5. 将上下文注入到请求中
            self._inject_context_into_request(request, context_enhancement, image_urls_for_context)

        except Exception as e:
            logger.error(f"上下文增强时发生错误: {e}")
            logger.error(traceback.format_exc())
        finally:
            duration = (time.monotonic() - start_time) * 1000
            logger.debug(f"[Profiler] on_llm_request took: {duration:.2f} ms")

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

    def _extract_messages_for_context(self, sorted_messages: list[GroupMessage]) -> dict:
        """从已排序的合并消息列表中提取和筛选数据"""
        recent_chats = []
        bot_replies = []
        # image_urls 列表不再需要，因为图片信息已在文本中

        # 读取配置
        max_chats = self.config.recent_chats_count
        max_bot_replies = self.config.bot_replies_count

        # 从后向前遍历已排序的列表来收集所需数量的消息
        for msg in reversed(sorted_messages):
            # 收集机器人回复
            if msg.message_type == ContextMessageType.BOT_REPLY:
                if len(bot_replies) < max_bot_replies:
                    bot_replies.append(f"你回复了: {msg.text_content}")
            # 收集普通聊天记录
            else:
                if len(recent_chats) < max_chats:
                    # 保留原始换行符，让LLM更好地理解格式
                    content = msg.text_content
                    # 文本内容现在已包含图片描述，无需额外处理
                    if content:
                        recent_chats.append(f"{msg.sender_name}: {content}")
        
        # 反转列表以恢复正确的时序
        recent_chats.reverse()
        bot_replies.reverse()

        return {
            "recent_chats": recent_chats,
            "bot_replies": bot_replies,
        }

    def _build_context_enhancement(
        self,
        sorted_messages: list[GroupMessage],
        original_prompt: str,
        triggering_message: Optional[GroupMessage],
        scene: str,
        event: AstrMessageEvent,
    ) -> tuple[str, list[str]]:
        """
        构建要追加到原始提示词的增强内容和图片URL列表。
        返回一个元组: (增强内容字符串, 图片URL列表)
        """
        extracted_data = self._extract_messages_for_context(sorted_messages)

        # 提取图片URL
        image_urls = []
        for msg in sorted_messages:
            if msg.images:
                image_urls.extend(msg.images)
        
        # 限制图片数量
        if len(image_urls) > self.config.max_images_in_context:
            image_urls = image_urls[-self.config.max_images_in_context:]


        # 构建历史聊天记录部分
        history_parts = [ContextConstants.PROMPT_HEADER]
        history_parts.extend(self._format_recent_chats_section(extracted_data["recent_chats"]))
        history_parts.extend(self._format_bot_replies_section(extracted_data["bot_replies"]))
        context_str = "\n".join(part for part in history_parts if part)

        # 根据场景选择并格式化指令
        instruction_prompt = self._format_situation_instruction(
            original_prompt, triggering_message, scene, event
        )

        # 组合成最终的增强内容
        final_enhancement = f"{context_str}\n\n{instruction_prompt}"
        
        return final_enhancement, image_urls

    def _inject_context_into_request(
        self, request: ProviderRequest, context_enhancement: str, image_urls: list[str]
    ):
        """将生成的增强内容和图片URL注入到 ProviderRequest 对象中"""
        if context_enhancement:
            # 核心逻辑：直接使用构建好的、包含完整指令的增强内容替换原始 prompt
            request.prompt = context_enhancement
            setattr(request, '_context_enhanced', True)  # 设置标志位
            logger.debug(f"上下文注入完成，新prompt长度: {len(request.prompt)}")

        if image_urls:
            if not hasattr(request, 'image_urls') or request.image_urls is None:
                request.image_urls = []
            request.image_urls.extend(image_urls)
            logger.debug(f"向请求中追加了 {len(image_urls)} 张图片URL。")

    def _find_triggering_message_from_event(self, sorted_messages: list[GroupMessage], llm_request_event: AstrMessageEvent) -> tuple[Optional[GroupMessage], str]:
        """
        在 on_llm_request 事件中，从已排序的合并消息列表中根据 nonce 精确查找触发 LLM 调用的消息，并判断场景。

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

        # 3. 遍历已排序的列表查找匹配的 nonce
        for message in reversed(sorted_messages):
            if message.nonce == nonce:
                logger.debug(f"通过 nonce 成功匹配到触发消息，判定为'被动回复'")
                return message, "被动回复"

        # 修复 #2: 核心逻辑变更 - 有 nonce 就一定是“被动回复”
        # 即使在缓冲区中找不到消息，也应保持场景判断的一致性。
        logger.warning(f"持有 nonce 但在缓冲区中未找到匹配的触发消息。仍判定为'被动回复'场景。")
        return None, "被动回复"

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
        event: AstrMessageEvent,
    ) -> str:
        """根据场景格式化指令性提示词"""
        if scenario == "被动回复":
            # 修复 #2: 即使 triggering_message 为 None，也使用被动回复模板
            instruction = self.config.passive_reply_instruction

            # 优先从 triggering_message 获取用户信息，如果为空则从当前事件获取
            if triggering_message:
                sender_name = triggering_message.sender_name
                sender_id = triggering_message.sender_id
            else:
                # 使用统一的用户信息提取方法
                sender_name, sender_id = self._extract_user_info_from_event(event)

            return instruction.format(
                sender_name=sender_name,
                sender_id=sender_id,
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

                buffers = await self._get_or_create_group_buffers(group_id)
                lock = self._get_or_create_lock(group_id)
                async with lock:
                    buffers.bot_replies.append(bot_reply)

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
                if group_id in self.group_messages:
                    lock = self._get_or_create_lock(group_id)
                    async with lock:
                        # 使用 pop 安全地移除并返回条目，如果键不存在则返回 None，避免错误
                        self.group_messages.pop(group_id, None)
                        self.group_locks.pop(group_id, None)
                        self.group_last_activity.pop(group_id, None)
                    logger.info(f"已为群组 {group_id} 清理上下文缓存。")
            else:
                async with self._global_lock:
                    self.group_messages.clear()
                self.group_last_activity.clear()
                logger.info("内存中的所有上下文缓存已清空。")
                if await aio_os.path.exists(self.cache_path):
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

