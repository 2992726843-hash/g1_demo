from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_MEDICINE_ID = "med_bp_001"
DEFAULT_PROFILE_PATH = "data/medicine_profile.json"


def resolve_medicine_id(text: str, profile: dict) -> Optional[str]:
    """Resolve a medicine id from local aliases in medicine_profile.json."""
    query = str(text or "").strip()
    if not query:
        return None
    medicines = profile.get("medicines", profile) if isinstance(profile, dict) else {}
    if not isinstance(medicines, dict):
        return None
    pairs: list[tuple[str, str]] = []
    for medicine_id, raw_info in medicines.items():
        info = raw_info if isinstance(raw_info, dict) else {}
        aliases = []
        display_name = str(info.get("display_name") or "").strip()
        if display_name:
            aliases.append(display_name)
        raw_aliases = info.get("aliases", [])
        if isinstance(raw_aliases, list):
            aliases.extend(str(alias or "").strip() for alias in raw_aliases)
        for alias in aliases:
            if alias:
                pairs.append((alias, str(medicine_id)))
    for alias, medicine_id in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        if alias in query:
            return medicine_id
    return None


class MedicineManager:
    def __init__(
        self,
        state_path: str = "data/medicine_status.json",
        profile_path: str = DEFAULT_PROFILE_PATH,
        default_medicine_id: str = DEFAULT_MEDICINE_ID,
    ):
        self.logger = logging.getLogger("modules.medicine.manager")
        self.state_path = Path(state_path)
        self.profile_path = Path(profile_path)
        self.default_medicine_id = str(default_medicine_id or DEFAULT_MEDICINE_ID).strip() or DEFAULT_MEDICINE_ID
        self.pending_medicine_id: str | None = None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._profile = self._load_profile()
        self._state = self._load_state()
        self._save_state()

    def set_pending_medicine(self, medicine_id: str | None) -> None:
        resolved = self._normalize_medicine_id(medicine_id)
        if resolved:
            self.pending_medicine_id = resolved

    def clear_pending_medicine(self) -> None:
        self.pending_medicine_id = None

    def mark_reminded(self, medicine_id: str | None = None, date: str | None = None) -> dict:
        med_id, day = self._resolve_args(medicine_id, date)
        state = self._ensure_medicine_day(med_id, day)
        state["last_reminded_at"] = self._now_iso()
        self._save_state()
        return self._with_profile_fields(med_id, dict(state))

    def mark_taken(self, medicine_id: str | None = None, date: str | None = None) -> dict:
        med_id, day = self._resolve_args(medicine_id, date)
        state = self._ensure_medicine_day(med_id, day)
        already_taken = bool(state.get("taken"))
        if not already_taken:
            state["taken"] = True
            state["taken_at"] = self._now_iso()
        state["snoozed"] = False
        state["snoozed_at"] = None
        state["snooze_minutes"] = None
        state["refused"] = False
        state["refused_at"] = None
        self.set_pending_medicine(med_id)
        self._save_state()
        result = self._with_profile_fields(med_id, dict(state))
        result["already_taken"] = already_taken
        return result

    def mark_refused(self, medicine_id: str | None = None, date: str | None = None) -> dict:
        med_id, day = self._resolve_args(medicine_id, date)
        state = self._ensure_medicine_day(med_id, day)
        state["refused"] = True
        state["refused_at"] = self._now_iso()
        self.set_pending_medicine(med_id)
        self._save_state()
        return self._with_profile_fields(med_id, dict(state))

    def mark_snooze(
        self,
        medicine_id: str | None = None,
        minutes: int | None = 10,
        date: str | None = None,
    ) -> dict:
        med_id, day = self._resolve_args(medicine_id, date)
        state = self._ensure_medicine_day(med_id, day)
        state["snoozed"] = True
        state["snoozed_at"] = self._now_iso()
        state["snooze_minutes"] = int(minutes) if minutes is not None else None
        self.set_pending_medicine(med_id)
        self._save_state()
        return self._with_profile_fields(med_id, dict(state))

    def clear_today(self, date: str | None = None) -> dict:
        """Clear all tracked medicines for today."""
        day = self._normalize_date(date)
        days = self._state.setdefault("days", {})
        if not isinstance(days, dict):
            days = {}
            self._state["days"] = days
        days[day] = self._default_day_state()
        self.clear_pending_medicine()
        self._save_state()
        return self.query_all_today(date=day)

    def query_medicine_today(self, medicine_id: str, date: str | None = None) -> dict:
        med_id, day = self._resolve_args(medicine_id, date)
        state = self._ensure_medicine_day(med_id, day)
        self._save_state()
        return self._with_profile_fields(med_id, dict(state))

    def query_all_today(self, date: str | None = None) -> dict:
        day = self._normalize_date(date)
        day_state = self._ensure_day(day)
        medicines = day_state.setdefault("medicines", {})
        if not isinstance(medicines, dict):
            medicines = {}
            day_state["medicines"] = medicines
        result: Dict[str, Any] = {
            "date": day,
            "medicines": {},
        }
        for medicine_id in self._known_medicine_ids():
            state = medicines.get(medicine_id)
            merged = self._merge_medicine_defaults(state if isinstance(state, dict) else {})
            medicines[medicine_id] = merged
            result["medicines"][medicine_id] = self._with_profile_fields(medicine_id, dict(merged))
        self._save_state()
        return result

    def query_today(self, date: str | None = None) -> dict:
        """Legacy compatibility: return default medicine status for today."""
        return self.query_medicine_today(self.default_medicine_id, date=date)

    def get_profile(self) -> dict:
        return {"medicines": dict(self._profile)}

    def resolve_medicine_id(self, text: str) -> Optional[str]:
        return resolve_medicine_id(text, {"medicines": self._profile})

    def _load_profile(self) -> Dict[str, Any]:
        if not self.profile_path.exists():
            return {}
        try:
            content = self.profile_path.read_text(encoding="utf-8")
            parsed = json.loads(content) if content.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("profile_root_not_dict")
            medicines = parsed.get("medicines", {})
            if not isinstance(medicines, dict):
                raise ValueError("medicines_not_dict")
            return medicines
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[MedicineManager] 药品档案读取失败，已使用空档案: %s", exc)
            return {}

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            content = self.state_path.read_text(encoding="utf-8")
            parsed = json.loads(content) if content.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError("state_root_not_dict")
            migrated = self._migrate_state(parsed)
            return migrated
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[MedicineManager] 状态文件损坏，已回退为空结构: %s", exc)
            return self._default_state()

    def _migrate_state(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        days = parsed.get("days")
        if not isinstance(days, dict):
            days = {}
        new_days: Dict[str, Any] = {}
        for day, raw_day_state in days.items():
            day_key = str(day or "").strip()
            if not day_key:
                continue
            day_state = raw_day_state if isinstance(raw_day_state, dict) else {}
            if isinstance(day_state.get("medicines"), dict):
                medicines: Dict[str, Any] = {}
                for medicine_id, raw_medicine_state in day_state["medicines"].items():
                    med_id = self._normalize_medicine_id(medicine_id) or self.default_medicine_id
                    med_state = raw_medicine_state if isinstance(raw_medicine_state, dict) else {}
                    medicines[med_id] = self._merge_medicine_defaults(med_state)
                new_days[day_key] = {"medicines": medicines}
            else:
                new_days[day_key] = {
                    "medicines": {
                        self.default_medicine_id: self._merge_medicine_defaults(day_state),
                    }
                }
        return {
            "version": 2,
            "updated_at": str(parsed.get("updated_at") or self._now_iso()),
            "days": new_days,
        }

    def _save_state(self) -> None:
        self._state["version"] = 2
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
        if not isinstance(current, dict):
            current = self._default_day_state()
        if not isinstance(current.get("medicines"), dict):
            current = self._migrate_state({"days": {day: current}})["days"][day]
        days[day] = current
        return current

    def _ensure_medicine_day(self, medicine_id: str | None = None, date: str | None = None) -> Dict[str, Any]:
        med_id = self._select_medicine_id(medicine_id)
        day_state = self._ensure_day(date)
        medicines = day_state.setdefault("medicines", {})
        if not isinstance(medicines, dict):
            medicines = {}
            day_state["medicines"] = medicines
        current = medicines.get(med_id)
        merged = self._merge_medicine_defaults(current if isinstance(current, dict) else {})
        medicines[med_id] = merged
        return merged

    def _known_medicine_ids(self) -> list[str]:
        ids = [str(med_id).strip() for med_id in self._profile if str(med_id).strip()]
        if self.default_medicine_id not in ids:
            ids.insert(0, self.default_medicine_id)
        return ids

    def _select_medicine_id(self, medicine_id: str | None = None) -> str:
        return self._normalize_medicine_id(medicine_id) or self.pending_medicine_id or self.default_medicine_id

    def _resolve_args(self, medicine_id: str | None = None, date: str | None = None) -> tuple[str, str]:
        med_arg = self._normalize_medicine_id(medicine_id)
        date_arg = date
        if med_arg and self._looks_like_date(med_arg) and not date:
            date_arg = med_arg
            med_arg = None
        return self._select_medicine_id(med_arg), self._normalize_date(date_arg)

    @staticmethod
    def _looks_like_date(value: str) -> bool:
        return len(value) == 10 and value[4] == "-" and value[7] == "-"

    @staticmethod
    def _default_state() -> Dict[str, Any]:
        return {
            "version": 2,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "days": {},
        }

    @staticmethod
    def _default_day_state() -> Dict[str, Any]:
        return {"medicines": {}}

    @staticmethod
    def _default_medicine_state() -> Dict[str, Any]:
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

    def _merge_medicine_defaults(self, state: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._default_medicine_state()
        if not isinstance(state, dict):
            return merged
        for key in merged:
            if key in state:
                merged[key] = state[key]
        return merged

    def _with_profile_fields(self, medicine_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        med_id = self._normalize_medicine_id(medicine_id) or self.default_medicine_id
        info = self._profile.get(med_id, {})
        info = info if isinstance(info, dict) else {}
        result = dict(state)
        result["medicine_id"] = med_id
        result["display_name"] = str(info.get("display_name") or med_id)
        result["dose"] = str(info.get("dose") or "")
        result["schedule_text"] = str(info.get("schedule_text") or "")
        return result

    @staticmethod
    def _normalize_medicine_id(medicine_id: Any) -> str | None:
        value = str(medicine_id or "").strip()
        return value or None

    @staticmethod
    def _normalize_date(date: str | None = None) -> str:
        value = (date or "").strip()
        if value:
            return value
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")
