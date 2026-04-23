import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.utils import ConfigLoader, setup_logger
from core.action_executor import ActionExecutor
from modules.llm_agent.qwen_client import QwenAgent

# ===== HTTP 视觉事件共享状态（队友跌倒检测 POST 接入） =====
vision_event_state = {"fall_flag": False}
vision_event_lock = threading.Lock()


class VisionFallEventHandler(BaseHTTPRequestHandler):
    """POST /api/vision/fall_event — 最小 JSON 跌倒事件接收。"""

    def log_message(self, format, *args):
        # 关闭 BaseHTTPRequestHandler 默认 stderr 噪声；业务日志走 logger
        return

    def _send_json_body(self, code: int, message: str, accepted: bool) -> None:
        body = json.dumps(
            {"code": code, "message": message, "accepted": accepted},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/api/vision/fall_event":
            self._send_json_body(1, "bad request", False)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json_body(1, "bad request", False)
            return
        if (
            isinstance(data, dict)
            and data.get("event_type") == "fall_alert"
            and data.get("is_fall") is True
        ):
            with vision_event_lock:
                vision_event_state["fall_flag"] = True
            log = getattr(self.server, "vision_logger", None)
            if log is not None:
                log.info("收到 fall_alert 事件")
            self._send_json_body(0, "received", True)
        else:
            self._send_json_body(1, "bad request", False)

    def do_GET(self) -> None:
        self._send_json_body(1, "bad request", False)


def _run_vision_event_http_server(logger) -> None:
    httpd = HTTPServer(("0.0.0.0", 8765), VisionFallEventHandler)
    httpd.vision_logger = logger
    httpd.serve_forever()


# ===== 保留环境感知占位符（等待真实代码接入） =====
class VisionDetector:
    def __init__(self, logger):
        self.logger = logger

    def check_fall_status(self, last_user_input: str = "") -> bool:
        """
        比赛演示版跌倒检测（可控，不随机）：
        - HTTP：队友 POST fall_alert 后置位 fall_flag，本处读取后立即清零，避免重复报警
        - 手动触发：当用户输入包含“测试跌倒”时返回 True
        """
        with vision_event_lock:
            if vision_event_state["fall_flag"]:
                vision_event_state["fall_flag"] = False
                self.logger.info("[VisionDetector] HTTP fall_alert 标志已消费，本轮回合并发跌倒")
                return True
        if isinstance(last_user_input, str) and ("测试跌倒" in last_user_input):
            self.logger.info("[VisionDetector] 手动触发：测试跌倒")
            return True
        return False


class IoTController:
    def __init__(self, logger):
        self.logger = logger

    def execute_device_action(self, device_id: str, action: str) -> bool:
        self.logger.info(f"[IoTController] 控制设备 {device_id} 动作: {action}")
        print(f"[IoT] 正在控制设备 {device_id} 执行动作：{action}")
        return True


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
        self.iot = IoTController(logger)
        self.state = self.IDLE
        # 保存上一轮用户输入，供“每轮循环开始的跌倒检测”使用（演示可控）
        self._last_user_input: str = ""

        _vision_http_thread = threading.Thread(
            target=_run_vision_event_http_server,
            args=(self.logger,),
            name="VisionFallEventHTTP",
            daemon=True,
        )
        _vision_http_thread.start()
        self.logger.info(
            "视觉事件服务已启动: host=0.0.0.0 port=8765 POST /api/vision/fall_event"
        )

    def run_loop(self):
        while True:
            # Step A: 跌倒检测（永远最高优先级）
            self._set_state(self.CHECKING_VISION)
            if self.vision.check_fall_status(self._last_user_input):
                self._set_state(self.EMERGENCY)
                # 比赛演示期的「上层中断」机制：清空后续动作并尽量停止当前任务；
                # 用于紧急事件优先，不等同于机器人底层实时急停。
                self.executor.interrupt_current_action()
                print("\n🗣️ G1 管家: 检测到跌倒，启动报警！\n")
                self.iot.execute_device_action(device_id="light_all", action="red_blink")
                self.logger.warning("[SystemCore] 紧急跌倒事件触发，已报警并让灯光变红。")
                continue

            # Step B: 输入（模拟语音指令）
            self._set_state(self.WAITING_INPUT)
            try:
                user_input = input("🎙️ 等待语音指令 (输入 'q' 退出): ").strip()
            except EOFError:
                user_input = "q"
            if user_input == "":
                continue
            # 记录本轮输入，供下一轮循环开始的跌倒检测使用
            self._last_user_input = user_input
            if user_input.lower() in ("q", "quit"):
                self.logger.info("用户请求退出。")
                break

            # 演示更直观：当轮输入包含“测试跌倒”则立即触发紧急流程（不用等下一轮）
            if self.vision.check_fall_status(user_input):
                self._set_state(self.EMERGENCY)
                # 比赛演示期的「上层中断」机制：清空后续动作并尽量停止当前任务；
                # 用于紧急事件优先，不等同于机器人底层实时急停。
                self.executor.interrupt_current_action()
                print("\n🗣️ G1 管家: 检测到跌倒，启动报警！\n")
                self.iot.execute_device_action(device_id="light_all", action="red_blink")
                self.logger.warning("[SystemCore] 紧急跌倒事件触发（手动测试），已报警并让灯光变红。")
                continue

            # Step C: LLM（调用千问 QwenAgent）
            self._set_state(self.PARSING_INTENT)
            intent = self.llm_agent.chat(user_input, context="当前位置: 客厅")
            action = str(intent.get("action", "none") or "none").strip()
            target = str(intent.get("target", "") or "")
            reply = str(intent.get("reply", "...") or "...")

            # Step D: 动作分类执行（最关键：避免机器人动作和 IoT 动作混发）
            category = self._classify_action(action)
            if category == "robot_action":
                # 机器人动作：只下发给 ActionExecutor
                self._set_state(self.EXECUTING_ROBOT_ACTION)
                if action in ("navigate", "move", "stop"):
                    self.logger.info("[SystemCore] 移动相关动作下发: %s -> %s", action, target)
                    self.executor.submit_action(action, target)
                    time.sleep(0.3)
                else:
                    self.logger.info(f"[SystemCore] 机器人动作下发: {action} -> {target}")
                    self.executor.submit_action(action, target)
                    # 比赛演示期的简化同步策略：给后台动作线程一点时间完成主要动作
                    # 注意：这不是严格的“动作完成检测”，只是为了“播报-动作-等待下一条输入”更自然。
                    time.sleep(1.2)
            elif category == "iot_action":
                # IoT 动作：只下发给 IoTController
                self._set_state(self.EXECUTING_IOT_ACTION)
                device_id = self._resolve_iot_device_id(action, target)
                self.logger.info(f"[SystemCore] IoT 动作下发: device_id={device_id} action={action}")
                self.iot.execute_device_action(device_id, action)
            else:
                # none / unknown：不执行任何动作，只播报
                if category == "unknown":
                    self.logger.warning("[SystemCore] 未知 action 已按 none 处理：%r", action)

            # Step E: reply 播报逻辑独立（只打印一次）
            self._set_state(self.SPEAKING)
            print(f"\n🗣️ G1 管家: {reply}\n")
            self.logger.info(f"[SystemCore] 已播报: {reply}")

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
        logger.info("系统安全关闭")
        print("👋 系统已安全退出。")
        core.executor.shutdown()
