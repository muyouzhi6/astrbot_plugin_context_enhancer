import asyncio
import base64
import hashlib
from typing import Optional, Union

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

    def _generate_cache_key(self, image: Union[str, bytes]) -> Optional[str]:
        """
        为图片内容生成SHA256哈希值作为缓存键。

        Args:
            image: 图片的base64编码字符串或字节数据。

        Returns:
            生成的缓存键（字符串），如果输入类型不支持则返回None。
        """
        if isinstance(image, str):
            return hashlib.sha256(image.encode("utf-8")).hexdigest()
        elif isinstance(image, bytes):
            return hashlib.sha256(image).hexdigest()
        else:
            logger.error(f"不支持的缓存键生成类型: {type(image)}")
            return None

    def _get_llm_provider(self, provider_id: Optional[str] = None):
        """根据 provider_id 或全局配置获取LLM提供商"""
        # 1. 尝试从函数参数获取
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider:
                return provider
            logger.warning(f"无法找到指定的提供商: {provider_id}，将尝试其他选项")

        # 2. 如果上一步失败，尝试从全局配置获取
        image_processing_config = self.config.get("image_processing", {})
        global_provider_id = image_processing_config.get("image_caption_provider_id")
        if global_provider_id:
            provider = self.context.get_provider_by_id(global_provider_id)
            if provider:
                return provider
            logger.warning(f"无法找到全局配置的提供商: {global_provider_id}，将使用默认提供商")

        # 3. 如果仍然失败，使用默认提供商
        return self.context.get_using_provider()

    async def generate_image_caption(
        self,
        image: Union[str, bytes],  # 图片的base64编码或URL
        timeout: int = 30,
        provider_id: Optional[str] = None,  # 可选的提供商ID
        custom_prompt: Optional[str] = None,  # 自定义提示词
    ) -> Optional[str]:
        """
        为单张图片生成文字描述

        Args:
            image: 图片的base64编码或URL
            timeout: 超时时间（秒）
            provider_id: 可选的提供商ID，如果为None则使用默认提供商
            custom_prompt: 自定义提示词，如果为None则使用默认提示词

        Returns:
            生成的图片描述文本，如果失败则返回None
        """
        # 生成内存效率高的缓存键
        cache_key = self._generate_cache_key(image)
        if not cache_key:
            return None

        # 检查缓存
        if cache_key in self._caption_cache:
            logger.debug(f"命中图片描述缓存: {image[:50]}...")
            return self._caption_cache[cache_key]

        # 获取配置
        if not self.config or not self.context:
            logger.warning("ImageCaptionUtils 未正确初始化")
            return None

        # 获取LLM提供商
        provider = self._get_llm_provider(provider_id)

        if not provider:
            logger.warning("无法获取任何可用的LLM提供商")
            return None

        # 确定使用的提示词
        if custom_prompt:
            prompt = custom_prompt
        else:
            # 尝试从全局配置获取
            image_processing_config = self.config.get("image_processing", {})
            prompt = image_processing_config.get(
                "image_caption_prompt", "请直接简短描述这张图片"
            )

        image_url: str = image if isinstance(image, str) else f"data:image/jpeg;base64,{base64.b64encode(image).decode()}"

        try:
            # 使用asyncio.wait_for添加超时控制
            llm_response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    image_urls=[image_url],  # 图片链接，支持路径和网络链接
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
