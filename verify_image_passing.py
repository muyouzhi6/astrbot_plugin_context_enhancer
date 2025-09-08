import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from typing import cast

# 模拟 AstrBot 的核心类和组件
from collections import deque
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.platform import MessageType

# 导入被测试的插件类
from main import ContextEnhancerV2, GroupMessage, ContextMessageType
from utils.image_caption import ImageCaptionUtils

class MockMessageComponent:
    def __init__(self, type, data):
        self.type = type
        self.data = data
    
    @property
    def url(self):
        return self.data.get("url")
    
    @property
    def file(self):
        return self.data.get("file")

class MockImage(MockMessageComponent):
    def __init__(self, url):
        super().__init__("image", {"url": url})

class MockPlain(MockMessageComponent):
    def __init__(self, text):
        super().__init__("text", {"text": text})

class MockSender:
    def __init__(self, nickname, user_id):
        self.nickname = nickname
        self.user_id = user_id

class MockMessageObject:
    def __init__(self, sender, message):
        self.sender = sender
        self.message = message

class MockAstrMessageEvent:
    def __init__(self, sender, message, group_id="12345"):
        self.message_obj = MockMessageObject(sender, message)
        self.unified_msg_origin = group_id

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE
    
    def get_group_id(self):
        return self.unified_msg_origin

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def get_sender_id(self):
        return self.message_obj.sender.user_id

class MockProviderRequest:
    def __init__(self, prompt):
        self.prompt = prompt
        self.image_urls = []

@patch.dict('sys.modules', {
    'astrabot': MagicMock(),
    'astrabot.api': MagicMock(),
    'astrabot.api.provider': MagicMock(),
    'astrabot.api.platform': MagicMock(),
})
class TestImagePassing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 创建 mock 对象
        self.mock_context = MagicMock()
        mock_config = MagicMock()
        
        # 模拟配置的 get 方法
        def mock_get(key, default=None):
            configs = {
                "启用群组": [],  # 对所有群生效
                "最近聊天记录数量": 15,
                "机器人回复数量": 5,
                "上下文图片最大数量": 4,
                "启用图片描述": True,
                "command_prefixes": ["/"],
            }
            return configs.get(key, default)
        
        mock_config.get.side_effect = mock_get
        self.mock_context.get_config.return_value = mock_config

        # 实例化被测试的插件
        self.plugin = ContextEnhancerV2(self.mock_context, mock_config)
        await self.plugin._async_init()

        # 模拟 utils (避免真实的网络请求)
        self.plugin.image_caption_utils = MagicMock()
        self.plugin.image_caption_utils.generate_image_caption = AsyncMock(return_value="一只猫")
        self.plugin.message_utils = None


    async def test_image_url_passing(self):
        group_id = "test_group_123"
        sender = MockSender("测试用户", "user_1")
        image_url = "http://example.com/cat.jpg"

        # 1. 模拟历史消息：发送一张图片
        image_event = MockAstrMessageEvent(sender, [MockImage(image_url)], group_id)
        image_msg = GroupMessage(
            message_type=ContextMessageType.IMAGE_MESSAGE,
            sender_id=sender.user_id,
            sender_name=sender.nickname,
            group_id=group_id,
            text_content="",
            images=[MockImage(image_url)]
        )

        # 手动生成图片描述
        await self.plugin._generate_image_captions(image_msg)
        
        # 将历史消息放入缓冲区
        buffer = self.plugin._get_group_buffer(group_id)
        buffer.append(image_msg)

        # 2. 模拟当前触发 LLM 的事件
        trigger_event = MockAstrMessageEvent(sender, [MockPlain("看这张图")], group_id)
        request = MockProviderRequest("看这张图")

        # 3. 调用核心方法
        await self.plugin.on_llm_request(
            cast("AstrMessageEvent", trigger_event), cast("ProviderRequest", request)
        )

        # 4. 断言检查
        # 检查 prompt 是否包含图片描述
        expected_context = "测试用户:  [图片: 一只猫]"
        self.assertIn(expected_context, request.prompt)
        
        # 检查 image_urls 是否包含原始图片 URL
        self.assertIn(image_url, request.image_urls)


# 使用 patch.dict 来模拟 sys.modules，避免 ModuleNotFoundError
@patch.dict('sys.modules', {
    'astrabot': MagicMock(),
    'astrabot.api': MagicMock(),
})
class TestImageCaptionCache(unittest.IsolatedAsyncioTestCase):
    
    async def asyncSetUp(self):
        """在每个测试前异步运行"""
        self.mock_context = MagicMock()
        self.mock_config = MagicMock()
        
        # 模拟LLM Provider
        self.mock_provider1 = MagicMock()
        self.mock_provider1.model_id = "gpt-4o"
        self.mock_provider1.text_chat = AsyncMock(return_value=MagicMock(completion_text="Caption from gpt-4o"))

        self.mock_provider2 = MagicMock()
        self.mock_provider2.model_id = "gpt-4-turbo"
        self.mock_provider2.text_chat = AsyncMock(return_value=MagicMock(completion_text="Caption from gpt-4-turbo"))

        def get_provider_by_id(provider_id):
            if provider_id == "provider_1":
                return self.mock_provider1
            if provider_id == "provider_2":
                return self.mock_provider2
            return None
        
        self.mock_context.get_provider_by_id.side_effect = get_provider_by_id

        # 实例化一个真实的 ImageCaptionUtils
        self.image_utils = ImageCaptionUtils(self.mock_context, self.mock_config)

    async def asyncTearDown(self):
        """在每个测试后运行，关闭 aiohttp session"""
        await self.image_utils.close()

    async def test_cache_differentiates_by_model_id(self):
        """测试缓存键是否能正确区分不同的 model_id"""
        image_bytes = b"dummy_image_bytes" # 使用虚拟的图片内容

        # 第一次调用，使用 provider_1 (gpt-4o)
        caption1 = await self.image_utils.generate_image_caption(image_bytes, provider_id="provider_1")
        self.assertEqual(caption1, "Caption from gpt-4o")
        self.mock_provider1.text_chat.assert_called_once()
        self.mock_provider2.text_chat.assert_not_called()

        # 第二次调用，使用相同的图片，但 provider_2 (gpt-4-turbo)
        # 由于 model_id 不同，缓存不应命中，应触发第二次真实调用
        caption2 = await self.image_utils.generate_image_caption(image_bytes, provider_id="provider_2")
        self.assertEqual(caption2, "Caption from gpt-4-turbo")
        self.mock_provider1.text_chat.assert_called_once() # 第一次的调用
        self.mock_provider2.text_chat.assert_called_once() # 第二次的调用

        # 第三次调用，再次使用 provider_1，这次应该命中缓存
        caption3 = await self.image_utils.generate_image_caption(image_bytes, provider_id="provider_1")
        self.assertEqual(caption3, "Caption from gpt-4o")
        # 断言 text_chat 的调用次数没有增加
        self.mock_provider1.text_chat.assert_called_once()
        self.mock_provider2.text_chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()