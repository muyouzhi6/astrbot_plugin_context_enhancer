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
    from .utils.message_utils import MessageUtils
except ImportError:
    ImageCaptionUtils = None
    MessageUtils = None
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
        serializable_components = []
        for comp in self.raw_components:
            if hasattr(comp, 'to_dict'):
                serializable_components.append(comp.to_dict())
            else:
                try:
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
            "images": self.images,
            "raw_components": serializable_components
        }

    @classmethod
    def from_dict(cls, data: dict):
        """从字典创建 GroupMessage 对象"""
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
        timestamp_str = data.get("timestamp")
        instance.timestamp = datetime.datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.datetime.now()
        instance.image_captions = data.get("image_captions", [])
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
    CACHE_LOAD_BUFFER_MULTIPLIER = 2

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context, config)
        self.raw_config = config
        self.config = self._load_plugin_config()
        self._global_lock = asyncio.Lock()
        logger.info("上下文增强器v2.0已初始化")

        # 初始化工具类
        self.image_caption_utils = None
        self.message_utils = None
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
        
        logger.info(f"上下文增强器配置加载完成: {self.config}")

    async def _async_init(self):
        """异步初始化部分，例如加载缓存"""
        await self._load_cache_from_file()

    async def terminate(self):
        """插件终止时，异步持久化上下文并关闭会话"""
        temp_path = self.cache_path + ".tmp"
        try:
            serializable_data = {}
            for group_id, buffers in self.group_messages.items():
                all_messages = list(heapq.merge(
                    buffers.recent_chats, buffers.bot_replies, buffers.image_messages, key=lambda msg: msg.timestamp
                ))

                max_messages_to_save = self.config.recent_chats_count + self.config.bot_replies_count
                if len(all_messages) > max_messages_to_save:
                    all_messages = all_messages[-max_messages_to_save:]

                serializable_data[group_id] = [msg.to_dict() for msg in all_messages]

            async with aiofiles.open(temp_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(serializable_data, ensure_ascii=False, indent=4))

            await aio_rename(temp_path, self.cache_path)
            logger.info(f"上下文缓存已成功原子化保存到 {self.cache_path}")

        except Exception as e:
            logger.error(f"异步保存上下文缓存失败: {e}")
        finally:
            if await aio_os.path.exists(temp_path):
                try:
                    await aio_remove(temp_path)
                except Exception as e:
                    logger.error(f"清理临时缓存文件 {temp_path} 失败: {e}")

        if self.image_caption_utils and hasattr(self.image_caption_utils, 'close'):
            await self.image_caption_utils.close()
            logger.info("ImageCaptionUtils 的 aiohttp session 已关闭。")

    def _load_plugin_config(self) -> PluginConfig:
        """
        通过动态读取 _conf_schema.json 文件来重构配置加载逻辑，
        解决因中英文键名不匹配及硬编码默认值导致的配置失效问题。
        """
        key_mapping = {
            "启用群组": "enabled_groups",
            "最近聊天记录数量": "recent_chats_count",
            "机器人回复数量": "bot_replies_count",
            "上下文图片最大数量": "max_images_in_context",
            "启用图片描述": "enable_image_caption",
            "图片描述提供商ID": "image_caption_provider_id",
            "图片描述提示词": "image_caption_prompt",
            "收集机器人回复": "collect_bot_replies"
        }

        schema_path = os.path.join(os.path.dirname(__file__), '_conf_schema.json')
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"无法加载配置文件模式: {schema_path}。错误: {e}")
            return PluginConfig(
                enabled_groups=[], recent_chats_count=15, bot_replies_count=5,
                collect_bot_replies=True, max_images_in_context=4, enable_image_caption=True,
                image_caption_provider_id="", image_caption_prompt="请简洁地描述这张图片的主要内容",
                image_caption_timeout=30, cleanup_interval_seconds=600, inactive_cleanup_days=7,
                command_prefixes=["/", "!", "！", "#", ".", "。"],
                duplicate_check_window_messages=5, duplicate_check_time_seconds=30,
                passive_reply_instruction="", active_speech_instruction=""
            )

        final_config = {key: details['default'] for key, details in schema.items() if key != 'verbose_context'}

        user_config = self.raw_config
        for cn_key, en_key in key_mapping.items():
            if cn_key in user_config:
                if en_key in final_config:
                    final_config[en_key] = user_config[cn_key]

        if 'enabled_groups' in final_config:
            final_config['enabled_groups'] = [str(g) for g in final_config['enabled_groups']]

        return PluginConfig(**final_config)

    def _initialize_utils(self):
        """初始化工具模块"""
        try:
            if ImageCaptionUtils:
                self.image_caption_utils = ImageCaptionUtils(self.context, self.raw_config)
                logger.debug("ImageCaptionUtils 初始化成功")
            else:
                logger.warning("ImageCaptionUtils 未导入，图片描述功能不可用。")

            if MessageUtils:
                self.message_utils = MessageUtils(
                    config=self.raw_config,
                    context=self.context,
                    image_caption_utils=self.image_caption_utils
                )
                logger.debug("MessageUtils 初始化成功")
            else:
                logger.error("MessageUtils 未导入，插件核心功能无法运行。")

        except Exception as e:
            logger.error(f"工具类初始化失败: {e}")
            self.image_caption_utils = None
            self.message_utils = None

    def _get_or_create_lock(self, group_id: str) -> Lock:
        return self.group_locks[group_id]

    async def _load_cache_from_file(self):
        """从文件异步加载缓存"""
        if not await aio_os.path.exists(self.cache_path):
            return
        try:
            async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                content = await f.read()
                if content:
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
            buffers = self._create_new_group_buffers()

            for msg_data in msg_list:
                try:
                    msg = GroupMessage.from_dict(msg_data)
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
        return GroupMessageBuffers(
            recent_chats=deque(maxlen=self.config.recent_chats_count * self.CACHE_LOAD_BUFFER_MULTIPLIER),
            bot_replies=deque(maxlen=self.config.bot_replies_count * self.CACHE_LOAD_BUFFER_MULTIPLIER),
            image_messages=deque(maxlen=self.config.max_images_in_context * self.CACHE_LOAD_BUFFER_MULTIPLIER)
        )

    async def _get_or_create_group_buffers(self, group_id: str) -> "GroupMessageBuffers":
        """获取或创建群聊的消息缓冲区集合"""
        current_dt = datetime.datetime.now()

        self.group_last_activity[group_id] = current_dt

        now = time.time()
        if now - self.last_cleanup_time > self.config.cleanup_interval_seconds:
            await self._cleanup_inactive_groups(current_dt)
            self.last_cleanup_time = now

        if group_id not in self.group_messages:
            async with self._global_lock:
                if group_id not in self.group_messages:
                    self.group_messages[group_id] = self._create_new_group_buffers()
        return self.group_messages[group_id]

    async def _cleanup_inactive_groups(self, current_time: datetime.datetime):
        """清理超过配置天数未活跃的群组缓存"""
        inactive_threshold = datetime.timedelta(
            days=self.config.inactive_cleanup_days
        )
        inactive_groups = []

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
            return True
        else:
            group_id = event.get_group_id()
            if not self.config.enabled_groups:
                return True
            return group_id in self.config.enabled_groups

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

    async def _create_group_message_from_event(self, event: AstrMessageEvent, message_type: str) -> GroupMessage:
        """从事件创建 GroupMessage 实例，并根据配置调用 message_utils 进行文本化处理"""
        
        message_obj = getattr(event, 'message_obj', None)
        raw_components = message_obj.message if message_obj and hasattr(message_obj, 'message') else []

        if self.message_utils:
            text_content = await self.message_utils.outline_message_list(raw_components)
        else:
            text_content = event.get_message_str() or ""
            logger.warning("MessageUtils 不可用，回退到基础文本提取。")

        images = [comp.url for comp in raw_components if isinstance(comp, Image) and getattr(comp, 'url', None)]

        sender_name = event.get_sender_name()
        raw_event = getattr(event, 'raw_event', None)
        if not sender_name and raw_event and isinstance(raw_event.get("sender"), dict):
            sender = raw_event.get("sender")
            sender_name = sender.get("card") or sender.get("nickname")

        final_sender_name = sender_name or "用户"
        
        return GroupMessage(
            message_type=message_type,
            sender_id=event.get_sender_id() or "unknown",
            sender_name=final_sender_name,
            group_id=event.get_group_id(),
            text_content=text_content.strip(),
            images=images,
            message_id=getattr(event, 'id', None) or (message_obj and getattr(message_obj, 'id', None)),
            nonce=getattr(event, '_context_enhancer_nonce', None),
            raw_components=raw_components
        )

    async def _handle_group_message(self, event: AstrMessageEvent):
        """处理群聊消息"""
        group_msg = await self._create_group_message_from_event(event, "")
        if not group_msg.text_content and not group_msg.has_image:
            logger.debug("消息为空（无文本无图片），跳过处理。")
            return

        try:
            if self._is_bot_message(event):
                logger.debug("收集到机器人自己的消息，用于保持上下文完整性。")

            message_type = self._classify_message(event)
            group_msg.message_type = message_type

            buffers = await self._get_or_create_group_buffers(group_msg.group_id)
            lock = self._get_or_create_lock(group_msg.group_id)

            async with lock:
                target_deque = None
                if message_type == ContextMessageType.BOT_REPLY:
                    target_deque = buffers.bot_replies
                else:
                    target_deque = buffers.recent_chats

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
        if new_msg.has_image:
            return False
            
        start_index = max(0, len(target_deque) - self.config.duplicate_check_window_messages)
        recent_messages = list(itertools.islice(target_deque, start_index, len(target_deque)))

        for existing_msg in recent_messages:
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
            bot_id = event.get_self_id()
            sender_id = event.get_sender_id()
            return bool(bot_id and sender_id and str(sender_id) == str(bot_id))
        except (AttributeError, KeyError) as e:
            logger.warning(f"检查机器人消息时出错（可能是不支持的事件类型或数据结构）: {e}")
            return False

    def _classify_message(self, event: AstrMessageEvent) -> str:
        """
        分类消息类型
        """
        if self._is_bot_message(event) and self.config.bot_replies_count > 0:
            return ContextMessageType.BOT_REPLY

        if self._is_directly_triggered(event):
            setattr(event, '_context_enhancer_nonce', uuid.uuid4().hex)
            return ContextMessageType.LLM_TRIGGERED

        return ContextMessageType.NORMAL_CHAT

    def _is_at_triggered(self, event: AstrMessageEvent) -> bool:
        """检查消息是否通过@机器人触发"""
        bot_id = event.get_self_id()
        if not bot_id:
            return False

        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, At) and (
                    str(comp.qq) == str(bot_id) or comp.qq == "all"
                ):
                    return True
        
        message_text = event.message_str or ""
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
        """
        return self._is_at_triggered(event) or self._is_keyword_triggered(event)

    @event_filter.on_llm_request(priority=100)
    async def on_llm_request(self, event: AstrMessageEvent, request: ProviderRequest):
        """
        LLM请求时提供上下文增强。
        """
        start_time = time.monotonic()
        group_id = event.get_group_id()
        if event.get_message_type() == MessageType.GROUP_MESSAGE and not group_id:
            logger.warning(f"LLM 请求事件缺少 group_id，无法增强上下文。")
            return
            
        try:
            if not self._should_enhance_context(event, request):
                return

            buffers = await self._get_or_create_group_buffers(group_id)
            if not any([buffers.recent_chats, buffers.bot_replies, buffers.image_messages]):
                logger.debug("所有消息缓冲区都为空，跳过增强")
                return

            lock = self._get_or_create_lock(group_id)
            async with lock:
                all_messages = list(heapq.merge(buffers.recent_chats, buffers.bot_replies, buffers.image_messages, key=lambda x: x.timestamp))
                
                triggering_message, scene = self._find_triggering_message_from_event(all_messages, event)

                context_enhancement, image_urls_for_context = self._build_context_enhancement(
                    all_messages, request.prompt, triggering_message, scene
                )

            self._inject_context_into_request(request, context_enhancement, image_urls_for_context)

        except Exception as e:
            logger.error(f"上下文增强时发生错误: {e}")
            logger.error(traceback.format_exc())
        finally:
            duration = (time.monotonic() - start_time) * 1000
            logger.debug(f"[Profiler] on_llm_request took: {duration:.2f} ms")

    def _should_enhance_context(self, event: AstrMessageEvent, request: ProviderRequest) -> bool:
        """检查是否应执行上下文增强"""
        if hasattr(request, '_context_enhanced'):
            return False

        if not self.is_chat_enabled(event):
            return False

        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False

        return True

    def _extract_messages_for_context(self, sorted_messages: list[GroupMessage]) -> dict:
        """从已排序的合并消息列表中提取和筛选数据"""
        recent_chats = []
        bot_replies = []

        max_chats = self.config.recent_chats_count
        max_bot_replies = self.config.bot_replies_count

        for msg in reversed(sorted_messages):
            if msg.message_type == ContextMessageType.BOT_REPLY:
                if len(bot_replies) < max_bot_replies:
                    bot_replies.append(f"你回复了: {msg.text_content}")
            else:
                if len(recent_chats) < max_chats:
                    content = msg.text_content
                    if content:
                        recent_chats.append(f"{msg.sender_name}: {content}")
        
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
    ) -> tuple[str, list[str]]:
        """
        构建要追加到原始提示词的增强内容和图片URL列表。
        """
        extracted_data = self._extract_messages_for_context(sorted_messages)

        image_urls = []
        for msg in sorted_messages:
            if msg.images:
                image_urls.extend(msg.images)
        
        if len(image_urls) > self.config.max_images_in_context:
            image_urls = image_urls[-self.config.max_images_in_context:]

        history_parts = [ContextConstants.PROMPT_HEADER]
        history_parts.extend(self._format_recent_chats_section(extracted_data["recent_chats"]))
        history_parts.extend(self._format_bot_replies_section(extracted_data["bot_replies"]))
        context_str = "\n".join(part for part in history_parts if part)

        instruction_prompt = self._format_situation_instruction(
            original_prompt, triggering_message, scene
        )

        final_enhancement = f"{context_str}\n\n{instruction_prompt}"
        
        return final_enhancement, image_urls

    def _inject_context_into_request(
        self, request: ProviderRequest, context_enhancement: str, image_urls: list[str]
    ):
        """将生成的增强内容和图片URL注入到 ProviderRequest 对象中"""
        if context_enhancement:
            request.prompt = context_enhancement
            setattr(request, '_context_enhanced', True)
            logger.debug(f"上下文注入完成，新prompt长度: {len(request.prompt)}")

        if image_urls:
            if not hasattr(request, 'image_urls') or request.image_urls is None:
                request.image_urls = []
            request.image_urls.extend(image_urls)
            logger.debug(f"向请求中追加了 {len(image_urls)} 张图片URL。")

    def _find_triggering_message_from_event(self, sorted_messages: list[GroupMessage], llm_request_event: AstrMessageEvent) -> tuple[Optional[GroupMessage], str]:
        """
        在 on_llm_request 事件中，从已排序的合并消息列表中根据 nonce 精确查找触发 LLM 调用的消息，并判断场景。
        """
        nonce = getattr(llm_request_event, '_context_enhancer_nonce', None)

        if not nonce:
            logger.debug("事件中未找到 nonce，判定为'主动发言'")
            return None, "主动发言"

        for message in reversed(sorted_messages):
            if message.nonce == nonce:
                logger.debug(f"通过 nonce 成功匹配到触发消息，判定为'被动回复'")
                return message, "被动回复"

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
    ) -> str:
        """根据场景格式化指令性提示词"""
        if scenario == "被动回复":
            instruction = self.config.passive_reply_instruction
            sender_name = triggering_message.sender_name if triggering_message else "未知用户"
            sender_id = triggering_message.sender_id if triggering_message else "unknown"
            return instruction.format(
                sender_name=sender_name,
                sender_id=sender_id,
                original_prompt=original_prompt,
            )
        else:
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

                response_text = ""
                if hasattr(resp, "completion_text"):
                    response_text = resp.completion_text
                elif hasattr(resp, "text"):
                    response_text = resp.text
                else:
                    response_text = str(resp)

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
        """
        try:
            if group_id:
                if group_id in self.group_messages:
                    lock = self._get_or_create_lock(group_id)
                    async with lock:
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
