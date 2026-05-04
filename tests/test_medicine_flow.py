"""Medicine interaction flow tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.task_executor import TaskExecutor
from core.task_orchestrator import TaskOrchestrator
from modules.medicine import MedicineManager


class _FakeIoT:
    def __init__(self, fail_scenes: set[str] | None = None):
        self.fail_scenes = fail_scenes or set()
        self.scenes: list[str] = []

    def call_scene(self, name: str) -> bool:
        self.scenes.append(name)
        return name not in self.fail_scenes

    def device_on(self, _name: str) -> bool:
        return True

    def device_off(self, _name: str) -> bool:
        return True

    def device_set(self, _name: str, service: str = "turn_on", attributes: dict | None = None) -> bool:
        _ = service, attributes
        return True

    def get_status(self, _name: str | None = None) -> dict:
        return {"state": "off"}


class _FakeActionExecutor:
    def __init__(self):
        self.actions: list[tuple[str, str]] = []

    def submit_action(self, action: str, target: str) -> None:
        self.actions.append((action, target))

    def interrupt_current_action(self) -> None:
        return


class _BrokenMedicineManager:
    def mark_reminded(self, date: str | None = None) -> dict:
        _ = date
        raise RuntimeError("boom")

    def mark_taken(self, date: str | None = None) -> dict:
        _ = date
        raise RuntimeError("boom")

    def mark_refused(self, date: str | None = None) -> dict:
        _ = date
        raise RuntimeError("boom")

    def mark_snooze(self, minutes: int | None = 10, date: str | None = None) -> dict:
        _ = minutes, date
        raise RuntimeError("boom")

    def query_today(self, date: str | None = None) -> dict:
        _ = date
        raise RuntimeError("boom")


def test_orchestrator_medicine_reminder_plan():
    plan = TaskOrchestrator().build_plan("测试用药提醒", {})
    assert plan.name == "medicine_reminder"
    assert [(s.type, s.name, s.action) for s in plan.steps[:3]] == [
        ("iot_scene", "medicine_mode", ""),
        ("medicine_record", "", ""),
        ("robot", "", "hands_up"),
    ]
    assert plan.steps[1].meta.get("action") == "reminded"


def test_orchestrator_medicine_taken_plan():
    plan = TaskOrchestrator().build_plan("我已经吃药了", {})
    assert plan.name == "medicine_taken"
    assert plan.steps[0].type == "medicine_record"
    assert plan.steps[0].meta.get("action") == "taken"
    assert plan.steps[1].type == "robot" and plan.steps[1].action == "clap"
    assert plan.steps[2].type == "iot_scene" and plan.steps[2].name == "reset_mode"


def test_orchestrator_medicine_refuse_plan():
    plan = TaskOrchestrator().build_plan("我不想吃药", {})
    assert plan.name == "medicine_refuse"
    assert plan.steps[1].type == "robot"
    assert plan.steps[1].action == "reject"


def test_orchestrator_medicine_snooze_plan():
    plan = TaskOrchestrator().build_plan("等会儿再吃药", {})
    assert plan.name == "medicine_snooze"
    assert plan.steps[0].type == "medicine_record"
    assert plan.steps[0].meta.get("action") == "snooze"
    assert plan.steps[0].meta.get("minutes") == 10


def test_orchestrator_medicine_query_plan():
    plan = TaskOrchestrator().build_plan("我今天吃药了吗", {})
    assert plan.name == "medicine_query"
    assert plan.steps[0].type == "medicine_query_today"
    assert plan.steps[1].type == "robot" and plan.steps[1].action == "wave_hand"
    assert plan.steps[2].type == "speak"


def test_medicine_manager_auto_create_file(tmp_path: Path):
    state_path = tmp_path / "medicine_status.json"
    assert not state_path.exists()
    _ = MedicineManager(str(state_path))
    assert state_path.exists()


def test_medicine_manager_mark_taken(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    mgr.mark_taken()
    today = mgr.query_today()
    assert today["taken"] is True
    assert today["taken_at"] is not None
    assert today["snoozed"] is False
    assert today["refused"] is False


def test_medicine_manager_mark_snooze(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    mgr.mark_snooze(minutes=10)
    today = mgr.query_today()
    assert today["snoozed"] is True
    assert today["snooze_minutes"] == 10


def test_medicine_manager_mark_refused(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    mgr.mark_refused()
    today = mgr.query_today()
    assert today["refused"] is True
    assert today["taken"] is False


def test_medicine_manager_broken_json_fallback(tmp_path: Path):
    state_path = tmp_path / "medicine_status.json"
    state_path.write_text("{bad-json", encoding="utf-8")
    mgr = MedicineManager(str(state_path))
    today = mgr.query_today()
    assert today["taken"] is False
    assert today["snoozed"] is False
    assert today["refused"] is False


def test_executor_medicine_record_taken_success(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    orch = TaskOrchestrator()
    plan = orch.build_plan("我已经吃药了", {})
    spoken: list[str] = []
    actions = _FakeActionExecutor()
    iot = _FakeIoT()
    executor = TaskExecutor(
        iot_controller=iot,
        action_executor=actions,
        speak_callback=spoken.append,
        medicine_manager=mgr,
    )
    result = executor.execute(plan)
    assert result["ok"] is True
    assert mgr.query_today()["taken"] is True
    assert ("clap", "") in actions.actions
    assert iot.scenes == ["reset_mode"]
    assert spoken[-1] == "已记录您今天已服药。"


def test_executor_query_taken_dynamic_speak(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    mgr.mark_taken()
    plan = TaskOrchestrator().build_plan("我今天吃药了吗", {})
    spoken: list[str] = []
    executor = TaskExecutor(
        iot_controller=_FakeIoT(),
        action_executor=_FakeActionExecutor(),
        speak_callback=spoken.append,
        medicine_manager=mgr,
    )
    executor.execute(plan)
    assert spoken
    assert "记录服药" in spoken[-1]


def test_executor_query_not_taken_dynamic_speak(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    plan = TaskOrchestrator().build_plan("今天有没有吃药", {})
    spoken: list[str] = []
    executor = TaskExecutor(
        iot_controller=_FakeIoT(),
        action_executor=_FakeActionExecutor(),
        speak_callback=spoken.append,
        medicine_manager=mgr,
    )
    executor.execute(plan)
    assert spoken[-1] == "今天还没有记录服药，请注意按时用药。"


@pytest.mark.parametrize(
    "user_text,expected_text",
    [
        ("我已经吃药了", "用药记录失败，请稍后再试。"),
        ("我今天吃药了吗", "用药记录查询失败，请稍后再试。"),
    ],
)
def test_executor_medicine_manager_exception_speak(user_text: str, expected_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    spoken: list[str] = []
    executor = TaskExecutor(
        iot_controller=_FakeIoT(),
        action_executor=_FakeActionExecutor(),
        speak_callback=spoken.append,
        medicine_manager=_BrokenMedicineManager(),
    )
    result = executor.execute(plan)
    assert result["ok"] is False
    assert spoken[-1] == expected_text


@pytest.mark.parametrize(
    "user_text,plan_name,first_type",
    [
        ("复位", "reset_system", "robot_stop"),
        ("关闭所有灯光", "iot_group_control", "iot_group_control"),
        ("检查一下1号开关", "iot_status_query", "iot_query"),
        ("打开1号开关", "iot_device_control", "iot_device_on"),
        ("跌倒", "fall_alert", "iot_scene"),
    ],
)
def test_regression_existing_rules_not_broken(user_text: str, plan_name: str, first_type: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == plan_name
    assert plan.steps[0].type == first_type


# ---------------------------------------------------------------------------
# 新增：用药分支扩展关键词
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("user_text", [
    "我刚才已经把药吃了",
    "我刚刚把药吃了",
    "我已经把药吃了",
    "我把药吃了",
    "药我已经吃了",
    "刚才吃过药了",
    "我已经服药了",
    "我服过药了",
])
def test_medicine_taken_extended_phrases(user_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == "medicine_taken", f"'{user_text}' 应命中 medicine_taken，实际: {plan.name}"


@pytest.mark.parametrize("user_text,expected_name", [
    ("我不想吃药", "medicine_refuse"),
    ("等会再吃药", "medicine_snooze"),
    ("我今天吃药没有", "medicine_query"),
    ("我还没吃药", "medicine_query"),
    ("我没有吃药", "medicine_query"),
])
def test_medicine_taken_not_triggered_by_negatives(user_text: str, expected_name: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name != "medicine_taken", f"'{user_text}' 不应命中 medicine_taken"
    assert plan.name == expected_name, f"'{user_text}' 应命中 {expected_name}，实际: {plan.name}"


@pytest.mark.parametrize("user_text", [
    "测试用药提醒", "用药提醒", "该吃药了", "提醒我吃药", "吃药时间到了",
])
def test_medicine_reminder_keyword_variants(user_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == "medicine_reminder"
    assert plan.steps[0].type == "iot_scene"
    assert plan.steps[0].name == "medicine_mode"


@pytest.mark.parametrize("user_text", [
    "帮我找药", "药在哪里", "药在哪", "找一下药", "找下药", "拿药", "取药",
])
def test_find_medicine_keyword_variants(user_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == "find_medicine"
    assert plan.steps[0].type == "iot_scene"
    assert plan.steps[0].name == "find_medicine_mode"


@pytest.mark.parametrize("user_text", [
    "等会再吃药", "等会再吃", "等会儿再吃药", "等会儿再吃",
    "一会再吃药", "一会儿再吃药", "过会再吃药", "过会儿再吃药",
    "稍后再吃", "稍后提醒我", "十分钟后提醒我",
])
def test_medicine_snooze_keyword_variants(user_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == "medicine_snooze"
    assert plan.steps[0].meta.get("action") == "snooze"


@pytest.mark.parametrize("user_text", [
    "我今天吃药了吗", "我今天吃药没有", "今天吃药没", "今天吃药了吗",
    "今天有没有吃药", "我吃药了没有", "今天吃过药了吗", "今天服药了吗",
])
def test_medicine_query_keyword_variants(user_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == "medicine_query"
    assert plan.steps[0].type == "medicine_query_today"


# ---------------------------------------------------------------------------
# 新增：reset_system 包含 medicine_clear_today
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("user_text", ["系统复位", "恢复默认", "解除报警"])
def test_reset_system_contains_medicine_clear_today(user_text: str):
    plan = TaskOrchestrator().build_plan(user_text, {})
    assert plan.name == "reset_system"
    types = [s.type for s in plan.steps]
    assert "robot_stop" in types
    assert "iot_scene" in types
    assert "medicine_clear_today" in types
    iot_step = next(s for s in plan.steps if s.type == "iot_scene")
    assert iot_step.name == "reset_mode"


def test_executor_reset_calls_clear_today(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    mgr.mark_taken()
    assert mgr.query_today()["taken"] is True

    plan = TaskOrchestrator().build_plan("系统复位", {})
    spoken: list[str] = []
    actions = _FakeActionExecutor()
    iot = _FakeIoT()
    executor = TaskExecutor(
        iot_controller=iot,
        action_executor=actions,
        speak_callback=spoken.append,
        medicine_manager=mgr,
    )
    executor.execute(plan)
    assert mgr.query_today()["taken"] is False


# ---------------------------------------------------------------------------
# 新增：MedicineManager.clear_today()
# ---------------------------------------------------------------------------

def test_medicine_manager_clear_today(tmp_path: Path):
    mgr = MedicineManager(str(tmp_path / "medicine_status.json"))
    mgr.mark_taken()
    mgr.mark_snooze()
    mgr.mark_refused()
    result = mgr.clear_today()
    assert result["taken"] is False
    assert result["snoozed"] is False
    assert result["refused"] is False
    today = mgr.query_today()
    assert today["taken"] is False
    assert today["snoozed"] is False
    assert today["refused"] is False


# ---------------------------------------------------------------------------
# 新增：ActionExecutor.clear_interrupt()
# ---------------------------------------------------------------------------

def test_action_executor_clear_interrupt():
    from core.action_executor import ActionExecutor
    executor = ActionExecutor()
    executor.interrupt_current_action()
    assert executor._interrupt_event.is_set()
    executor.clear_interrupt()
    assert not executor._interrupt_event.is_set()
