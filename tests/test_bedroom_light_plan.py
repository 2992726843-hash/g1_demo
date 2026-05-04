"""TaskOrchestrator：灯光参数调节规则（无需 HA）。"""
from __future__ import annotations

import pytest

from core.task_executor import TaskExecutor
from core.task_orchestrator import TaskOrchestrator


def _first_set_step(plan, expected_name: str):
    assert plan.steps
    st = plan.steps[0]
    assert st.type == "iot_device_set"
    assert st.name == expected_name
    return st


@pytest.mark.parametrize(
    "user_text,expected_name,expected_attrs",
    [
        ("卧室有点太暗了", "bedroom_light", {"brightness": 180}),
        ("卧室灯太暗了", "bedroom_light", {"brightness": 180}),
        ("今天光线有点暗", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("屋里有点暗", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("房间有点暗", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("有点暗", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("太暗了", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("帮我调亮一点", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("今天光线太亮了", "bedroom_light", {"brightness": 60, "color_temp_kelvin": 3000}),
        ("有点刺眼", "bedroom_light", {"brightness": 60, "color_temp_kelvin": 3000}),
        ("太刺眼了", "bedroom_light", {"brightness": 60, "color_temp_kelvin": 3000}),
        ("柔和一点", "bedroom_light", {"brightness": 80, "color_temp_kelvin": 3000}),
        ("客厅有点太暗了", "bedroom_light", {"brightness": 180}),
        ("客厅灯太亮了", "bedroom_light", {"brightness": 60}),
        ("把卧室灯调亮一点", "bedroom_light", {"brightness": 180}),
        ("把卧室灯调暗一点", "bedroom_light", {"brightness": 60}),
        ("把客厅灯调暖一点", "bedroom_light", {"color_temp_kelvin": 3000}),
        ("把客厅灯调白一点", "bedroom_light", {"color_temp_kelvin": 5000}),
        ("卧室灯亮度调到80", "bedroom_light", {"brightness": 80}),
        ("客厅灯亮度调到180", "bedroom_light", {"brightness": 180}),
        ("卧室灯色温调到4000", "bedroom_light", {"color_temp_kelvin": 4000}),
        ("客厅灯色温调到5000", "bedroom_light", {"color_temp_kelvin": 5000}),
        ("把卧室灯调到最亮", "bedroom_light", {"brightness": 255}),
        ("卧室灯调冷一点", "bedroom_light", {"color_temp_kelvin": 6400}),
        ("把卧室灯调暗一点暖一点", "bedroom_light", {"brightness": 60, "color_temp_kelvin": 3000}),
        ("把卧室灯调亮一点，白一点", "bedroom_light", {"brightness": 180, "color_temp_kelvin": 5000}),
        ("卧室灯调成阅读灯光", "bedroom_light", {"brightness": 200, "color_temp_kelvin": 5000}),
        ("客厅灯调成阅读模式", "bedroom_light", {"brightness": 200, "color_temp_kelvin": 5000}),
        ("卧室灯调成睡前灯光", "bedroom_light", {"brightness": 30, "color_temp_kelvin": 3000}),
        ("我要看书", "bedroom_light", {"brightness": 200, "color_temp_kelvin": 5000}),
        ("我要睡觉", "bedroom_light", {"brightness": 30, "color_temp_kelvin": 3000}),
        ("灯带调成红色", "path_strip", {"rgb_color": [255, 0, 0]}),
        ("路径灯调成蓝色", "path_strip", {"rgb_color": [0, 80, 255]}),
        ("氛围灯改成暖黄", "path_strip", {"rgb_color": [255, 180, 80]}),
        ("灯带闪烁", "path_strip", {"brightness": 255, "effect": "RGB Strobe"}),
        ("灯带呼吸", "path_strip", {"brightness": 180, "effect": "RGB Breath"}),
        ("路径灯渐变", "path_strip", {"brightness": 180, "effect": "RGB Gradient"}),
        ("灯带调成红色并闪烁", "path_strip", {"brightness": 255, "rgb_color": [255, 0, 0], "effect": "RGB Strobe"}),
        ("路径灯改成蓝色亮一点", "path_strip", {"brightness": 180, "rgb_color": [0, 80, 255]}),
        ("灯带太亮了", "path_strip", {"brightness": 60}),
        ("灯带暗一点", "path_strip", {"brightness": 60}),
        ("卧室灯调成暖光", "bedroom_light", {"color_temp_kelvin": 3000}),
    ],
)
def test_light_param_adjust_iot_device_set(user_text: str, expected_name: str, expected_attrs: dict):
    orch = TaskOrchestrator()
    plan = orch.build_plan(user_text, {})
    assert plan.name == "light_param_adjust"
    step = _first_set_step(plan, expected_name)
    assert step.meta.get("service") == "turn_on"
    attrs = step.meta.get("attributes") or {}
    assert attrs == expected_attrs


@pytest.mark.parametrize(
    "user_text,expected_type,expected_name",
    [
        ("打开卧室灯", "iot_device_on", "bedroom_light"),
        ("关闭卧室灯", "iot_device_off", "bedroom_light"),
        ("打开客厅灯", "iot_device_on", "bedroom_light"),
        ("关闭客厅灯", "iot_device_off", "bedroom_light"),
        ("开灯", "iot_device_on", "bedroom_light"),
        ("关灯", "iot_device_off", "bedroom_light"),
        ("打开灯带", "iot_device_on", "path_strip"),
        ("关闭灯带", "iot_device_off", "path_strip"),
    ],
)
def test_plain_light_switch_still_uses_device_on_off(
    user_text: str,
    expected_type: str,
    expected_name: str,
):
    orch = TaskOrchestrator()
    plan = orch.build_plan(user_text, {})
    assert plan.name == "iot_device_control"
    assert plan.steps[0].type == expected_type
    assert plan.steps[0].name == expected_name


def test_clamp_brightness_zero_and_kelvin_low():
    orch = TaskOrchestrator()
    plan = orch.build_plan("把卧室灯亮度调到0", {})
    step = _first_set_step(plan, "bedroom_light")
    assert step.meta["attributes"]["brightness"] == 1

    plan2 = orch.build_plan("把卧室灯色温调到2500", {})
    step2 = _first_set_step(plan2, "bedroom_light")
    assert step2.meta["attributes"]["color_temp_kelvin"] == 3000


def test_bedroom_light_red_does_not_generate_rgb_color():
    orch = TaskOrchestrator()
    plan = orch.build_plan("卧室灯调成红色", {})
    if plan.steps and plan.steps[0].type == "iot_device_set":
        attrs = plan.steps[0].meta.get("attributes") or {}
        assert "rgb_color" not in attrs
        assert "effect" not in attrs


@pytest.mark.parametrize(
    "user_text,operation,expected_devices",
    [
        (
            "关闭所有灯光",
            "off",
            ["bedroom_light", "path_strip"],
        ),
        (
            "关掉所有灯光",
            "off",
            ["bedroom_light", "path_strip"],
        ),
        (
            "把所有灯都关了",
            "off",
            ["bedroom_light", "path_strip"],
        ),
        (
            "打开所有灯光",
            "on",
            ["bedroom_light", "path_strip"],
        ),
        (
            "关闭全部灯",
            "off",
            ["bedroom_light", "path_strip"],
        ),
        ("关闭主灯", "off", ["bedroom_light"]),
        ("打开主灯", "on", ["bedroom_light"]),
        ("打开夜间辅助灯", "on", ["path_strip"]),
        ("关闭夜间辅助灯", "off", ["path_strip"]),
    ],
)
def test_device_group_control_plan(user_text: str, operation: str, expected_devices: list[str]):
    orch = TaskOrchestrator()
    plan = orch.build_plan(user_text, {})
    assert plan.name == "iot_group_control"
    assert plan.source == "rule"
    assert plan.steps[0].type == "iot_group_control"
    assert plan.steps[0].meta["operation"] == operation
    assert plan.steps[0].meta["devices"] == expected_devices
    assert not (plan.steps[0].type == "iot_device_off" and plan.steps[0].name == "living_room_light")
    assert plan.name != "chat"


def test_all_lights_group_excludes_alarm_socket():
    orch = TaskOrchestrator()
    plan = orch.build_plan("关闭所有灯光", {})
    devices = plan.steps[0].meta["devices"]
    assert "alarm_socket" not in devices
    assert "living_room_light" not in devices
    assert "medicine_light" not in devices
    assert "night_light_socket" not in devices


class _FakeIoT:
    def __init__(self, fail: set[str] | None = None):
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []
        self.scenes: list[str] = []

    def device_on(self, name: str) -> bool:
        self.calls.append(("on", name))
        return name not in self.fail

    def device_off(self, name: str) -> bool:
        self.calls.append(("off", name))
        return name not in self.fail

    def call_scene(self, name: str) -> bool:
        self.scenes.append(name)
        return name not in self.fail


class _FakeActionExecutor:
    def __init__(self):
        self.actions: list[tuple[str, str]] = []
        self.interrupted = False

    def submit_action(self, action: str, target: str) -> None:
        self.actions.append((action, target))

    def interrupt_current_action(self) -> None:
        self.interrupted = True


def test_group_control_partial_failure_replaces_success_feedback():
    orch = TaskOrchestrator()
    plan = orch.build_plan("关闭主灯", {})
    spoken: list[str] = []
    actions = _FakeActionExecutor()
    executor = TaskExecutor(
        iot_controller=_FakeIoT(fail={"bedroom_light"}),
        action_executor=actions,
        speak_callback=spoken.append,
    )
    result = executor.execute(plan)
    assert result["ok"] is False
    assert actions.actions == []
    assert spoken == ["主灯控制失败，请检查家电服务或设备连接。"]


def test_llm_fallback_iot_action_uses_default_bedroom_light():
    orch = TaskOrchestrator()
    intent = {"category": "iot_action", "action": "light_on", "reply": ""}
    plan = orch.build_plan("请把灯打开", intent)
    assert plan.steps[0].type == "iot_device_on"
    assert plan.steps[0].name == "bedroom_light"


@pytest.mark.parametrize(
    "user_text,device,label",
    [
        ("检查一下1号开关", "alarm_socket", "1号开关"),
        ("检查一下开关1", "alarm_socket", "1号开关"),
        ("看看1号开关", "alarm_socket", "1号开关"),
        ("查一下报警器", "alarm_socket", "1号开关"),
        ("报警器现在开着吗", "alarm_socket", "1号开关"),
        ("检查一下2号开关", "night_light_socket", "2号开关"),
        ("看看备用插座是不是关了", "night_light_socket", "2号开关"),
    ],
)
def test_socket_status_query_rules(user_text: str, device: str, label: str):
    orch = TaskOrchestrator()
    plan = orch.build_plan(user_text, {})
    assert plan.name == "iot_status_query"
    assert plan.source == "rule"
    assert plan.steps[0].type == "iot_query"
    assert plan.steps[0].name == device
    assert plan.steps[0].label == label
    assert plan.name != "chat"


@pytest.mark.parametrize(
    "user_text,device",
    [
        ("打开1号开关", "alarm_socket"),
        ("关闭1号开关", "alarm_socket"),
        ("关掉报警器", "alarm_socket"),
        ("打开2号开关", "night_light_socket"),
        ("关闭备用插座", "night_light_socket"),
    ],
)
def test_socket_device_control_rules(user_text: str, device: str):
    orch = TaskOrchestrator()
    plan = orch.build_plan(user_text, {})
    assert plan.name == "iot_device_control"
    assert plan.source == "rule"
    st = plan.steps[0]
    assert st.name == device
    assert st.type in ("iot_device_on", "iot_device_off")


def test_close_all_lights_only_controls_light_group():
    orch = TaskOrchestrator()
    plan = orch.build_plan("关闭所有灯光", {})
    assert plan.name == "iot_group_control"
    st = plan.steps[0]
    assert st.type == "iot_group_control"
    assert st.meta["operation"] == "off"
    assert st.meta["devices"] == ["bedroom_light", "path_strip"]
    assert "alarm_socket" not in st.meta["devices"]
    assert "night_light_socket" not in st.meta["devices"]


@pytest.mark.parametrize("user_text", ["复位", "恢复默认", "解除报警", "全部关闭"])
def test_reset_system_plan(user_text: str):
    orch = TaskOrchestrator()
    plan = orch.build_plan(user_text, {})
    assert plan.name == "reset_system"
    assert plan.source == "rule"
    step_types = [st.type for st in plan.steps]
    assert "robot_stop" in step_types
    assert "iot_scene" in step_types
    assert "medicine_clear_today" in step_types
    assert "speak" in step_types
    iot_step = next(s for s in plan.steps if s.type == "iot_scene")
    assert iot_step.name == "reset_mode"


def test_reset_system_executor_calls_stop_and_scene():
    orch = TaskOrchestrator()
    plan = orch.build_plan("复位", {})
    spoken: list[str] = []
    iot = _FakeIoT()
    actions = _FakeActionExecutor()
    executor = TaskExecutor(iot_controller=iot, action_executor=actions, speak_callback=spoken.append)
    result = executor.execute(plan)
    assert result["ok"] is True
    assert actions.interrupted is True
    assert actions.actions == [("stop", "")]
    assert iot.scenes == ["reset_mode"]
    assert spoken == ["系统已恢复默认状态。"]


def test_reset_system_executor_replaces_success_speech_on_scene_failure():
    orch = TaskOrchestrator()
    plan = orch.build_plan("复位", {})
    spoken: list[str] = []
    iot = _FakeIoT(fail={"reset_mode"})
    actions = _FakeActionExecutor()
    executor = TaskExecutor(iot_controller=iot, action_executor=actions, speak_callback=spoken.append)
    result = executor.execute(plan)
    assert result["ok"] is False
    assert iot.scenes == ["reset_mode"]
    assert spoken == ["系统复位失败，请检查家电服务连接。"]
