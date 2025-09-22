from typing import List, Optional, Any
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

    def __init__(self, config: AstrBotConfig, context: Context, image_caption_utils: Optional[ImageCaptionUtils]):
        """初始化消息处理工具类"""
        self.config = config
        self.context = context
        self.image_caption_utils = image_caption_utils
        self.component_handlers = {
            "Plain": self._handle_text_component,
            "Image": self._handle_image_component,
            "Face": self._handle_face_component,
            "At": self._handle_at_component,
            "AtAll": self._handle_at_all_component,
            "Reply": self._handle_reply_component,
            "Record": self._handle_record_component,
            "Video": self._handle_video_component,
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
        if current_depth >= max_depth:
            return "[回复内容过深]"

        results: List[Optional[Any]] = [None] * len(message_list)
        tasks = []
        task_indices = []

        for i, component in enumerate(message_list):
            handler = self.component_handlers.get(
                type(component).__name__, self._handle_unknown_component
            )
            
            if asyncio.iscoroutinefunction(handler):
                tasks.append(handler(component, max_depth=max_depth, current_depth=current_depth))
                task_indices.append(i)
            else:
                results[i] = handler(component)

        if tasks:
            task_results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in zip(task_indices, task_results):
                if isinstance(result, Exception):
                    logger.debug(f"处理组件 {type(message_list[i]).__name__} 时出错: {result}")
                    results[i] = f"[{type(message_list[i]).__name__}处理异常]"
                else:
                    results[i] = result
        
        return "".join(str(res) for res in results if res is not None)

    def _handle_text_component(self, component: Plain) -> str:
        return component.text

    async def _handle_image_component(self, component: Image, **kwargs) -> str:
        image_url = component.file or component.url
        if not image_url:
            return "[图片]"
        
        if not self.image_caption_utils:
            return "[图片]"

        try:
            caption = await self.image_caption_utils.generate_image_caption(image_url)
            return f"[图片: {caption}]" if caption else "[图片]"
        except FileNotFoundError:
            logger.debug(f"图片文件未找到: {image_url}")
            return "[图片: 文件未找到]"
        except (IOError, PermissionError) as e:
            logger.debug(f"读取图片文件时发生IO错误或权限错误: {e}")
            return "[图片: 读取失败]"
        except Exception as e:
            logger.debug(f"生成图片描述时发生未知错误: {e}")
            return "[图片: 处理异常]"

    def _handle_face_component(self, component: Face) -> str:
        return f"[表情: id={component.id}, name={component.name or '未知'}]"

    def _handle_at_component(self, component: At) -> str:
        return f"@{component.name or component.qq}"
    
    def _handle_at_all_component(self, component: AtAll) -> str:
        return "[@全体成员]"

    async def _handle_reply_component(self, component: Reply, **kwargs) -> str:
        max_depth = kwargs.get("max_depth", 3)
        current_depth = kwargs.get("current_depth", 0)
        
        sender_info = f"{component.sender_nickname or '未知用户'}({component.sender_id or 'unknown'})"
        reply_id = getattr(component, 'id', 'unknown_id')
        base_str = f"[回复: id={reply_id}, user={sender_info}, content="

        if component.chain:
            reply_content = await self.outline_message_list(
                component.chain, max_depth, current_depth + 1
            )
            return f"{base_str}{reply_content})]"
        elif component.message_str:
            return f"{base_str}{component.message_str})]"
        else:
            return f"{base_str}一条消息)]"
            
    def _handle_record_component(self, component: Record) -> str:
        file_info = getattr(component, 'file', '未知文件')
        return f"[语音: file={file_info}]"

    def _handle_video_component(self, component: Video) -> str:
        file_info = getattr(component, 'file', '未知文件')
        return f"[视频: file={file_info}]"

    def _handle_unknown_component(self, component: BaseMessageComponent) -> str:
        if hasattr(component, "type"):
            return f"[{component.type}]"
        else:
            return f"[{type(component).__name__}]"
