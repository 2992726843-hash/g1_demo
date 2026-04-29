from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Optional

from core.utils import ConfigLoader, setup_logger

logger = logging.getLogger(__name__)


class G1HardwareInterface:
    """
    G1HardwareInterface：工业级高可用硬件接口（比赛演示稳定版）。

    核心目标：
    - SDK/DDS 成功：可以发送 G1 高层动作（ExecuteAction）
    - SDK/DDS 失败：必须降级，绝不让主控崩溃（所有异常吞掉并记录）
    - 禁止在 play_action 内 new Client（必须复用 __init__ 内创建的 client）
    """

    _instance: Optional["G1HardwareInterface"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "G1HardwareInterface":
        # 保持单例：主控各处拿到的是同一个底层 client（避免 DDS/SDK 重复初始化）
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        # 防止单例被重复初始化
        if getattr(self, "_initialized", False):
            return

        self._logger: logging.Logger = logger_ or setup_logger("hardware.real_g1")

        # ===== 状态字段（用于高可用兜底 + 现场排障）=====
        self._initialized: bool = False
        self._connected: bool = False
        self._client: Any = None
        self._action_map: Optional[dict[str, int]] = None
        self._network_interface: str = "eth0"
        self._last_action_id: Optional[int] = None
        self._last_exec_id: Optional[int] = None
        self._last_result: Optional[bool] = None
        self._last_error: Optional[str] = None
        self._emergency: bool = False

        # 必须在 __init__ 中完成且仅完成一次 SDK 初始化
        try:
            # 0) 开发阶段常见问题：本机未安装 unitree_sdk2py
            #    这里按约定将本地 SDK 目录动态加入 sys.path，再尝试导入。
            sdk_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "unitree_sdk2_python"))
            if sdk_dir not in sys.path:
                sys.path.insert(0, sdk_dir)

            # 1) 尝试导入 Unitree SDK2（未安装时会抛 ImportError）
            import unitree_sdk2py  # type: ignore[import-not-found]  # noqa: F401

            # 2) DDS 通道初始化（参考官方示例）
            from unitree_sdk2py.core.channel import (  # type: ignore[import-not-found]
                ChannelFactoryInitialize,
            )

            self._network_interface = str(
                ConfigLoader().get_nested("robot", "network_interface", default="eth0") or "eth0"
            ).strip() or "eth0"

            ChannelFactoryInitialize(0, self._network_interface)

            # 3) 实例化正确的 Client（官方示例：G1ArmActionClient）
            from unitree_sdk2py.g1.arm.g1_arm_action_client import (  # type: ignore[import-not-found]
                G1ArmActionClient,
                action_map,
            )

            arm_client = G1ArmActionClient()
            arm_client.SetTimeout(10.0)
            arm_client.Init()

            self._client = arm_client
            self._action_map = dict(action_map) if isinstance(action_map, dict) else None
            if isinstance(self._action_map, dict):
                self._logger.info("Unitree action_map keys: %s", list(self._action_map.keys()))

            self._connected = True
            self._last_error = None
            self._logger.info(
                "Unitree SDK2 初始化成功：已连接（G1ArmActionClient）。net_if=%s",
                self._network_interface,
            )
        except Exception as exc:  # noqa: BLE001
            # 关键：任何异常都不能让主程序崩溃
            self._connected = False
            self._client = None
            self._action_map = None
            self._last_error = str(exc)
            self._logger.warning(
                "Unitree SDK2 初始化失败，已进入降级模式（connected=False）。"
                "可能原因：未安装 SDK / DDS 网络异常 / SDK 版本不匹配。err=%s",
                exc,
            )
        finally:
            self._initialized = True

    def _resolve_action_id(self, action_id: int) -> int:
        """
        把上层 action_id 映射为 SDK ExecuteAction 的 exec_id。

        规则：
        - 优先通过 action_name_by_id + self._action_map 映射
        - 若 action_map 缺失/找不到 key：回退为 int(action_id)

        重要说明（避免踩坑）：
        - 这里处理的是 `G1ArmActionClient.action_map` 中的 **真实 ExecuteAction ID**
        - 不是 `hardware/unitree_sdk2_python/example/*` 示例程序里的 option_list “菜单编号”
        - 不要混用 loco example 的 id 或示例菜单 id
        """
        # 官方 Arm Action 反向映射（与 G1ArmActionClient.action_map 对齐）
        action_name_by_id: dict[int, str] = {
            99: "release arm",
            11: "two-hand kiss",
            12: "left kiss",
            13: "right kiss",
            15: "hands up",
            17: "clap",
            18: "high five",
            19: "hug",
            20: "heart",
            21: "right heart",
            22: "reject",
            23: "right hand up",
            24: "x-ray",
            25: "face wave",
            26: "high wave",
            27: "shake hand",
        }

        try:
            aid = int(action_id)
        except Exception:  # noqa: BLE001
            aid = 0

        if not isinstance(self._action_map, dict):
            return aid

        name = action_name_by_id.get(aid)
        if not name:
            return aid

        mapped = self._action_map.get(name)
        if mapped is None:
            self._logger.info(
                "action_map 未找到动作名映射：name=%r，将回退 ExecuteAction(%s)",
                name,
                aid,
            )
            return aid
        return int(mapped)

    def play_action(self, action_id: int) -> bool:
        """
        发送动作 ID（唯一职责）。

        返回值规范（高可用）：
        - 未连接：返回 False（并更新 last_result/last_error）
        - emergency=True：拒绝普通动作（返回 False）
        - 已连接：复用 self._client.ExecuteAction(exec_id) 发送
          - 无异常：认为“发送成功”，返回 True
          - code != 0：记录 warning，但不抛异常；仍返回 True（指令已发出）
          - 异常：返回 False

        重要：禁止在本方法内重新 new Client。
        """
        self._last_action_id = None
        self._last_exec_id = None

        try:
            aid = int(action_id)
        except Exception as exc:  # noqa: BLE001
            self._last_result = False
            self._last_error = f"bad action_id: {exc}"
            self._logger.warning("play_action 输入 action_id 非法：%r", action_id)
            return False

        self._last_action_id = aid

        # 紧急态：拒绝执行“普通动作”，但允许 release arm（99）尝试执行
        if self._emergency and aid != 99:
            self._last_result = False
            self._last_error = "emergency mode"
            self._logger.warning("[EMERGENCY] 当前处于紧急状态，拒绝执行动作：action_id=%s", aid)
            return False

        if not self._connected:
            self._last_result = False
            self._last_error = self._last_error or "not connected"
            self._logger.info("play_action ignored: connected=False action_id=%s", aid)
            return False

        # 绝对禁止在这里 new Client；必须复用 self._client
        try:
            client = self._client
            if client is None or not hasattr(client, "ExecuteAction"):
                raise AttributeError("G1ArmActionClient not initialized or missing ExecuteAction().")

            exec_id = self._resolve_action_id(aid)
            self._last_exec_id = exec_id

            code = client.ExecuteAction(exec_id)
            # 没有异常：就认为发送成功（比赛演示：不追求严格反馈）
            self._last_result = True
            self._last_error = None

            if isinstance(code, int) and code != 0:
                # code 非 0：记录 warning，但不影响主控（仍返回 True）
                self._logger.warning(
                    "ExecuteAction 返回非 0：action_id=%s exec_id=%s code=%s（指令已发送）",
                    aid,
                    exec_id,
                    code,
                )

            self._logger.info("play_action 已发送：action_id=%s exec_id=%s", aid, exec_id)
            return True
        except Exception as exc:  # noqa: BLE001
            # 高可用：发送失败也不允许抛出到上层
            self._last_result = False
            self._last_error = str(exc)
            self._logger.error("play_action 发送失败：action_id=%s err=%s", aid, exc)
            return False

    def get_status(self) -> dict:
        """获取当前硬件状态（用于主控/诊断）。"""
        try:
            return {
                "mode": "real",
                "initialized": bool(self._initialized),
                "connected": bool(self._connected),
                "network_interface": str(self._network_interface or ""),
                "emergency": bool(self._emergency),
                "last_action_id": self._last_action_id,
                "last_exec_id": self._last_exec_id,
                "last_result": self._last_result,
                "last_error": self._last_error,
            }
        except Exception as exc:  # noqa: BLE001
            self._logger.info("get_status 异常已忽略: err=%s", exc)
            return {"mode": "real", "initialized": True, "connected": False, "emergency": False}

    def emergency_stop(self) -> bool:
        """
        进入紧急态：
        - 设置 _emergency=True
        - 尝试释放手臂（如果 SDK 可用），失败也不抛异常
        """
        try:
            self._emergency = True
            self._logger.warning("[EMERGENCY] 触发紧急停止（进入紧急态）")
            # 紧急态下 play_action 会拒绝普通动作，因此这里直接尝试“底层发送 release arm”
            try:
                if self._connected and self._client is not None and hasattr(self._client, "ExecuteAction"):
                    exec_id = self._resolve_action_id(99)
                    self._client.ExecuteAction(exec_id)
                    self._logger.info("[EMERGENCY] 已尝试发送 release arm：exec_id=%s", exec_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning("[EMERGENCY] release arm 发送失败已忽略: err=%s", exc)
            return True
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("emergency_stop 异常已忽略: err=%s", exc)
            self._emergency = True
            return True

    def reset_emergency(self) -> bool:
        """解除紧急态。"""
        try:
            self._emergency = False
            self._logger.info("紧急状态已解除")
            return True
        except Exception as exc:  # noqa: BLE001
            self._logger.info("reset_emergency 异常已忽略: err=%s", exc)
            self._emergency = False
            return True

    def stop(self) -> bool:
        """
        普通停止：
        - 当前不接真实底盘，仅记录日志
        - 可尝试释放手臂（不依赖成功）
        """
        try:
            self._logger.info("收到 stop（真实底盘未接入，已降级处理）")
            # stop 也可以尝试 release arm，但不要求成功
            try:
                if self._connected and self._client is not None and hasattr(self._client, "ExecuteAction"):
                    exec_id = self._resolve_action_id(99)
                    self._client.ExecuteAction(exec_id)
                    self._logger.info("stop: 已尝试发送 release arm：exec_id=%s", exec_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.info("stop: release arm 失败已忽略: err=%s", exc)
            return True
        except Exception as exc:  # noqa: BLE001
            self._logger.info("stop 异常已忽略: err=%s", exc)
            return True


# -----------------------------------------------------------------------------
# 模块级默认硬件对象：导入时立即初始化（用于尽早暴露 SDK/DDS/network_interface 问题）
# -----------------------------------------------------------------------------
_DEFAULT_HW = G1HardwareInterface()


def play_action(action_id: int) -> bool:
    """
    模块级兼容接口：供上层直接 import 调用。

    - 返回 bool，便于上层判断是否发送成功
    """
    try:
        return bool(_DEFAULT_HW.play_action(action_id))
    except Exception as exc:  # noqa: BLE001
        # 最终兜底：模块级接口也不能抛异常
        logger.info("module play_action 异常已忽略: err=%s", exc)
        return False


class RealG1Robot:
    """
    主控侧使用的 Real 机器人封装（接口对齐 MockG1Robot）。

    设计原则：
    - execute_action：尽力发送真实动作；失败返回 False，但不抛异常
    - loco_control：当前不做真实运动（降级），但返回 True，避免演示被打断
    - speak：仅记录日志（真实 TTS 未接入）
    - guide_to_bathroom / medicine_reminder / fall_alert_response：组合演示，不依赖导航成功
    """

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger: logging.Logger = logger_ or setup_logger("hardware.real_g1.robot")
        # 复用模块级默认硬件对象，避免出现多条初始化路径
        self._hw = _DEFAULT_HW

    def execute_action(self, action_id: int) -> bool:
        try:
            return bool(self._hw.play_action(action_id))
        except Exception as exc:  # noqa: BLE001
            self.logger.info("execute_action 异常已忽略: err=%s", exc)
            return False

    def loco_control(self, cmd: str) -> bool:
        # 真实底盘暂未接入：降级为“只打印日志 + 返回 True”
        #
        # 重要（避免踩坑）：
        # - `hardware/unitree_sdk2_python/example/g1/high_level/g1_loco_client_example.py` 的 option_list id
        #   只是示例菜单分支编号，并不是底层任务 ID
        # - TODO: future real loco integration should call LocoClient methods directly, e.g. Move/StopMove/WaveHand/ShakeHand, not pass example menu IDs.
        try:
            cmd_clean = str(cmd or "").strip()
            self.logger.warning("loco_control('%s') ignored: real base not implemented (degraded).", cmd_clean)
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.info("loco_control 异常已忽略: err=%s", exc)
            return True

    def speak(self, text: str) -> None:
        # 真实 TTS 未接入：只记录日志；不因 emergency 阻止 speak
        try:
            t = str(text or "").strip()
            if not t:
                self.logger.warning("speak ignored: empty text")
                return
            self.logger.info("[REAL] speak: %s", t)
        except Exception as exc:  # noqa: BLE001
            self.logger.info("speak 异常已忽略: err=%s", exc)

    # -------------------------------------------------------------------------
    # 对齐 MockG1Robot 的扩展接口
    # -------------------------------------------------------------------------
    def get_status(self) -> dict:
        try:
            return dict(self._hw.get_status())
        except Exception as exc:  # noqa: BLE001
            self.logger.info("get_status 异常已忽略: err=%s", exc)
            return {"mode": "real", "initialized": True, "connected": False, "emergency": False}

    def stop(self) -> bool:
        try:
            _ = self._hw.stop()
            # 也可以尝试释放手臂（若失败不影响）
            try:
                _ = self.execute_action(99)
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.info("stop 异常已忽略: err=%s", exc)
            return True

    def emergency_stop(self) -> bool:
        try:
            return bool(self._hw.emergency_stop())
        except Exception as exc:  # noqa: BLE001
            self.logger.info("emergency_stop 异常已忽略: err=%s", exc)
            return True

    def reset_emergency(self) -> bool:
        try:
            return bool(self._hw.reset_emergency())
        except Exception as exc:  # noqa: BLE001
            self.logger.info("reset_emergency 异常已忽略: err=%s", exc)
            return True

    def guide_to_bathroom(self) -> bool:
        try:
            self.speak("我已经帮您打开夜灯，请慢一点走，我会在旁边提醒您。")
            _ = self.execute_action(26) or self.execute_action(15)
            _ = self.loco_control("navigate:bathroom")
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.info("guide_to_bathroom 异常已忽略: err=%s", exc)
            return True

    def medicine_reminder(self) -> bool:
        try:
            self.speak("药盒附近的提示灯已经打开，请您按时服药。")
            _ = self.execute_action(26)
            _ = self.loco_control("navigate:medicine_area")
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.info("medicine_reminder 异常已忽略: err=%s", exc)
            return True

    def fall_alert_response(self) -> bool:
        """
        跌倒报警响应：
        - 即使 emergency=True，也允许 speak 输出（动作可能失败也不影响返回）
        """
        try:
            self.speak("检测到可能跌倒，我已经触发报警，请保持原地等待帮助。")
            try:
                _ = self.execute_action(99)
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.info("fall_alert_response 异常已忽略: err=%s", exc)
            return True
