from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Tuple


@dataclass
class TaskStep:
    type: str
    name: str = ""
    label: str = ""
    action: str = ""
    target: str = ""
    text: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskPlan:
    name: str
    steps: List[TaskStep]
    source: str = "rule"
    user_text: str = ""
    intent: Dict[str, Any] = field(default_factory=dict)


class TaskOrchestrator:
    """轻量任务规划器：只负责 user_text + intent -> TaskPlan。"""

    DEVICE_ALIASES: Dict[str, str] = {
        "客厅灯": "living_room_light",
        "主灯": "living_room_light",
        "卧室灯": "bedroom_light",
        "路径灯": "path_strip",
        "灯带": "path_strip",
        "药盒灯": "medicine_light",
        "药盒提示灯": "medicine_light",
        "报警器": "alarm_socket",
        "开关1": "alarm_socket",
        "开关一": "alarm_socket",
        "一号开关": "alarm_socket",
        "夜灯": "night_light_socket",
        "小夜灯": "night_light_socket",
        "开关2": "night_light_socket",
        "开关二": "night_light_socket",
        "二号开关": "night_light_socket",
    }

    NIGHT_KEYWORDS: List[str] = [
        "起夜",
        "卫生间",
        "洗手间",
        "厕所",
        "廁所",
        "上厕所",
        "去卫生间",
        "夜里起来",
        "晚上起来",
    ]
    MEDICINE_KEYWORDS: List[str] = [
        "帮我找药",
        "找药",
        "吃药",
        "药盒",
        "用药",
        "服药",
        "藥",
        "帮我找一下药",
        "帮我找下药",
        "帮我拿一下药",
        "药在哪里",
        "药在哪",
    ]
    MEDICINE_PATTERNS: List[str] = [r"找.*药", r"拿.*药", r"取.*药", r"吃.*药"]
    FALL_KEYWORDS: List[str] = ["测试跌倒", "测试摔倒", "跌倒", "摔倒", "倒地", "报警", "救命"]
    QUERY_KEYWORDS: List[str] = [
        "看看",
        "看一下",
        "是不是",
        "状态",
        "开了吗",
        "关了吗",
        "有没有开",
        "有没有关",
    ]
    ON_KEYWORDS: List[str] = ["打开", "开启", "开一下", "打开一下"]
    OFF_KEYWORDS: List[str] = ["关闭", "关掉", "关一下"]

    ROBOT_ACTIONS: set[str] = {
        "navigate",
        "move",
        "stop",
        "wave_hand",
        "wave_face",
        "shake_hand",
        "high_five",
        "blow_kiss",
        "hug",
        "clap",
        "hands_up",
        "reject",
        "release_arm",
        "greet",
        "say_hello",
        "goodbye",
        "two_hand_kiss",
        "left_kiss",
        "right_kiss",
        "heart",
        "right_heart",
        "right_hand_up",
        "x_ray",
    }

    IOT_SCENE_ACTIONS: set[str] = {"night_mode", "medicine_mode", "fall_alert"}
    IOT_ON_ACTIONS: set[str] = {"light_on", "fan_on", "ac_on"}
    IOT_OFF_ACTIONS: set[str] = {"light_off", "fan_off", "ac_off"}

    def build_plan(self, user_text: str, intent: dict) -> TaskPlan:
        text = str(user_text or "").strip()
        compact_text = self._compact_text(text)
        intent_dict = intent if isinstance(intent, dict) else {}

        # 规则优先级 1：紧急/场景化规则
        if self._contains_any(text, self.NIGHT_KEYWORDS) or self._contains_any(compact_text, self.NIGHT_KEYWORDS):
            return TaskPlan(
                name="night_guidance",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_scene", name="night_mode"),
                    TaskStep(type="speak", text="夜间辅助灯已经打开，我带您去卫生间，请慢一点。"),
                    TaskStep(type="robot", action="navigate", target="卫生间"),
                    TaskStep(type="robot", action="wave_hand"),
                ],
            )

        if (
            self._contains_any(text, self.MEDICINE_KEYWORDS)
            or self._contains_any(compact_text, self.MEDICINE_KEYWORDS)
            or self._matches_any_pattern(compact_text, self.MEDICINE_PATTERNS)
        ):
            return TaskPlan(
                name="medicine_guidance",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_scene", name="medicine_mode"),
                    TaskStep(type="speak", text="药盒提示灯已经打开，请注意按时服药。"),
                    TaskStep(type="robot", action="wave_hand"),
                ],
            )

        if self._contains_any(text, self.FALL_KEYWORDS) or self._contains_any(compact_text, self.FALL_KEYWORDS):
            return TaskPlan(
                name="fall_alert",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_scene", name="fall_alert"),
                    TaskStep(type="speak", text="检测到可能的跌倒风险，我已启动报警联动。"),
                    TaskStep(type="robot", action="hands_up"),
                ],
            )

        # 规则优先级 2：家电状态查询
        alias_hit = self._match_device_alias(text, compact_text)
        if alias_hit is not None and (
            self._contains_any(text, self.QUERY_KEYWORDS) or self._contains_any(compact_text, self.QUERY_KEYWORDS)
        ):
            alias, device_name = alias_hit
            return TaskPlan(
                name="iot_status_query",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_query", name=device_name, label=alias),
                ],
            )

        # 规则优先级 3：家电开关控制
        if alias_hit is not None:
            alias, device_name = alias_hit
            if self._contains_any(text, self.ON_KEYWORDS) or self._contains_any(compact_text, self.ON_KEYWORDS):
                return TaskPlan(
                    name="iot_device_control",
                    source="rule",
                    user_text=text,
                    intent=intent_dict,
                    steps=[
                        TaskStep(type="iot_device_on", name=device_name, label=alias),
                        TaskStep(type="robot", action="wave_hand"),
                        TaskStep(type="speak", text=f"{alias}已经打开。"),
                    ],
                )
            if self._contains_any(text, self.OFF_KEYWORDS) or self._contains_any(compact_text, self.OFF_KEYWORDS):
                return TaskPlan(
                    name="iot_device_control",
                    source="rule",
                    user_text=text,
                    intent=intent_dict,
                    steps=[
                        TaskStep(type="iot_device_off", name=device_name, label=alias),
                        TaskStep(type="robot", action="wave_hand"),
                        TaskStep(type="speak", text=f"{alias}已经关闭。"),
                    ],
                )

        # 规则优先级 4：LLM fallback
        return self._build_llm_fallback_plan(text, intent_dict)

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        if not text:
            return False
        return any(k in text for k in keywords)

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @staticmethod
    def _matches_any_pattern(text: str, patterns: List[str]) -> bool:
        if not text:
            return False
        return any(re.search(p, text) is not None for p in patterns)

    def _match_device_alias(self, text: str, compact_text: str) -> Tuple[str, str] | None:
        if not text and not compact_text:
            return None
        # 优先匹配更长别名，避免“药盒提示灯”被“药盒灯”抢先匹配
        pairs = sorted(self.DEVICE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
        for alias, device in pairs:
            if alias in text or alias in compact_text:
                return alias, device
        return None

    def _build_llm_fallback_plan(self, user_text: str, intent: Dict[str, Any]) -> TaskPlan:
        category = str(intent.get("category", "") or "").strip()
        action = str(intent.get("action", "") or "").strip()
        target = str(intent.get("target", "") or "").strip()
        reply = str(intent.get("reply", "") or "").strip()

        if category == "robot_action" and action:
            return TaskPlan(
                name="robot_action",
                source="llm_fallback",
                user_text=user_text,
                intent=intent,
                steps=[
                    TaskStep(type="robot", action=action, target=target),
                    TaskStep(type="speak", text=reply or "好的。"),
                ],
            )

        if category == "iot_action":
            if action in self.IOT_SCENE_ACTIONS:
                return TaskPlan(
                    name="iot_scene",
                    source="llm_fallback",
                    user_text=user_text,
                    intent=intent,
                    steps=[
                        TaskStep(type="iot_scene", name=action),
                        TaskStep(type="robot", action="wave_hand"),
                        TaskStep(type="speak", text=reply or "家电场景已经执行。"),
                    ],
                )
            if action in self.IOT_ON_ACTIONS:
                return TaskPlan(
                    name="iot_device_control",
                    source="llm_fallback",
                    user_text=user_text,
                    intent=intent,
                    steps=[
                        TaskStep(type="iot_device_on", name="living_room_light", label="客厅灯"),
                        TaskStep(type="robot", action="wave_hand"),
                        TaskStep(type="speak", text=reply or "客厅灯已经打开。"),
                    ],
                )
            if action in self.IOT_OFF_ACTIONS:
                return TaskPlan(
                    name="iot_device_control",
                    source="llm_fallback",
                    user_text=user_text,
                    intent=intent,
                    steps=[
                        TaskStep(type="iot_device_off", name="living_room_light", label="客厅灯"),
                        TaskStep(type="robot", action="wave_hand"),
                        TaskStep(type="speak", text=reply or "客厅灯已经关闭。"),
                    ],
                )

        if action in self.ROBOT_ACTIONS:
            return TaskPlan(
                name="robot_action",
                source="llm_fallback",
                user_text=user_text,
                intent=intent,
                steps=[
                    TaskStep(type="robot", action=action, target=target),
                    TaskStep(type="speak", text=reply or "好的。"),
                ],
            )

        return TaskPlan(
            name="chat",
            source="llm_fallback",
            user_text=user_text,
            intent=intent,
            steps=[TaskStep(type="speak", text=reply or "我听到了。")],
        )