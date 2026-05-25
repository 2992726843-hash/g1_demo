from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class G1HttpClient:
    """
    PC-side HTTP adapter for a G1-local robot proxy.

    This client must never raise transport errors to the main control loop. All
    public methods return {"ok": bool, "data": ...} or {"ok": False, "error": "..."}.
    """

    def __init__(self, base_url: str, timeout_s: float = 3) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        try:
            self.timeout_s = float(timeout_s)
        except (TypeError, ValueError):
            self.timeout_s = 3.0
        if self.timeout_s <= 0:
            self.timeout_s = 3.0
        self.health_timeout_s = 3.0
        self.speak_timeout_s = 5.0
        self.action_timeout_s = 15.0
        self.loco_timeout_s = 35.0
        self.stop_timeout_s = 3.0

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", timeout_s=self.health_timeout_s)

    def play_action(self, action_id: int, action_name: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "action_id": int(action_id),
            "action_name": str(action_name or "").strip(),
        }
        return self._request("POST", "/api/robot/action", json=payload, timeout_s=self.action_timeout_s)

    def loco_control(self, action: str, target: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "action": str(action or "").strip(),
            "target": "" if target is None else str(target).strip(),
        }
        return self._request("POST", "/api/robot/loco", json=payload, timeout_s=self.loco_timeout_s)

    def speak(self, text: str, speaker_id: int = 0) -> Dict[str, Any]:
        payload = {
            "text": str(text or "").strip(),
            "speaker_id": int(speaker_id),
        }
        return self._request("POST", "/api/robot/speak", json=payload, timeout_s=self.speak_timeout_s)

    def stop(self) -> Dict[str, Any]:
        return self._request("POST", "/api/robot/stop", json={}, timeout_s=self.stop_timeout_s)

    def safe_stop(self) -> Dict[str, Any]:
        return self.stop()

    def get_status(self) -> Dict[str, Any]:
        return self._request("GET", "/api/robot/status", timeout_s=self.health_timeout_s)

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not self.base_url:
            return {"ok": False, "error": "G1 proxy base_url is empty"}

        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        method_upper = method.upper()
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            logger.info("[G1HttpClient] %s %s timeout=%.1fs payload=%s", method_upper, url, timeout, json)
            resp = requests.request(method_upper, url, json=json, timeout=timeout)
        except requests.RequestException as exc:
            logger.warning("[G1HttpClient] request failed: %s %s err=%s", method_upper, url, exc)
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[G1HttpClient] unexpected request error: %s %s err=%s", method_upper, url, exc)
            return {"ok": False, "error": str(exc)}

        text_preview = ""
        try:
            text_preview = (resp.text or "")[:200]
        except Exception:  # noqa: BLE001
            text_preview = ""

        if not resp.ok:
            error = f"HTTP {resp.status_code}: {text_preview}"
            logger.warning("[G1HttpClient] proxy returned error: %s %s %s", method_upper, url, error)
            return {"ok": False, "error": error}

        try:
            data: Any = resp.json()
        except ValueError:
            data = text_preview

        if isinstance(data, dict) and data.get("ok") is False:
            error = data.get("error") or data.get("message") or "proxy returned ok=false"
            logger.warning("[G1HttpClient] proxy ok=false: %s %s error=%s", method_upper, url, error)
            return {"ok": False, "error": str(error), "data": data}

        logger.info("[G1HttpClient] request ok: %s %s", method_upper, url)
        return {"ok": True, "data": data}
