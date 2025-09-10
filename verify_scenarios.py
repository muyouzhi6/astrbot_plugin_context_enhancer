import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from collections import deque
import datetime

# 导入被测试的插件和官方的 ProviderRequest
from main import ContextEnhancerV2, GroupMessage, ContextMessageType, ContextConstants
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest
from astrbot.api.platform import MessageType
from astrbot.api.message_components import Plain, At

# --- 模拟 AstrBot 核心对象 ---

class MockMessageComponent:
    def __init__(self, type, data):
        self.type = type
        self.data = data

class MockPlain(Plain):
    def __init__(self, text):
        super().__init__(text=text)

class MockSender:
    def __init__(self, user_id, nickname):
        self.user_id = user_id
        self.nickname = nickname

class MockMessage:
    def __init__(self, sender, message=None):
        self.sender = sender
        self.message = message or []

class MockEvent(MagicMock):
    def get_sender_id(self):
        if self.message_obj and self.message_obj.sender:
            return self.message_obj.sender.user_id
        return None

    def get_sender_name(self):
        if self.message_obj and self.message_obj.sender:
            return self.message_obj.sender.nickname
        return None
        
    def get_group_id(self):
        return "test_group_123"

    def get_self_id(self):
        # 直接硬编码返回字符串，消除 mock 对象问题
        return "self_123"

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE # 修正：返回正确的枚举类型

# --- 测试用例 ---

