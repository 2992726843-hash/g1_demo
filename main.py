import json
import queue
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from core.utils import ConfigLoader, setup_logger
from core.action_executor import ActionExecutor
from core.task_executor import TaskExecutor
from core.task_orchestrator import TaskOrchestrator
from modules.llm_agent.qwen_client import QwenAgent
from modules.iot.iot_controller import IoTController
from modules.care import CareLogManager

FALL_TYPE_LABELS = {
    "front": "前向跌倒",
    "side": "侧向跌倒",
    "lost": "目标丢失或异常姿态",
}

MEDICINE_PROFILE_PATH = Path("data/medicine_profile.json")
MEDICINE_PATH_STRIP_COLORS = {
    "med_bp_001": [255, 128, 0],
    "med_vit_001": [0, 200, 0],
    "med_cal_001": [0, 120, 255],
}
MEDICINE_PATH_STRIP_BRIGHTNESS = 180


def _load_medicine_profile(logger) -> dict:
    try:
        content = MEDICINE_PROFILE_PATH.read_text(encoding="utf-8")
        parsed = json.loads(content) if content.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("profile_root_not_dict")
        medicines = parsed.get("medicines", {})
        if not isinstance(medicines, dict):
            raise ValueError("medicines_not_dict")
        return medicines
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("[MedicineVision] failed to load profile path=%s err=%s", MEDICINE_PROFILE_PATH, exc)
        return {}


def _speak_medicine_vision_decision(logger, speak_callback, decision: str, text: str) -> None:
    if logger is not None:
        logger.info("[MedicineVision] speak decision=%s", decision)
    print(f"\n🗣️ G1 管家: {text}\n")
    try:
        speak_callback(text)
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("[MedicineVision] speak callback failed err=%s", exc)


def _append_care_log(
    logger,
    care_log_manager,
    event_type: str,
    title: str,
    level: str = "info",
    source: str = "",
    detail: dict | None = None,
) -> None:
    try:
        if care_log_manager is not None:
            care_log_manager.append_event(
                event_type=event_type,
                title=title,
                level=level,
                source=source,
                detail=detail if isinstance(detail, dict) else {},
            )
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("[CareLog] append failed event_type=%s err=%s", event_type, exc)


def _set_medicine_path_strip(logger, iot_controller, medicine_id: str) -> None:
    rgb_color = MEDICINE_PATH_STRIP_COLORS.get(str(medicine_id or "").strip())
    if rgb_color is None:
        return
    if logger is not None:
        logger.info("[MedicineVision] path_strip color medicine_id=%s rgb_color=%s", medicine_id, rgb_color)
    if iot_controller is None:
        if logger is not None:
            logger.warning("[MedicineVision] iot controller unavailable, skip path_strip medicine_id=%s", medicine_id)
        return
    try:
        iot_controller.device_set(
            "path_strip",
            service="turn_on",
            attributes={
                "rgb_color": rgb_color,
                "brightness": MEDICINE_PATH_STRIP_BRIGHTNESS,
            },
        )
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("[MedicineVision] path_strip set failed medicine_id=%s err=%s", medicine_id, exc)


