import asyncio
import base64
import hashlib
import aiofiles
from pathlib import Path
from collections import OrderedDict
from typing import Optional, Union
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from aiohttp import ClientError
from asyncio import TimeoutError

from astrbot.api.star import Context
from astrbot.api import AstrBotConfig, logger


# 缓存最大大小
CACHE_MAX_SIZE = 128


class ImageCaptionUtils:
    """
    图片转述工具类
    用于调用大语言模型将图片转述为文本描述
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        """初始化图片转述工具类"""
        self.context = context
        self.config = config
        self._caption_cache: OrderedDict = OrderedDict()
        self._cache_lock = asyncio.Lock()

    async def close(self):
        """关闭 aiohttp session"""
        pass

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


    def _get_image_mime_type(self, image_bytes: bytes) -> Optional[str]:
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
        # 如果无法识别，则返回 None
        return None

    @staticmethod
    def _get_image_hash(image_content: bytes) -> str:
        """计算图片内容的 SHA256 哈希值"""
        return hashlib.sha256(image_content).hexdigest()

    async def generate_image_caption(
        self,
        image: Union[str, bytes],
        timeout: int = 30,
        provider_id: Optional[str] = None,
        custom_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """
        为单张图片生成文字描述。
        借鉴 spectrecore 的实现，简化处理流程，将数据直接传递给 provider。
        """
        image_url: str
        image_content: Optional[bytes] = None

        # 1. 准备 image_url 和 image_content
        if isinstance(image, bytes):
            image_content = image
        elif isinstance(image, str):
            image_path = Path(image)
            if image.startswith(('http://', 'https://')):
                image_url = image
                # 对于URL，我们稍后在需要时再获取内容
            elif image.startswith('data:image'):
                image_url = image
                try:
                    image_content = base64.b64decode(image.split(',')[1])
                except (IndexError, ValueError):
                    logger.warning("无法解码 Base64 图片字符串，将不计算图片哈希")
            elif image_path.is_file():
                try:
                    async with aiofiles.open(image_path, 'rb') as f:
                        image_content = await f.read()
                    image = image_content  # 将 image 变量更新为 bytes
                except Exception as e:
                    logger.error(f"无法读取本地图片文件 {image_path}: {e}")
                    return f"[无法读取图片: {image_path.name}]"
            else:
                logger.warning(f"字符串图片源既不是有效的URL、Data URL，也不是本地文件路径: {image}")
                image_url = image # 保持原样，让后续逻辑处理
        else:
            logger.error(f"无效的图片输入源: {type(image)}")
            return None

        # 如果 image 已被读取为 bytes，统一处理
        if isinstance(image, bytes):
            image_content = image
            mime_type = self._get_image_mime_type(image)
            if mime_type is None:
                logger.warning("无法识别图片MIME类型，将默认使用 'image/jpeg'")
                mime_type = 'image/jpeg'
            image_url = f"data:{mime_type};base64,{base64.b64encode(image).decode()}"

        # 2. 准备缓存键 (使用图片哈希)
        image_hash = self._get_image_hash(image_content) if image_content else image_url
        provider = self._get_llm_provider(provider_id)
        model_id = getattr(provider, 'model_id', 'default_model')
        cache_key = (image_hash, custom_prompt, provider_id, model_id)

        async with self._cache_lock:
            if cache_key in self._caption_cache:
                self._caption_cache.move_to_end(cache_key)
                logger.debug(f"命中图片描述缓存 (key: {image_hash})")
                return self._caption_cache[cache_key]

        # 3. 如果缓存未命中，则调用LLM
        if not provider:
            logger.warning("无法获取任何可用的LLM提供商")
            return None

        prompt = custom_prompt or self.config.get("image_caption_prompt", "请直接简短描述这张图片")

        logger.debug(f"准备调用LLM进行图片描述...")
        logger.debug(f"  - Provider: {provider_id or '默认'}")
        logger.debug(f"  - Prompt: '{prompt}'")
        logger.debug(f"  - Image URL: '{image_url[:100]}...'")

        caption = await self._caption_image_with_provider(provider, prompt, [image_url], timeout)

        # 4. 将新结果存入缓存
        if caption:
            async with self._cache_lock:
                if len(self._caption_cache) >= CACHE_MAX_SIZE:
                    self._caption_cache.popitem(last=False)
                self._caption_cache[cache_key] = caption
                logger.debug(f"缓存图片描述 (key: {image_hash}): {caption}")

        return caption

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ClientError, TimeoutError)),
        reraise=True
    )
    async def _caption_image_with_provider(self, provider, prompt, image_urls, timeout):
        """调用LLM提供商进行图片描述，带有重试机制"""
        try:
            llm_response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, image_urls=image_urls),
                timeout=timeout
            )
            return llm_response.completion_text
        except TimeoutError as e:
            logger.warning(f"图片转述超时，超过了{timeout}秒")
            raise e
        except ClientError as e:
            logger.error(f"图片转述LLM请求失败 (网络错误): {e}")
            raise e
