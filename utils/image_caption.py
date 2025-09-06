import asyncio
import hashlib
from typing import Optional

from astrbot.api.star import Context
from astrbot.api import AstrBotConfig, logger


class ImageCaptionUtils:
    """
    图片转述工具类
    用于调用大语言模型将图片转述为文本描述
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        """初始化图片转述工具类"""
        self.context = context
        self.config = config
        # 使用有界缓存避免内存泄漏
        self._caption_cache = {}
        self._max_cache_size = 100  # 最大缓存条目数

    def _manage_cache(self, key: str, value: str):
        """管理缓存大小，使用简单的FIFO（先进先出）策略"""
        if len(self._caption_cache) >= self._max_cache_size:
            # 删除最早加入的缓存项
            oldest_key = next(iter(self._caption_cache))
            del self._caption_cache[oldest_key]
        self._caption_cache[key] = value

    def _generate_cache_key(self, image: str) -> str:
        """生成内存效率高的缓存键，使用SHA256哈希值"""
        try:
            # 对图片内容生成SHA256哈希值，减少内存占用
            return hashlib.sha256(image.encode("utf-8")).hexdigest()
        except Exception as e:
            # 如果哈希生成失败，使用截断的原始值作为备选
            logger.debug(f"生成缓存键失败: {e}")
            return image[:64] if len(image) > 64 else image

    async def generate_image_caption(
        self,
        image: str,  # 图片的base64编码或URL
        timeout: int = 30,
    ) -> Optional[str]:
        """
        为单张图片生成文字描述

        Args:
            image: 图片的base64编码或URL
            timeout: 超时时间（秒）

        Returns:
            生成的图片描述文本，如果失败则返回None
        """
        # 生成内存效率高的缓存键
        cache_key = self._generate_cache_key(image)

        # 检查缓存
        if cache_key in self._caption_cache:
            logger.debug(f"命中图片描述缓存: {image[:50]}...")
            return self._caption_cache[cache_key]

        # 获取配置
        if not self.config or not self.context:
            logger.warning("ImageCaptionUtils 未正确初始化")
            return None

        # 检查是否已启用图片转述
        image_processing_config = self.config.get("image_processing", {})
        if not image_processing_config.get("use_image_caption", False):
            return None

        provider_id = image_processing_config.get("image_caption_provider_id", "")
        # 获取提供商
        if provider_id == "":
            provider = self.context.get_using_provider()
        else:
            provider = self.context.get_provider_by_id(provider_id)

        if not provider:
            logger.warning(f"无法找到提供商: {provider_id if provider_id else '默认'}")
            return None

        try:
            # 使用asyncio.wait_for添加超时控制
            llm_response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=image_processing_config.get(
                        "image_caption_prompt", "请直接简短描述这张图片"
                    ),
                    contexts=[],
                    image_urls=[image],  # 图片链接，支持路径和网络链接
                    system_prompt="",  # 系统提示，可以不传
                ),
                timeout=timeout,
            )
            caption = llm_response.completion_text

            # 缓存结果
            if caption:
                self._manage_cache(cache_key, caption)
                logger.debug(f"缓存图片描述: hash({cache_key[:8]}...) -> {caption}")

            return caption
        except asyncio.TimeoutError:
            logger.warning(f"图片转述超时，超过了{timeout}秒")
            return None
        except Exception as e:
            logger.error(f"图片转述失败: {e}")
            return None