def _handle_medicine_vision_result(
    logger,
    medicine_manager,
    speak_callback,
    data: dict,
    set_pending_callback=None,
    iot_controller=None,
    care_log_manager=None,
) -> dict:
    request_id = str(data.get("request_id") or "").strip()
    mode = str(data.get("mode") or "").strip()
    success = bool(data.get("success", False))
    medicine_id = str(data.get("medicine_id") or "").strip()
    confidence = data.get("confidence", 0.0)
    if logger is not None:
        logger.info(
            "[MedicineVision] received result request_id=%s mode=%s success=%s medicine_id=%s confidence=%s",
            request_id,
            mode,
            success,
            medicine_id,
            confidence,
        )

    if str(data.get("event_type") or "").strip() != "medicine_vision_result":
        return {"ok": False, "message": "unknown event_type"}

    if not success:
        text = "我没有识别清楚，请把药盒正面对准摄像头后再试一次。"
        _append_care_log(
            logger,
            care_log_manager,
            "medicine_vision_failed",
            "药品视觉识别失败",
            source="medicine_vision",
            detail={"request_id": request_id, "medicine_id": medicine_id, "confidence": confidence},
        )
        _speak_medicine_vision_decision(logger, speak_callback, "failed", text)
        return {"ok": True, "decision": "failed", "request_id": request_id}

    profile = _load_medicine_profile(logger)
    medicine = profile.get(medicine_id) if medicine_id else None
    if not isinstance(medicine, dict):
        text = "我识别到一个未登记的药品，请您先人工确认，不要直接服用。"
        _append_care_log(
            logger,
            care_log_manager,
            "medicine_unknown",
            "识别到未登记药品",
            level="warning",
            source="medicine_vision",
            detail={"request_id": request_id, "medicine_id": medicine_id, "confidence": confidence},
        )
        _speak_medicine_vision_decision(logger, speak_callback, "unknown", text)
        return {"ok": True, "decision": "unknown", "request_id": request_id, "medicine_id": medicine_id}

    _set_medicine_path_strip(logger, iot_controller, medicine_id)

    display_name = str(medicine.get("display_name") or medicine.get("name") or "该药品").strip()
    dose = str(medicine.get("dose") or "本地档案未登记剂量").strip()
    schedule_text = str(medicine.get("schedule_text") or "本地档案未登记用药时间").strip()
    if logger is not None:
        logger.info("[MedicineVision] profile matched medicine_id=%s display_name=%s", medicine_id, display_name)
    _append_care_log(
        logger,
        care_log_manager,
        "medicine_visual_result",
        f"识别到{display_name}",
        source="medicine_vision",
        detail={
            "request_id": request_id,
            "medicine_id": medicine_id,
            "medicine_name": display_name,
            "confidence": confidence,
        },
    )

    today = {}
    if medicine_manager is None:
        if logger is not None:
            logger.warning("[MedicineVision] medicine manager unavailable")
    else:
        try:
            queried = medicine_manager.query_medicine_today(medicine_id)
            today = queried if isinstance(queried, dict) else {}
            if hasattr(medicine_manager, "set_pending_medicine"):
                medicine_manager.set_pending_medicine(medicine_id)
        except Exception as exc:  # noqa: BLE001
            if logger is not None:
                logger.warning("[MedicineVision] query_medicine_today failed medicine_id=%s err=%s", medicine_id, exc)
    try:
        if set_pending_callback is not None:
            set_pending_callback(medicine_id)
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("[MedicineVision] set pending callback failed err=%s", exc)
    taken = bool(today.get("taken"))
    if logger is not None:
        logger.info("[MedicineVision] today medicine_id=%s taken=%s", medicine_id, taken)
        logger.info("[MedicineVision] pending_medicine_id=%s", medicine_id)

    if taken:
        text = (
            f"我识别到这是{display_name}。今天已经记录服用，请不要重复服用。"
            "如不确定，请联系家属或医生确认。"
        )
        _speak_medicine_vision_decision(logger, speak_callback, "already_taken", text)
        return {"ok": True, "decision": "already_taken", "request_id": request_id, "medicine_id": medicine_id}

    text = (
        f"我识别到这是{display_name}，本地计划为{schedule_text}，每次{dose}。"
        "今天还没有记录服用。请您按照医嘱确认是否已经服用。"
    )
    _speak_medicine_vision_decision(logger, speak_callback, "should_confirm", text)
    return {"ok": True, "decision": "should_confirm", "request_id": request_id, "medicine_id": medicine_id}


