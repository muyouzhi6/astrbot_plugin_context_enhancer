import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
from collections import deque
import datetime

# 导入被测试的插件和官方的 ProviderRequest
from main import ContextEnhancerV2, GroupMessage, ContextMessageType, ContextConstants
from astrbot.api.provider import ProviderRequest
from astrbot.api.platform import MessageType

# --- 模拟 AstrBot 核心对象 ---

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

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE # 修正：返回正确的枚举类型

# --- 测试用例 ---

class TestContextEnhancerScenarios(unittest.TestCase):
    def setUp(self):
        """在每个测试前运行"""
        print(f"\n--- Running test: {self._testMethodName} ---")
        # 模拟 Context 对象
        mock_context = MagicMock()
        
        # 实例化插件
        self.plugin = ContextEnhancerV2(mock_context)
        
        # 清空并预置聊天缓存
        self.plugin.group_messages = {}
        buffer = self.plugin._get_group_buffer("test_group_123")
        
        # 模拟一些历史消息
        mock_event_past = MockEvent()
        mock_event_past.message_obj = MockMessage(MockSender("10001", "张三"))
        
        past_msg = GroupMessage(mock_event_past, ContextMessageType.NORMAL_CHAT)
        past_msg.text_content = "今天天气不错"
        buffer.append(past_msg)

    def test_passive_user_trigger_scenario(self):
        """测试场景一：用户被动触发"""
        print("Step 1: 构造一个包含有效用户信息的 Event 对象")
        event = MockEvent()
        event.message_obj = MockMessage(MockSender("10002", "李四"))

        print("Step 2: 构造初始请求")
        # 使用真实的 ProviderRequest 对象
        request = ProviderRequest(prompt="你有什么建议吗？")

        print("Step 3: 调用 on_llm_request 方法")
        asyncio.run(self.plugin.on_llm_request(event, request))

        print("Step 4: 验证 Prompt 是否使用了 USER_TRIGGER_TEMPLATE")
        final_prompt = request.prompt
        print(f"Final Prompt:\n---\n{final_prompt}\n---")
        
        # 核心断言
        self.assertIn("现在 李四（ID: 10002）发了一个消息", final_prompt, "Prompt 应该包含用户触发信息")
        self.assertNotIn("主动就以下内容发表观点", final_prompt, "Prompt 不应该包含主动触发信息")
        
        print("✅ Test Passed: 被动回复场景按预期工作。")

    def test_proactive_system_trigger_scenario(self):
        """测试场景二：系统主动触发"""
        print("Step 1: 构造一个没有用户信息的 Event 对象 (sender=None)")
        event = MockEvent()
        event.message_obj = MockMessage(sender=None) # 关键点：没有发送者

        print("Step 2: 构造初始请求 (来自一个假设的定时任务)")
        # 使用真实的 ProviderRequest 对象
        request = ProviderRequest(prompt="播报一则晚间新闻")

        print("Step 3: 调用 on_llm_request 方法")
        asyncio.run(self.plugin.on_llm_request(event, request))

        print("Step 4: 验证 Prompt 是否使用了 PROACTIVE_TRIGGER_TEMPLATE")
        final_prompt = request.prompt
        print(f"Final Prompt:\n---\n{final_prompt}\n---")
        
        # 核心断言
        self.assertIn("主动就以下内容发表观点: 播报一则晚间新闻", final_prompt, "Prompt 应该包含主动触发信息")
        self.assertNotIn("发了一个消息", final_prompt, "Prompt 不应该包含用户触发信息")

        print("✅ Test Passed: 主动回复场景按预期工作。")

def run_tests():
    """手动运行测试并打印结果"""
    print("=============================================")
    print("  开始验证 ContextEnhancerV2 的两种核心场景  ")
    print("=============================================")
    
    # 创建测试实例
    test_suite = TestContextEnhancerScenarios()
    
    # 手动运行测试一
    try:
        test_suite.setUp()
        test_suite.test_passive_user_trigger_scenario()
    except AssertionError as e:
        print(f"❌ Test Failed: 被动回复场景测试失败 - {e}")
    
    print("\n---------------------------------------------\n")

    # 手动运行测试二
    try:
        test_suite.setUp()
        test_suite.test_proactive_system_trigger_scenario()
    except AssertionError as e:
        print(f"❌ Test Failed: 主动回复场景测试失败 - {e}")

    print("\n=============================================")
    print("              测试执行完毕              ")
    print("=============================================")

if __name__ == "__main__":
    run_tests()