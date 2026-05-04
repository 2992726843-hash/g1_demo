from __future__ import annotations

import time
from datetime import datetime
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
        medicine_manager=None,
    ):
        self.iot = iot_controller
        self.action_executor = action_executor
        self.speak_callback = speak_callback
        self.logger = logger or logging.getLogger("core.task_executor")
        self.set_state_callback = set_state_callback
        self.states = states or {}
        self.medicine_manager = medicine_manager
        self._runtime_context: Dict[str, Any] = {}

    def execute(self, plan: TaskPlan) -> dict:
        self._runtime_context = {}
        result = {
            "ok": True,
            "plan": plan.name,
            "failed_steps": [],
        }
        context = {
            "last_iot_control_failed": False,
            "last_iot_control_label": "",
            "last_iot_control_action": "",
            "last_iot_device_set_failed": False,
            "last_iot_device_set_label": "",
            "last_iot_group_control_failed": False,
            "last_iot_group_control_message": "",
            "last_reset_failed": False,
            "last_medicine_record_failed": False,
            "last_medicine_query_failed": False,
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
            self._execute_step(step=step, index=idx, result=result, context=context)

        if result["failed_steps"]:
            result["ok"] = False
        return result

    def _execute_step(self, step: TaskStep, index: int, result: dict, context: Dict[str, Any]) -> None:
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
                    if step.name == "reset_mode":
                        context["last_reset_failed"] = True
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
                    context["last_iot_control_failed"] = True
                    context["last_iot_control_label"] = (step.label or step.name or "该设备").strip()
                    context["last_iot_control_action"] = "打开"
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_device_on_failed"}
                    )
                    self.logger.warning("[IoT][WARN] 打开设备失败: %s", step.name)
                else:
                    context["last_iot_control_failed"] = False
                    context["last_iot_control_label"] = ""
                    context["last_iot_control_action"] = ""
                return

            if step_type == "iot_device_off":
                self._set_state("EXECUTING_IOT_ACTION")
                self.logger.info("[IoT] 关闭设备: %s", step.name)
                ok = bool(self.iot.device_off(step.name))
                if not ok:
                    context["last_iot_control_failed"] = True
                    context["last_iot_control_label"] = (step.label or step.name or "该设备").strip()
                    context["last_iot_control_action"] = "关闭"
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_device_off_failed"}
                    )
                    self.logger.warning("[IoT][WARN] 关闭设备失败: %s", step.name)
                else:
                    context["last_iot_control_failed"] = False
                    context["last_iot_control_label"] = ""
                    context["last_iot_control_action"] = ""
                return

            if step_type == "iot_device_set":
                self._set_state("EXECUTING_IOT_ACTION")
                service = str(step.meta.get("service", "turn_on") or "turn_on").strip() or "turn_on"
                raw_attrs = step.meta.get("attributes", {})
                attributes = raw_attrs if isinstance(raw_attrs, dict) else {}
                self.logger.info(
                    "[IoT] 设备属性设置: %s service=%s attributes=%s",
                    step.name,
                    service,
                    attributes,
                )
                ok = bool(self.iot.device_set(step.name, service=service, attributes=attributes))
                if not ok:
                    context["last_iot_device_set_failed"] = True
                    context["last_iot_device_set_label"] = (step.label or step.name or "该设备").strip()
                    result["failed_steps"].append(
                        {
                            "index": index,
                            "type": step_type,
                            "name": step.name,
                            "reason": "iot_device_set_failed",
                        }
                    )
                    self.logger.warning("[IoT][WARN] 设备属性设置失败: %s", step.name)
                else:
                    context["last_iot_device_set_failed"] = False
                    context["last_iot_device_set_label"] = ""
                return

            if step_type == "iot_group_control":
                self._set_state("EXECUTING_IOT_ACTION")
                operation = str(step.meta.get("operation", "") or "").strip().lower()
                raw_devices = step.meta.get("devices", [])
                devices = raw_devices if isinstance(raw_devices, list) else []
                label = (step.label or "设备组").strip()
                success: list[str] = []
                failed: list[str] = []
                self.logger.info("[IoT] 设备组控制: label=%s operation=%s devices=%s", label, operation, devices)
                for raw_name in devices:
                    name = str(raw_name or "").strip()
                    if not name:
                        continue
                    if operation == "on":
                        ok = bool(self.iot.device_on(name))
                    elif operation == "off":
                        ok = bool(self.iot.device_off(name))
                    else:
                        ok = False
                    if ok:
                        success.append(name)
                    else:
                        failed.append(name)

                if failed:
                    result["failed_steps"].append(
                        {
                            "index": index,
                            "type": step_type,
                            "label": label,
                            "failed": list(failed),
                        }
                    )
                    context["last_iot_group_control_failed"] = True
                    if success:
                        context["last_iot_group_control_message"] = (
                            f"部分灯光控制失败，请检查：{'、'.join(failed)}。"
                        )
                    else:
                        context["last_iot_group_control_message"] = (
                            f"{label}控制失败，请检查家电服务或设备连接。"
                        )
                    self.logger.warning("[IoT][WARN] 设备组控制失败: label=%s failed=%s", label, failed)
                else:
                    context["last_iot_group_control_failed"] = False
                    context["last_iot_group_control_message"] = ""
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

            if step_type == "medicine_record":
                action = str(step.meta.get("action", "") or "").strip().lower()
                if self.medicine_manager is None:
                    context["last_medicine_record_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_record_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] manager 未初始化，无法记录 action=%s", action)
                    return
                try:
                    if action == "reminded":
                        self.medicine_manager.mark_reminded()
                    elif action == "taken":
                        self.medicine_manager.mark_taken()
                    elif action == "refused":
                        self.medicine_manager.mark_refused()
                    elif action == "snooze":
                        minutes = step.meta.get("minutes", 10)
                        minutes_val = int(minutes) if minutes is not None else 10
                        self.medicine_manager.mark_snooze(minutes=minutes_val)
                    else:
                        raise ValueError(f"unsupported_action:{action}")
                except Exception as exc:  # noqa: BLE001
                    context["last_medicine_record_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_record_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] 记录失败 action=%s err=%s", action, exc)
                return

            if step_type == "medicine_query_today":
                if self.medicine_manager is None:
                    context["last_medicine_query_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_query_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] manager 未初始化，无法查询今日状态")
                    return
                try:
                    status = self.medicine_manager.query_today()
                    self._runtime_context["medicine_query"] = status if isinstance(status, dict) else {}
                except Exception as exc:  # noqa: BLE001
                    context["last_medicine_query_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_query_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] 查询失败 err=%s", exc)
                return

            if step_type == "medicine_clear_today":
                if self.medicine_manager is None:
                    self.logger.warning("[Medicine][WARN] manager 未初始化，跳过 clear_today")
                    return
                try:
                    self.medicine_manager.clear_today()
                    self.logger.info("[Medicine] clear_today 成功")
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[Medicine][WARN] clear_today 失败: %s", exc)
                return

            if step_type == "robot_stop":
                self._set_state("EXECUTING_ROBOT_ACTION")
                self.logger.info("[Robot] 停止当前动作并清空动作队列")
                if hasattr(self.action_executor, "interrupt_current_action"):
                    try:
                        self.action_executor.interrupt_current_action()
                    except Exception as exc:  # noqa: BLE001
                        self.logger.warning("[Robot][WARN] 中断当前动作失败: %s", exc)
                try:
                    self.action_executor.submit_action("stop", "")
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[Robot][WARN] 提交 stop 动作失败: %s", exc)
                # 短暂等待当前动作退出，再清除中断标志，让后续任务的动作可以正常执行
                time.sleep(0.1)
                if hasattr(self.action_executor, "clear_interrupt"):
                    try:
                        self.action_executor.clear_interrupt()
                    except Exception as exc:  # noqa: BLE001
                        self.logger.warning("[Robot][WARN] 清除中断标志失败: %s", exc)
                return

            if step_type == "robot":
                self._set_state("EXECUTING_ROBOT_ACTION")
                action = str(step.action or "").strip()
                target = str(step.target or "").strip()
                if not action:
                    result["failed_steps"].append({"index": index, "type": step_type, "reason": "empty_robot_action"})
                    self.logger.warning("[Robot][WARN] step=%s action 为空，已跳过", index)
                    return
                if (
                    context.get("last_iot_control_failed")
                    or context.get("last_iot_device_set_failed")
                    or context.get("last_iot_group_control_failed")
                ) and action == "wave_hand":
                    self.logger.info("[TaskPlan] 跳过成功反馈动作，因为上一步家电控制失败")
                    return
                self.logger.info("[Robot] 提交动作: %s target=%s", action, target)
                self.action_executor.submit_action(action, target)
                return

            if step_type == "speak":
                text = str(step.text or "").strip()
                if context.get("last_medicine_query_failed"):
                    text = "用药记录查询失败，请稍后再试。"
                    context["last_medicine_query_failed"] = False
                elif context.get("last_medicine_record_failed"):
                    text = "用药记录失败，请稍后再试。"
                    context["last_medicine_record_failed"] = False
                elif context.get("last_reset_failed"):
                    text = "系统复位失败，请检查家电服务连接。"
                    context["last_reset_failed"] = False
                elif context.get("last_iot_group_control_failed"):
                    text = str(context.get("last_iot_group_control_message") or "").strip()
                    context["last_iot_group_control_failed"] = False
                    context["last_iot_group_control_message"] = ""
                elif context.get("last_iot_device_set_failed"):
                    label = str(context.get("last_iot_device_set_label") or "该设备").strip()
                    text = f"{label}参数调节失败，请检查家电服务或设备连接。"
                    context["last_iot_device_set_failed"] = False
                    context["last_iot_device_set_label"] = ""
                elif context.get("last_iot_control_failed"):
                    label = str(context.get("last_iot_control_label") or "该设备").strip()
                    op = str(context.get("last_iot_control_action") or "操作").strip()
                    text = f"{label}{op}失败，请检查家电服务或设备连接。"
                    context["last_iot_control_failed"] = False
                    context["last_iot_control_label"] = ""
                    context["last_iot_control_action"] = ""
                elif not text and "medicine_query" in self._runtime_context:
                    text = self._medicine_query_speak_text(self._runtime_context.get("medicine_query"))
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

    @staticmethod
    def _medicine_query_speak_text(status: Any) -> str:
        data = status if isinstance(status, dict) else {}
        taken = bool(data.get("taken"))
        snoozed = bool(data.get("snoozed"))
        refused = bool(data.get("refused"))
        taken_at = str(data.get("taken_at") or "").strip()
        if taken and taken_at:
            try:
                dt = datetime.fromisoformat(taken_at)
                return f"您今天已在 {dt.strftime('%H:%M')} 记录服药。"
            except ValueError:
                return "您今天已经记录服药。"
        if taken:
            return "您今天已经记录服药。"
        if snoozed:
            return "您刚才选择稍后提醒，目前还没有记录服药。"
        if refused:
            return "您刚才表示暂时不想吃药，目前还没有记录服药。"
        return "今天还没有记录服药，请注意按时用药。"

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

