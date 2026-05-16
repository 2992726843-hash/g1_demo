"""
比赛开发期替身机器人（Mock）。

目标：
- 在没有真机/SDK/DDS 的情况下，提供一个行为可预期、可控、稳定的“虚拟 G1”，
  让主控层逻辑（FSM、LLM 意图解析、动作调度、IoT 联动、跌倒报警）可以完整演示。
- 对外接口尽量贴近 `hardware/real_g1.py` 的形态：`execute_action` / `loco_control` / `speak`。

约束：
- 不引入 logging、不引入任何第三方库。
- 只允许标准库（本文件仅使用 time、random），所有输出使用 print。
"""

from __future__ import annotations

import random
import time


class MockG1Robot:
    """
    Mock 机器人仅用于演示：
    - 不追求真实运动控制，只需要“过程可解释、效果可预期”
    - 遇到异常也不应影响主控流程（本文件尽量做到内部自洽，外部调用无需 try/except）
    """

    def __init__(self):
        print("[MOCK] 虚拟 G1 机器人已上线。")

        # ===== 核心状态（用于主控演示/调试）=====
        self.mode = "mock"
        self.initialized = True
        self.busy = False
        self.current_action = None
        self.emergency = False
        self.last_action_id = None
        self.last_action_name = None
        self.last_result = None

        # ===== 预设动作字典（比赛常用）：尽量对齐真实 G1 的 action_id 语义 =====
        # 注意：本 Mock 只负责展示“动作名字 + 过程”，不做真实关节控制。
        self.actions: dict[int, str] = {
            11: "双手飞吻",
            12: "左手飞吻",
            13: "右手飞吻",
            15: "双手举起",
            17: "鼓掌",
            18: "击掌",
            19: "拥抱",
            20: "比心",
            21: "右手比心",
            22: "拒绝动作",
            23: "右手举起",
            24: "X光/检查动作",
            25: "挥手到脸旁",
            26: "挥手",
            27: "握手",
            99: "释放手臂",
        }

        # 方便组合演示方法使用：给一些“语义动作”选一个稳定的 action_id
        # - wave_hand：优先用高举挥手
        # - hands_up：举手
        self._action_wave_hand_id = 26
        self._action_hands_up_id = 15

    # -------------------------------------------------------------------------
    # 基础工具方法（内部使用）
    # -------------------------------------------------------------------------
    def _sleep(self, seconds: float) -> None:
        """sleep 的小封装：避免传入异常值导致崩溃。"""
        try:
            sec = float(seconds)
            if sec <= 0:
                return
            time.sleep(sec)
        except Exception:  # noqa: BLE001
            return

    def _set_busy(self, busy: bool, action_name: str | None) -> None:
        """统一维护 busy/current_action。"""
        self.busy = bool(busy)
        self.current_action = action_name if busy else None

    # -------------------------------------------------------------------------
    # 对齐 real_g1.py 的三大主接口
    # -------------------------------------------------------------------------
    def execute_action(self, action_id: int) -> bool:
        """
        模拟执行手臂/上肢动作（与真实机型保持一致的 action_id）。

        行为规范（比赛演示优先）：
        - 如果 emergency=True：拒绝执行（但不抛异常）
        - action_id 未知：打印 warning，last_result=False，返回 False
        - 命中动作：busy=True -> sleep -> busy=False，记录 last_*，返回 True
        """
        try:
            if self.emergency:
                print("[MOCK][EMERGENCY] 当前处于紧急状态，拒绝执行动作")
                self.last_result = False
                return False

            if action_id not in self.actions:
                print(f"[MOCK][WARN] 未知动作 ID:{action_id}，已忽略。")
                self.last_action_id = action_id
                self.last_action_name = None
                self.last_result = False
                return False

            action_name = self.actions[action_id]
            self._set_busy(True, action_name)
            print(f"[MOCK] 开始执行动作: {action_name} (ID:{action_id})")

            # release_arm 更快；其它动作给一个稳定、自然的演示节奏
            if action_id == 99:
                self._sleep(0.3)
            else:
                self._sleep(random.uniform(0.8, 1.2))

            print(f"[MOCK] 动作完成: {action_name} (ID:{action_id})")
            self._set_busy(False, None)

            self.last_action_id = action_id
            self.last_action_name = action_name
            self.last_result = True
            return True
        except Exception as exc:  # noqa: BLE001
            # Mock 不允许把异常冒泡到主控
            print(f"[MOCK][WARN] execute_action 异常已忽略: err={exc}")
            self._set_busy(False, None)
            self.last_result = False
            return False

    def loco_control(self, cmd: str) -> bool:
        """
        模拟底盘/导航控制（比赛开发期不做真实运动，避免误动/翻车）。

        支持命令：
        - move:forward / backward / left / right
        - turn:left / right
        - navigate:bedroom / bathroom / living_room / medicine_area
        - stop

        行为要求：
        - emergency=True 时，除 stop 外全部拒绝
        - navigate 打印“开始导航 -> 到达目标点”，中间 sleep ~1s
        - stop 必须立即 busy=False、current_action=None
        """
        cmd_clean = (cmd or "").strip()
        try:
            if self.emergency and cmd_clean != "stop":
                print("[MOCK][EMERGENCY] 当前处于紧急状态，拒绝执行底盘指令")
                self.last_result = False
                return False

            # stop：立即生效
            if cmd_clean == "stop":
                print("[MOCK] 底盘停止（stop）")
                self._set_busy(False, None)
                self.last_result = True
                return True

            supported = {
                "move:forward",
                "move:backward",
                "move:left",
                "move:right",
                "turn:left",
                "turn:right",
                "navigate:bedroom",
                "navigate:bathroom",
                "navigate:living_room",
                "navigate:medicine_area",
            }

            if cmd_clean not in supported:
                print(f"[MOCK][WARN] 未知底盘指令: {cmd_clean}，已忽略。")
                self.last_result = False
                return False

            # navigate：演示“出发 -> 到达”
            if cmd_clean.startswith("navigate:"):
                dest = cmd_clean.split(":", 1)[1]
                self._set_busy(True, f"navigate:{dest}")
                print(f"[MOCK] 开始导航: -> {dest}")
                self._sleep(1.0)
                print(f"[MOCK] 已到达目标点: {dest}")
                self._set_busy(False, None)
                self.last_result = True
                return True

            # move/turn：给一个短暂停顿即可（模拟执行）
            self._set_busy(True, cmd_clean)
            mapping = {
                "move:forward": "[MOCK] 底盘前进中",
                "move:backward": "[MOCK] 底盘后退中",
                "move:left": "[MOCK] 底盘向左移动",
                "move:right": "[MOCK] 底盘向右移动",
                "turn:left": "[MOCK] 原地左转中",
                "turn:right": "[MOCK] 原地右转中",
            }
            print(mapping.get(cmd_clean, f"[MOCK] 底盘控制命令: {cmd_clean}"))
            self._sleep(random.uniform(0.4, 0.7))
            self._set_busy(False, None)
            self.last_result = True
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] loco_control 异常已忽略: err={exc}")
            self._set_busy(False, None)
            self.last_result = False
            return False

    def speak(self, text: str) -> None:
        """
        模拟播报：不依赖 TTS，只打印。
        - text 为空：打印 warning
        - 不应因为 emergency 阻止 speak（跌倒报警仍需要播报）
        """
        try:
            t = (text or "").strip()
            if not t:
                print("[MOCK][WARN] speak 文本为空，已忽略。")
                return
            print(f"[MOCK] 播报: {t}")
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] speak 异常已忽略: err={exc}")

    # -------------------------------------------------------------------------
    # 新增接口（用于主控演示闭环）
    # -------------------------------------------------------------------------
    def get_status(self) -> dict:
        """返回 Mock 关键状态，便于主控/测试读取。"""
        try:
            return {
                "mode": self.mode,
                "initialized": self.initialized,
                "busy": self.busy,
                "current_action": self.current_action,
                "emergency": self.emergency,
                "last_action_id": self.last_action_id,
                "last_action_name": self.last_action_name,
                "last_result": self.last_result,
            }
        except Exception:  # noqa: BLE001
            # 即使出错也返回一个最小信息，避免外部崩溃
            return {"mode": "mock", "initialized": True, "busy": False, "emergency": False}

    def stop(self) -> bool:
        """普通停止：清理 busy/current_action，不进入紧急态。"""
        try:
            print("[MOCK] 收到普通停止指令")
            self._set_busy(False, None)
            self.last_result = True
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] stop 异常已忽略: err={exc}")
            self._set_busy(False, None)
            self.last_result = False
            return False

    def emergency_stop(self) -> bool:
        """紧急停止：进入紧急态，并停止当前动作。"""
        try:
            print("[MOCK][EMERGENCY] 触发紧急停止")
            self.emergency = True
            self._set_busy(False, None)
            self.last_result = True
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] emergency_stop 异常已忽略: err={exc}")
            self.emergency = True
            self._set_busy(False, None)
            self.last_result = False
            return False

    def reset_emergency(self) -> bool:
        """解除紧急态：恢复可执行动作/导航。"""
        try:
            print("[MOCK] 紧急状态已解除")
            self.emergency = False
            self.last_result = True
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] reset_emergency 异常已忽略: err={exc}")
            self.last_result = False
            return False

    def guide_to_bathroom(self) -> bool:
        """
        起夜辅助演示（组合动作）：
        - speak + execute_action + navigate
        """
        try:
            self.speak("我已经帮您打开夜灯，请慢一点走，我会在旁边提醒您。")
            # 展示一个“我在引导”的动作（挥手或举手）
            _ = self.execute_action(self._action_wave_hand_id) or self.execute_action(self._action_hands_up_id)
            return bool(self.loco_control("navigate:bathroom"))
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] guide_to_bathroom 异常已忽略: err={exc}")
            return False

    def medicine_reminder(self) -> bool:
        """用药提醒演示：播报 + 简单动作。"""
        try:
            self.speak("药盒附近的提示灯已经打开，请您按时服药。")
            _ = self.execute_action(self._action_wave_hand_id)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] medicine_reminder 异常已忽略: err={exc}")
            return False

    def fall_alert_response(self) -> bool:
        """
        跌倒报警响应（比赛演示）：
        - 即使 emergency=True，也允许播报报警信息
        - 尝试执行一个安全动作；若因紧急态被拒绝，不报错，仍返回 True
        """
        try:
            self.speak("检测到可能跌倒，我已经触发报警，请保持原地等待帮助。")
            # 这里选择“释放手臂”作为更安全的动作；紧急态下会被 execute_action 拒绝，但不影响流程
            _ = self.execute_action(99)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[MOCK][WARN] fall_alert_response 异常已忽略: err={exc}")
            return True