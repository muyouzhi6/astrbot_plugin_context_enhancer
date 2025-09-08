import asyncio
import base64
import aiohttp
import hashlib
import aiofiles
from collections import OrderedDict
from typing import Optional, Union

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
        self.session: Optional[aiohttp.ClientSession] = None
        self._caption_cache: OrderedDict = OrderedDict()
        self._cache_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取 aiohttp ClientSession，如果不存在或已关闭则创建新的"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """关闭 aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()

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
        """为单张图片生成文字描述，并使用基于内容的共享LRU缓存。"""
        image_bytes: Optional[bytes] = None

        # 1. 获取图片字节流
        if isinstance(image, bytes):
            image_bytes = image
        elif isinstance(image, str):
            if image.startswith(('http://', 'https://')):
                try:
                    session = await self._get_session()
                    timeout_obj = aiohttp.ClientTimeout(total=timeout)
                    async with session.get(image, timeout=timeout_obj) as response:
                        response.raise_for_status()
                        image_bytes = await response.read()
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.error(f"下载图片失败: {image}, 错误: {e}")
                    return None
            else:
                try:
                    # 假定为本地文件路径
                    async with aiofiles.open(image, "rb") as f:
                        image_bytes = await f.read()
                except FileNotFoundError:
                    logger.error(f"图片文件未找到: {image}")
                    return None
                except (IOError, PermissionError) as e:
                    logger.error(f"读取图片文件时发生权限或IO错误: {image}, 错误: {e}")
                    return None
        else:
            logger.error(f"无效的图片输入源: {type(image)}")
            return None

        if not image_bytes:
            return None

        # 2. 计算哈希并检查缓存
        image_hash = self._get_image_hash(image_bytes)
        provider = self._get_llm_provider(provider_id)
        model_id = getattr(provider, 'model_id', 'default_model')
        # 缓存键包含图片内容哈希、自定义提示、提供商ID和模型ID，以确保结果的绝对唯一性
        cache_key = (image_hash, custom_prompt, provider_id, model_id)

        async with self._cache_lock:
            if cache_key in self._caption_cache:
                # 将命中的项目移到末尾（LRU行为）
                self._caption_cache.move_to_end(cache_key)
                return self._caption_cache[cache_key]

        # 3. 如果缓存未命中，则调用LLM
        if not provider:
            logger.warning("无法获取任何可用的LLM提供商")
            return None

        prompt = custom_prompt or self.config.get("image_caption_prompt", "请直接简短描述这张图片")

        mime_type = self._get_image_mime_type(image_bytes)
        if mime_type is None:
            logger.debug("无法识别图片MIME类型，将默认使用 'image/jpeg'")
            mime_type = 'image/jpeg'

        image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"

        try:
            llm_response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, image_urls=[image_url]),
                timeout=timeout
            )
            caption = llm_response.completion_text

            # 4. 将新结果存入缓存
            if caption:
                async with self._cache_lock:
                    if len(self._caption_cache) >= CACHE_MAX_SIZE:
                        # 移除最久未使用的项目
                        self._caption_cache.popitem(last=False)
                    self._caption_cache[cache_key] = caption
            
            return caption
        except asyncio.TimeoutError:
            logger.warning(f"图片转述超时，超过了{timeout}秒")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"图片转述LLM请求失败 (网络错误): {e}")
            return None
