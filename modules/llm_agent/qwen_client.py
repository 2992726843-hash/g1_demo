import json
import logging
import os
import re
import sys
from typing import Any, Dict, Optional

import requests

# Allow running this file directly: add project root to sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.utils import ConfigLoader, setup_logger  # noqa: E402


class QwenClient:
    SYSTEM_PROMPT: str = (
        "你是“适老化智能管家 G1”，负责把老人的自然语言转换为机器人可执行的控制意图。\n"
        "\n"
        "极其重要的输出规则（必须严格遵守）：\n"
        "1) 你【只能】输出一段“纯净 JSON”，禁止输出任何解释性文字。\n"
        "2) 禁止输出任何 Markdown 标记（例如 ```json / ```）。\n"
        '3) JSON 必须严格为：{"action":"...","target":"...","reply":"..."}，只能有这三个字段。\n'
        "4) action 的可选值仅限以下之一：\n"
        '   - "stand_up"\n'
        '   - "sit"\n'
        '   - "squat"\n'
        '   - "action_11"（飞吻）\n'
        '   - "action_17"（鼓掌）\n'
        '   - "action_26"（挥手）\n'
        '   - "noop"（无动作）\n'
        "5) target 是动作对象/区域；若不需要请置空字符串。\n"
        "6) reply 必须是第一人称、口语化的中文回复，体现对老人的关怀，并结合我给你的 context 环境状态进行情境化回应。\n"
        "7) 若用户意图不明确或存在风险（例如不适/头晕/高血压夜间等），优先选择 noop，并在 reply 中温和地建议休息/测量/寻求帮助。\n"
        "\n"
        "现在我会给你两段信息：\n"
        "- context：JSON 字符串，包含环境与老人状态\n"
        "- user_text：老人的原话\n"
        "请只输出最终 JSON。"
    )

    _ALLOWED_ACTIONS = {"stand_up", "sit", "squat", "action_11", "action_17", "action_26", "noop"}

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger: logging.Logger = logger or setup_logger("modules.llm_agent.qwen_client")

        cfg = ConfigLoader()
        self.api_key: str = str(cfg.get_nested("llm", "api_key", default="") or "")
        self.base_url: str = str(cfg.get_nested("llm", "base_url", default="") or "")
        self.model_name: str = str(cfg.get_nested("llm", "model_name", default="") or "")

        if not self.api_key:
            self.logger.warning("LLM api_key not found in config at llm.api_key.")

    def analyze_intent(self, user_text: str, context: dict) -> dict:
        fallback: Dict[str, str] = {
            "action": "noop",
            "target": "",
            "reply": "抱歉爷爷，我刚刚走神了，您能再说一遍吗？",
        }

        try:
            ctx_str = json.dumps(context, ensure_ascii=False)
        except Exception:
            ctx_str = "{}"

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"context: {ctx_str}"},
            {"role": "user", "content": f"user_text: {user_text.strip()}"},
        ]

        url = self._chat_completions_url(self.base_url)
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: Dict[str, Any] = {
            "model": self.model_name or "qwen-turbo",
            "messages": messages,
            "temperature": 0.2,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json()
            content = self._extract_content(data)
        except Exception as e:
            self.logger.warning("LLM request failed: %s", e)
            return fallback

        parsed = self._robust_parse_json(content)
        if parsed is None:
            self.logger.warning("LLM response JSON parse failed. raw=%r", content)
            return fallback

        action = str(parsed.get("action", "")).strip()
        target = str(parsed.get("target", "")).strip()
        reply = str(parsed.get("reply", "")).strip()

        if action not in self._ALLOWED_ACTIONS:
            self.logger.warning("LLM returned unsupported action: %r", action)
            return fallback
        if not isinstance(target, str) or not isinstance(reply, str):
            return fallback
        if not reply:
            return fallback

        return {"action": action, "target": target, "reply": reply}

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
            if choices and isinstance(choices, list):
                msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
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

    @staticmethod
    def _robust_parse_json(text: str) -> Optional[Dict[str, Any]]:
        if not isinstance(text, str):
            return None

        s = text.strip()
        if not s:
            return None

        # Remove common code fences like ```json ... ```
        s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)

        # Extract first JSON object block if there's extra noise
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            s = m.group(0).strip()

        # Attempt strict json parsing
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None


if __name__ == "__main__":
    client = QwenClient()
    fake_context = {
        "time": "22:00",
        "location": "living_room",
        "light": "off",
        "user_profile": "有高血压",
    }
    result = client.analyze_intent("我有点头晕，我想休息了", fake_context)
    print(json.dumps(result, ensure_ascii=False, indent=2))
