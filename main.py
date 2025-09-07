from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Plain, At, Image
from astrbot.api.platform import MessageType
import traceback
import json
import datetime
from collections import deque
import os
import shutil
import pickle
from typing import Dict, Any, Optional
import time

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
    INACTIVE_GROUP_CLEANUP_DAYS = 30 # 清理不活跃群组缓存的天数

    # 命令前缀
    COMMAND_PREFIXES = ["/", "!", "！", "#", ".", "。"]

    # Prompt 模板
    PROMPT_HEADER = "你正在浏览聊天软件，查看群聊消息。"
    RECENT_CHATS_HEADER = "\n最近的聊天记录:"
    BOT_REPLIES_HEADER = "\n你最近的回复:"
    # 区分用户触发和主动触发的模板
    USER_TRIGGER_TEMPLATE = "\n现在 {sender_name}（ID: {sender_id}）发了一个消息: {original_prompt}"
    PROACTIVE_TRIGGER_TEMPLATE = "\n你需要根据以上聊天记录，主动就以下内容发表观点: {original_prompt}"
    PROMPT_FOOTER = "需要你在心里理清当前到底讨论的什么，搞清楚形势，谁在跟谁说话，你是在插话还是回复，然后根据你的设定和当前形势做出最自然的回复。"


class GroupMessage:
    """群聊消息包装类（最终简化版）"""

    def __init__(self, event: Optional[AstrMessageEvent], message_type: str):
        self.message_type = message_type
        self.timestamp = datetime.datetime.now()
        
        if event and event.message_obj:
            self.sender_name = event.message_obj.sender.nickname if event.message_obj.sender else "用户"
            self.sender_id = event.message_obj.sender.user_id if event.message_obj.sender else "unknown"
            self.group_id = event.get_group_id() if hasattr(event, "get_group_id") else event.unified_msg_origin
            self.text_content = self._extract_text(event)
            self.images = self._extract_images(event)
        else:
            # 用于从字典恢复
            self.sender_name = "用户"
            self.sender_id = "unknown"
            self.group_id = ""
            self.text_content = ""
            self.images = []

        self.has_image = len(self.images) > 0
        self.image_captions = []  # 存储图片描述

    def to_dict(self) -> dict:
        """将消息对象转换为可序列化为 JSON 的字典"""
        return {
            "message_type": self.message_type,
            "timestamp": self.timestamp.isoformat(),
            "sender_name": self.sender_name,
            "sender_id": self.sender_id,
            "group_id": self.group_id,
            "text_content": self.text_content,
            "has_image": self.has_image,
            "image_captions": self.image_captions,
            # 图片信息简化为URL或路径，便于恢复
            "image_urls": [getattr(img, "url", None) or getattr(img, "file", None) for img in self.images]
        }

    @classmethod
    def from_dict(cls, data: dict):
        """从字典创建 GroupMessage 对象（简化版）"""
        from astrbot.api.platform import AstrBotMessage, PlatformMetadata

        # 创建一个满足类型检查的最小化 mock event
        mock_message_obj = AstrBotMessage() # 直接实例化
        mock_platform_meta = PlatformMetadata(name="mock", description="mock platform")
        event = AstrMessageEvent(message_str="", message_obj=mock_message_obj, platform_meta=mock_platform_meta, session_id="")

        instance = cls(event, data["message_type"])

        # 恢复属性
        instance.timestamp = datetime.datetime.fromisoformat(data["timestamp"])
        instance.sender_name = data.get("sender_name", "用户")
        instance.sender_id = data.get("sender_id", "unknown")
        instance.group_id = data.get("group_id")
        instance.text_content = data.get("text_content", "")
        instance.has_image = data.get("has_image", False)
        instance.image_captions = data.get("image_captions", [])
        
        # 从 URL 重建简化的 Image 对象
        instance.images = [Image.fromURL(url=url) for url in data.get("image_urls", []) if url]
        
        return instance

    def _extract_text(self, event: AstrMessageEvent) -> str:
        """提取消息中的文本内容"""
        text = ""
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, Plain):
                    text += comp.text
                elif isinstance(comp, At):
                    text += f"@{comp.qq}"
        return text.strip()

    def _extract_images(self, event: AstrMessageEvent) -> list:
        """提取消息中的图片"""
        images = []
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, Image):
                    images.append(comp)
        return images



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

    def __init__(self, context: Context, config: AstrBotConfig):
        self.context = context
        self.config = config
        logger.info("上下文增强器v2.0已初始化")

        # 初始化工具类
        self._initialize_utils()

        # 群聊消息缓存 - 每个群独立存储
        self.group_messages = {}  # group_id -> deque of GroupMessage
        self.group_last_activity = {}  # group_id -> last_activity_time (用于清理不活跃群组)
        self.last_cleanup_time = time.time()

        # 加载持久化的上下文
        self.data_dir = os.path.join(
            StarTools.get_data_dir(), "astrbot_plugin_context_enhancer"
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.cache_path = os.path.join(self.data_dir, "context_cache.json")  # Changed from .pkl
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.group_messages = self._load_group_messages_from_dict(data)
                logger.info(f"成功从 {self.cache_path} 加载上下文缓存。")
            except Exception as e:
                logger.error(f"加载上下文缓存失败: {e}")

        # 显示当前配置
        logger.info(
            f"上下文增强器配置 - 聊天记录: {self.config.get('最近聊天记录数量', 15)}, "
            f"机器人回复: {self.config.get('机器人回复数量', 5)}, "
            f"最大图片数: {self.config.get('上下文图片最大数量', 4)}"
        )

    def terminate(self):
        """插件终止时，持久化上下文"""
        try:
            # 将 GroupMessage 对象转换为可序列化的字典
            serializable_messages = {}
            for group_id, messages in self.group_messages.items():
                serializable_messages[group_id] = [msg.to_dict() for msg in messages]

            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(serializable_messages, f, ensure_ascii=False, indent=4)
            logger.info(f"上下文缓存已成功保存到 {self.cache_path}")
        except Exception as e:
            logger.error(f"保存上下文缓存失败: {e}")

    def _initialize_utils(self):
        """初始化工具模块"""
        try:
            if ImageCaptionUtils is not None:
                self.image_caption_utils = ImageCaptionUtils(
                    self.context, self.context.get_config()
                )
                logger.debug("ImageCaptionUtils 初始化成功")
            else:
                self.image_caption_utils = None
                logger.warning("ImageCaptionUtils 不可用，将使用基础图片处理")

            if MessageUtils is not None:
                self.message_utils = MessageUtils(self.context.get_config(), self.context)
                logger.debug("MessageUtils 初始化成功")
            else:
                self.message_utils = None
                logger.warning("MessageUtils 不可用，将使用基础消息格式化")
        except Exception as e:
            logger.error(f"工具类初始化失败: {e}")
            self.image_caption_utils = None
            self.message_utils = None

    def _load_group_messages_from_dict(
        self, data: Dict[str, list]
    ) -> Dict[str, deque]:
        """从字典加载群组消息"""
        group_messages = {}
        max_len_multiplier = 2  # 与 _get_group_buffer 保持一致

        # 计算 maxlen
        base_max_len = self.config.get(
            "最近聊天记录数量", 15
        ) + self.config.get("机器人回复数量", 5)
        max_len = base_max_len * max_len_multiplier

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

    def _get_group_buffer(self, group_id: str) -> deque:
        """获取群聊的消息缓冲区，并管理内存"""
        current_dt = datetime.datetime.now()

        # 更新活动时间
        self.group_last_activity[group_id] = current_dt

        # 基于时间的缓存清理
        now = time.time()
        cleanup_interval = self.config.get("cleanup_interval_seconds", 600)
        if now - self.last_cleanup_time > cleanup_interval:
            self._cleanup_inactive_groups(current_dt)
            self.last_cleanup_time = now

        if group_id not in self.group_messages:
            # 优化 maxlen 计算逻辑，使其与实际上下文使用的配置项关联
            # 乘以 2 是为了提供一个缓冲区，避免在消息快速增长时 deque 频繁丢弃旧消息
            max_len = (
                self.config.get("最近聊天记录数量", 15)
                + self.config.get("机器人回复数量", 5)
            ) * 2
            self.group_messages[group_id] = deque(maxlen=max_len)
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
            return bool(bot_id and sender_id and str(sender_id) == str(bot_id))
        except (AttributeError, KeyError) as e:
            logger.debug(f"检查机器人消息时出错（可能是不支持的事件类型或数据结构）: {e}")
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
        """判断消息是否会触发LLM回复（优化版）"""
        # 1. 检查唤醒状态 (最高效)
        if getattr(event, "is_wake", False) or getattr(
            event, "is_at_or_wake_command", False
        ):
            return True

        # 2. 检查@机器人
        if self._is_at_triggered(event):
            return True

        # 3. 检查命令前缀
        if self._is_keyword_triggered(event):
            return True

        return False

    def _is_at_triggered(self, event: AstrMessageEvent) -> bool:
        """检查消息是否通过@机器人触发"""
        if event.message_obj and event.message_obj.message:
            bot_id = event.get_self_id()
            for comp in event.message_obj.message:
                if isinstance(comp, At) and (
                    str(comp.qq) == str(bot_id) or comp.qq == "all"
                ):
                    return True
        return False

    def _is_keyword_triggered(self, event: AstrMessageEvent) -> bool:
        """检查消息是否通过命令前缀触发"""
        message_text = (event.message_str or "").lower().strip()
        if not message_text:
            return False

        return any(
            message_text.startswith(prefix)
            for prefix in ContextConstants.COMMAND_PREFIXES
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

            # 【重构】从 buffer 提取消息和图片
            extracted_data = self._extract_messages_for_context(buffer)

            # 格式化 prompt
            enhanced_prompt = self._format_prompt_from_messages(
                original_prompt=request.prompt,
                event=event,
                recent_chats=extracted_data["recent_chats"],
                bot_replies=extracted_data["bot_replies"],
            )

            if enhanced_prompt and enhanced_prompt != request.prompt:
                request.prompt = enhanced_prompt
                logger.debug(f"上下文增强完成，新prompt长度: {len(enhanced_prompt)}")

            # 根据配置截取最终的图片 URL 列表
            max_images = self.config.get("上下文图片最大数量", 4)
            final_image_urls = list(dict.fromkeys(extracted_data["image_urls"]))[
                -max_images:
            ]

            if final_image_urls:
                if not request.image_urls:
                    request.image_urls = []
                # 合并并去重
                request.image_urls = list(
                    dict.fromkeys(final_image_urls + request.image_urls)
                )
                logger.debug(f"上下文中新增了 {len(final_image_urls)} 张图片")

        except Exception as e:
            logger.error(f"上下文增强时发生错误: {e}")

    def _extract_messages_for_context(self, buffer: deque) -> dict:
        """从消息缓冲区中提取和筛选数据"""
        recent_chats = []
        bot_replies = []
        image_urls = []

        # 读取配置
        max_chats = self.config.get("最近聊天记录数量", 15)
        max_bot_replies = self.config.get("机器人回复数量", 5)

        # 从后向前遍历 buffer 来收集所需数量的消息
        for msg in reversed(buffer):
            if (
                len(recent_chats) < max_chats
                and msg.message_type
                in [
                    ContextMessageType.NORMAL_CHAT,
                    ContextMessageType.LLM_TRIGGERED,
                    ContextMessageType.IMAGE_MESSAGE,
                ]
            ):
                text_part = f"{msg.sender_name}: {msg.text_content}"
                caption_part = ""
                if msg.image_captions:
                    simple_captions = [
                        c.split(": ", 1)[-1] for c in msg.image_captions
                    ]
                    caption_part = f" [图片: {'; '.join(simple_captions)}]"

                if msg.text_content or caption_part:
                    recent_chats.insert(0, f"{text_part}{caption_part}")

                if msg.has_image:
                    for img in msg.images:
                        image_url = getattr(img, "url", None) or getattr(
                            img, "file", None
                        )
                        if image_url:
                            image_urls.insert(0, image_url)

            elif (
                len(bot_replies) < max_bot_replies
                and msg.message_type == ContextMessageType.BOT_REPLY
            ):
                bot_replies.insert(0, f"你回复了: {msg.text_content}")

            # 如果两类消息都已收集足够，则提前结束循环
            if len(recent_chats) >= max_chats and len(bot_replies) >= max_bot_replies:
                break

        return {
            "recent_chats": recent_chats,
            "bot_replies": bot_replies,
            "image_urls": image_urls,
        }

    def _format_prompt_from_messages(
        self,
        original_prompt: str,
        event: AstrMessageEvent,
        recent_chats: list,
        bot_replies: list,
    ) -> str:
        """将提取出的数据格式化为最终的 prompt 字符串"""
        context_parts = [ContextConstants.PROMPT_HEADER]

        context_parts.extend(self._format_recent_chats_section(recent_chats))
        context_parts.extend(self._format_bot_replies_section(bot_replies))
        context_parts.append(self._format_situation_section(original_prompt, event))
        context_parts.append(ContextConstants.PROMPT_FOOTER)

        return "\n".join(part for part in context_parts if part)

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

    def _format_situation_section(self, original_prompt: str, event: AstrMessageEvent) -> str:
        """格式化当前情景部分"""
        sender_id = event.get_sender_id()
        if sender_id:
            sender_name = event.get_sender_name() or "用户"
            return ContextConstants.USER_TRIGGER_TEMPLATE.format(
                sender_name=sender_name,
                sender_id=sender_id,
                original_prompt=original_prompt,
            )
        else:
            return ContextConstants.PROACTIVE_TRIGGER_TEMPLATE.format(
                original_prompt=original_prompt
            )

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

                # 创建机器人回复记录 - 优化：不再依赖原始event
                bot_reply = GroupMessage(event=None, message_type=ContextMessageType.BOT_REPLY)
                bot_reply.group_id = group_id
                bot_reply.text_content = response_text
                bot_reply.sender_name = "助手"
                bot_reply.sender_id = "bot"

                buffer = self._get_group_buffer(group_id)
                buffer.append(bot_reply)

                logger.debug(f"记录机器人回复: {response_text[:50]}...")

        except Exception as e:
            logger.error(f"记录机器人回复时发生错误: {e}")

    def clear_context_cache(self):
        """清空所有上下文缓存"""
        try:
            # [诊断日志] 打印清空前的缓存状态
            logger.info(f"[诊断] 清空前 group_messages 包含 {len(self.group_messages)} 个群组。")
            logger.info(f"[诊断] 清空前 group_last_activity 包含 {len(self.group_last_activity)} 个群组。")

            # 清空内存中的缓存
            self.group_messages.clear()
            self.group_last_activity.clear()
            logger.info("内存中的上下文缓存已清空。")

            # [诊断日志] 打印清空后的缓存状态
            logger.info(f"[诊断] 清空后 group_messages 包含 {len(self.group_messages)} 个群组。")
            logger.info(f"[诊断] 清空后 group_last_activity 包含 {len(self.group_last_activity)} 个群组。")

            # 删除持久化的缓存文件
            if os.path.exists(self.cache_path):
                os.remove(self.cache_path)
                logger.info(f"持久化缓存文件 {self.cache_path} 已删除。")
            
        except Exception as e:
            logger.error(f"清空上下文缓存时发生错误: {e}")

    @filter.command("reset", "new", description="清空上下文缓存")
    async def on_command(self, event: AstrMessageEvent):
        """处理 reset 和 new 命令，清空上下文缓存"""
        command = getattr(event, 'command', None)
        logger.info(f"收到命令 '{command}'，开始清空上下文缓存。")
        self.clear_context_cache()

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
