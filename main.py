import json
import queue
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.utils import ConfigLoader, setup_logger
from core.action_executor import ActionExecutor
from core.task_executor import TaskExecutor
from core.task_orchestrator import TaskOrchestrator
from modules.llm_agent.qwen_client import QwenAgent
from modules.iot.iot_controller import IoTController

FALL_TYPE_LABELS = {
    "front": "前向跌倒",
    "side": "侧向跌倒",
    "lost": "目标丢失或异常姿态",
}


class VisionFallEventHandler(BaseHTTPRequestHandler):
    """POST /api/vision/fall_event — 接收视觉模块 FallEvent，支持 is_fall 分流。"""

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
        if self.path != "/api/vision/fall_event":
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
            })
            self._send_json({"ok": True, "message": "fall cleared queued", "is_fall": False})

    def do_GET(self) -> None:
        self._send_json({"ok": False, "message": "bad request"})


def _run_vision_event_http_server(logger, emergency_queue) -> None:
    httpd = HTTPServer(("0.0.0.0", 8765), VisionFallEventHandler)
    httpd.vision_logger = logger
    httpd.emergency_queue = emergency_queue
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
        self.emergency_queue = queue.Queue()
        self._last_fall_event_ts = 0.0
        self._fall_event_cooldown = 5.0

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
        )

        self._emergency_worker_thread = threading.Thread(
            target=self._emergency_worker_loop,
            name="EmergencyWorker",
            daemon=True,
        )
        self._emergency_worker_thread.start()

        _vision_http_thread = threading.Thread(
            target=_run_vision_event_http_server,
            args=(self.logger, self.emergency_queue),
            name="VisionFallEventHTTP",
            daemon=True,
        )
        _vision_http_thread.start()
        self.logger.info(
            "视觉事件服务已启动: host=0.0.0.0 port=8765 POST /api/vision/fall_event"
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
                return
            # tts.enabled 由 SpeechManager 内部处理（不可用会降级为 print）
            self.speech.speak(reply)
        except Exception:  # noqa: BLE001
            return

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
            self._set_state(self.EMERGENCY)

            try:
                self._stop_speaking_safe()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency] 停止语音失败: %s", exc)

            try:
                self.executor.interrupt_current_action()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[Emergency] 中断机器人动作失败: %s", exc)

            try:
                self._set_state(self.EXECUTING_IOT_ACTION)
                self.logger.info("[Emergency] 调用 IoT fall_alert")
                ok = self.iot.call_scene("fall_alert")
                if not ok:
                    self.logger.warning("[Emergency][IoT] fall_alert 调用失败")
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("[Emergency][IoT] fall_alert 异常: %s", exc)

            try:
                self._set_state(self.EXECUTING_ROBOT_ACTION)
                self.logger.info("[Emergency] 提交机器人报警动作 hands_up")
                self.executor.submit_action("hands_up", "")
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
        - text：原 input()
        - voice：语音 listen_once
        - hybrid：优先语音，失败回退文本
        """
        # 纯文本或语音模块不可用：完全保持原逻辑
        if (not self._speech_enabled) or (self.speech is None) or (self._speech_input_mode == "text"):
            try:
                return input("🎙️ 等待语音指令 (输入 'q' 退出): ").strip()
            except EOFError:
                return "q"

        # voice / hybrid
        if self._speech_input_mode in ("voice", "hybrid"):
            text = ""
            try:
                text = str(self.speech.listen_once() or "").strip()
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
            try:
                self.executor.interrupt_current_action()
            except Exception:  # noqa: BLE001
                pass

            # 2) 状态切换（不新增复杂状态，复用 EMERGENCY）
            try:
                self._set_state(self.EMERGENCY)
            except Exception:  # noqa: BLE001
                pass

            # 2.5) 停止播报（如果有）
            self._stop_speaking_safe()

            # 3) 本地固定回复（不走 LLM）
            print("已停止当前任务。")
            self.logger.info("[SystemCore] 高优先级中断触发：已中断当前动作。")
            return True
        except Exception:  # noqa: BLE001
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
            if user_input.lower() in ("q", "quit"):
                self.logger.info("用户请求退出。")
                self.running = False
                break

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
            intent = self.llm_agent.chat(user_input, context="当前位置: 客厅")
            plan = self.task_orchestrator.build_plan(user_input, intent)
            result = self.task_executor.execute(plan)
            self.logger.info(
                "[SystemCore] 任务执行完成: plan=%s ok=%s failed_steps=%s",
                result.get("plan"),
                result.get("ok"),
                result.get("failed_steps"),
            )

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
        self._stop_speaking_safe()
        try:
            self.executor.interrupt_current_action()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore][WARN] 中断机器人动作失败: %s", exc)
        try:
            self.logger.info("[SystemCore] 调用 IoT reset_mode")
            ok = self.iot.call_scene("reset_mode")
            if not ok:
                self.logger.warning("[SystemCore][WARN] reset_mode 调用失败，跳过退出复位")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore][WARN] reset_mode 调用异常: %s", exc)
        try:
            if self.medicine_manager is not None:
                self.medicine_manager.clear_today()
                self.logger.info("[SystemCore] 退出时用药记录已清除")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("[SystemCore][WARN] 退出时清除用药记录失败: %s", exc)
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
