import asyncio
import base64
import hashlib
import aiohttp
from typing import Optional, Union
from collections import OrderedDict

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
        self.session = aiohttp.ClientSession()
        self._max_cache_size = 100
        self._caption_cache = OrderedDict()

    async def close(self):
        """关闭 aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()

    def _manage_lru_cache(self, key: str, value: Optional[str] = None) -> Optional[str]:
        """手动实现 LRU 缓存逻辑"""
        if key in self._caption_cache:
            # 将已存在的键移到末尾（标记为最近使用）
            self._caption_cache.move_to_end(key)
            return self._caption_cache[key]
        
        if value is not None:
            # 添加新项
            self._caption_cache[key] = value
            # 如果超出大小，则移除最久未使用的项
            if len(self._caption_cache) > self._max_cache_size:
                self._caption_cache.popitem(last=False)
        
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

    def _get_image_mime_type(self, image_bytes: bytes) -> str:
        """通过检查文件的魔术数字来检测常见的MIME类型"""
        if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        elif image_bytes.startswith(b'GIF87a') or image_bytes.startswith(b'GIF89a'):
            return 'image/gif'
        elif image_bytes.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        elif image_bytes.startswith(b'RIFF') and image_bytes[8:12] == b'WEBP':
            return 'image/webp'
        elif image_bytes.startswith(b'BM'):
            return 'image/bmp'
        # 如果无法识别，则默认为 jpeg
        return 'image/jpeg'

    async def generate_image_caption(
        self,
        image: Union[str, bytes],
        timeout: int = 30,
        provider_id: Optional[str] = None,
        custom_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """为单张图片生成文字描述，并使用基于内容的LRU缓存。"""
        image_bytes: Optional[bytes] = None
        
        # 1. 获取图片字节流
        if isinstance(image, bytes):
            image_bytes = image
        elif isinstance(image, str) and image.startswith(('http://', 'https://')):
            try:
                # 明确创建 ClientTimeout 对象
                timeout_obj = aiohttp.ClientTimeout(total=timeout)
                async with self.session.get(image, timeout=timeout_obj) as response:
                    response.raise_for_status()
                    image_bytes = await response.read()
            except Exception as e:
                logger.error("下载图片失败: %s, 错误: %s", image, e)
                return None
        else:
            logger.error("无效的图片输入源: %s", type(image))
            return None

        if not image_bytes:
            return None

        # 2. 生成缓存键并检查缓存
        cache_key = hashlib.sha256(image_bytes).hexdigest()
        cached_caption = self._manage_lru_cache(cache_key)
        if cached_caption:
            logger.debug("命中图片描述缓存: hash(%s...)", cache_key[:8])
            return cached_caption

        # 3. 如果缓存未命中，则调用LLM
        provider = self._get_llm_provider(provider_id)
        if not provider:
            logger.warning("无法获取任何可用的LLM提供商")
            return None

        prompt = custom_prompt or self.config.get("image_caption_prompt", "请直接简短描述这张图片")
        mime_type = self._get_image_mime_type(image_bytes)
        image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"

        try:
            llm_response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, image_urls=[image_url]),
                timeout=timeout
            )
            caption = llm_response.completion_text
            
            # 4. 缓存新结果
            if caption:
                self._manage_lru_cache(cache_key, caption)
            
            return caption
        except asyncio.TimeoutError:
            logger.warning("图片转述超时，超过了%d秒", timeout)
            return None
        except Exception as e:
            logger.error("图片转述LLM请求失败: %s", e)
            return None