# 使用 patch.dict 来模拟 sys.modules，避免 ModuleNotFoundError
@patch.dict('sys.modules', {
    'astrabot': MagicMock(),
    'astrabot.api': MagicMock(),
    'astrabot.api.provider': MagicMock(),
    'astrabot.api.platform': MagicMock(),
})
class TestContextEnhancerScenarios(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """在每个测试前异步运行"""
        logger.info(f"\n--- Running test: {self._testMethodName} ---")
        # 模拟 Context 对象
        mock_context = MagicMock()
        mock_config = MagicMock()

        # 模拟配置的 get 方法
        def mock_get(key, default=None):
            config_map = {
                "command_prefixes": ["/", "!", "！", "#", ".", "。", "reset", "new"],
                "passive_reply_instruction": '现在，群成员 {sender_name} (ID: {sender_id}) 正在对你说话，或者提到了你，TA说："{original_prompt}"\n你需要根据以上聊天记录和你的角色设定，直接回复该用户。',
                "active_speech_instruction": '以上是最近的聊天记录。现在，你决定主动参与讨论，并想就以下内容发表你的看法："{original_prompt}"\n你需要根据以上聊天记录和你的角色设定，自然地切入对话。'
            }
            return config_map.get(key, default)

        mock_config.get.side_effect = mock_get
        
        # 实例化插件
        self.plugin = ContextEnhancerV2(mock_context, mock_config)
        # 异步初始化
        await self.plugin._async_init()
        
        # 清空并预置聊天缓存
        self.plugin.group_messages = {}
        buffers = await self.plugin._get_or_create_group_buffers("test_group_123")
        
        # 模拟一些历史消息
        mock_sender_past = MockSender("10001", "张三")
        mock_event_past = MockEvent()
        mock_event_past.message_obj = MockMessage(mock_sender_past, [MockPlain("今天天气不错")])
        
        past_msg = GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT,
            sender_id=mock_sender_past.user_id,
            sender_name=mock_sender_past.nickname,
            group_id="test_group_123",
            text_content="今天天气不错"
        )
        buffers.recent_chats.append(past_msg)

    async def test_passive_user_trigger_scenario(self):
        """测试场景一：用户被动触发"""
        logger.info("Step 1: 构造一个包含有效用户信息的 Event 对象")
        event = MockEvent()
        # 关键修复：确保 event 包含原始文本，以便 on_message 能处理
        event.message_str = "你有什么建议吗？"
        # 终极修复：确保 message_obj.message 列表被正确填充，并模拟 @ 消息
        bot_id = "self_123"
        event.message_obj = MockMessage(
            MockSender("10002", "李四"),
            [At(qq=bot_id), MockPlain(" 你有什么建议吗？")]
        )
        event.message_str = f"@{bot_id} 你有什么建议吗？"

        logger.info("Step 2: 构造初始请求")
        # 使用真实的 ProviderRequest 对象
        request = ProviderRequest(prompt="你有什么建议吗？")

        logger.info("Step 3: [修正] 先调用 on_message 模拟消息入队，再调用 on_llm_request")
        # 关键修复：必须先让 on_message 把消息记录到缓冲区
        await self.plugin.on_message(event)
        await self.plugin.on_llm_request(event, request)

        logger.info("Step 4: 验证 Prompt 是否使用了正确的被动回复指令")
        final_prompt = request.prompt
        logger.info(f"Final Prompt:\n---\n{final_prompt}\n---")
        
        # 核心断言
        # 验证是否包含被动回复指令的关键部分
        self.assertIn('李四 (ID: 10002) 正在对你说话', final_prompt)
        self.assertIn('TA说："你有什么建议吗？"', final_prompt)
        self.assertIn("直接回复该用户", final_prompt)
        # 验证不包含主动发言的指令
        self.assertNotIn("主动参与讨论", final_prompt)
        
        logger.info("Test Passed: 被动回复场景按预期工作。")

    async def test_proactive_system_trigger_scenario(self):
        """测试场景二：系统主动触发"""
        logger.info("Step 1: 构造一个没有用户信息的 Event 对象 (sender=None)")
        event = MockEvent()
        event.message_obj = MockMessage(sender=None) # 关键点：没有发送者

        logger.info("Step 2: 构造初始请求 (来自一个假设的定时任务)")
        # 使用真实的 ProviderRequest 对象
        request = ProviderRequest(prompt="播报一则晚间新闻")

        logger.info("Step 3: 调用 on_llm_request 方法")
        await self.plugin.on_llm_request(event, request)

        logger.info("Step 4: 验证 Prompt 是否使用了正确的主动发言指令")
        final_prompt = request.prompt
        logger.info(f"Final Prompt:\n---\n{final_prompt}\n---")
        
        # 核心断言
        # 验证是否包含主动发言指令的关键部分
        self.assertIn("主动参与讨论", final_prompt)
        self.assertIn('想就以下内容发表你的看法："{original_prompt}"'.format(original_prompt="播报一则晚间新闻"), final_prompt)
        self.assertIn("自然地切入对话", final_prompt)
        # 验证不包含被动回复的指令
        self.assertNotIn("正在对你说话", final_prompt)

        logger.info("Test Passed: 主动回复场景按预期工作。")

    async def test_reset_command_isolates_groups(self):
        """测试`reset`指令只影响当前群组，不影响其他群组。"""
        logger.info("Step 1: 为两个不同的群组 group_A 和 group_B 添加消息")
        
        # 为 group_A 添加消息
        buffers_A = await self.plugin._get_or_create_group_buffers("group_A")
        buffers_A.recent_chats.append(GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT,
            sender_id="user_A",
            sender_name="UserA",
            group_id="group_A",
            text_content="Message in group A"
        ))
        
        # 为 group_B 添加消息
        buffers_B = await self.plugin._get_or_create_group_buffers("group_B")
        buffers_B.recent_chats.append(GroupMessage(
            message_type=ContextMessageType.NORMAL_CHAT,
            sender_id="user_B",
            sender_name="UserB",
            group_id="group_B",
            text_content="Message in group B"
        ))

        # 断言两个群组都有消息
        self.assertEqual(len(self.plugin.group_messages["group_A"].recent_chats), 1)
        self.assertEqual(len(self.plugin.group_messages["group_B"].recent_chats), 1)

        logger.info("Step 2: 模拟在 group_A 中执行 reset 指令")
        # 构造一个模拟的 Event，代表来自 group_A 的 reset 请求
        mock_event_A = MockEvent()
        # 使用 patch 来临时修改 get_group_id 的返回值
        with patch.object(mock_event_A, 'get_group_id', return_value='group_A'):
            # 模拟用户发送 "reset" 指令
            mock_event_A.message_obj = MockMessage(MockSender("user_reset", "Resetter"), [MockPlain("reset")])
            mock_event_A.message_str = "reset" # 确保 message_str 也被设置
            
            # 调用 on_message 方法处理指令
            await self.plugin.on_message(mock_event_A)

        logger.info("Step 3: 验证 group_A 的上下文是否被清空，而 group_B 保持不变")
        # 检查 group_A 的 recent_chats deque 是否为空
        self.assertEqual(len(self.plugin.group_messages["group_A"].recent_chats), 0, "group_A 的上下文应该被清空")
        
        # 检查 group_B 的 deque 是否仍然有内容，以确保隔离性
        self.assertIn("group_B", self.plugin.group_messages, "group_B 的键不应该被删除")
        self.assertNotEqual(len(self.plugin.group_messages["group_B"].recent_chats), 0, "group_B 的上下文不应该被清空")
        self.assertEqual(len(self.plugin.group_messages["group_B"].recent_chats), 1, "group_B 的消息数量应该保持不变")
        self.assertEqual(self.plugin.group_messages["group_B"].recent_chats[0].text_content, "Message in group B")

        logger.info("Test Passed: `reset` 指令成功隔离了群组上下文。")

if __name__ == "__main__":
    unittest.main()