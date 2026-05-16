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

    DEFAULT_LIGHT: str = "bedroom_light"
    DEFAULT_LIGHT_LABEL: str = "卧室灯"

    # 当前真实设备逻辑名 — 不含 living_room_light / medicine_light
    CANONICAL_DEVICE_LABELS: Dict[str, str] = {
        "bedroom_light": "卧室灯",
        "path_strip": "RGB灯带",
        "alarm_socket": "1号开关",
        "night_light_socket": "2号开关",
    }

    DEVICE_ALIASES: Dict[str, str] = {
        "卧室灯": "bedroom_light",
        "房间灯": "bedroom_light",
        "床头灯": "bedroom_light",
        "灯泡": "bedroom_light",
        "主灯": "bedroom_light",
        "灯": "bedroom_light",
        "灯带": "path_strip",
        "路径灯": "path_strip",
        "路径灯带": "path_strip",
        "氛围灯": "path_strip",
        "彩灯": "path_strip",
        "地灯": "path_strip",
        "1号开关": "alarm_socket",
        "一号开关": "alarm_socket",
        "开关1": "alarm_socket",
        "开关一": "alarm_socket",
        "一号插座": "alarm_socket",
        "1号插座": "alarm_socket",
        "报警器插座": "alarm_socket",
        "报警插座": "alarm_socket",
        "报警器": "alarm_socket",
        "2号开关": "night_light_socket",
        "二号开关": "night_light_socket",
        "开关2": "night_light_socket",
        "开关二": "night_light_socket",
        "二号插座": "night_light_socket",
        "2号插座": "night_light_socket",
        "备用插座": "night_light_socket",
        "小夜灯插座": "night_light_socket",
        # 兼容旧说法（映射到当前卧室灯）
        "客厅灯": "bedroom_light",
        "客厅": "bedroom_light",
        "夜灯": "night_light_socket",
        "小夜灯": "night_light_socket",
    }

    LIGHT_DEVICE_ALIASES: Dict[str, str] = {
        "卧室": "bedroom_light",
        "卧室灯": "bedroom_light",
        "房间": "bedroom_light",
        "房间灯": "bedroom_light",
        "床头灯": "bedroom_light",
        "灯泡": "bedroom_light",
        "灯": "bedroom_light",
        "客厅": "bedroom_light",
        "客厅灯": "bedroom_light",
        "主灯": "bedroom_light",
        "路径灯": "path_strip",
        "路径灯带": "path_strip",
        "灯带": "path_strip",
        "氛围灯": "path_strip",
        "彩灯": "path_strip",
        "地灯": "path_strip",
    }
    LIGHT_DEVICE_LABELS: Dict[str, str] = {
        "bedroom_light": "卧室灯",
        "path_strip": "RGB灯带",
    }
    RGB_COLOR_ALIASES: Dict[str, List[int]] = {
        "红色": [255, 0, 0],
        "红": [255, 0, 0],
        "蓝色": [0, 80, 255],
        "蓝": [0, 80, 255],
        "绿色": [0, 255, 0],
        "绿": [0, 255, 0],
        "黄色": [255, 200, 0],
        "黄": [255, 200, 0],
        "暖黄": [255, 180, 80],
        "白色": [255, 255, 255],
        "白": [255, 255, 255],
        "紫色": [180, 0, 255],
        "紫": [180, 0, 255],
    }
    RGB_EFFECT_ALIASES: Dict[str, str] = {
        "闪烁": "RGB Strobe",
        "频闪": "RGB Strobe",
        "呼吸": "RGB Breath",
        "渐变": "RGB Gradient",
        "彩色": "Colorful",
        "彩虹": "Colorful",
        "音乐": "Music",
    }
    STRIP_VIVID_KEYWORDS: List[str] = ["鲜艳", "更鲜艳", "颜色鲜艳", "饱和", "饱和度"]
    STRIP_SOFT_KEYWORDS: List[str] = ["柔和", "别太刺眼", "不要太刺眼", "太刺眼", "刺眼"]
    STRIP_FLASH_KEYWORDS: List[str] = ["醒目", "报警", "闪一点", "闪烁", "频闪"]
    DEVICE_GROUPS: Dict[str, Dict[str, Any]] = {
        "all_lights": {
            "label": "所有灯光",
            "devices": [
                "bedroom_light",
                "path_strip",
            ],
        },
        "main_lights": {
            "label": "主灯",
            "devices": [
                "bedroom_light",
            ],
        },
        "guide_lights": {
            "label": "引导灯",
            "devices": [
                "path_strip",
            ],
        },
        "alarm_devices": {
            "label": "报警设备",
            "devices": [
                "alarm_socket",
                "path_strip",
            ],
        },
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
    MEDICINE_REMINDER_KEYWORDS: List[str] = [
        "测试用药提醒",
        "用药提醒",
        "该吃药了",
        "提醒我吃药",
        "吃药时间到了",
    ]
    FIND_MEDICINE_KEYWORDS: List[str] = [
        "帮我找药",
        "药在哪里",
        "药在哪",
        "找一下药",
        "找下药",
        "找药",
        "拿药",
        "取药",
        "帮我找一下药",
        "帮我找下药",
        "帮我拿一下药",
    ]
    FIND_MEDICINE_PATTERNS: List[str] = [r"找.*药", r"拿.*药", r"取.*药"]
    MEDICINE_TAKEN_KEYWORDS: List[str] = [
        "我已经吃药了",
        "我吃过药了",
        "记录一下我吃药了",
        "我已经服药了",
    ]
    MEDICINE_TAKEN_PATTERNS: List[str] = [
        r"(已经|刚才|刚刚).*把药.*(吃|服).*了",
        r"(已经|刚才|刚刚).*药.*(吃|服).*了",
        r"药.*已经.*(吃|服).*了",
        r".*吃过药了",
        r".*服过药了",
        r"我.*(刚才|刚刚).*(服药|吃药)",
        r"我把药吃了",
    ]
    MEDICINE_TAKEN_NEGATIVE_KEYWORDS: List[str] = [
        "不想",
        "等会",
        "等会儿",
        "稍后",
        "一会",
        "一会儿",
        "过会",
        "过会儿",
        "还没",
        "没有",
        "没吃",
        "没服",
        "吃药没有",
        "吃药了吗",
        "有没有",
    ]
    MEDICINE_REFUSE_KEYWORDS: List[str] = [
        "我不想吃药",
        "不想吃药",
        "我不想吃",
    ]
    MEDICINE_SNOOZE_KEYWORDS: List[str] = [
        "等会再吃药",
        "等会再吃",
        "等会儿再吃药",
        "等会儿再吃",
        "一会再吃药",
        "一会儿再吃药",
        "过会再吃药",
        "过会儿再吃药",
        "稍后再吃",
        "稍后提醒我",
        "十分钟后提醒我",
    ]
    MEDICINE_QUERY_KEYWORDS: List[str] = [
        "我今天吃药了吗",
        "我今天吃药没有",
        "今天吃药没",
        "今天吃药了吗",
        "今天有没有吃药",
        "我吃药了没有",
        "今天吃过药了吗",
        "今天服药了吗",
        "我还没吃药",
        "我没有吃药",
        "还没吃药",
        "没有吃药",
    ]
    FALL_KEYWORDS: List[str] = ["测试跌倒", "测试摔倒", "跌倒", "摔倒", "倒地", "报警", "救命"]
    RESET_SYSTEM_KEYWORDS: List[str] = [
        "复位",
        "一键复位",
        "系统复位",
        "恢复默认",
        "恢复默认状态",
        "全部关闭",
        "关闭所有设备",
        "重置系统",
        "解除报警",
        "停止报警",
    ]
    STATUS_QUERY_KEYWORDS: List[str] = [
        "检查",
        "检查一下",
        "看看",
        "看一下",
        "查一下",
        "查询",
        "状态",
        "是不是开",
        "是不是关",
        "开了吗",
        "关了吗",
        "有没有开",
        "有没有关",
        "现在开着吗",
        "现在关着吗",
        # 兼容旧短语
        "是不是",
    ]
    ON_KEYWORDS: List[str] = ["打开", "开启", "开一下", "打开一下", "启动"]
    OFF_KEYWORDS: List[str] = ["关闭", "关掉", "关一下", "关了", "关上", "停止"]

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

    IOT_SCENE_ACTIONS: set[str] = {"night_mode", "medicine_mode", "find_medicine_mode", "fall_alert", "reset_mode"}
    IOT_ON_ACTIONS: set[str] = {"light_on", "fan_on", "ac_on"}
    IOT_OFF_ACTIONS: set[str] = {"light_off", "fan_off", "ac_off"}

    def build_plan(self, user_text: str, intent: dict) -> TaskPlan:
        text = str(user_text or "").strip()
        norm = self._normalize_text(text)
        intent_dict = intent if isinstance(intent, dict) else {}

        # 规则优先级 1：紧急/场景化规则
        if self._contains_any(norm, self.NIGHT_KEYWORDS):
            return TaskPlan(
                name="night_guidance",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_scene", name="night_mode"),
                    TaskStep(type="speak", text="夜间辅助灯已经打开，我带您去卫生间，请慢一点。"),
                    TaskStep(type="robot", action="right_hand_up"),
                ],
            )

        if self._contains_any(norm, self.RESET_SYSTEM_KEYWORDS):
            return TaskPlan(
                name="reset_system",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="robot_stop"),
                    TaskStep(type="iot_scene", name="reset_mode"),
                    TaskStep(type="medicine_clear_today"),
                    TaskStep(type="speak", text="系统已恢复默认状态。"),
                ],
            )

        group_control_plan = self._plan_device_group_control(text, norm, intent_dict)
        if group_control_plan is not None:
            return group_control_plan

        light_adjust_plan = self._plan_light_param_adjust(text, norm, intent_dict)
        if light_adjust_plan is not None:
            return light_adjust_plan

        if self._contains_any(norm, self.FALL_KEYWORDS) and not self._contains_any(
            norm, ["报警器", "报警插座", "报警器插座"]
        ):
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

        medicine_plan = self._plan_medicine_interaction(text, norm, intent_dict)
        if medicine_plan is not None:
            return medicine_plan

        # 规则优先级 4：家电状态查询（须在 LLM fallback 之前，避免误落 chat）
        alias_hit = self._match_device_alias(norm)
        if alias_hit is not None and self._contains_any(norm, self.STATUS_QUERY_KEYWORDS):
            _, device_name = alias_hit
            label = self.CANONICAL_DEVICE_LABELS.get(device_name, alias_hit[0])
            return TaskPlan(
                name="iot_status_query",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_query", name=device_name, label=label),
                ],
            )

        # 规则优先级 5：家电开关控制
        if alias_hit is not None:
            _, device_name = alias_hit
            label = self.CANONICAL_DEVICE_LABELS.get(device_name, alias_hit[0])
            if self._contains_any(norm, self.ON_KEYWORDS):
                return TaskPlan(
                    name="iot_device_control",
                    source="rule",
                    user_text=text,
                    intent=intent_dict,
                    steps=[
                        TaskStep(type="iot_device_on", name=device_name, label=label),
                        TaskStep(type="robot", action="heart"),
                        TaskStep(type="speak", text=f"{label}已经打开。"),
                    ],
                )
            if self._contains_any(norm, self.OFF_KEYWORDS):
                return TaskPlan(
                    name="iot_device_control",
                    source="rule",
                    user_text=text,
                    intent=intent_dict,
                    steps=[
                        TaskStep(type="iot_device_off", name=device_name, label=label),
                        TaskStep(type="robot", action="heart"),
                        TaskStep(type="speak", text=f"{label}已经关闭。"),
                    ],
                )

        default_light_plan = self._plan_default_light_switch(text, norm, intent_dict)
        if default_light_plan is not None:
            return default_light_plan

        # 规则优先级 6：LLM fallback
        return self._build_llm_fallback_plan(text, intent_dict)

    @staticmethod
    def _contains_any(text: str, keywords: List[str]) -> bool:
        if not text:
            return False
        return any(k in text for k in keywords)

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _normalize_text(self, text: str) -> str:
        s = (text or "").strip()
        s = s.replace(" ", "").replace("\u3000", "")
        replacements = {
            "开关一": "开关1",
            "一号开关": "1号开关",
            "开关二": "开关2",
            "二号开关": "2号开关",
            "一号插座": "1号插座",
            "二号插座": "2号插座",
        }
        for old, new in replacements.items():
            s = s.replace(old, new)
        return s

    @staticmethod
    def _matches_any_pattern(text: str, patterns: List[str]) -> bool:
        if not text:
            return False
        return any(re.search(p, text) is not None for p in patterns)

    def _plan_medicine_interaction(self, text: str, norm: str, intent_dict: Dict[str, Any]) -> TaskPlan | None:
        if self._contains_any(norm, self.MEDICINE_QUERY_KEYWORDS):
            return TaskPlan(
                name="medicine_query",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="medicine_query_today"),
                    TaskStep(type="speak", text=""),
                ],
            )

        _taken_hit = (
            self._contains_any(norm, self.MEDICINE_TAKEN_KEYWORDS)
            or self._matches_any_pattern(norm, self.MEDICINE_TAKEN_PATTERNS)
        )
        if _taken_hit and not self._contains_any(norm, self.MEDICINE_TAKEN_NEGATIVE_KEYWORDS):
            return TaskPlan(
                name="medicine_taken",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="medicine_record", meta={"action": "taken"}),
                    TaskStep(type="iot_scene", name="reset_mode"),
                    TaskStep(type="robot", action="clap"),
                    TaskStep(type="speak", text="已记录您今天已服药。"),
                ],
            )

        if self._contains_any(norm, self.MEDICINE_REFUSE_KEYWORDS):
            return TaskPlan(
                name="medicine_refuse",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="medicine_record", meta={"action": "refused"}),
                    TaskStep(type="robot", action="x_ray"),
                    TaskStep(type="speak", text="我理解您现在不太想吃药，但按时用药很重要。要不要我稍后再提醒您？"),
                ],
            )

        if self._contains_any(norm, self.MEDICINE_SNOOZE_KEYWORDS):
            return TaskPlan(
                name="medicine_snooze",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="medicine_record", meta={"action": "snooze", "minutes": 10}),
                    TaskStep(type="robot", action="wave_hand"),
                    TaskStep(type="speak", text="好的，我稍后再提醒您。"),
                ],
            )

        if self._contains_any(norm, self.FIND_MEDICINE_KEYWORDS) or self._matches_any_pattern(
            norm, self.FIND_MEDICINE_PATTERNS
        ):
            return TaskPlan(
                name="find_medicine",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_scene", name="find_medicine_mode"),
                    TaskStep(type="robot", action="wave_hand"),
                    TaskStep(type="speak", text="我已经帮您打开药品位置提示灯，请注意查看。"),
                ],
            )

        if self._contains_any(norm, self.MEDICINE_REMINDER_KEYWORDS):
            return TaskPlan(
                name="medicine_reminder",
                source="rule",
                user_text=text,
                intent=intent_dict,
                steps=[
                    TaskStep(type="iot_scene", name="medicine_mode"),
                    TaskStep(type="medicine_record", meta={"action": "reminded"}),
                    TaskStep(type="robot", action="wave_face"),
                    TaskStep(type="speak", text="现在是用药时间，请按时服药。"),
                ],
            )

        return None

    @staticmethod
    def _clamp_brightness(value: int) -> int:
        return max(1, min(255, int(value)))

    @staticmethod
    def _clamp_kelvin(value: int) -> int:
        return max(3000, min(6400, int(value)))

    @staticmethod
    def _extract_brightness_number(text: str) -> int | None:
        m = re.search(r"亮度\D*(\d{1,3})\b", text)
        if not m:
            return None
        return int(m.group(1))

    @staticmethod
    def _extract_kelvin_number(text: str) -> int | None:
        m = re.search(r"色温\D*(\d{4,5})\b", text)
        if not m:
            return None
        return int(m.group(1))

    @staticmethod
    def _subany(text: str, compact: str, *subs: str) -> bool:
        return any(s in text or s in compact for s in subs if s)

    def _detect_group_operation(self, text: str, compact: str) -> str:
        if self._subany(text, compact, "解除报警"):
            return "off"
        if self._contains_any(text, self.ON_KEYWORDS) or self._contains_any(compact, self.ON_KEYWORDS):
            return "on"
        if self._contains_any(text, self.OFF_KEYWORDS) or self._contains_any(compact, self.OFF_KEYWORDS):
            return "off"
        if self._subany(text, compact, "都打开", "全打开"):
            return "on"
        if self._subany(text, compact, "都关", "全关", "关灯光", "关了"):
            return "off"
        return ""

    def _match_device_group(self, text: str, compact: str) -> str:
        # 不把单独「报警器」归为设备组，以便「关掉报警器」走单路 alarm_socket
        if self._subany(text, compact, "解除报警", "报警设备"):
            return "alarm_devices"
        if self._subany(text, compact, "夜间辅助灯", "路径灯和夜灯", "引导灯"):
            return "guide_lights"
        if self._subany(text, compact, "所有主灯", "全部主灯", "主灯"):
            return "main_lights"
        if self._subany(text, compact, "所有灯光", "所有灯", "全部灯", "灯都", "灯光"):
            return "all_lights"
        return ""

    def _group_speak_text(self, label: str, operation: str) -> str:
        verb = "打开" if operation == "on" else "关闭"
        return f"{label}已经{verb}。"

    def _plan_device_group_control(self, text: str, compact_text: str, intent_dict: Dict[str, Any]) -> TaskPlan | None:
        operation = self._detect_group_operation(text, compact_text)
        if operation not in ("on", "off"):
            return None
        group_name = self._match_device_group(text, compact_text)
        if not group_name:
            return None
        group = self.DEVICE_GROUPS.get(group_name)
        if not isinstance(group, dict):
            return None
        label = str(group.get("label") or "设备组").strip() or "设备组"
        raw_devices = group.get("devices", [])
        devices = [str(d).strip() for d in raw_devices if str(d).strip()] if isinstance(raw_devices, list) else []
        if not devices:
            return None
        return TaskPlan(
            name="iot_group_control",
            source="rule",
            user_text=text,
            intent=intent_dict,
            steps=[
                TaskStep(
                    type="iot_group_control",
                    label=label,
                    meta={"operation": operation, "devices": devices},
                ),
                TaskStep(type="robot", action="heart"),
                TaskStep(type="speak", text=self._group_speak_text(label, operation)),
            ],
        )

    def _make_default_light_switch_plan(
        self,
        text: str,
        intent_dict: Dict[str, Any],
        operation: str,
    ) -> TaskPlan:
        step_type = "iot_device_on" if operation == "on" else "iot_device_off"
        action_text = "打开" if operation == "on" else "关闭"
        return TaskPlan(
            name="iot_device_control",
            source="rule",
            user_text=text,
            intent=intent_dict,
            steps=[
                TaskStep(type=step_type, name=self.DEFAULT_LIGHT, label=self.DEFAULT_LIGHT_LABEL),
                TaskStep(type="robot", action="heart"),
                TaskStep(type="speak", text=f"{self.DEFAULT_LIGHT_LABEL}已经{action_text}。"),
            ],
        )

    def _plan_default_light_switch(self, text: str, compact_text: str, intent_dict: Dict[str, Any]) -> TaskPlan | None:
        if not self._subany(text, compact_text, "开灯", "打开灯", "关灯", "关闭灯"):
            return None
        if self._subany(text, compact_text, "开灯", "打开灯"):
            return self._make_default_light_switch_plan(text, intent_dict, "on")
        if self._subany(text, compact_text, "关灯", "关闭灯"):
            return self._make_default_light_switch_plan(text, intent_dict, "off")
        return None

    def _generic_default_light_reason(self, text: str, compact: str) -> str:
        if self._subany(text, compact, "太刺眼", "有点刺眼", "刺眼"):
            return "default_too_bright"
        if self._subany(text, compact, "柔和一点", "灯光柔和", "柔和"):
            return "default_soft"
        if self._subany(text, compact, "光线太亮", "屋里太亮", "太亮"):
            return "default_too_bright"
        if self._subany(
            text,
            compact,
            "光线有点暗",
            "屋里有点暗",
            "房间有点暗",
            "有点暗",
            "太暗",
            "光线太暗",
            "帮我调亮一点",
            "调亮一点",
            "亮一点",
            "光线亮一点",
        ):
            return "default_brighten"
        return ""

    def _light_utterance_has_tuning_intent(self, text: str, compact: str) -> bool:
        if self._subany(
            text,
            compact,
            "太暗",
            "有点暗",
            "太亮",
            "有点亮",
            "调亮",
            "亮一点",
            "最亮",
            "调暗",
            "暗一点",
            "亮度",
            "调暖",
            "暖一点",
            "暖光",
            "调白",
            "白一点",
            "调冷",
            "冷一点",
            "色温",
            "阅读亮度",
            "阅读模式",
            "睡前灯光",
            "睡前模式",
            "看书",
            "调成阅读",
            "调成睡前",
            "夜间灯光",
            "夜间模式",
        ):
            return True
        if (
            self._contains_any(text, self.STRIP_VIVID_KEYWORDS)
            or self._contains_any(compact, self.STRIP_VIVID_KEYWORDS)
            or self._contains_any(text, self.STRIP_SOFT_KEYWORDS)
            or self._contains_any(compact, self.STRIP_SOFT_KEYWORDS)
            or self._contains_any(text, self.STRIP_FLASH_KEYWORDS)
            or self._contains_any(compact, self.STRIP_FLASH_KEYWORDS)
        ):
            return True
        if self._match_rgb_color(text, compact) is not None:
            return True
        if self._match_rgb_effect(text, compact) is not None:
            return True
        if "我要看书" in text or "我要睡觉" in text:
            return True
        return False

    def _is_light_simple_switch_only(self, text: str, compact: str, alias: str) -> bool:
        if alias not in text and alias not in compact:
            return False
        on_hit = self._contains_any(text, self.ON_KEYWORDS) or self._contains_any(compact, self.ON_KEYWORDS)
        off_hit = self._contains_any(text, self.OFF_KEYWORDS) or self._contains_any(compact, self.OFF_KEYWORDS)
        if not on_hit and not off_hit:
            return False
        if self._light_utterance_has_tuning_intent(text, compact):
            return False
        return True

    def _match_light_alias(self, text: str, compact_text: str) -> Tuple[str, str] | None:
        pairs = sorted(self.LIGHT_DEVICE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
        for alias, device in pairs:
            if alias in text or alias in compact_text:
                return alias, device
        return None

    def _match_rgb_color(self, text: str, compact_text: str) -> Tuple[str, List[int]] | None:
        pairs = sorted(self.RGB_COLOR_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
        for alias, color in pairs:
            if alias in text or alias in compact_text:
                return alias, list(color)
        return None

    def _match_rgb_effect(self, text: str, compact_text: str) -> Tuple[str, str] | None:
        pairs = sorted(self.RGB_EFFECT_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
        for alias, effect in pairs:
            if alias in text or alias in compact_text:
                return alias, effect
        return None

    def _light_set_success_speak(self, label: str, attrs: Dict[str, Any], reason: str) -> str:
        if reason == "brighten":
            return f"已为您调亮{label}。"
        if reason == "dim":
            return f"已为您调暗{label}。"
        if reason == "warm":
            return f"已为您把{label}调成暖光。"
        if reason == "white":
            return f"已为您把{label}调成白光。"
        if reason == "cold":
            return f"已为您把{label}调成冷光。"
        if reason == "rgb_color":
            return f"已为您把{label}调成指定颜色。"
        if reason == "rgb_effect":
            return f"已为您打开{label}灯效。"
        if reason == "rgb_color_effect":
            return f"已为您设置{label}颜色和灯效。"
        if reason == "strip_vivid":
            return f"已为您把{label}调得更鲜艳。"
        if reason == "strip_soft":
            return f"已为您把{label}调柔和一些。"
        if reason == "strip_flash":
            return f"已为您把{label}调成醒目的闪烁效果。"
        if reason == "default_brighten":
            return f"已为您把{label}调亮一些。"
        if reason == "default_too_bright":
            return f"已为您把{label}调暗并调成暖光。"
        if reason == "default_soft":
            return f"已为您把{label}调柔和一些。"
        if reason == "reading":
            return f"已为您把{label}调成阅读灯光。"
        if reason == "sleep":
            return f"已为您把{label}调成睡前灯光。"
        if reason == "night":
            return f"已为您把{label}调成夜间灯光。"
        if reason == "brightness_number" and "brightness" in attrs:
            return f"已为您把{label}亮度调到{attrs['brightness']}。"
        if reason == "kelvin_number" and "color_temp_kelvin" in attrs:
            return f"已为您把{label}色温调到{attrs['color_temp_kelvin']}。"
        return f"已为您调节{label}参数。"

    def _make_light_param_adjust_plan(
        self,
        user_text: str,
        intent_dict: Dict[str, Any],
        device_name: str,
        label: str,
        attrs: Dict[str, Any],
        reason: str,
    ) -> TaskPlan:
        return TaskPlan(
            name="light_param_adjust",
            source="rule",
            user_text=user_text,
            intent=intent_dict,
            steps=[
                TaskStep(
                    type="iot_device_set",
                    name=device_name,
                    label=label,
                    meta={"service": "turn_on", "attributes": dict(attrs)},
                ),
                TaskStep(type="robot", action="heart"),
                TaskStep(type="speak", text=self._light_set_success_speak(label, attrs, reason)),
            ],
        )

    def _plan_light_param_adjust(self, text: str, compact_text: str, intent_dict: Dict[str, Any]) -> TaskPlan | None:
        """灯光参数调节规则：仅允许白名单灯别名，并对 brightness / color_temp_kelvin 做 clamp。"""
        t = text
        c = compact_text

        implicit_sleep = ("我要睡觉" in t) or ("睡前模式" in t) or ("睡前灯光" in t)
        implicit_read = ("我要看书" in t) or ("阅读模式" in t) or ("阅读亮度" in t)
        implicit_night = ("夜间模式" in t) or ("夜间灯光" in t)
        generic_default_reason = self._generic_default_light_reason(t, c)

        alias_hit = self._match_light_alias(t, c)
        if alias_hit is None:
            if implicit_read or implicit_sleep or implicit_night:
                alias, device_name = self.DEFAULT_LIGHT_LABEL, self.DEFAULT_LIGHT
            elif generic_default_reason:
                alias, device_name = self.DEFAULT_LIGHT_LABEL, self.DEFAULT_LIGHT
            else:
                return None
        else:
            alias, device_name = alias_hit
        is_rgb_strip = device_name == "path_strip"

        generic_aliases = {"房间", "房间灯", "灯"}
        if generic_default_reason and device_name == self.DEFAULT_LIGHT and (
            alias_hit is None or alias in generic_aliases
        ):
            if generic_default_reason == "default_brighten":
                attrs = {
                    "brightness": self._clamp_brightness(180),
                    "color_temp_kelvin": self._clamp_kelvin(5000),
                }
            elif generic_default_reason == "default_soft":
                attrs = {
                    "brightness": self._clamp_brightness(80),
                    "color_temp_kelvin": self._clamp_kelvin(3000),
                }
            else:
                attrs = {
                    "brightness": self._clamp_brightness(60),
                    "color_temp_kelvin": self._clamp_kelvin(3000),
                }
            label = self.LIGHT_DEVICE_LABELS.get(device_name, alias)
            return self._make_light_param_adjust_plan(t, intent_dict, device_name, label, attrs, generic_default_reason)

        if implicit_sleep and not implicit_read:
            attrs = {"brightness": self._clamp_brightness(30)}
            if not is_rgb_strip:
                attrs["color_temp_kelvin"] = self._clamp_kelvin(3000)
            label = self.LIGHT_DEVICE_LABELS.get(device_name, alias)
            return self._make_light_param_adjust_plan(t, intent_dict, device_name, label, attrs, "sleep")
        if implicit_read:
            attrs = {"brightness": self._clamp_brightness(200)}
            if not is_rgb_strip:
                attrs["color_temp_kelvin"] = self._clamp_kelvin(5000)
            label = self.LIGHT_DEVICE_LABELS.get(device_name, alias)
            return self._make_light_param_adjust_plan(t, intent_dict, device_name, label, attrs, "reading")
        if implicit_night:
            attrs = {"brightness": self._clamp_brightness(50)}
            if is_rgb_strip:
                attrs["rgb_color"] = [255, 180, 80]
            else:
                attrs["color_temp_kelvin"] = self._clamp_kelvin(3000)
            label = self.LIGHT_DEVICE_LABELS.get(device_name, alias)
            return self._make_light_param_adjust_plan(t, intent_dict, device_name, label, attrs, "night")

        if self._is_light_simple_switch_only(t, c, alias):
            return None
        if not self._light_utterance_has_tuning_intent(t, c):
            return None

        b_raw = self._extract_brightness_number(t)
        k_raw = None if is_rgb_strip else self._extract_kelvin_number(t)
        b_val: int | None = self._clamp_brightness(b_raw) if b_raw is not None else None
        k_val: int | None = self._clamp_kelvin(k_raw) if k_raw is not None else None
        rgb_color: List[int] | None = None
        rgb_effect: str | None = None
        reason = ""

        if b_val is None:
            if self._subany(t, c, "最亮"):
                b_val = self._clamp_brightness(255)
                reason = "brightness_number"
            elif self._subany(t, c, "太暗", "有点暗", "调亮", "亮一点"):
                b_val = self._clamp_brightness(180)
                reason = "brighten"
            elif self._subany(t, c, "太亮", "有点亮", "调暗", "暗一点"):
                b_val = self._clamp_brightness(60)
                reason = "dim"
        else:
            reason = "brightness_number"

        if k_val is None and not is_rgb_strip:
            if self._subany(t, c, "调暖", "暖一点", "暖光"):
                k_val = self._clamp_kelvin(3000)
                reason = reason or "warm"
            elif self._subany(t, c, "调白", "白一点"):
                k_val = self._clamp_kelvin(5000)
                reason = reason or "white"
            elif self._subany(t, c, "调冷", "冷一点"):
                k_val = self._clamp_kelvin(6400)
                reason = reason or "cold"
        else:
            reason = reason or "kelvin_number"

        if is_rgb_strip:
            color_hit = self._match_rgb_color(t, c)
            effect_hit = self._match_rgb_effect(t, c)
            vivid_hit = self._contains_any(t, self.STRIP_VIVID_KEYWORDS) or self._contains_any(
                c, self.STRIP_VIVID_KEYWORDS
            )
            soft_hit = self._contains_any(t, self.STRIP_SOFT_KEYWORDS) or self._contains_any(
                c, self.STRIP_SOFT_KEYWORDS
            )
            flash_hit = self._contains_any(t, self.STRIP_FLASH_KEYWORDS) or self._contains_any(
                c, self.STRIP_FLASH_KEYWORDS
            )
            if vivid_hit:
                b_val = self._clamp_brightness(220)
                rgb_effect = "Colorful"
                reason = "strip_vivid"
            elif soft_hit:
                b_val = self._clamp_brightness(80)
                rgb_effect = "RGB Breath"
                reason = "strip_soft"
            elif flash_hit:
                b_val = self._clamp_brightness(255)
                rgb_effect = "RGB Strobe"
                reason = "strip_flash"
            if color_hit is not None:
                _color_alias, rgb_color = color_hit
                reason = reason or "rgb_color"
            if effect_hit is not None and rgb_effect is None:
                effect_alias, rgb_effect = effect_hit
                reason = "rgb_color_effect" if rgb_color is not None else "rgb_effect"
                if b_val is None:
                    if effect_alias in ("闪烁", "频闪"):
                        b_val = self._clamp_brightness(255)
                    else:
                        b_val = self._clamp_brightness(180)

        reading_style = self._subany(t, c, "阅读亮度", "阅读灯光", "阅读模式", "看书", "调成阅读")
        sleep_style = self._subany(t, c, "睡前灯光", "睡前模式", "调成睡前")
        if reading_style:
            if b_val is None:
                b_val = self._clamp_brightness(200)
            if k_val is None and not is_rgb_strip:
                k_val = self._clamp_kelvin(5000)
            reason = "reading"
        if sleep_style:
            if b_val is None:
                b_val = self._clamp_brightness(30)
            if k_val is None and not is_rgb_strip:
                k_val = self._clamp_kelvin(3000)
            reason = "sleep"

        if b_val is None and k_val is None and rgb_color is None and rgb_effect is None:
            return None

        attrs: Dict[str, Any] = {}
        if b_val is not None:
            attrs["brightness"] = b_val
        if k_val is not None:
            attrs["color_temp_kelvin"] = k_val
        if rgb_color is not None:
            attrs["rgb_color"] = rgb_color
        if rgb_effect is not None:
            attrs["effect"] = rgb_effect
        label = self.LIGHT_DEVICE_LABELS.get(device_name, alias)
        return self._make_light_param_adjust_plan(t, intent_dict, device_name, label, attrs, reason)

    def _match_device_alias(self, normalized_text: str) -> Tuple[str, str] | None:
        nt = normalized_text or ""
        if not nt:
            return None
        pairs = sorted(self.DEVICE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)
        for alias, device in pairs:
            if alias not in nt:
                continue
            if alias == "灯" and nt in ("关灯", "关闭灯", "开灯", "打开灯"):
                continue
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
                        TaskStep(type="robot", action="heart"),
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
                        TaskStep(type="iot_device_on", name=self.DEFAULT_LIGHT, label=self.DEFAULT_LIGHT_LABEL),
                        TaskStep(type="robot", action="heart"),
                        TaskStep(type="speak", text=reply or f"{self.DEFAULT_LIGHT_LABEL}已经打开。"),
                    ],
                )
            if action in self.IOT_OFF_ACTIONS:
                return TaskPlan(
                    name="iot_device_control",
                    source="llm_fallback",
                    user_text=user_text,
                    intent=intent,
                    steps=[
                        TaskStep(type="iot_device_off", name=self.DEFAULT_LIGHT, label=self.DEFAULT_LIGHT_LABEL),
                        TaskStep(type="robot", action="heart"),
                        TaskStep(type="speak", text=reply or f"{self.DEFAULT_LIGHT_LABEL}已经关闭。"),
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