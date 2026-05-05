from __future__ import annotations

import os
import queue
import sys
import time
from typing import Any, Dict, List


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.action_executor import ActionExecutor  # noqa: E402
from core.utils import ConfigLoader, setup_logger  # noqa: E402
from main import SystemCore  # noqa: E402


BASE_URL = "http://127.0.0.1:9001"


class _FakeIoT:
    def __init__(self) -> None:
        self.scenes: List[str] = []

    def call_scene(self, scene_name: str) -> bool:
        scene = str(scene_name or "").strip()
        self.scenes.append(scene)
        print(f"[FakeIoT] call_scene({scene!r})")
        return scene in {"fall_alert", "reset_mode"}


class _FakeMedicineManager:
    def clear_today(self) -> None:
        print("[FakeMedicine] clear_today()")


def _patch_minimal_http_proxy_config() -> None:
    cfg = ConfigLoader()
    config: Dict[str, Any] = dict(cfg.get_config())
    robot = dict(config.get("robot", {}) or {})
    robot.update(
        {
            "control_backend": "http_proxy",
            "proxy_base_url": BASE_URL,
            "proxy_timeout_s": 3,
        }
    )
    emergency = dict(config.get("emergency", {}) or {})
    emergency.update(
        {
            "fall_robot_action_enabled": False,
            "fall_robot_action": "hands_up",
        }
    )
    config["robot"] = robot
    config["emergency"] = emergency
    config["mode"] = "real"
    cfg._config = config  # type: ignore[attr-defined]  # manual test only


def _build_minimal_core(executor: ActionExecutor, iot: _FakeIoT) -> SystemCore:
    core = SystemCore.__new__(SystemCore)
    core.logger = setup_logger("manual.stop_reset_emergency")
    core.executor = executor
    core.iot = iot
    core.speech = None
    core._speech_enabled = False
    core.state = SystemCore.IDLE
    core.running = True
    core.emergency_queue = queue.Queue()
    core._last_fall_event_ts = 0.0
    core._fall_event_cooldown = 0.0
    core._fall_active = False
    core.medicine_manager = _FakeMedicineManager()
    return core


def main() -> None:
    _patch_minimal_http_proxy_config()
    executor = ActionExecutor()
    iot = _FakeIoT()
    core = _build_minimal_core(executor, iot)

    print("Testing safe stop / fall priority / system reset against fake G1 proxy.")
    print(f"proxy_base_url={BASE_URL}")
    print("Start tests/fake_g1_proxy_server.py in another terminal to inspect received requests.")

    try:
        print("\n[1] user_stop: _handle_high_priority_interrupt('停止')")
        core._handle_high_priority_interrupt("停止")
        time.sleep(0.5)

        print("\n[2] fall_alert: _handle_emergency_event(is_fall=True)")
        core._handle_emergency_event({"event_type": "fall_alert", "is_fall": True, "fall_type": "front"})
        time.sleep(0.5)

        print("\n[3] system_reset: _handle_system_reset_command('系统复位')")
        core._handle_system_reset_command("系统复位")
        time.sleep(0.5)
    finally:
        executor.shutdown(timeout_s=3.0)

    print("\nExpected fake G1 proxy requests:")
    print("user_stop:    POST /api/robot/stop {}")
    print("fall_alert:   POST /api/robot/stop {}")
    print("system_reset: POST /api/robot/stop {}")
    print("\nExpected FakeIoT scenes:")
    print("fall_alert should call: fall_alert")
    print("system_reset should try: system_reset, then reset_mode if system_reset is unavailable")
    print("\nThere should be no POST /api/robot/action hands_up unless emergency.fall_robot_action_enabled=true.")


if __name__ == "__main__":
    main()
