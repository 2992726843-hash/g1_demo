from __future__ import annotations

import time
from datetime import datetime
import logging
from typing import Any, Dict, Optional

from core.task_orchestrator import TaskPlan, TaskStep
from core.utils import ConfigLoader


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
        care_log_manager=None,
    ):
        self.iot = iot_controller
        self.action_executor = action_executor
        self.speak_callback = speak_callback
        self.logger = logger or logging.getLogger("core.task_executor")
        self.set_state_callback = set_state_callback
        self.states = states or {}
        self.medicine_manager = medicine_manager
        self.care_log_manager = care_log_manager
        self._runtime_context: Dict[str, Any] = {}
        self.pending_medicine_id: str | None = None
        self.pending_medicine_ts: float | None = None
        self.pending_medicine_ttl = 60.0
        try:
            cfg = ConfigLoader()
            self.perf_log = bool(cfg.get_nested("debug", "perf_log", default=True))
            self.verbose_log = bool(cfg.get_nested("debug", "verbose_log", default=True))
            self.task_step_log = bool(cfg.get_nested("debug", "task_step_log", default=self.verbose_log))
        except Exception:
            self.perf_log = True
            self.verbose_log = True
            self.task_step_log = True

    def set_pending_medicine(self, medicine_id: str | None, ts: float | None = None) -> None:
        med_id = str(medicine_id or "").strip()
        if not med_id:
            self.pending_medicine_id = None
            self.pending_medicine_ts = None
            return
        self.pending_medicine_id = med_id
        self.pending_medicine_ts = float(ts if ts is not None else time.time())
        try:
            if self.medicine_manager is not None and hasattr(self.medicine_manager, "set_pending_medicine"):
                self.medicine_manager.set_pending_medicine(med_id)
        except Exception:  # noqa: BLE001
            pass

    def reset_runtime_state(self) -> None:
        self._runtime_context = {}
        self.pending_medicine_id = None
        self.pending_medicine_ts = None

    def execute(self, plan: TaskPlan) -> dict:
        total_start = time.perf_counter()
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
            "last_medicine_record": None,
            "last_medicine_record_action": "",
        }
        steps = list(plan.steps or [])
        self.logger.info("[TaskPlan] 执行任务: %s source=%s", plan.name, plan.source)

        for idx, step in enumerate(steps, start=1):
            if self.task_step_log:
                self.logger.info(
                    "[TaskPlan] step=%s type=%s name=%s action=%s target=%s",
                    idx,
                    step.type,
                    step.name,
                    step.action,
                    step.target,
                )
            step_start = time.perf_counter()
            failed_before = len(result["failed_steps"])
            self._execute_step(step=step, index=idx, result=result, context=context)
            if self.perf_log:
                failed_after = len(result["failed_steps"])
                step_ok = failed_after == failed_before
                cost_ms = (time.perf_counter() - step_start) * 1000.0
                self.logger.info(
                    "[PERF][TaskExecutor] step=%s type=%s name=%s action=%s cost=%.1f ms ok=%s",
                    idx,
                    step.type,
                    step.name,
                    step.action,
                    cost_ms,
                    step_ok,
                )

        if result["failed_steps"]:
            result["ok"] = False
        if self.perf_log:
            total_ms = (time.perf_counter() - total_start) * 1000.0
            self.logger.info("[PERF][TaskExecutor] total cost=%.1f ms ok=%s", total_ms, result["ok"])
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
                self._debug_info("[IoT] 调用场景: %s", step.name)
                ok = bool(self.iot.call_scene(step.name))
                if not ok:
                    if step.name == "reset_mode":
                        context["last_reset_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "name": step.name, "reason": "iot_scene_failed"}
                    )
                    self.logger.warning("[IoT][WARN] 场景 %s 调用失败，请检查 iot_service 是否启动", step.name)
                elif step.name == "night_mode":
                    self._append_care_log(
                        "night_guidance",
                        "已启动起夜辅助",
                        source="task_executor",
                        detail={"scene": "night_mode"},
                    )
                return

            if step_type == "iot_device_on":
                self._set_state("EXECUTING_IOT_ACTION")
                self._debug_info("[IoT] 打开设备: %s", step.name)
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
                self._debug_info("[IoT] 关闭设备: %s", step.name)
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
                self._debug_info(
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
                self._debug_info("[IoT] 设备组控制: label=%s operation=%s devices=%s", label, operation, devices)
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
                self._debug_info("[IoT] 查询设备状态: %s", step.name)
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
                medicine_id = self._resolve_step_medicine_id(step)
                if self.medicine_manager is None:
                    context["last_medicine_record_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_record_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] manager 未初始化，无法记录 action=%s", action)
                    return
                try:
                    if action == "reminded":
                        record = self.medicine_manager.mark_reminded(medicine_id)
                    elif action == "taken":
                        record = self.medicine_manager.mark_taken(medicine_id)
                    elif action == "refused":
                        record = self.medicine_manager.mark_refused(medicine_id)
                    elif action == "snooze":
                        minutes = step.meta.get("minutes", 10)
                        minutes_val = int(minutes) if minutes is not None else 10
                        record = self.medicine_manager.mark_snooze(medicine_id, minutes=minutes_val)
                    else:
                        raise ValueError(f"unsupported_action:{action}")
                    context["last_medicine_record"] = record if isinstance(record, dict) else {}
                    context["last_medicine_record_action"] = action
                    if isinstance(record, dict) and record.get("medicine_id"):
                        self.set_pending_medicine(str(record.get("medicine_id")))
                    self._append_medicine_record_care_log(action, record if isinstance(record, dict) else {})
                except Exception as exc:  # noqa: BLE001
                    context["last_medicine_record_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_record_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] 记录失败 action=%s err=%s", action, exc)
                return

            if step_type == "care_log":
                if bool(step.meta.get("require_iot_success")) and (
                    context.get("last_iot_control_failed")
                    or context.get("last_iot_device_set_failed")
                    or context.get("last_iot_group_control_failed")
                ):
                    self._debug_info("[CareLog] 跳过记录，因为前序 IoT 控制失败")
                    return
                detail = step.meta.get("detail", {})
                self._append_care_log(
                    str(step.meta.get("event_type") or step.name or "").strip(),
                    str(step.meta.get("title") or step.text or "").strip(),
                    level=str(step.meta.get("level") or "info").strip() or "info",
                    source=str(step.meta.get("source") or "task_executor").strip(),
                    detail=detail if isinstance(detail, dict) else {},
                )
                return

            if step_type == "medicine_query_today":
                medicine_id = self._resolve_step_medicine_id(step)
                if self.medicine_manager is None:
                    context["last_medicine_query_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_query_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] manager 未初始化，无法查询今日状态")
                    return
                try:
                    if medicine_id:
                        status = self.medicine_manager.query_medicine_today(medicine_id)
                    else:
                        status = self.medicine_manager.query_today()
                    self._runtime_context["medicine_query"] = status if isinstance(status, dict) else {}
                except Exception as exc:  # noqa: BLE001
                    context["last_medicine_query_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_query_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] 查询失败 err=%s", exc)
                return

            if step_type == "medicine_query_all_today":
                if self.medicine_manager is None:
                    context["last_medicine_query_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_query_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] manager 未初始化，无法查询今日全部用药状态")
                    return
                try:
                    status = self.medicine_manager.query_all_today()
                    self._runtime_context["medicine_query_all"] = status if isinstance(status, dict) else {}
                except Exception as exc:  # noqa: BLE001
                    context["last_medicine_query_failed"] = True
                    result["failed_steps"].append(
                        {"index": index, "type": step_type, "reason": "medicine_query_failed"}
                    )
                    self.logger.warning("[Medicine][WARN] 查询全部用药状态失败 err=%s", exc)
                return

            if step_type == "medicine_clear_today":
                if self.medicine_manager is None:
                    self.logger.warning("[Medicine][WARN] manager 未初始化，跳过 clear_today")
                    return
                try:
                    self.medicine_manager.clear_today()
                    self.set_pending_medicine(None)
                    self._debug_info("[Medicine] clear_today 成功")
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[Medicine][WARN] clear_today 失败: %s", exc)
                return

            if step_type == "care_log_clear":
                try:
                    if self.care_log_manager is not None:
                        self.care_log_manager.clear_all()
                        self._debug_info("[CareLog] clear_all 成功")
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[CareLog][WARN] clear_all 失败: %s", exc)
                return

            if step_type == "robot_stop":
                self._set_state("EXECUTING_ROBOT_ACTION")
                self._debug_info("[Robot] 停止当前动作并清空动作队列")
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
                    self._debug_info("[TaskPlan] 跳过成功反馈动作，因为上一步家电控制失败")
                    return
                self._debug_info("[Robot] 提交动作: %s target=%s", action, target)
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
                elif not text and context.get("last_medicine_record") is not None:
                    text = self._medicine_record_speak_text(
                        context.get("last_medicine_record"),
                        str(context.get("last_medicine_record_action") or ""),
                    )
                    context["last_medicine_record"] = None
                    context["last_medicine_record_action"] = ""
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
                elif not text and "medicine_query_all" in self._runtime_context:
                    text = self._medicine_query_all_speak_text(self._runtime_context.get("medicine_query_all"))
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

    def _resolve_step_medicine_id(self, step: TaskStep) -> str:
        raw = step.meta.get("medicine_id", "")
        med_id = str(raw or "").strip()
        if med_id:
            return med_id
        med_id = self._get_valid_pending_medicine()
        if med_id:
            return med_id
        try:
            if self.medicine_manager is not None:
                pending = str(getattr(self.medicine_manager, "pending_medicine_id", "") or "").strip()
                if pending:
                    return pending
                default_id = str(getattr(self.medicine_manager, "default_medicine_id", "") or "").strip()
                if default_id:
                    return default_id
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _get_valid_pending_medicine(self) -> str:
        med_id = str(self.pending_medicine_id or "").strip()
        ts = self.pending_medicine_ts
        if not med_id or ts is None:
            return ""
        if time.time() - float(ts) > self.pending_medicine_ttl:
            self.pending_medicine_id = None
            self.pending_medicine_ts = None
            return ""
        return med_id

    @staticmethod
    def _format_time(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw)
            return f"{dt.hour}点{dt.minute:02d}分"
        except ValueError:
            return ""

    @classmethod
    def _medicine_record_speak_text(cls, status: Any, action: str) -> str:
        data = status if isinstance(status, dict) else {}
        name = str(data.get("display_name") or "该药").strip()
        if action == "taken":
            if bool(data.get("already_taken")):
                return f"{name}今天已经记录服用，请不要重复服用。"
            return f"已记录您今天已服用{name}。"
        if action == "refused":
            return f"我理解您现在不太想服用{name}，我已经帮您记录。如不确定，请联系家属或医生确认。"
        if action == "snooze":
            return f"好的，我稍后再提醒您确认{name}。"
        if action == "reminded":
            return "现在是用药时间，请按医嘱确认是否服用。"
        return "用药记录已更新。"

    def _append_medicine_record_care_log(self, action: str, record: Dict[str, Any]) -> None:
        event_map = {
            "taken": "medicine_taken",
            "refused": "medicine_refused",
            "snooze": "medicine_snoozed",
            "reminded": "medicine_reminded",
        }
        event_type = event_map.get(action)
        if not event_type:
            return
        medicine_id = str(record.get("medicine_id") or "").strip()
        medicine_name = str(record.get("display_name") or medicine_id or "该药").strip()
        title_map = {
            "taken": f"已记录服用{medicine_name}",
            "refused": f"已记录拒绝服用{medicine_name}",
            "snooze": f"已记录稍后提醒{medicine_name}",
            "reminded": "已发出用药提醒",
        }
        self._append_care_log(
            event_type,
            title_map.get(action, "用药记录已更新"),
            source="task_executor",
            detail={
                "medicine_id": medicine_id,
                "medicine_name": medicine_name,
            },
        )

    def _append_care_log(
        self,
        event_type: str,
        title: str,
        level: str = "info",
        source: str = "",
        detail: dict | None = None,
    ) -> None:
        try:
            if self.care_log_manager is not None:
                self.care_log_manager.append_event(
                    event_type=event_type,
                    title=title,
                    level=level,
                    source=source,
                    detail=detail if isinstance(detail, dict) else {},
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[CareLog] append failed event_type=%s err=%s", event_type, exc)

    @classmethod
    def _medicine_query_speak_text(cls, status: Any) -> str:
        data = status if isinstance(status, dict) else {}
        name = str(data.get("display_name") or "该药").strip()
        schedule_text = str(data.get("schedule_text") or "本地计划未登记").strip()
        dose = str(data.get("dose") or "本地剂量未登记").strip()
        taken = bool(data.get("taken"))
        snoozed = bool(data.get("snoozed"))
        refused = bool(data.get("refused"))
        taken_at_text = cls._format_time(data.get("taken_at"))
        if taken and taken_at_text:
            return f"{name}今天已经记录服用，记录时间是{taken_at_text}。"
        if taken:
            return f"{name}今天已经记录服用。"
        if snoozed:
            return f"{name}刚才选择稍后提醒，目前还没有记录服用。"
        if refused:
            return f"{name}今天记录为暂时不想服用，目前还没有记录服用。"
        return f"{name}今天还没有记录服用，本地计划为{schedule_text}，每次{dose}，请按医嘱确认。"

    @classmethod
    def _medicine_query_all_speak_text(cls, status: Any) -> str:
        data = status if isinstance(status, dict) else {}
        medicines = data.get("medicines", {})
        if not isinstance(medicines, dict) or not medicines:
            return "今天还没有可查询的用药记录。"
        taken_names = []
        not_taken_names = []
        other_parts = []
        for _medicine_id, raw_status in medicines.items():
            item = raw_status if isinstance(raw_status, dict) else {}
            name = str(item.get("display_name") or "该药").strip()
            if bool(item.get("taken")):
                taken_names.append(name)
            elif bool(item.get("snoozed")):
                other_parts.append(f"{name}稍后提醒")
            elif bool(item.get("refused")):
                other_parts.append(f"{name}暂未服用")
            else:
                not_taken_names.append(name)
        parts = []
        if taken_names:
            parts.append("今天已经记录服用：" + "、".join(taken_names))
        if not_taken_names:
            parts.append("还没有记录：" + "、".join(not_taken_names))
        if other_parts:
            parts.append("其他状态：" + "、".join(other_parts))
        return "。".join(parts) + "。"

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

    def _debug_info(self, msg: str, *args) -> None:
        if not getattr(self, "verbose_log", True):
            return
        self.logger.info(msg, *args)

    def _speak(self, text: str) -> None:
        msg = str(text or "").strip()
        if not msg:
            return
        self._set_state("SPEAKING")
        print(f"\n🗣️ G1 管家: {msg}\n")
        self._debug_info("[Speech] 播报: %s", msg)
        try:
            self.speak_callback(msg)
        except Exception:  # noqa: BLE001
            return