class VisionFallEventHandler(BaseHTTPRequestHandler):
    """接收 G1 视觉模块 HTTP 事件。"""

    def log_message(self, format, *args):
        return

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path not in ("/api/vision/fall_event", "/api/medicine/vision_result"):
            self._send_json({"ok": False, "message": "bad request"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}

        if self.path == "/api/medicine/vision_result":
            srv_log = getattr(self.server, "vision_logger", None)
            medicine_manager = getattr(self.server, "medicine_manager", None)
            speak_callback = getattr(self.server, "speak_callback", None)
            set_pending_callback = getattr(self.server, "set_pending_callback", None)
            care_log_manager = getattr(self.server, "care_log_manager", None)
            if speak_callback is None:
                if srv_log is not None:
                    srv_log.warning("[MedicineVision] speak callback unavailable")
                self._send_json({"ok": False, "message": "service unavailable"})
                return
            response = _handle_medicine_vision_result(
                srv_log,
                medicine_manager,
                speak_callback,
                data,
                set_pending_callback=set_pending_callback,
                iot_controller=getattr(self.server, "iot_controller", None),
                care_log_manager=care_log_manager,
            )
            self._send_json(response)
            return

        # 字段解析（兼容旧版本：缺失字段给默认值）
        event_type = str(data.get("event_type") or "fall_alert").strip().lower()
        raw_is_fall = data.get("is_fall", None)
        is_fall = bool(raw_is_fall) if raw_is_fall is not None else True
        fall_type = data.get("fall_type", None)

        if event_type != "fall_alert":
            self._send_json({"ok": False, "message": "unknown event_type"})
            return

        srv_log = getattr(self.server, "vision_logger", None)
        event_queue = getattr(self.server, "emergency_queue", None)
        if event_queue is None:
            if srv_log is not None:
                srv_log.warning("[Emergency] 事件队列未初始化，忽略 HTTP 跌倒事件")
            self._send_json({"ok": False, "message": "service unavailable"})
            return

        if is_fall:
            if srv_log is not None:
                srv_log.info("收到 fall_alert 事件 is_fall=True fall_type=%s", fall_type)
            event_queue.put({
                "event_type": "fall_alert",
                "is_fall": True,
                "fall_type": fall_type,
                "source": "vision",
                "enqueued_at": time.time(),
            })
            self._send_json({"ok": True, "message": "fall event queued", "is_fall": True, "fall_type": fall_type})
        else:
            if srv_log is not None:
                srv_log.info("收到 fall_alert 解除事件 is_fall=False")
            event_queue.put({
                "event_type": "fall_alert",
                "is_fall": False,
                "fall_type": fall_type,
                "source": "vision",
                "enqueued_at": time.time(),
            })
            self._send_json({"ok": True, "message": "fall cleared queued", "is_fall": False})

    def do_GET(self) -> None:
        self._send_json({"ok": False, "message": "bad request"})


def _run_vision_event_http_server(
    logger,
    emergency_queue,
    medicine_manager=None,
    speak_callback=None,
    set_pending_callback=None,
    iot_controller=None,
    care_log_manager=None,
) -> None:
    httpd = HTTPServer(("0.0.0.0", 8765), VisionFallEventHandler)
    httpd.vision_logger = logger
    httpd.emergency_queue = emergency_queue
    httpd.medicine_manager = medicine_manager
    httpd.speak_callback = speak_callback
    httpd.set_pending_callback = set_pending_callback
    httpd.iot_controller = iot_controller
    httpd.care_log_manager = care_log_manager
    httpd.serve_forever()


# ===== 保留环境感知占位符（等待真实代码接入） =====
class VisionDetector:
    def __init__(self, logger):
        self.logger = logger

    def check_fall_status(self, last_user_input: str = "") -> bool:
        """
        比赛演示版手动跌倒检测（可控，不随机）：
        - 手动触发：当用户输入包含“测试跌倒 / 测试摔倒”时返回 True
        """
        if isinstance(last_user_input, str) and (
            ("测试跌倒" in last_user_input) or ("测试摔倒" in last_user_input)
        ):
            self.logger.info("[VisionDetector] 手动触发：测试跌倒/测试摔倒")
            return True
        return False


# ======= SystemCore 主控 =======
class SystemCore:
    # MVP 状态机：只允许这些状态（比赛演示用，避免逻辑发散）
    IDLE = "IDLE"
    CHECKING_VISION = "CHECKING_VISION"
    WAITING_INPUT = "WAITING_INPUT"
    PARSING_INTENT = "PARSING_INTENT"
    EXECUTING_ROBOT_ACTION = "EXECUTING_ROBOT_ACTION"
    EXECUTING_IOT_ACTION = "EXECUTING_IOT_ACTION"
    SPEAKING = "SPEAKING"
    EMERGENCY = "EMERGENCY"

    def __init__(self, logger):
        self.logger = logger
        try:
            self._perf_log = bool(ConfigLoader().get_nested("debug", "perf_log", default=True))
        except Exception:
            self._perf_log = True
        self.executor = ActionExecutor()
        self.llm_agent = QwenAgent(logger)
        self.vision = VisionDetector(logger)
        self.iot = IoTController()
        self.speech = None
        self._speech_enabled = False
        self._speech_input_mode = "text"
        self._speech_fallback_text = True
        self.state = self.IDLE
        self.running = True
        self._shutting_down = False
        self._reset_in_progress = False
        self.emergency_queue = queue.Queue()
        self._last_fall_event_ts = 0.0
        self._fall_event_cooldown = 5.0
        self._fall_active = False
        self.pending_medicine_id = None
        self.pending_medicine_ts = None
        self._pending_medicine_ttl = 60.0

        # ===== Speech（可选模块：缺依赖/无设备时必须降级，不允许崩溃）=====
        try:
            cfg = ConfigLoader()
            speech_cfg = cfg.get_nested("speech", default={}) or {}
            self._speech_enabled = bool(speech_cfg.get("enabled", False))
            self._speech_input_mode = str(speech_cfg.get("input_mode", "hybrid") or "hybrid").strip().lower()
            fb = speech_cfg.get("fallback", {}) or {}
            self._speech_fallback_text = bool(fb.get("text_input_on_asr_fail", True))
            if self._speech_enabled:
                try:
                    from modules.speech import SpeechManager

                    self.speech = SpeechManager(speech_cfg, logger=self.logger)
                    # 如果 ASR 不可用，也保持 speech 对象存在（用于 TTS/stop_speaking），输入自动走降级
                    if getattr(self.speech, "enabled", False):
                        self.logger.info("[SystemCore] Speech 已启用：input_mode=%s", self._speech_input_mode)
                    else:
                        self._speech_enabled = False
                except Exception as exc:  # noqa: BLE001
                    self._speech_enabled = False
                    self.speech = None
                    self.logger.warning("[SystemCore] Speech 初始化失败，已降级为文本输入：err=%s", exc)
        except Exception:  # noqa: BLE001
            self._speech_enabled = False
            self.speech = None

        self.medicine_manager = None
        try:
            from modules.medicine import MedicineManager

            self.medicine_manager = MedicineManager()
        except Exception as exc:  # noqa: BLE001
            self.medicine_manager = None
            self.logger.warning("[SystemCore] MedicineManager 初始化失败，已降级: err=%s", exc)

        self.care_log_manager = None
        try:
            self.care_log_manager = CareLogManager()
        except Exception as exc:  # noqa: BLE001
            self.care_log_manager = None
            self.logger.warning("[SystemCore] CareLogManager 初始化失败，已降级: err=%s", exc)

        self.task_orchestrator = TaskOrchestrator()
        self.task_executor = TaskExecutor(
            iot_controller=self.iot,
            action_executor=self.executor,
            speak_callback=self._speak_reply_safe,
            logger=self.logger,
            set_state_callback=self._set_state,
            states={
                "EXECUTING_IOT_ACTION": self.EXECUTING_IOT_ACTION,
                "EXECUTING_ROBOT_ACTION": self.EXECUTING_ROBOT_ACTION,
                "SPEAKING": self.SPEAKING,
            },
            medicine_manager=self.medicine_manager,
            care_log_manager=self.care_log_manager,
        )
        try:
            self.task_executor.perf_log = self._perf_log
        except Exception:
            pass

        self._emergency_worker_thread = threading.Thread(
            target=self._emergency_worker_loop,
            name="EmergencyWorker",
            daemon=True,
        )
        self._emergency_worker_thread.start()

        _vision_http_thread = threading.Thread(
            target=_run_vision_event_http_server,
            args=(
                self.logger,
                self.emergency_queue,
                self.medicine_manager,
                self._speak_reply_safe,
                self._set_pending_medicine,
                self.iot,
                self.care_log_manager,
            ),
            name="VisionFallEventHTTP",
            daemon=True,
        )
        _vision_http_thread.start()
        self.logger.info(
            "视觉事件服务已启动: host=0.0.0.0 port=8765 POST /api/vision/fall_event POST /api/medicine/vision_result"
        )

    def _stop_speaking_safe(self) -> None:
        """紧急流程/中断前尝试停止 TTS（失败也不影响主流程）。"""
        try:
            if self.speech is not None:
                self.speech.stop_speaking()
        except Exception:  # noqa: BLE001
            return

    def _speak_reply_safe(self, reply: str) -> None:
        """打印后可选 TTS 播报（失败降级，不影响 FSM）。"""
        try:
            if not self._speech_enabled or self.speech is None:
                self.logger.warning("[Speech][TTS][WARN] fallback to print because: speech module unavailable")
                return
            # tts.enabled 由 SpeechManager 内部处理（不可用会降级为 print）
            self.speech.speak(reply)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[Speech][TTS][WARN] fallback to print because: speak_callback failed: %s", exc)
            return

    def _set_pending_medicine(self, medicine_id: str | None) -> None:
        med_id = str(medicine_id or "").strip()
        if not med_id:
            self._clear_pending_medicine()
            return
        self.pending_medicine_id = med_id
        self.pending_medicine_ts = time.time()
        try:
            if self.medicine_manager is not None and hasattr(self.medicine_manager, "set_pending_medicine"):
                self.medicine_manager.set_pending_medicine(med_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self.task_executor, "set_pending_medicine"):
                self.task_executor.set_pending_medicine(med_id, self.pending_medicine_ts)
        except Exception:  # noqa: BLE001
            pass

    def _clear_pending_medicine(self) -> None:
        self.pending_medicine_id = None
        self.pending_medicine_ts = None
        try:
            if self.medicine_manager is not None and hasattr(self.medicine_manager, "clear_pending_medicine"):
                self.medicine_manager.clear_pending_medicine()
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self.task_executor, "set_pending_medicine"):
                self.task_executor.set_pending_medicine(None, None)
        except Exception:  # noqa: BLE001
            pass

    def _get_valid_pending_medicine(self) -> str | None:
        med_id = str(self.pending_medicine_id or "").strip()
        ts = self.pending_medicine_ts
        if not med_id or ts is None:
            return None
        if time.time() - float(ts) > self._pending_medicine_ttl:
            self.pending_medicine_id = None
            self.pending_medicine_ts = None
            try:
                if self.medicine_manager is not None and hasattr(self.medicine_manager, "clear_pending_medicine"):
                    self.medicine_manager.clear_pending_medicine()
            except Exception:  # noqa: BLE001
                pass
            return None
        return med_id

    def _perf(self, stage: str, start: float, metrics: dict | None = None) -> float:
        cost_ms = (time.perf_counter() - start) * 1000.0
        if metrics is not None:
            metrics[stage] = cost_ms
        if getattr(self, "_perf_log", True):
            self.logger.info("[PERF] %s cost=%.1f ms", stage, cost_ms)
        return cost_ms

    def _log_perf_summary(self, metrics: dict) -> None:
        if not getattr(self, "_perf_log", True):
            return
        order = [
            "wake_reply_tts",
            "command_record",
            "command_asr",
            "command_normalize",
            "wait_wake_and_transcribe_total",
            "llm_chat",
            "orchestrator_build_plan",
            "executor_execute_total",
            "user_command_pipeline_total",
            "voice_interaction_total_after_wake",
        ]
        parts = []
        for key in order:
            if key in metrics:
                parts.append(f"{key}={float(metrics[key]):.1f}ms")
        if parts:
            self.logger.info("[PERF][SUMMARY] %s", " ".join(parts))

    def _safe_stop_robot(self, reason: str) -> None:
        try:
            if hasattr(self.executor, "safe_stop_robot"):
                self.executor.safe_stop_robot(reason=reason)
            else:
                self.executor.interrupt_current_action()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore] 安全停止机器人失败: reason=%s err=%s", reason, exc)

    def _reset_robot_action_queue(self, reason: str) -> None:
        try:
            if hasattr(self.executor, "safe_stop_robot"):
                self.executor.safe_stop_robot(reason=reason)
            elif hasattr(self.executor, "interrupt_current_action"):
                self.executor.interrupt_current_action()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemReset][Robot] 请求当前动作停止失败: reason=%s err=%s", reason, exc)

        try:
            if hasattr(self.executor, "clear_pending_actions"):
                cleared = int(self.executor.clear_pending_actions())
                self.logger.info("[SystemReset][Robot] 已清空动作队列 count=%s", cleared)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemReset][Robot] 清空动作队列失败: %s", exc)

        try:
            if hasattr(self.executor, "clear_interrupt"):
                self.executor.clear_interrupt()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemReset][Robot] 清除中断标志失败: %s", exc)

        self.logger.info("[SystemReset][Robot] 已执行 safe_stop_robot，不再重复提交 stop 动作")

        try:
            if hasattr(self.executor, "clear_interrupt"):
                self.executor.clear_interrupt()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemReset][Robot] stop 后清除中断标志失败: %s", exc)

    def _perform_safe_reset(self, reason: str, clear_logs: bool = True, speak: bool = False) -> None:
        self.logger.info("[SystemReset] 开始安全复位 reason=%s clear_logs=%s", reason, clear_logs)
        reset_started_ts = time.time()
        self._reset_in_progress = True
        try:
            self._set_state(self.EMERGENCY)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._stop_speaking_safe()
            self._reset_robot_action_queue(reason=reason)
            self._clear_emergency_queue_safe(cutoff_ts=reset_started_ts)
            self._fall_active = False
            self._last_fall_event_ts = 0.0

            try:
                self._set_state(self.EXECUTING_IOT_ACTION)
            except Exception:  # noqa: BLE001
                pass
            for op, device in (
                ("off", "bedroom_light"),
                ("off", "path_strip"),
                ("on", "alarm_socket"),
                ("off", "night_light_socket"),
            ):
                try:
                    if op == "on":
                        ok = bool(self.iot.device_on(device))
                    else:
                        ok = bool(self.iot.device_off(device))
                    if not ok:
                        self.logger.warning("[SystemReset][IoT] %s %s failed", device, op)
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[SystemReset][IoT] %s %s exception: %s", device, op, exc)

            try:
                if self.medicine_manager is not None:
                    self.medicine_manager.clear_today()
                    self.logger.info("[SystemReset] 用药记录已清空")
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[SystemReset] 清空用药状态失败: %s", exc)

            self._clear_pending_medicine()
            try:
                if hasattr(self.task_executor, "reset_runtime_state"):
                    self.task_executor.reset_runtime_state()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[SystemReset] 清空任务运行态失败: %s", exc)

            if clear_logs:
                try:
                    if self.care_log_manager is not None:
                        self.care_log_manager.clear_all()
                        self.logger.info("[SystemReset] 看护记录已清空")
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[SystemReset] 清空看护记录失败: %s", exc)

            if speak:
                try:
                    self._set_state(self.SPEAKING)
                    self._speak_reply_safe("系统已复位，已清空当前演示状态。")
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[SystemReset] 复位播报失败: %s", exc)

            try:
                self._set_state(self.IDLE)
            except Exception:  # noqa: BLE001
                pass
            self.logger.info("[SystemReset] 安全复位完成 reason=%s", reason)
        finally:
            self._reset_in_progress = False

    def _clear_emergency_queue_safe(self, cutoff_ts: float | None = None) -> None:
        cleared = 0
        kept = []
        try:
            while True:
                try:
                    event = self.emergency_queue.get_nowait()
                    should_clear = True
                    if cutoff_ts is not None and isinstance(event, dict):
                        enqueued_at = event.get("enqueued_at")
                        try:
                            should_clear = float(enqueued_at) <= float(cutoff_ts) if enqueued_at is not None else True
                        except Exception:
                            should_clear = True
                    if should_clear:
                        cleared += 1
                    else:
                        kept.append(event)
                    try:
                        self.emergency_queue.task_done()
                    except Exception:
                        pass
                except queue.Empty:
                    break
            for event in kept:
                try:
                    self.emergency_queue.put_nowait(event)
                except Exception:
                    pass
            if cleared:
                self.logger.info("[SystemCore] 已清空紧急事件残留: count=%s kept=%s", cleared, len(kept))
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore] 清空紧急事件队列失败: %s", exc)

    def _fall_robot_action_config(self) -> tuple[bool, str]:
        try:
            cfg = ConfigLoader()
            enabled = bool(cfg.get_nested("emergency", "fall_robot_action_enabled", default=False))
            action = str(cfg.get_nested("emergency", "fall_robot_action", default="hands_up") or "hands_up").strip()
            return enabled, action
        except Exception:
            return False, "hands_up"

    def _emergency_worker_loop(self) -> None:
        while self.running:
            try:
                event = self.emergency_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if event is None:
                continue

            try:
                self._handle_emergency_event(event)
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("[Emergency] 处理紧急事件失败: %s", exc)

    def _handle_emergency_event(self, event: dict) -> None:
        is_fall = event.get("is_fall", True)
        fall_type = event.get("fall_type", None)

        if is_fall:
            # 冷却仅对 is_fall=True 生效，避免连续重复报警
            now = time.time()
            if now - self._last_fall_event_ts < self._fall_event_cooldown:
                self.logger.info("[Emergency] 跌倒事件冷却中，忽略重复事件")
                return
            self._last_fall_event_ts = now

            label = FALL_TYPE_LABELS.get(fall_type, "未知类型") if fall_type else "未知类型"
            self.logger.warning(
                "[Emergency] 触发跌倒紧急流程 fall_type=%s label=%s", fall_type, label
            )
            _append_care_log(
                self.logger,
                self.care_log_manager,
                "fall_alert",
                "已触发跌倒报警",
                level="warning",
                source=str(event.get("source") or "emergency"),
                detail={"fall_type": fall_type, "label": label},
            )
            self._fall_active = True
            self._set_state(self.EMERGENCY)

            try:
                self._stop_speaking_safe()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency] 停止语音失败: %s", exc)

            try:
                self._safe_stop_robot(reason="fall_alert")
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency] 安全停止机器人失败: %s", exc)

            try:
                self._set_state(self.EXECUTING_IOT_ACTION)
                self.logger.info("[Emergency] 调用 IoT fall_alert")
                ok = self.iot.call_scene("fall_alert")
                if not ok:
                    self.logger.warning("[Emergency][IoT] fall_alert 调用失败")
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("[Emergency][IoT] fall_alert 异常: %s", exc)

            try:
                enabled, action = self._fall_robot_action_config()
                if enabled and action:
                    self._set_state(self.EXECUTING_ROBOT_ACTION)
                    self.logger.info("[Emergency] 配置允许，提交机器人报警动作 %s", action)
                    self.executor.submit_action(action, "")
                else:
                    self.logger.info("[Emergency] 跌倒后不提交机器人动作，仅保持 safe_stop")
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency][Robot] 报警动作提交失败: %s", exc)

            try:
                self._set_state(self.SPEAKING)
                self.logger.info("[Emergency] 播报报警提示 fall_type=%s", fall_type)
                if fall_type == "front":
                    reply = "检测到前向跌倒风险，我已启动报警联动。"
                elif fall_type == "side":
                    reply = "检测到侧向跌倒风险，我已启动报警联动。"
                elif fall_type == "lost":
                    reply = "检测到目标丢失或异常姿态，我已启动安全提醒。"
                else:
                    reply = "检测到可能的跌倒风险，我已启动报警联动。"
                print(f"\n🗣️ G1 管家: {reply}\n")
                self._speak_reply_safe(reply)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency][Speech] 播报失败: %s", exc)

            self._set_state(self.IDLE)

        else:
            # is_fall=False：解除报警，不调用 reset_mode，不清除用药记录
            self._fall_active = False
            self.logger.info("[Emergency] 收到跌倒状态解除事件")
            try:
                ok = self.iot.call_scene("fall_clear")
                if ok:
                    self.logger.info("[Emergency][IoT] fall_clear 调用成功")
                else:
                    self.logger.warning("[Emergency][IoT] fall_clear 调用失败或场景不存在，仅记录日志")
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency][IoT] fall_clear 调用异常: %s", exc)
            try:
                reply = "跌倒报警状态已解除。"
                print(f"\n🗣️ G1 管家: {reply}\n")
                self._speak_reply_safe(reply)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency][Speech] 解除播报失败: %s", exc)

    def _get_user_command(self) -> str:
        """
        统一输入入口：
        - text/manual：原 input()
        - voice/speech：语音 listen_once
        - hybrid：优先语音，失败回退文本
        - wake_word：自动监听唤醒词，唤醒后识别正式指令
        """
        # 纯文本或语音模块不可用：完全保持原逻辑
        if (
            (not self._speech_enabled)
            or (self.speech is None)
            or (self._speech_input_mode in ("text", "manual"))
        ):
            try:
                return input("🎙️ 等待语音指令 (输入 'q' 退出): ").strip()
            except EOFError:
                return "q"

        # wake_word：唤醒词细节由 SpeechManager 内部处理
        if self._speech_input_mode == "wake_word":
            if not getattr(self.speech, "asr_available", False):
                self.logger.warning("[WakeWord][WARN] ASR 不可用，请将 speech.input_mode 切回 text/manual；当前临时回退键盘输入。")
                try:
                    return input("⌨️ ASR 不可用，请输入文字指令 (输入 'q' 退出): ").strip()
                except EOFError:
                    return "q"
            try:
                return str(self.speech.wait_wake_and_transcribe() or "").strip()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[WakeWord][WARN] 唤醒词输入失败，已回到等待状态: %s", exc)
                return ""

        # voice / hybrid
        if self._speech_input_mode in ("voice", "speech", "hybrid"):
            text = ""
            try:
                text = str(self.speech.listen_once() or "").strip()
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001
                text = ""
            if text:
                print(f"🎙️ 识别结果: {text}")
                return text

            if self._speech_input_mode == "hybrid" and self._speech_fallback_text:
                try:
                    return input("⌨️ 语音未识别，请输入文字指令 (输入 'q' 退出): ").strip()
                except EOFError:
                    return "q"
            return ""

        # 未知模式：降级文本
        try:
            return input("🎙️ 等待语音指令 (输入 'q' 退出): ").strip()
        except EOFError:
            return "q"

    def _handle_high_priority_interrupt(self, user_input: str) -> bool:
        """
        比赛演示版“高优先级文本中断”（最高优先级）：
        - 关键词命中则立刻中断动作队列
        - 不走 LLM
        - 不走普通动作分类
        """
        try:
            text = (user_input or "").strip()
            if not text:
                return False

            keywords = ["停止", "停下", "取消", "别说了", "闭嘴", "安静"]
            if not any(k in text for k in keywords):
                return False

            # 1) 中断当前动作队列
            self._safe_stop_robot(reason="user_stop")

            # 2) 状态切换（不新增复杂状态，复用 EMERGENCY）
            try:
                self._set_state(self.EMERGENCY)
            except Exception:  # noqa: BLE001
                pass

            # 2.5) 停止播报（如果有）
            self._stop_speaking_safe()

            # 3) 本地固定回复（不走 LLM）
            print("已停止当前任务。")
            self.logger.info("[SystemCore] 高优先级中断触发：已执行 safe_stop。")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _handle_system_reset_command(self, user_input: str) -> bool:
        """
        测试/演示后的清场复位：
        - 不触发 fall_alert
        - 不提交机器人普通动作
        - 语音停止、机器人 safe_stop、紧急队列清空、IoT 显式复位、清空本次演示状态
        """
        try:
            text = (user_input or "").strip()
            if not text:
                return False
            keywords = ["系统复位", "恢复默认", "清空状态", "复位", "一键复位", "重置系统"]
            if not any(k in text for k in keywords):
                return False

            self._perform_safe_reset(reason="system_reset", clear_logs=True, speak=True)
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemReset] 系统复位处理失败: %s", exc)
            return False

    def _is_alarm_clear_text(self, user_input: str) -> bool:
        text = (user_input or "").strip()
        if not text:
            return False
        keywords = [
            "关闭警报器",
            "关闭报警器",
            "解除报警",
            "停止报警",
            "别响了",
            "报警器停一下",
            "恢复报警器供电",
            "关闭1号开关报警",
        ]
        return any(k in text for k in keywords)

    def _handle_alarm_clear_command(self, user_input: str) -> bool:
        try:
            if not self._is_alarm_clear_text(user_input):
                return False
            plan = self.task_orchestrator.build_plan(user_input, {"category": "chat", "action": "none"})
            if getattr(plan, "name", "") != "alarm_clear":
                return False
            result = self.task_executor.execute(plan)
            self.logger.info(
                "[SystemCore] 警报器关闭任务完成: ok=%s failed_steps=%s",
                result.get("ok"),
                result.get("failed_steps"),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore] 关闭警报器处理失败: %s", exc)
            return False

    def run_loop(self):
        while self.running:
            # Step A: 视觉状态检查（HTTP 跌倒由后台线程直接处理）
            self._set_state(self.CHECKING_VISION)

            # Step B: 输入（模拟语音指令）
            self._set_state(self.WAITING_INPUT)
            user_input = self._get_user_command()
            if user_input == "":
                continue
            if user_input.lower() in ("q", "quit", "exit"):
                self.logger.info("用户请求退出。")
                self.running = False
                break
            pipeline_start = time.perf_counter()
            perf_metrics = {}
            try:
                if self.speech is not None:
                    speech_perf = getattr(self.speech, "last_perf", {}) or {}
                    if isinstance(speech_perf, dict):
                        perf_metrics.update(speech_perf)
            except Exception:
                pass

            # Step B.0: 系统复位（清场优先，不进入 LLM/普通任务规划）
            if self._handle_system_reset_command(user_input):
                continue

            # Step B.0.5: 关闭断电报警器，只恢复 alarm_socket 供电，不清空演示状态
            if self._handle_alarm_clear_command(user_input):
                continue

            # Step B.1: 高优先级语音中断（比赛优先、可中断、可抢占）
            # 关键词命中则直接停止播报+中断动作，不进入 LLM/动作分类。
            if self._handle_high_priority_interrupt(user_input):
                continue

            # 演示更直观：当轮输入包含“测试跌倒”则立即触发紧急流程（不用等下一轮）
            if self.vision.check_fall_status(user_input):
                self._handle_emergency_event(
                    {
                        "event_type": "fall_alert",
                        "is_fall": True,
                        "fall_type": None,
                        "source": "manual_text",
                    }
                )
                continue

            # Step C: LLM（调用千问 QwenAgent）
            self._set_state(self.PARSING_INTENT)
            llm_start = time.perf_counter()
            intent = self.llm_agent.chat(user_input, context="当前位置: 客厅")
            self._perf("llm_chat", llm_start, perf_metrics)
            orch_start = time.perf_counter()
            pending_medicine_id = self._get_valid_pending_medicine()
            if pending_medicine_id and isinstance(intent, dict):
                intent = dict(intent)
                intent["pending_medicine_id"] = pending_medicine_id
            try:
                if hasattr(self.task_executor, "set_pending_medicine"):
                    self.task_executor.set_pending_medicine(pending_medicine_id, self.pending_medicine_ts)
            except Exception:  # noqa: BLE001
                pass
            plan = self.task_orchestrator.build_plan(user_input, intent)
            self._perf("orchestrator_build_plan", orch_start, perf_metrics)
            if getattr(self, "_perf_log", True):
                self.logger.info(
                    "[PERF] orchestrator_build_plan source=%s plan=%s steps=%s",
                    getattr(plan, "source", ""),
                    getattr(plan, "name", ""),
                    len(getattr(plan, "steps", []) or []),
                )
            exec_start = time.perf_counter()
            result = self.task_executor.execute(plan)
            self._perf("executor_execute_total", exec_start, perf_metrics)
            self.logger.info(
                "[SystemCore] 任务执行完成: plan=%s ok=%s failed_steps=%s",
                result.get("plan"),
                result.get("ok"),
                result.get("failed_steps"),
            )
            self._perf("user_command_pipeline_total", pipeline_start, perf_metrics)
            try:
                wake_ts = getattr(self.speech, "last_wake_detected_ts", None) if self.speech is not None else None
                if wake_ts is not None:
                    cost_ms = (time.perf_counter() - float(wake_ts)) * 1000.0
                    perf_metrics["voice_interaction_total_after_wake"] = cost_ms
                    if getattr(self, "_perf_log", True):
                        self.logger.info("[PERF] voice_interaction_total_after_wake cost=%.1f ms", cost_ms)
            except Exception:
                pass
            self._log_perf_summary(perf_metrics)
            if self._speech_input_mode == "wake_word":
                self.logger.info("[SystemCore] 任务执行完成，回到唤醒词监听")

            # Step F: 短 sleep，避免刷屏
            self._set_state(self.IDLE)
            time.sleep(0.3)

    def _set_state(self, new_state: str) -> None:
        """
        MVP 状态切换：明确打印 [FSM] A -> B，便于比赛现场讲解与排障。
        """
        if new_state == self.state:
            return
        self.logger.info("[FSM] %s -> %s", self.state, new_state)
        self.state = new_state

    def shutdown(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        self.running = False
        self.logger.info("[SystemCore] 正在执行安全退出复位...")
        try:
            self.emergency_queue.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
        self._perform_safe_reset(reason="shutdown", clear_logs=True, speak=False)
        try:
            self.executor.shutdown(timeout_s=3.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore][WARN] action_executor 关闭失败: %s", exc)
        self.logger.info("[SystemCore] 安全退出复位完成")

    def _classify_action(self, action: str) -> str:
        """
        本地分类器（不依赖修改 qwen_client.py）：
        - 机器人动作：手臂动作 +（暂时）navigate/move/stop
        - IoT 动作：灯/风扇/空调开关
        - none：只播报 reply
        - unknown：统一按 none 处理并记录 warning

        这样做的原因：当前 MVP 阶段最怕“同一个 action 同时触发机器人和 IoT”，造成语义混乱。
        """
        robot_actions = {
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
        }
        iot_actions = {"light_on", "light_off", "fan_on", "fan_off", "ac_on", "ac_off"}
        loco_actions = {"navigate", "move", "stop"}

        if action == "none":
            return "none"
        if action in robot_actions:
            return "robot_action"
        if action in iot_actions:
            return "iot_action"
        if action in loco_actions:
            return "robot_action"
        return "unknown"

    def _resolve_iot_device_id(self, action: str, target: str) -> str:
        """
        比赛阶段的本地兜底映射：
        - 优先使用 LLM 返回的 target（未来可直接对接 Home Assistant entity_id）
        - 若 target 为空，则根据 action 给一个稳定默认设备，避免出现 ac_on 却发给 light_default 的错误
        """
        if target:
            return target
        if action in ("light_on", "light_off"):
            return "light.living_room"
        if action in ("ac_on", "ac_off"):
            return "climate.bedroom_ac"
        if action in ("fan_on", "fan_off"):
            return "switch.fan"
        return "iot.default_device"


def print_banner():
    banner = r"""
   ____      _     _            _    __  __        _     _           
  / ___| ___| |__ (_) ___  _ __| |_ |  \/  | ___  | |__ | | ___  ___ 
 | |  _ / _ \ '_ \| |/ _ \| '__| __|| |\/| |/ _ \ | '_ \| |/ _ \/ __|
 | |_| |  __/ |_) | | (_) | |  | |_ | |  | |  __/ | |_) | |  __/\__ \
  \____|\___|_.__/|_|\___/|_|   \__||_|  |_|\___| |_.__/|_|\___||___/
    """
    print("\033[36m" + banner + "\033[0m")


if __name__ == "__main__":
    cfg = ConfigLoader().get_config()
    logger = setup_logger("system.main")
    print_banner()
    logger.info("System Initializing...")
    core = SystemCore(logger)
    try:
        core.run_loop()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在安全退出...")
        logger.info("收到 Ctrl+C，用户中断。")
    finally:
        core.shutdown()
        logger.info("系统安全关闭")
        print("👋 系统已安全退出。")
