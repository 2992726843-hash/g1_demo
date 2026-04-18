from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from core.utils import ConfigLoader, setup_logger

logger = logging.getLogger(__name__)


class G1HardwareInterface:
    """
    G1HardwareInterface：工业级高可用硬件底层接口（单例）。

    约束与目标（严格按需求）：
    - 单例 + 一次性初始化：__init__ 内仅做一次 Unitree SDK2 初始化（创建 client 并 Init）。
    - 容错与降级：SDK 未安装或 DDS 网络异常时，记录明显 Warning，并将 _connected=False，绝不让主程序崩溃。
    - 动作执行抽象：仅提供 play_action(action_id)，且绝对禁止在该方法内重新 new Client。
    - 代码保持精简：只专注“发送动作 ID”这一件事。
    """

    _instance: Optional["G1HardwareInterface"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "G1HardwareInterface":
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
        self._connected: bool = False
        self._client: Any = None
        self._action_map: Optional[dict[str, int]] = None

        # 必须在 __init__ 中完成且仅完成一次 SDK 初始化
        try:
            # 0) 开发阶段常见问题：本机未安装 unitree_sdk2py
            #    这里按约定将本地 SDK 目录动态加入 sys.path，再尝试导入。
            import os
            import sys

            sdk_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "unitree_sdk2_python"))
            if sdk_dir not in sys.path:
                sys.path.insert(0, sdk_dir)

            # 1) 尝试导入 Unitree SDK2（未安装时会抛 ImportError）
            import unitree_sdk2py  # type: ignore[import-not-found]  # noqa: F401

            # 2) DDS 通道初始化（严格参考官方示例）
            from unitree_sdk2py.core.channel import (  # type: ignore[import-not-found]
                ChannelFactoryInitialize,
            )

            network_interface = str(
                ConfigLoader().get_nested("robot", "network_interface", default="eth0") or "eth0"
            ).strip() or "eth0"

            ChannelFactoryInitialize(0, network_interface)

            # 3) 实例化正确的 Client（官方示例：G1ArmActionClient）
            from unitree_sdk2py.g1.arm.g1_arm_action_client import (  # type: ignore[import-not-found]
                G1ArmActionClient,
                action_map,
            )

            arm_client = G1ArmActionClient()
            arm_client.SetTimeout(10.0)
            arm_client.Init()

            self._client = arm_client
            # action_map: Dict[str, int]，用于把语义名映射为 ExecuteAction 的参数
            self._action_map = dict(action_map) if isinstance(action_map, dict) else None

            self._connected = True
            self._logger.info("Unitree SDK2 初始化成功：已连接（G1ArmActionClient）。net_if=%s", network_interface)
        except Exception as e:
            # 关键：任何异常都不能让主程序崩溃
            self._connected = False
            self._client = None
            self._action_map = None
            self._logger.warning(
                "Unitree SDK2 初始化失败，已进入降级模式（_connected=False）。"
                "可能原因：未安装 SDK / DDS 网络异常 / SDK 版本不匹配。错误：%s",
                e,
            )

        self._initialized = True

    def play_action(self, action_id: int) -> None:
        """
        发送动作 ID（唯一职责）。

        - 若未连接：只打印 Mock 日志并返回（不抛异常）。
        - 若已连接：复用 __init__ 中创建的 client 发送指令。
        """
        if not self._connected:
            self._logger.info("[MOCK COMPAT] 当前为非真实控制环境，动作仅作为演示")
            self._logger.info("[MOCK] play_action(action_id=%s) ignored: _connected=False", action_id)
            return

        # 绝对禁止在这里 new Client；必须复用 self._client
        try:
            client = self._client
            if client is None or not hasattr(client, "ExecuteAction"):
                raise AttributeError("G1ArmActionClient not initialized or missing ExecuteAction().")

            # 官方示例用法：armAction_client.ExecuteAction(action_map.get("xxx"))
            # 本项目上层传入的是 action_id（例如 26/25/27/18/11/19/17/15/22/99）。
            # 这里做一个最小 if/elif 映射：优先按 action_map 的“语义名”取值，
            # 若 action_map 缺失则回退为直接 ExecuteAction(action_id)。
            action_name_by_id: dict[int, str] = {
                99: "release arm",
                27: "shake hand",
                18: "high five",
                19: "hug",
                17: "clap",
                15: "hands up",
                22: "reject",
                25: "face wave",
                26: "high wave",
                11: "left kiss",
            }

            exec_id: int
            if self._action_map is not None and int(action_id) in action_name_by_id:
                name = action_name_by_id[int(action_id)]
                mapped = self._action_map.get(name)
                if mapped is None:
                    # action_map 里没有该名字：回退到直接使用 action_id
                    self._logger.info(
                        "action_map 未找到动作名映射：name=%r，将回退 ExecuteAction(%s)",
                        name,
                        action_id,
                    )
                    exec_id = int(action_id)
                else:
                    exec_id = int(mapped)
            else:
                exec_id = int(action_id)

            code = client.ExecuteAction(exec_id)
            if isinstance(code, int) and code != 0:
                self._logger.info(
                    "[SDK DEGRADED] 动作已发送（可能未实际执行）：action_id=%s code=%s",
                    action_id,
                    code,
                )

            self._logger.info("play_action 已发送：action_id=%s", action_id)
        except Exception as e:
            # 高可用：发送失败也不允许抛出到上层
            self._logger.info("play_action 发送失败（已降级为演示模式）：action_id=%s error=%s", action_id, e)


_DEFAULT_HW = G1HardwareInterface()


def play_action(action_id: int) -> None:
    """
    模块级兼容接口：供上层直接 import 调用。
    """

    _DEFAULT_HW.play_action(action_id)


class RealG1Robot:
    """
    向后兼容旧调用方的薄封装。

    说明：
    - 历史代码可能调用 robot.execute_action(...)。
    - 这里不再做复杂能力（如底盘控制/TTS），只保持最小可用，避免破坏现有主流程。
    """

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger: logging.Logger = logger_ or setup_logger("hardware.real_g1.robot")
        self._hw = G1HardwareInterface(logger_=self.logger)

    def execute_action(self, action_id: int) -> bool:
        self._hw.play_action(action_id)
        return True

    def loco_control(self, cmd: str) -> bool:
        self.logger.warning("[MOCK] loco_control('%s') ignored: not implemented in new interface.", cmd)
        return False

    def speak(self, text: str) -> None:
        self.logger.info("[MOCK] speak: %s", text)
