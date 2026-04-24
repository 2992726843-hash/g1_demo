"""
IoT Service HTTP 客户端（requests 同步）。

约束：
- iot_service 是独立 FastAPI 服务，主控只能通过 HTTP 调用
- 比赛演示期优先稳定：所有异常兜底，不能让主程序崩溃
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class IoTController:
    def __init__(self, base_url: str = "http://127.0.0.1:5001", timeout: int = 3) -> None:
        self.base_url = (base_url or "http://127.0.0.1:5001").rstrip("/")
        self.timeout = timeout

    def _ok(self, resp: requests.Response) -> bool:
        try:
            if resp.status_code != 200:
                return False
            data = resp.json()
            return bool(isinstance(data, dict) and data.get("ok") is True)
        except Exception:  # noqa: BLE001
            return False

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=self.timeout)
            return self._ok(resp)
        except Exception as exc:  # noqa: BLE001
            logger.info("[IoTController] health_check 失败: err=%s", exc)
            return False

    def call_scene(self, scene_name: str) -> bool:
        scene = (scene_name or "").strip()
        if not scene:
            return False
        try:
            resp = requests.post(
                f"{self.base_url}/iot/scene/{scene}",
                timeout=self.timeout,
            )
            if self._ok(resp):
                return True
            err: Any = None
            try:
                err = resp.json()
            except Exception:  # noqa: BLE001
                err = resp.text[:300]
            logger.warning("[IoTController] 调用失败: scene=%s err=%s", scene, err)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IoTController] 调用失败: scene=%s err=%s", scene, exc)
            return False

    def device_on(self, name: str, **kwargs) -> bool:
        dev = (name or "").strip()
        if not dev:
            return False
        payload = {"name": dev, **(kwargs or {})}
        try:
            resp = requests.post(
                f"{self.base_url}/iot/device/on",
                json=payload,
                timeout=self.timeout,
            )
            if self._ok(resp):
                return True
            err: Any = None
            try:
                err = resp.json()
            except Exception:  # noqa: BLE001
                err = resp.text[:300]
            logger.warning("[IoTController] 调用失败: device_on name=%s err=%s", dev, err)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IoTController] 调用失败: device_on name=%s err=%s", dev, exc)
            return False

    def device_off(self, name: str) -> bool:
        dev = (name or "").strip()
        if not dev:
            return False
        payload = {"name": dev}
        try:
            resp = requests.post(
                f"{self.base_url}/iot/device/off",
                json=payload,
                timeout=self.timeout,
            )
            if self._ok(resp):
                return True
            err: Any = None
            try:
                err = resp.json()
            except Exception:  # noqa: BLE001
                err = resp.text[:300]
            logger.warning("[IoTController] 调用失败: device_off name=%s err=%s", dev, err)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IoTController] 调用失败: device_off name=%s err=%s", dev, exc)
            return False

    def get_status(self, name: str | None = None) -> dict:
        try:
            params = {}
            if isinstance(name, str) and name.strip():
                params["name"] = name.strip()
            resp = requests.get(
                f"{self.base_url}/iot/status",
                params=params,
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.info("[IoTController] get_status 失败: err=%s", exc)
            return {}

