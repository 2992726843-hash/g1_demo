from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, Dict


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from hardware.g1_http_client import G1HttpClient  # noqa: E402


BASE_URL = "http://127.0.0.1:9001"


def _print_result(name: str, result: Dict[str, Any]) -> None:
    print(f"\n{name}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _run_step(name: str, fn: Callable[[], Dict[str, Any]]) -> None:
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": f"unexpected exception: {exc}"}
    _print_result(name, result)


def main() -> None:
    client = G1HttpClient(BASE_URL, timeout_s=3)
    print(f"Testing G1HttpClient against {BASE_URL}")
    print("If fake_g1_proxy_server.py is not running, each step should return ok=false instead of crashing.")

    _run_step("health()", client.health)
    _run_step('play_action(26, "wave_hand")', lambda: client.play_action(26, "wave_hand"))
    _run_step('play_action(15, "hands_up")', lambda: client.play_action(15, "hands_up"))
    _run_step('loco_control("move", "forward")', lambda: client.loco_control("move", "forward"))
    _run_step("stop()", client.stop)
    _run_step("get_status()", client.get_status)


if __name__ == "__main__":
    main()
