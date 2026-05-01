#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

import requests


BASE_URL = os.environ.get("IOT_BASE_URL", "http://127.0.0.1:5001").rstrip("/")


def _pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _print_step(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _request(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.request(method, url, json=json_body, timeout=8)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {method} {url}")
        print(f"  error: {e}")
        return False, {}

    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {"_raw": resp.text}

    ok = bool(resp.status_code == 200 and isinstance(data, dict) and data.get("ok") is True)
    tag = "[OK]" if ok else "[FAIL]"
    print(f"{tag} {method} {url}  (HTTP {resp.status_code})")
    if json_body is not None:
        print("request json:")
        print(_pretty(json_body))
    print("response:")
    print(_pretty(data))
    return ok, data if isinstance(data, dict) else {}


def main() -> int:
    print(f"IoT modes test against: {BASE_URL}")

    _print_step("1) GET /health")
    ok1, _ = _request("GET", "/health")

    _print_step("2) GET /iot/devices")
    ok2, _ = _request("GET", "/iot/devices")

    _print_step("3) POST /iot/scene/night_mode")
    ok3, _ = _request("POST", "/iot/scene/night_mode")

    _print_step("4) GET /iot/status?name=living_room_light")
    ok4, _ = _request("GET", "/iot/status?name=living_room_light")

    _print_step("5) POST /iot/scene/fall_alert")
    ok5, _ = _request("POST", "/iot/scene/fall_alert")

    _print_step("6) GET /iot/status?name=alarm_socket")
    ok6, _ = _request("GET", "/iot/status?name=alarm_socket")

    _print_step("7) POST /iot/scene/medicine_mode")
    ok7, _ = _request("POST", "/iot/scene/medicine_mode")

    _print_step("8) GET /iot/status?name=medicine_light")
    ok8, _ = _request("GET", "/iot/status?name=medicine_light")

    passed = sum(1 for x in (ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8) if x)
    total = 8
    print("\n" + "-" * 80)
    print(f"Result: {passed}/{total} steps OK")
    print("-" * 80)
    return 0 if passed == total else 2


if __name__ == "__main__":
    sys.exit(main())

