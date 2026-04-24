"""HA 客户端抽象 + 真实 HA 实现 + Mock 实现。

统一接口：
    get_state(entity_id)     -> dict { "entity_id", "state", "attributes" }
    turn_on(entity_id, **kw) -> dict
    turn_off(entity_id)      -> dict
    list_states()            -> list[dict]
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import requests


log = logging.getLogger("iot.ha")


class HAError(Exception):
    """Home Assistant 调用相关错误。"""

    def __init__(self, message: str, code: int = 1001):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# 真实 Home Assistant 客户端
# --------------------------------------------------------------------------- #
class HAClient:
    def __init__(self, base_url: str, token: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # -- 内部 ------------------------------------------------------------- #
    def _request(self, method: str, path: str, json: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers, json=json, timeout=self.timeout
            )
        except requests.Timeout as e:
            raise HAError(f"请求超时: {url}", code=1002) from e
        except requests.ConnectionError as e:
            raise HAError(f"无法连接 Home Assistant: {url}", code=1003) from e
        except requests.RequestException as e:
            raise HAError(f"请求异常: {e}", code=1004) from e

        if resp.status_code == 401:
            raise HAError("Home Assistant token 无效或缺失", code=1005)
        if resp.status_code == 404:
            raise HAError(f"资源不存在: {path}", code=1006)
        if resp.status_code >= 400:
            raise HAError(
                f"HA 返回错误 {resp.status_code}: {resp.text[:200]}", code=1007
            )
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- 公共 API --------------------------------------------------------- #
    def get_state(self, entity_id: str) -> Dict[str, Any]:
        data = self._request("GET", f"/api/states/{entity_id}")
        if not isinstance(data, dict):
            raise HAError(f"状态返回格式异常: {entity_id}", code=1008)
        return {
            "entity_id": data.get("entity_id", entity_id),
            "state": data.get("state", "unknown"),
            "attributes": data.get("attributes", {}),
        }

    def list_states(self) -> List[Dict[str, Any]]:
        data = self._request("GET", "/api/states")
        if not isinstance(data, list):
            return []
        return [
            {
                "entity_id": d.get("entity_id"),
                "state": d.get("state"),
                "attributes": d.get("attributes", {}),
            }
            for d in data
        ]

    def _call_service(self, domain: str, service: str, payload: dict) -> None:
        self._request("POST", f"/api/services/{domain}/{service}", json=payload)

    def turn_on(self, entity_id: str, **attrs: Any) -> Dict[str, Any]:
        domain = entity_id.split(".", 1)[0]
        payload: Dict[str, Any] = {"entity_id": entity_id}
        extra = {k: v for k, v in attrs.items() if v is not None}
        payload.update(extra)
        try:
            self._call_service(domain, "turn_on", payload)
        except HAError as e:
            # HA 400 通常是该实体不支持 brightness/rgb_color 等属性，回退到无属性
            if e.code == 1007 and extra:
                log.warning("turn_on %s 含属性 400，回退到基础 turn_on: %s", entity_id, extra)
                self._call_service(domain, "turn_on", {"entity_id": entity_id})
            else:
                raise
        return self.get_state(entity_id)

    def turn_off(self, entity_id: str) -> Dict[str, Any]:
        domain = entity_id.split(".", 1)[0]
        self._call_service(domain, "turn_off", {"entity_id": entity_id})
        return self.get_state(entity_id)


# --------------------------------------------------------------------------- #
# Mock Home Assistant（进程内模拟，线程安全）
# --------------------------------------------------------------------------- #
class MockHAClient:
    """模拟 Home Assistant，无需真实环境即可调试。

    状态存储在内存里，可通过 /mock/* 管理接口增删改查。
    """

    def __init__(self, preset_entities: Optional[List[str]] = None):
        self._lock = threading.RLock()
        self._states: Dict[str, Dict[str, Any]] = {}
        for ent in preset_entities or []:
            self.ensure_entity(ent)

    # -- 管理辅助 --------------------------------------------------------- #
    def ensure_entity(self, entity_id: str, state: str = "off") -> None:
        with self._lock:
            if entity_id not in self._states:
                self._states[entity_id] = {
                    "entity_id": entity_id,
                    "state": state,
                    "attributes": {},
                    "last_changed": time.time(),
                }

    def set_state(
        self,
        entity_id: str,
        state: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            rec = self._states.get(entity_id) or {
                "entity_id": entity_id,
                "attributes": {},
            }
            rec["state"] = state
            if attributes is not None:
                rec["attributes"] = attributes
            rec["last_changed"] = time.time()
            self._states[entity_id] = rec
            return self._snapshot(rec)

    def remove_entity(self, entity_id: str) -> bool:
        with self._lock:
            return self._states.pop(entity_id, None) is not None

    def reset(self) -> None:
        with self._lock:
            for rec in self._states.values():
                rec["state"] = "off"
                rec["attributes"] = {}
                rec["last_changed"] = time.time()

    @staticmethod
    def _snapshot(rec: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "entity_id": rec["entity_id"],
            "state": rec.get("state", "unknown"),
            "attributes": dict(rec.get("attributes", {})),
        }

    # -- 与 HAClient 对齐的公共 API --------------------------------------- #
    def get_state(self, entity_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._states.get(entity_id)
            if rec is None:
                raise HAError(f"设备不存在: {entity_id}", code=1006)
            return self._snapshot(rec)

    def list_states(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._snapshot(r) for r in self._states.values()]

    def turn_on(self, entity_id: str, **attrs: Any) -> Dict[str, Any]:
        with self._lock:
            self.ensure_entity(entity_id)
            rec = self._states[entity_id]
            rec["state"] = "on"
            # 合并属性（仅保留非 None）
            merged = dict(rec.get("attributes", {}))
            for k, v in attrs.items():
                if v is not None:
                    merged[k] = v
            rec["attributes"] = merged
            rec["last_changed"] = time.time()
            log.info("[MOCK] turn_on %s attrs=%s", entity_id, merged)
            return self._snapshot(rec)

    def turn_off(self, entity_id: str) -> Dict[str, Any]:
        with self._lock:
            self.ensure_entity(entity_id)
            rec = self._states[entity_id]
            rec["state"] = "off"
            rec["last_changed"] = time.time()
            log.info("[MOCK] turn_off %s", entity_id)
            return self._snapshot(rec)
