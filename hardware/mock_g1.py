"""
比赛开发期替身机器人（Mock）。

目标：
- 在没有真机/SDK/DDS 的情况下，提供一个行为可预期的“虚拟 G1”，
  让主控层逻辑（状态机、意图解析、动作调度）可以稳定演示。
- 接口风格尽量贴近 `hardware/real_g1.py` 的对外形态：
  `execute_action` / `loco_control` / `speak`。

约束：
- 保持简单：只用 print，不引入 logging、不引入额外依赖。
"""

import random
import time

class MockG1Robot:
    def __init__(self):
        print("[MOCK] 虚拟 G1 机器人已上线。")
        # 预设动作字典（比赛常用）：尽量对齐真实 G1 的 action_id 语义
        self.actions: dict[int, str] = {
            11: "双手飞吻",
            15: "举手",
            17: "鼓掌",
            18: "击掌",
            19: "拥抱",
            22: "拒绝",
            25: "面部挥手",
            26: "高举挥手",
            27: "握手",
            99: "释放手臂",
        }

    def execute_action(self, action_id: int) -> bool:
        """
        模拟执行手臂动作（与真实机型保持一致的 action_id）。
        - 命中：打印日志，sleep 0.8~1.2 秒，返回 True
        - 未命中：打印 warning，返回 False
        """
        if action_id in self.actions:
            action_name = self.actions[action_id]
            print(f"[MOCK] 执行动作: {action_name} (ID:{action_id})")
            time.sleep(random.uniform(0.8, 1.2))
            return True

        print(f"[MOCK][WARN] 未知动作 ID:{action_id}，已忽略。")
        return False

    def loco_control(self, cmd: str) -> bool:
        """
        模拟底盘控制（比赛开发期不做真实运动，避免误动/翻车）。
        """
        cmd_clean = (cmd or "").strip()
        lines = {
            "move:forward": "[MOCK] 底盘前进中",
            "move:backward": "[MOCK] 底盘后退中",
            "move:left": "[MOCK] 底盘向左移动",
            "move:right": "[MOCK] 底盘向右移动",
            "navigate:bedroom": "[MOCK] 正在前往卧室",
            "navigate:living_room": "[MOCK] 正在前往客厅",
            "stop": "[MOCK] 底盘紧急停止",
        }
        print(lines.get(cmd_clean, f"[MOCK] 底盘控制命令: {cmd_clean}"))
        time.sleep(random.uniform(0.5, 1.0))
        return True

    def speak(self, text: str) -> None:
        """
        模拟播报：不依赖 TTS 库，只打印。
        """
        print(f"[MOCK] 播报: {text}")