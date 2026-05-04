from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


class MedicineManager:
    def __init__(self, state_path: str = "data/medicine_status.json"):
        self.logger = logging.getLogger("modules.medicine.manager")
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._save_state()

    def mark_reminded(self, date: str | None = None) -> dict:
        state = self._ensure_day(date)
        state["last_reminded_at"] = self._now_iso()
        self._save_state()
        return dict(state)

    def mark_taken(self, date: str | None = None) -> dict:
        state = self._ensure_day(date)
        state["taken"] = True
        state["taken_at"] = self._now_iso()
        state["snoozed"] = False
        state["snoozed_at"] = None
        state["snooze_minutes"] = None
        state["refused"] = False
        state["refused_at"] = None
        self._save_state()
        return dict(state)

    def mark_refused(self, date: str | None = None) -> dict:
        state = self._ensure_day(date)
        state["refused"] = True
        state["refused_at"] = self._now_iso()
        self._save_state()
        return dict(state)

    def mark_snooze(self, minutes: int | None = 10, date: str | None = None) -> dict:
        state = self._ensure_day(date)
        state["snoozed"] = True
        state["snoozed_at"] = self._now_iso()
        state["snooze_minutes"] = int(minutes) if minutes is not None else None
        self._save_state()
        return dict(state)

    def clear_today(self, date: str | None = None) -> dict:
        """将今天的用药状态恢复为默认（全 False / None）。"""
        day = self._normalize_date(date)
        days = self._state.setdefault("days", {})
        days[day] = self._default_day_state()
        self._save_state()
        return dict(days[day])

    def query_today(self, date: str | None = None) -> dict:
        day = self._normalize_date(date)
        days = self._state.get("days", {})
        if not isinstance(days, dict):
            days = {}
            self._state["days"] = days
        raw = days.get(day)
        if not isinstance(raw, dict):
            return self._default_day_state()
        return self._merge_day_defaults(raw)

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            content = self.state_path.read_text(encoding="utf-8")
            parsed = json.loads(content) if content.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("state_root_not_dict")
            days = parsed.get("days")
            if not isinstance(days, dict):
                parsed["days"] = {}
            if "version" not in parsed:
                parsed["version"] = 1
            if "updated_at" not in parsed:
                parsed["updated_at"] = self._now_iso()
            return parsed
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[MedicineManager] 状态文件损坏，已回退为空结构: %s", exc)
            return self._default_state()

    def _save_state(self) -> None:
        self._state["version"] = 1
        self._state["updated_at"] = self._now_iso()
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_day(self, date: str | None = None) -> Dict[str, Any]:
        day = self._normalize_date(date)
        days = self._state.setdefault("days", {})
        if not isinstance(days, dict):
            days = {}
            self._state["days"] = days
        current = days.get(day)
        merged = self._merge_day_defaults(current if isinstance(current, dict) else {})
        days[day] = merged
        return merged

    @staticmethod
    def _default_state() -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "days": {},
        }

    @staticmethod
    def _default_day_state() -> Dict[str, Any]:
        return {
            "taken": False,
            "taken_at": None,
            "snoozed": False,
            "snoozed_at": None,
            "snooze_minutes": None,
            "refused": False,
            "refused_at": None,
            "last_reminded_at": None,
        }

    def _merge_day_defaults(self, state: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._default_day_state()
        for key in merged:
            if key in state:
                merged[key] = state[key]
        return merged

    @staticmethod
    def _normalize_date(date: str | None = None) -> str:
        value = (date or "").strip()
        if value:
            return value
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")
