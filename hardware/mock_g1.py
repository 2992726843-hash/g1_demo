# mock_drivers/mock_g1.py
import time

class MockG1Robot:
    def __init__(self):
        print("[MOCK] 虚拟 G1 机器人已上线。")
        # 预设官方动作字典，规范化开发
        self.actions = {
            11: "双手飞吻",
            17: "鼓掌",
            26: "高举挥手"
        }

    def execute_action(self, action_id):
        if action_id in self.actions:
            action_name = self.actions[action_id]
            print(f"[MOCK] 🤖 执行动作: {action_name} (ID:{action_id})")
            time.sleep(1) # 模拟动作执行耗时
            return True
        else:
            print(f"[MOCK] ❌ 未知动作 ID:{action_id}")
            return False

    def speak(self, text):
        print(f"[MOCK] 🔊 播报: '{text}'")