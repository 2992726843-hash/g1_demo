# core_control/task_orchestrator.py
import sys
import os

# 把 mock_drivers 目录加入路径，方便导入
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'mock_drivers'))
from hardware.mock_g1 import MockG1Robot

class TaskOrchestrator:
    def __init__(self):
        self.robot = MockG1Robot()

    def handle_intent(self, intent, parameters):
        """处理由大模型解析出来的意图"""
        if intent == "greet":
            self.robot.speak("爷爷好，今天感觉怎么样？")
            self.robot.execute_action(26) # 挥手
            
        elif intent == "emergency_fall":
            # 留给未来视觉系统的接口
            print("[核心] 收到跌倒警报！中断当前任务！")
            self.robot.speak("检测到异常跌倒，正在为您呼叫紧急联系人！")
            # 这里未来调用 HA 的接口，让全屋灯光闪烁变红
            
        else:
            print(f"[核心] 暂不识别的意图: {intent}")

if __name__ == "__main__":
    orchestrator = TaskOrchestrator()
    print("--- 开始测试流程 ---")
    orchestrator.handle_intent("greet", {})
    print("--- 测试中断响应 ---")
    orchestrator.handle_intent("emergency_fall", {})