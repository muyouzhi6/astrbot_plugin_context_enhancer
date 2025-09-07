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
        self.component_handlers = {
            "Plain": self._handle_text_component,
            "Image": self._handle_image_component,
            "Face": self._handle_face_component,
            "At": self._handle_at_component,
            "AtAll": self._handle_at_all_component,
            "Reply": self._handle_reply_component,
            "Record": lambda i: "[语音]",
            "Video": lambda i: "[视频]",
        }

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

        tasks = []
        results = []
        # Keep track of where coroutine results should be inserted
        placeholders = []

        for i, component in enumerate(message_list):
            handler = self.component_handlers.get(
                type(component).__name__, self._handle_unknown_component
            )
            result = handler(component)
            
            if asyncio.iscoroutine(result):
                tasks.append(result)
                # Mark the position and type of the coroutine
                placeholders.append((i, type(component).__name__))
                results.append(None)
            else:
                results.append(result)

        if tasks:
            coroutine_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, (original_index, component_type) in enumerate(placeholders):
                result = coroutine_results[i]
                if component_type == "Image":
                    results[original_index] = self._format_image_caption(result)
                elif component_type == "Reply":
                     if isinstance(result, Exception):
                        results[original_index] = "[回复处理异常]"
                     else:
                        results[original_index] = result

        # Replace any remaining Nones (e.g., from failed reply handling)
        results = [res if res is not None else "" for res in results]

        return "".join(str(r) for r in results)

    def _handle_text_component(self, component: Plain) -> str:
        return component.text

    def _handle_image_component(self, component: Image):
        image = component.file if component.file else component.url
        if image:
            return self.image_caption_utils.generate_image_caption(image)
        return "[图片]"

    def _format_image_caption(self, caption_result) -> str:
        if isinstance(caption_result, Exception):
            logger.debug(f"单个图片描述生成失败: {caption_result}")
            return "[图片]"
        elif caption_result:
            return f"[图片: {caption_result}]"
        else:
            return "[图片]"

    def _handle_face_component(self, component: Face) -> str:
        return f"[表情:{component.id}]"

    def _handle_at_component(self, component: At) -> str:
        return f"[At:{component.qq}{f'({component.name})' if component.name else ''}]"
    
    def _handle_at_all_component(self, component: AtAll) -> str:
        return "[At:全体成员]"

    async def _handle_reply_component(
        self, component: Reply, max_depth: int = 3, current_depth: int = 0
    ) -> str:
        if component.chain:
            sender_info = (
                f"{component.sender_nickname}({component.sender_id})"
                if component.sender_nickname
                else f"{component.sender_id}"
            )
            reply_content = await self.outline_message_list(
                component.chain, max_depth, current_depth + 1
            )
            return f"[回复({sender_info}: {reply_content})]"
        elif component.message_str:
            sender_info = (
                f"{component.sender_nickname}({component.sender_id})"
                if component.sender_nickname
                else f"{component.sender_id}"
            )
            return f"[回复({sender_info}: {component.message_str})]"
        else:
            return "[回复消息]"

    def _handle_unknown_component(self, component: BaseMessageComponent) -> str:
        if hasattr(component, "type"):
            return f"[{component.type}]"
        else:
            return f"[{type(component).__name__}]"
