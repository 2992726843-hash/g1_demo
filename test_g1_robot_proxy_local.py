from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict


SAFE_TEST_ACTIONS = {"heart", "wave_face", "x_ray", "clap", "hands_up"}
BASE_URL = "http://127.0.0.1:9001"


def request(method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            return {"ok": True, "status": resp.status, "body": body}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def print_result(name: str, result: Dict[str, Any]) -> None:
    print(f"\n{name}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def parse_args() -> tuple[str, str]:
    base_url = "http://127.0.0.1:9001"
    action = ""
    args = sys.argv[1:]
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item == "--action" and idx + 1 < len(args):
            action = args[idx + 1].strip()
            idx += 2
            continue
        if item.startswith("--action="):
            action = item.split("=", 1)[1].strip()
            idx += 1
            continue
        if item.startswith("http://") or item.startswith("https://"):
            base_url = item.rstrip("/")
        idx += 1
    return base_url, action


def main() -> None:
    global BASE_URL
    BASE_URL, action = parse_args()
    print(f"Testing g1_robot_proxy at {BASE_URL}")
    print("If the service is not running, requests should print connection errors instead of crashing.")
    print("Default mode only checks /health and /api/robot/status.")
    print("To execute one safe arm action: python3 test_g1_robot_proxy_local.py http://<G1_IP>:9001 --action heart")

    print_result("GET /health", request("GET", "/health"))
    print_result("GET /api/robot/status", request("GET", "/api/robot/status"))

    if not action:
        return
    if action not in SAFE_TEST_ACTIONS:
        print(f"\nRefusing to execute action={action!r}. Allowed test actions: {sorted(SAFE_TEST_ACTIONS)}")
        return
    print_result(
        f"POST /api/robot/action {action}",
        request("POST", "/api/robot/action", {"action_name": action}),
    )


if __name__ == "__main__":
    main()
