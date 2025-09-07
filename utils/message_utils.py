from datetime import datetime
from typing import List
import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context
from astrbot.api.message_components import (
    Plain,
    At,
    AtAll,
    Image,
    Face,
    Reply,
    Record,
    Video,
    BaseMessageComponent,
)

from .image_caption import ImageCaptionUtils


class MessageUtils:
    """
    消息处理工具类
    """

    def __init__(self, config: AstrBotConfig, context: Context):
        """初始化消息处理工具类"""
        self.config = config
        self.context = context
        self.image_caption_utils = ImageCaptionUtils(context, config)

    async def format_history_for_llm(
        self, history_messages: List, max_messages: int = 20
    ) -> str:
        """
        将历史消息列表格式化为适合输入给大模型的文本格式

        Args:
            history_messages: 历史消息列表
            max_messages: 最大消息数量，默认20条

        Returns:
            格式化后的历史消息文本
        """
        if not history_messages:
            return ""

        # 限制消息数量
        if len(history_messages) > max_messages:
            history_messages = history_messages[-max_messages:]

        formatted_text = ""
        divider = "\n" + "---" + "\n"

        for idx, msg in enumerate(history_messages):
            # 获取发送者信息
            sender_name = "未知用户"
            sender_id = "unknown"
            if hasattr(msg, "sender") and msg.sender:
                sender_name = msg.sender.nickname or "未知用户"
                sender_id = msg.sender.user_id or "unknown"

            # 获取发送时间
            send_time = "未知时间"
            if hasattr(msg, "timestamp") and msg.timestamp and msg.timestamp > 0:
                try:
                    time_obj = datetime.fromtimestamp(msg.timestamp)
                    send_time = time_obj.strftime("%Y-%m-%d %H:%M:%S")
                except (OSError, ValueError, OverflowError) as e:
                    logger.debug(f"时间戳转换失败: {e}")
                    pass

            # 获取消息内容 (异步调用)
            message_content = (
                await self.outline_message_list(msg.message)
                if hasattr(msg, "message") and msg.message
                else ""
            )

            # 格式化该条消息
            message_text = f"发送者: {sender_name} (ID: {sender_id})\n"
            message_text += f"时间: {send_time}\n"
            message_text += f"内容: {message_content}"

            # 添加到结果中
            formatted_text += message_text

            # 除了最后一条消息，每条消息后添加分割线
            if idx < len(history_messages) - 1:
                formatted_text += divider

        return formatted_text

    async def outline_message_list(
        self,
        message_list: List[BaseMessageComponent],
        max_depth: int = 3,
        current_depth: int = 0,
    ) -> str:
        """
        获取消息概要。

        除了文本消息外，其他消息类型会被转换为对应的占位符，同时保留尽可能多的信息。
        图片会尝试进行转述。

        Astrbot中get_message_outline()方法的扩展版本，支持更多消息类型和更详细的内容。

        Args:
            message_list: 消息段列表
            max_depth: 最大递归深度，防止无限递归
            current_depth: 当前递归深度

        Returns:
            消息概要文本
        """
        # 防止无限递归
        if current_depth >= max_depth:
            return "[回复内容过深]"

        outline = ""

        # 收集所有图片以便并发处理
        image_tasks = []
        image_indices = []

        for idx, i in enumerate(message_list):
            if isinstance(i, Plain):
                outline += i.text
            elif isinstance(i, Image):
                # 收集图片任务，稍后并发处理
                image = i.file if i.file else i.url
                if image:
                    image_tasks.append(
                        self.image_caption_utils.generate_image_caption(image)
                    )
                    image_indices.append(idx)
                    outline += f"[图片_PLACEHOLDER_{len(image_tasks) - 1}]"
                else:
                    outline += "[图片]"
            elif isinstance(i, Face):
                outline += f"[表情:{i.id}]"
            elif isinstance(i, At):
                outline += f"[At:{i.qq}{f'({i.name})' if i.name else ''}]"
            elif isinstance(i, AtAll):
                outline += "[At:全体成员]"
            elif isinstance(i, Reply):
                if i.chain:
                    sender_info = (
                        f"{i.sender_nickname}({i.sender_id})"
                        if i.sender_nickname
                        else f"{i.sender_id}"
                    )
                    # 异步调用，传递递归深度
                    reply_content = await self.outline_message_list(
                        i.chain, max_depth, current_depth + 1
                    )
                    outline += f"[回复({sender_info}: {reply_content})]"
                elif i.message_str:
                    sender_info = (
                        f"{i.sender_nickname}({i.sender_id})"
                        if i.sender_nickname
                        else f"{i.sender_id}"
                    )
                    outline += f"[回复({sender_info}: {i.message_str})]"
                else:
                    outline += "[回复消息]"
            elif isinstance(i, Record):
                outline += "[语音]"
            elif isinstance(i, Video):
                outline += "[视频]"
            else:
                # 对于其他所有类型，使用一个通用的占位符
                # 这样可以避免因未来新增消息类型而导致解析失败
                # i.type 属性通常是消息类型的字符串表示
                if hasattr(i, "type"):
                    outline += f"[{i.type}]"
                else:
                    outline += f"[{type(i).__name__}]"

        # 并发处理所有图片描述任务
        if image_tasks:
            try:
                # 使用asyncio.gather并发执行所有图片描述任务
                image_captions = await asyncio.gather(
                    *image_tasks, return_exceptions=True
                )

                # 替换占位符
                for i, caption in enumerate(image_captions):
                    placeholder = f"[图片_PLACEHOLDER_{i}]"
                    if isinstance(caption, Exception):
                        # 如果某个图片处理失败，使用默认占位符
                        logger.debug(f"单个图片描述生成失败: {caption}")
                        replacement = "[图片]"
                    elif caption:
                        replacement = f"[图片: {caption}]"
                    else:
                        replacement = "[图片]"

                    outline = outline.replace(placeholder, replacement)
            except asyncio.CancelledError:
                logger.warning("图片描述任务被取消")
                for i in range(len(image_tasks)):
                    placeholder = f"[图片_PLACEHOLDER_{i}]"
                    outline = outline.replace(placeholder, "[图片(处理被取消)]")
            except Exception as e:
                # 如果 gather 或结果处理过程出现意外错误
                logger.error(f"图片描述并发处理流程发生意外错误: {e}")
                for i in range(len(image_tasks)):
                    placeholder = f"[图片_PLACEHOLDER_{i}]"
                    outline = outline.replace(placeholder, "[图片(处理异常)]")

        return outline
