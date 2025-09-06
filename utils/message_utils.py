
from datetime import datetime
from typing import List, Optional

from astrbot.api.all import *

from .image_caption import ImageCaptionUtils


class MessageUtils:
    """
    消息处理工具类
    """
    config: Optional[AstrBotConfig] = None

    @staticmethod
    def init(config: AstrBotConfig):
        MessageUtils.config = config

    @staticmethod
    async def format_history_for_llm(
        history_messages: List[AstrBotMessage], max_messages: int = 20
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
                except:
                    pass

            # 获取消息内容 (异步调用)
            message_content = (
                await MessageUtils.outline_message_list(msg.message)
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

    @staticmethod
    async def outline_message_list(
        message_list: List[BaseMessageComponent],
    ) -> str:
        """
        获取消息概要。

        除了文本消息外，其他消息类型会被转换为对应的占位符，同时保留尽可能多的信息。
        图片会尝试进行转述。

        Astrbot中get_message_outline()方法的扩展版本，支持更多消息类型和更详细的内容。

        Args:
            message_list: 消息段列表

        Returns:
            消息概要文本
        """
        outline = ""
        for i in message_list:
            if isinstance(i, Plain):
                outline += i.text
            elif isinstance(i, Image):
                try:
                    image = i.file if i.file else i.url
                    if image:
                        caption = await ImageCaptionUtils.generate_image_caption(image)
                        if caption:
                            outline += f"[图片: {caption}]"
                        else:
                            outline += f"[图片]"
                    else:
                        outline += f"[图片]"
                except Exception as e:
                    logger.error(f"处理图片消息失败: {e}")
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
                    # 异步调用
                    reply_content = await MessageUtils.outline_message_list(i.chain)
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
        return outline
