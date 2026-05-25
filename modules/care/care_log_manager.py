from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class CareLogManager:
    def __init__(self, path: str = "data/care_log.json") -> None:
        self.logger = logging.getLogger("modules.care.care_log_manager")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()
        self._save()

    def append_event(
        self,
        event_type: str,
        title: str,
        level: str = "info",
        source: str = "",
        detail: dict | None = None,
    ) -> None:
        try:
            event = {
                "time": self._now_iso(),
                "event_type": str(event_type or "").strip(),
                "level": str(level or "info").strip() or "info",
                "source": str(source or "").strip(),
                "title": str(title or "").strip(),
                "detail": detail if isinstance(detail, dict) else {},
            }
            if not event["event_type"]:
                return
            events = self._data.setdefault("events", [])
            if not isinstance(events, list):
                events = []
                self._data["events"] = events
            events.append(event)
            self._save()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[CareLog] append_event failed event_type=%s err=%s", event_type, exc)

    def query_today_events(self) -> List[Dict[str, Any]]:
        try:
            today = datetime.now().date().isoformat()
            events = self._data.get("events", [])
            if not isinstance(events, list):
                return []
            result = []
            for raw in events:
                item = raw if isinstance(raw, dict) else {}
                ts = str(item.get("time") or "")
                if ts.startswith(today):
                    result.append(dict(item))
            return result
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[CareLog] query_today_events failed err=%s", exc)
            return []

    def clear_all(self) -> None:
        try:
            self._data = self._empty_data()
            self._save()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[CareLog] clear_all failed err=%s", exc)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._empty_data()
        try:
            content = self.path.read_text(encoding="utf-8")
            parsed = json.loads(content) if content.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("care_log_root_not_dict")
            events = parsed.get("events", [])
            if not isinstance(events, list):
                raise ValueError("care_log_events_not_list")
            return {
                "version": 1,
                "updated_at": str(parsed.get("updated_at") or self._now_iso()),
                "events": events,
            }
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[CareLog] JSON damaged, fallback empty path=%s err=%s", self.path, exc)
            return self._empty_data()

    def _save(self) -> None:
        self._data["version"] = 1
        self._data["updated_at"] = self._now_iso()
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _empty_data() -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "events": [],
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")
