import asyncio
import unittest
from unittest.mock import MagicMock, patch
from collections import deque
import datetime
import time

# 导入被测试的插件和相关类
from main import ContextEnhancerV2, GroupMessage, ContextMessageType
from astrbot.api import logger
# 导入 verify_scenarios 中的模拟类以复用
from verify_scenarios import MockSender, MockMessage, MockPlain, MockEvent

# --- 测试用例 ---

@patch.dict('sys.modules', {
    'astrabot': MagicMock(),
    'astrabot.api': MagicMock(),
    'astrabot.api.provider': MagicMock(),
    'astrabot.api.platform': MagicMock(),
})
class TestCoreLogic(unittest.IsolatedAsyncioTestCase):
    
    async def _setup_plugin_with_config(self, config_overrides: dict):
        """辅助函数：根据指定的配置覆盖来异步设置插件实例"""
        mock_context = MagicMock()
        mock_config = MagicMock()

        base_config = {
            "duplicate_check_window_messages": 5,
            "duplicate_check_time_seconds": 30,
            "command_prefixes": ["/"], # 添加默认值以防万一
            "inactive_cleanup_days": 7, # 添加默认值
        }
        final_config = {**base_config, **config_overrides}

        def mock_get(key, default=None):
            return final_config.get(key, default)
        
        mock_config.get.side_effect = mock_get
        
        plugin = ContextEnhancerV2(mock_context, mock_config)
        await plugin._async_init() # 安全地进行异步初始化
        plugin.group_messages = {} 
        return plugin

    async def test_is_duplicate_message_with_varied_configs(self):
        """测试 _is_duplicate_message 函数在不同配置下的行为"""
        logger.info(f"\n--- Running test: {self._testMethodName} ---")

        sender = MockSender("user1", "Alice")
        now = datetime.datetime.now()
        
        existing_msg = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender.user_id, sender_name=sender.nickname,
            group_id="group_1", text_content="Hello"
        )
        existing_msg.timestamp = now - datetime.timedelta(seconds=10)
        
        new_msg = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender.user_id, sender_name=sender.nickname,
            group_id="group_1", text_content="Hello"
        )
        new_msg.timestamp = now

        logger.info("场景1: 默认配置，应视为重复")
        plugin_default = await self._setup_plugin_with_config({})
        buffers_default = await plugin_default._get_or_create_group_buffers("group_1")
        buffers_default.recent_chats.append(existing_msg)
        self.assertTrue(plugin_default._is_duplicate_message(buffers_default.recent_chats, new_msg), "在默认配置下，此消息应被视为重复")

        logger.info("场景2: 缩短去重时间，应不视为重复")
        plugin_short_time = await self._setup_plugin_with_config({"duplicate_check_time_seconds": 5})
        buffers_short_time = await plugin_short_time._get_or_create_group_buffers("group_1")
        buffers_short_time.recent_chats.append(existing_msg)
        self.assertFalse(plugin_short_time._is_duplicate_message(buffers_short_time.recent_chats, new_msg), "缩短去重时间后，此消息不应被视为重复")

        logger.info("场景3: 缩小去重窗口，应不视为重复")
        plugin_small_window = await self._setup_plugin_with_config({"duplicate_check_window_messages": 2})
        buffers_small_window = await plugin_small_window._get_or_create_group_buffers("group_1")
        
        buffers_small_window.recent_chats.append(existing_msg)
        for i in range(3):
            filler_msg = GroupMessage(
                message_type=ContextMessageType.NORMAL_CHAT, sender_id="filler", sender_name="Filler",
                group_id="group_1", text_content=f"filler {i}"
            )
            filler_msg.timestamp = now - datetime.timedelta(seconds=5 - i)
            buffers_small_window.recent_chats.append(filler_msg)

        self.assertFalse(plugin_small_window._is_duplicate_message(buffers_small_window.recent_chats, new_msg), "缩小消息窗口后，此消息不应被视为重复")
        
        logger.info("Test Passed: _is_duplicate_message 函数对配置更改的响应符合预期。")

    async def test_is_duplicate_message_scenarios(self):
        """为 _is_duplicate_message 函数测试各种核心场景"""
        logger.info(f"\n--- Running test: {self._testMethodName} ---")
        plugin = await self._setup_plugin_with_config({})
        buffers = await plugin._get_or_create_group_buffers("group_dedupe")
        buffer = buffers.recent_chats
        
        sender1 = MockSender("user1", "Alice")
        sender2 = MockSender("user2", "Bob")
        now = datetime.datetime.now()

        base_msg = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender1.user_id, sender_name=sender1.nickname,
            group_id="group_dedupe", text_content="Same content"
        )
        base_msg.timestamp = now - datetime.timedelta(seconds=15)
        buffer.append(base_msg)

        logger.info("场景1: 完全重复的消息")
        duplicate_msg = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender1.user_id, sender_name=sender1.nickname,
            group_id="group_dedupe", text_content="Same content"
        )
        duplicate_msg.timestamp = now
        self.assertTrue(plugin._is_duplicate_message(buffer, duplicate_msg), "完全重复的消息应该被识别")

        logger.info("场景2: 不同发送者")
        msg_from_another_sender = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender2.user_id, sender_name=sender2.nickname,
            group_id="group_dedupe", text_content="Same content"
        )
        msg_from_another_sender.timestamp = now
        self.assertFalse(plugin._is_duplicate_message(buffer, msg_from_another_sender), "不同发送者的消息不应视为重复")

        logger.info("场景3: 不同内容")
        msg_with_different_content = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender1.user_id, sender_name=sender1.nickname,
            group_id="group_dedupe", text_content="Different content"
        )
        msg_with_different_content.timestamp = now
        self.assertFalse(plugin._is_duplicate_message(buffer, msg_with_different_content), "不同内容的消息不应视为重复")

        logger.info("场景4: 超出时间窗口")
        msg_out_of_time = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT, sender_id=sender1.user_id, sender_name=sender1.nickname,
            group_id="group_dedupe", text_content="Same content"
        )
        msg_out_of_time.timestamp = now + datetime.timedelta(seconds=40)
        self.assertFalse(plugin._is_duplicate_message(buffer, msg_out_of_time), "超出时间窗口的消息不应视为重复")

        logger.info("场景5: 包含图片")
        msg_with_image = GroupMessage(
            message_type=ContextMessageType.IMAGE_MESSAGE, sender_id=sender1.user_id, sender_name=sender1.nickname,
            group_id="group_dedupe", text_content="Same content", images=[MagicMock()]
        )
        msg_with_image.timestamp = now
        self.assertFalse(plugin._is_duplicate_message(buffer, msg_with_image), "包含图片的消息永远不应视为重复")

        logger.info("Test Passed: _is_duplicate_message 的所有核心场景均按预期工作。")

    async def test_cleanup_inactive_groups(self):
        """测试 _cleanup_inactive_groups 是否能正确清理不活跃的群组缓存"""
        logger.info(f"\n--- Running test: {self._testMethodName} ---")
        
        plugin = await self._setup_plugin_with_config({"inactive_cleanup_days": 10})
        
        now = datetime.datetime.now()

        def create_dummy_message(group_id, text):
            return GroupMessage(
                message_type=ContextMessageType.NORMAL_CHAT, sender_id="dummy",
                sender_name="Dummy", group_id=group_id, text_content=text
            )
        
        # 使用新的数据结构
        active_buffers = await plugin._get_or_create_group_buffers("active_group")
        active_buffers.recent_chats.append(create_dummy_message("active_group", "message1"))
        plugin.group_last_activity["active_group"] = now - datetime.timedelta(days=5)
        
        inactive_buffers = await plugin._get_or_create_group_buffers("inactive_group")
        inactive_buffers.recent_chats.append(create_dummy_message("inactive_group", "message2"))
        plugin.group_last_activity["inactive_group"] = now - datetime.timedelta(days=15)
        
        another_active_buffers = await plugin._get_or_create_group_buffers("another_active_group")
        another_active_buffers.recent_chats.append(create_dummy_message("another_active_group", "message3"))
        plugin.group_last_activity["another_active_group"] = now

        self.assertIn("active_group", plugin.group_messages)
        self.assertIn("inactive_group", plugin.group_messages)
        
        await plugin._cleanup_inactive_groups(now)
        
        self.assertIn("active_group", plugin.group_messages, "活跃群组不应被清理")
        self.assertIn("another_active_group", plugin.group_messages, "另一个活跃群组不应被清理")
        self.assertNotIn("inactive_group", plugin.group_messages, "不活跃群组的消息缓存应该被清理")
        self.assertNotIn("inactive_group", plugin.group_last_activity, "不活跃群组的活动记录应该被清理")
        
        logger.info("Test Passed: _cleanup_inactive_groups 成功清理了不活跃群组。")

    async def test_empty_message_handling(self):
        """测试插件是否会忽略完全为空（无文本、无图片）的消息"""
        logger.info(f"\n--- Running test: {self._testMethodName} ---")
        plugin = await self._setup_plugin_with_config({})
        
        mock_event = MockEvent()
        mock_event.message_obj = MockMessage(MockSender("user_empty", "EmptySender"), [])
        mock_event.message_str = ""
        
        with patch.object(mock_event, 'get_group_id', return_value='group_empty_test'):
            await plugin.on_message(mock_event)
        
        buffers = plugin.group_messages.get("group_empty_test")
        self.assertIsNone(buffers, "空消息不应该创建任何上下文缓存")

        logger.info("Test Passed: 插件成功忽略了空消息。")


if __name__ == "__main__":
    unittest.main()