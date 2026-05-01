from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.task_orchestrator import TaskPlan, TaskStep


class TaskExecutor:
    def __init__(
        self,
        iot_controller,
        action_executor,
        speak_callback,
        logger: Optional[logging.Logger] = None,
        set_state_callback=None,
        states: Optional[Dict[str, str]] = None,
    ):
        self.iot = iot_controller
        self.action_executor = action_executor
        self.speak_callback = speak_callback
        self.logger = logger or logging.getLogger("core.task_executor")
        self.set_state_callback = set_state_callback
        self.states = states or {}

    def execute(self, plan: TaskPlan) -> dict:
        result = {
            "ok": True,
            "plan": plan.name,
            "failed_steps": [],
        }
        steps = list(plan.steps or [])
        self.logger.info("[TaskPlan] 执行任务: %s source=%s", plan.name, plan.source)

        for idx, step in enumerate(steps, start=1):
            self.logger.info(
                "[TaskPlan] step=%s type=%s name=%s action=%s target=%s",
                idx,
                step.type,
                step.name,
                step.action,
                step.target,
            )
            self._execute_step(step=step, index=idx, result=result)

        if result["failed_steps"]:
            result["ok"] = False
        return result

    def _execute_step(self, step: TaskStep, index: int, result: dict) -> None:
        step_type = str(step.type or "").strip()
        if not step_type:
            result["failed_steps"].append({"index": index, "type": "", "reason": "empty_step_type"})
            self.logger.warning("[TaskPlan][WARN] step=%s 类型为空，已跳过", index)
            return

        try:
            if step_type == "iot_scene":
                self._set_state("EXECUTING_IOT_ACTION")
                self.logger.info("[IoT] 调用场景: %s", step.name)
                ok = bool(self.iot.call_scene(step.name))
                if not ok:
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_scene_failed"}
                    )
                    self.logger.warning("[IoT][WARN] 场景 %s 调用失败，请检查 iot_service 是否启动", step.name)
                return

            if step_type == "iot_device_on":
                self._set_state("EXECUTING_IOT_ACTION")
                self.logger.info("[IoT] 打开设备: %s", step.name)
                ok = bool(self.iot.device_on(step.name))
                if not ok:
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_device_on_failed"}
                    )
                    self.logger.warning("[IoT][WARN] 打开设备失败: %s", step.name)
                return

            if step_type == "iot_device_off":
                self._set_state("EXECUTING_IOT_ACTION")
                self.logger.info("[IoT] 关闭设备: %s", step.name)
                ok = bool(self.iot.device_off(step.name))
                if not ok:
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_device_off_failed"}
                    )
                    self.logger.warning("[IoT][WARN] 关闭设备失败: %s", step.name)
                return

            if step_type == "iot_query":
                self._set_state("EXECUTING_IOT_ACTION")
                self.logger.info("[IoT] 查询设备状态: %s", step.name)
                status: Dict[str, Any] = self.iot.get_status(step.name)
                label = (step.label or step.name or "该设备").strip()
                state = self._extract_state(status)
                if state == "on":
                    text = f"{label}现在是打开状态。"
                elif state == "off":
                    text = f"{label}现在是关闭状态。"
                else:
                    text = f"我暂时没有读取到{label}的状态，请检查家电服务是否启动。"
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_query_unknown_state"}
                    )
                self._speak(text)
                return

            if step_type == "robot":
                self._set_state("EXECUTING_ROBOT_ACTION")
                action = str(step.action or "").strip()
                target = str(step.target or "").strip()
                if not action:
                    result["failed_steps"].append({"index": index, "type": step_type, "reason": "empty_robot_action"})
                    self.logger.warning("[Robot][WARN] step=%s action 为空，已跳过", index)
                    return
                self.logger.info("[Robot] 提交动作: %s target=%s", action, target)
                self.action_executor.submit_action(action, target)
                return

            if step_type == "speak":
                text = str(step.text or "").strip()
                if text:
                    self._speak(text)
                return

            result["failed_steps"].append(
                {"index": index, "type": step_type, "name": step.name, "reason": "unsupported_step_type"}
            )
            self.logger.warning("[TaskPlan][WARN] 未支持的 step type: %s", step_type)
        except Exception as exc:  # noqa: BLE001
            result["failed_steps"].append(
                {"index": index, "type": step_type, "name": step.name, "reason": f"exception:{exc}"}
            )
            self.logger.exception("[TaskPlan][ERROR] step=%s type=%s 执行异常", index, step_type)

    @staticmethod
    def _extract_state(status: Dict[str, Any]) -> str:
        if not isinstance(status, dict):
            return ""
        state = status.get("state")
        if isinstance(state, str) and state.strip():
            return state.strip().lower()
        extra = status.get("extra")
        if isinstance(extra, dict):
            st2 = extra.get("state")
            if isinstance(st2, str) and st2.strip():
                return st2.strip().lower()
        return ""

    def _set_state(self, state_key: str) -> None:
        if self.set_state_callback is None:
            return
        state_value = self.states.get(state_key)
        if not state_value:
            return
        try:
            self.set_state_callback(state_value)
        except Exception:  # noqa: BLE001
            return

    def _speak(self, text: str) -> None:
        msg = str(text or "").strip()
        if not msg:
            return
        self._set_state("SPEAKING")
        print(f"\n🗣️ G1 管家: {msg}\n")
        self.logger.info("[Speech] 播报: %s", msg)
        try:
            self.speak_callback(msg)
        except Exception:  # noqa: BLE001
            return

