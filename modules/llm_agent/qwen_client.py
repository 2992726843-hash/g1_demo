from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

import requests

# 允许直接运行该文件：把项目根目录加入 sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.utils import ConfigLoader, setup_logger  # noqa: E402


class QwenAgent:
    """
    通义千问（DashScope OpenAI 兼容模式）接入客户端。

    关键约束：
    - System Prompt 强制模型只输出 JSON。
    - 三层防御解析器确保上层永远拿到结构正确且安全的 dict。
    - 动作集合从 `ActionExecutor.ACTION_MAP` 动态获取，避免 prompt 与系统能力脱节。
    """

    _FALLBACK: Dict[str, str] = {
        "category": "chat",
        "action": "none",
        "target": "",
        "reply": "抱歉，我没听清，您能再说一遍吗？",
    }

    # 比赛场景：类别只能四选一（高约束，低幻觉）
    CATEGORY_HINTS: List[str] = ["robot_action", "iot_action", "chat", "emergency"]

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger: logging.Logger = logger or setup_logger("modules.llm_agent.qwen_client")

        # 1) 先读取配置
        cfg = ConfigLoader()

        # 2) API Key：优先环境变量，其次配置文件（llm.api_key）
        env_key = str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
        cfg_key = str(cfg.get_nested("llm", "api_key", default="") or "").strip()
        self.api_key: str = env_key if env_key else cfg_key
        if not self.api_key:
            self.logger.warning("未检测到 DASHSCOPE_API_KEY；将无法调用真实大模型（仅能返回兜底）。")

        # 3) 可选配置：base_url / model_name
        self.base_url: str = str(cfg.get_nested("llm", "base_url", default="") or "")
        self.model_name: str = str(cfg.get_nested("llm", "model_name", default="") or "") or "qwen-turbo"

        # 3) 动态动作集合：从 ActionExecutor.ACTION_MAP.keys() 读取
        try:
            from core.action_executor import ActionExecutor

            dynamic_actions = list(ActionExecutor.ACTION_MAP.keys())
        except Exception as e:
            self.logger.warning("无法从 ActionExecutor 读取 ACTION_MAP，动态动作列表为空。error=%s", e)
            dynamic_actions = []

        # 比赛阶段只暴露“当前可稳定演示”的动作：
        # - 预留/未实现能力不进入 LLM 白名单，避免模型输出 custom_dance 这类无法演示的动作。
        dynamic_actions = [a for a in dynamic_actions if a not in {"custom_dance"}]

        # 系统基础动作（按需求：none / navigate / move 等）
        base_actions: List[str] = ["none", "navigate", "move", "stop"]
        # 比赛演示常用 IoT 动作：加入白名单，避免被误拦截
        iot_actions: List[str] = ["light_on", "light_off", "fan_on", "fan_off", "ac_on", "ac_off"]

        # 合并去重：保持稳定顺序，便于 prompt 一致性
        seen: set[str] = set()
        valid: List[str] = []
        for a in base_actions + iot_actions + dynamic_actions:
            s = str(a).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            valid.append(s)

        self.VALID_ACTIONS: List[str] = valid
        self._system_prompt: str = self._build_system_prompt()
        self.logger.info("QwenAgent 初始化完成：VALID_ACTIONS=%s", self.VALID_ACTIONS)

    def chat(self, user_text: str, context: str = "") -> Dict[str, str]:
        """
        核心通信方法：
        - requests 调用 DashScope OpenAI 兼容接口（temperature=0.2）
        - 将 context + user_text 拼装后发给模型
        - 网络失败/超时/接口报错：返回安全兜底字典
        """
        if not isinstance(user_text, str) or not user_text.strip():
            return dict(self._FALLBACK)

        # 比赛场景先求稳：常见指令直接短路，不走网络，避免幻觉/延迟/断网翻车
        shortcut = self._shortcut_intent(user_text)
        if shortcut is not None:
            return shortcut

        ctx = (context or "").strip()
        user_text_clean = user_text.strip()
        combined = f"context: {ctx}\nuser_text: {user_text_clean}" if ctx else f"user_text: {user_text_clean}"

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": combined},
        ]

        url = self._chat_completions_url(self.base_url)
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.2,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json()
            raw = self._extract_content(data)
        except Exception as e:
            self.logger.error("Qwen 请求失败（已返回安全兜底）。error=%s", e)
            self.logger.info("[LLM FALLBACK] 使用兜底回复")
            return dict(self._FALLBACK)

        return self._parse_and_sanitize(raw)

    def analyze_intent(self, user_text: str, context: dict) -> Dict[str, str]:
        """
        向后兼容接口：旧代码使用 analyze_intent(context: dict)。
        """
        try:
            ctx_str = json.dumps(context, ensure_ascii=False)
        except Exception:
            ctx_str = ""
        return self.chat(user_text=user_text, context=ctx_str)

    def _parse_and_sanitize(self, raw_text: str) -> Dict[str, str]:
        """
        【最核心：三层防御解析器】
        1) 正则提取：用 r"\\{.*?\\}" + DOTALL 抠出第一个 JSON 对象
        2) json.loads：失败立刻返回兜底
        3) 类型与边界校验重建：防止 null/非法类型/幻觉动作注入
        """
        # 第一层（正则提取）
        s = raw_text if isinstance(raw_text, str) else str(raw_text)
        m = re.search(r"\{.*?\}", s, flags=re.DOTALL)
        extracted = m.group(0) if m else ""

        # 第二层（解析与捕获）
        try:
            obj = json.loads(extracted)
        except json.JSONDecodeError as e:
            self.logger.error("LLM JSONDecodeError：%s raw=%r extracted=%r", e, raw_text, extracted)
            return dict(self._FALLBACK)
        except Exception as e:
            self.logger.error("LLM JSON 解析异常：%s raw=%r extracted=%r", e, raw_text, extracted)
            return dict(self._FALLBACK)

        if not isinstance(obj, dict):
            self.logger.error("LLM 返回非 dict JSON：type=%s raw=%r", type(obj), raw_text)
            return dict(self._FALLBACK)

        # 第三层（类型与边界校验重建）
        # 1) category：缺失/非法 -> chat
        category_raw = obj.get("category", "chat")
        category = str(category_raw).strip()
        if category not in self.CATEGORY_HINTS:
            self.logger.warning("LLM category 非法已纠正：category=%r raw=%r", category, raw_text)
            category = "chat"

        # 2) action：越界 -> none
        action_raw = obj.get("action", "none")
        action = str(action_raw).strip()
        if action != "none" and action not in self.VALID_ACTIONS:
            self.logger.warning(
                "LLM 幻觉动作已被拦截：action=%r valid=%s raw=%r",
                action,
                self.VALID_ACTIONS,
                raw_text,
            )
            action = "none"

        # 3) 一致性修正（比赛场景下先求稳，再求聪明）
        if category == "chat":
            # 普通对话：强制不做动作
            if action != "none":
                self.logger.warning("category=chat 但 action!=none，已强制改写为 none。action=%r raw=%r", action, raw_text)
            action = "none"
        elif action == "none":
            # 允许 category 保留（可能用于 UI 展示/后续策略），但记录日志便于排查
            self.logger.info("action=none 但 category=%s，将仅播报 reply。raw=%r", category, raw_text)

        target_raw = obj.get("target", "")
        target = "" if target_raw is None else str(target_raw)

        reply_raw = obj.get("reply", None)
        reply = self._FALLBACK["reply"] if reply_raw is None else (str(reply_raw).strip() or self._FALLBACK["reply"])

        return {"category": category, "action": action, "target": target, "reply": reply}

    def _build_system_prompt(self) -> str:
        valid_actions_str = ", ".join([json.dumps(a, ensure_ascii=False) for a in self.VALID_ACTIONS])
        return (
            "你的身份：宇树 G1 适老化智能看护管家。\n"
            "\n"
            "你的输出格式：绝对且仅能输出 JSON，格式为："
            '{"category": "...", "action": "...", "target": "...", "reply": "..."}'
            "。\n"
            "\n"
            "category 限制：必须且只能从以下四选一："
            "[\"robot_action\", \"iot_action\", \"chat\", \"emergency\"]。\n"
            "action 限制：必须从以下列表中选择，或为 \"none\"："
            f"[{valid_actions_str}]。\n"
            "如果只是普通对话，不需要执行动作：输出 category=\"chat\" 且 action=\"none\"。\n"
            "\n"
            "额外硬性规则：\n"
            "1) 禁止输出任何解释性文字。\n"
            "2) 禁止输出 Markdown，禁止输出 ```json 或 ```。\n"
            "3) target 若不需要必须为 \"\"。\n"
        )

    def _shortcut_intent(self, user_text: str) -> Optional[Dict[str, str]]:
        """
        比赛场景短路规则：
        - 先求稳：常见指令直接给确定输出，不走网络
        - 避免幻觉：把动作固定在白名单里
        - 比赛环境优先使用本地 shortcut，避免 LLM 网络波动。
        """
        t = (user_text or "").strip()
        if not t:
            return None

        # 比赛阶段 shortcut 只处理「单意图短句」；复合任务交给后续 LLM/API 解析链路，
        # 不在 shortcut 层强行截断，避免「向前走两步，再挥挥手」因含「挥手」被误判为单动作。
        if any(m in t for m in ("然后", "并且", "同时", ",", "，", "接着")):
            return None
        # 「再」作连接词时拦截；先去掉「再见」避免该词内的「再」字误判为复合句。
        if "再" in t.replace("再见", ""):
            return None

        # 只做非常简单的关键词匹配（不引入复杂 NLP）
        def _ret(category: str, action: str, target: str, reply: str) -> Optional[Dict[str, str]]:
            """
            shortcut 的硬约束：
            - action 必须在系统白名单内（ActionExecutor.ACTION_MAP + base_actions + iot_actions 合并结果）
            - 禁止返回示例菜单名/id，禁止返回裸数字 id
            """
            try:
                a = str(action or "").strip()
                if not a:
                    return None
                if a != "none" and a not in self.VALID_ACTIONS:
                    return None
                return {
                    "category": str(category or "chat").strip() or "chat",
                    "action": a,
                    "target": "" if target is None else str(target),
                    "reply": str(reply or "").strip() or self._FALLBACK["reply"],
                }
            except Exception:
                return None

        # ========== emergency / 急停 ==========
        if any(k in t for k in ("急停", "紧急停止", "立刻停止", "马上停下", "停止动作", "别动了")):
            return _ret("emergency", "stop", "", "好的，已立即停止。")

        # ========== 亲吻类（必须在通用“飞吻”之前）==========
        if any(k in t for k in ("双手飞吻", "双手亲吻")):
            return _ret("robot_action", "two_hand_kiss", "", "好的，送您一个双手飞吻。")
        if any(k in t for k in ("左边飞吻", "左手飞吻", "左吻")):
            return _ret("robot_action", "left_kiss", "", "好的，送您一个左手飞吻。")
        if any(k in t for k in ("右边飞吻", "右手飞吻", "右吻")):
            return _ret("robot_action", "right_kiss", "", "好的，送您一个右手飞吻。")

        # ========== 右手举起（必须放在普通“举手”之前）==========
        if any(k in t for k in ("右手举起", "举右手", "抬右手")):
            return _ret("robot_action", "right_hand_up", "", "好的，我举起右手。")

        # ========== 常用交互动作 ==========
        if any(k in t for k in ("握手", "握个手", "和我握手")):
            return _ret("robot_action", "shake_hand", "", "好的，和您握手。")
        if any(k in t for k in ("击掌", "high five", "High five", "来个击掌")):
            return _ret("robot_action", "high_five", "", "好的，来个击掌。")
        if any(k in t for k in ("拥抱", "抱抱", "给我一个拥抱")):
            return _ret("robot_action", "hug", "", "好的，给您一个拥抱。")
        if any(k in t for k in ("比心", "爱心", "比个心")):
            return _ret("robot_action", "heart", "", "好的，我来比个心。")
        if any(k in t for k in ("举手", "双手举起", "把手举起来", "hands up")):
            return _ret("robot_action", "hands_up", "", "好的，我把手举起来。")
        if any(k in t for k in ("拒绝", "摆手拒绝", "不可以", "不要这样")):
            return _ret("robot_action", "reject", "", "好的，我拒绝。")
        if any(k in t for k in ("x光", "X光", "x-ray", "X-ray", "扫描")):
            return _ret("robot_action", "x_ray", "", "好的，我来做个扫描动作。")

        if "挥手" in t:
            return _ret("robot_action", "wave_hand", "", "好的，我来挥挥手。")
        if "鼓掌" in t:
            return _ret("robot_action", "clap", "", "好的，我来鼓掌。")
        if "飞吻" in t:
            return _ret("robot_action", "blow_kiss", "", "好的，送您一个飞吻。")
        if any(k in t for k in ("打招呼", "打个招呼", "你好啊", "你好", "您好", "嗨")):
            return _ret("robot_action", "greet", "", "您好，我在。")
        if any(k in t for k in ("再见", "拜拜", "告别")):
            return _ret("robot_action", "goodbye", "", "好的，再见。")
        if "开灯" in t:
            return _ret("iot_action", "light_on", "light.living_room", "好的，我来开灯。")
        if "关灯" in t:
            return _ret("iot_action", "light_off", "light.living_room", "好的，我来关灯。")
        if any(k in t for k in ("关空调", "空调关掉")):
            return _ret("iot_action", "ac_off", "climate.bedroom_ac", "好的，我来帮您关闭空调。")
        if any(k in t for k in ("有点冷", "有点凉", "有点热", "太冷了")):
            return _ret("iot_action", "ac_on", "climate.bedroom_ac", "好的，我来帮您打开空调。")

        return None

    @staticmethod
    def _chat_completions_url(base_url: str) -> str:
        b = (base_url or "").strip()
        if not b:
            return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        b = b.rstrip("/")
        if b.endswith("/chat/completions"):
            return b
        if b.endswith("/v1"):
            return f"{b}/chat/completions"
        return f"{b}/v1/chat/completions"

    @staticmethod
    def _extract_content(resp_json: Dict[str, Any]) -> str:
        # OpenAI-compatible response: choices[0].message.content
        try:
            choices = resp_json.get("choices", [])
            if choices and isinstance(choices, list) and isinstance(choices[0], dict):
                msg = choices[0].get("message", {})
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    return content if isinstance(content, str) else str(content)
        except Exception:
            pass

        # Fallback: some providers use "output_text" etc.
        for k in ("output_text", "text", "content"):
            v = resp_json.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""


# 向后兼容旧类名（如果外部仍 import QwenClient）
QwenClient = QwenAgent


if __name__ == "__main__":
    client = QwenAgent()
    fake_context = {
        "time": "22:00",
        "location": "living_room",
        "light": "off",
        "user_profile": "有高血压",
    }
    result = client.chat("我有点头晕，我想休息了", context=json.dumps(fake_context, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=2))