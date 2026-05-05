from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.action_executor import ActionExecutor  # noqa: E402
from core.utils import ConfigLoader, setup_logger  # noqa: E402


BASE_URL = "http://127.0.0.1:9001"


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
    config["robot"] = robot
    config["mode"] = "real"
    cfg._config = config  # type: ignore[attr-defined]  # manual test only


def _submit_and_wait(executor: ActionExecutor, action: str, target: str = "") -> None:
    print(f"\nsubmit_action({action!r}, {target!r})")
    executor.submit_action(action, target)
    try:
        executor._queue.join()  # type: ignore[attr-defined]  # manual test only
    except Exception:
        time.sleep(1.5)
    time.sleep(0.2)


def main() -> None:
    setup_logger("core.action_executor")
    _patch_minimal_http_proxy_config()

    print("Testing ActionExecutor HTTP proxy path.")
    print(f"mode=real, robot.control_backend=http_proxy, proxy_base_url={BASE_URL}")
    print("Start tests/fake_g1_proxy_server.py in another terminal to inspect received requests.")
    print("This script should not import hardware.real_g1 when control_backend=http_proxy.")

    executor = ActionExecutor()
    try:
        _submit_and_wait(executor, "wave_hand")
        _submit_and_wait(executor, "hands_up")
        _submit_and_wait(executor, "stop")
    finally:
        executor.shutdown(timeout_s=3.0)

    print("\nExpected fake server requests:")
    print('POST /api/robot/action {"action_id": 99, "action_name": "release_arm"}')
    print('POST /api/robot/action {"action_id": 26, "action_name": "wave_hand"}')
    print('POST /api/robot/action {"action_id": 99, "action_name": "release_arm"}')
    print('POST /api/robot/action {"action_id": 15, "action_name": "hands_up"}')
    print("POST /api/robot/stop {}")


if __name__ == "__main__":
    main()
