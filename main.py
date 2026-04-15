import random
import sys
import time

from core.utils import ConfigLoader, setup_logger
from hardware.mock_g1 import MockG1Robot

# =====  Dummy "团队契约"接口 =====
class LLMAgent:
    def __init__(self, logger):
        self.logger = logger

    def analyze_intent(self, user_text: str) -> dict:
        self.logger.info(f"[LLMAgent] 分析意图: '{user_text}'")
        # 假装智能返回
        return {
            "action": "fetch_medicine",
            "target": "bedroom",
            "reply": "好的爷爷，我去拿药"
        }

class VisionDetector:
    def __init__(self, logger):
        self.logger = logger

    def check_fall_status(self) -> bool:
        # 模拟万分之一概率跌倒
        detected = random.random() < 0.0001
        self.logger.info(f"[VisionDetector] 跌倒检测: {'检测到' if detected else '未检测到'}")
        return detected

class IoTController:
    def __init__(self, logger):
        self.logger = logger

    def execute_device_action(self, device_id: str, action: str) -> bool:
        self.logger.info(f"[IoTController] 控制设备 {device_id} 动作: {action}")
        print(f"[IoT] 正在控制设备 {device_id} 执行动作：{action}")
        return True

# ======= SystemCore 主控 =======
class SystemCore:
    def __init__(self, logger):
        self.logger = logger
        self.llm_agent = LLMAgent(logger)
        self.vision = VisionDetector(logger)
        self.iot = IoTController(logger)
        self.robot = MockG1Robot()

    def run_loop(self):
        while True:
            # Step A: 跌倒检测
            if self.vision.check_fall_status():
                self.robot.speak("检测到跌倒，启动报警！")
                self.iot.execute_device_action(device_id="light_all", action="red_blink")
                self.logger.warning("[SystemCore] 紧急跌倒事件触发，已报警并让灯光变红。")
                continue
            # Step B: 输入（模拟语音指令）
            try:
                user_input = input("🎙️ 等待语音指令 (输入 'q' 退出): ").strip()
            except EOFError:
                user_input = "q"
            if user_input == "":
                continue
            if user_input.lower() == "q":
                self.logger.info("用户请求退出。")
                break
            # Step C: LLM
            intent = self.llm_agent.analyze_intent(user_input)
            # Step D: 执行动作
            reply = intent.get("reply", "执行完毕。")
            action = intent.get("action", None)
            target = intent.get("target", None)
            self.robot.speak(reply)
            self.logger.info(f"[SystemCore] 已播报: {reply}")
            # 假装执行动作
            if action:
                self.logger.info(f"[SystemCore] 执行机器人动作: {action} -> {target}")
                # 这里只是打印，不对应真实MockG1的动作ID
            if target:
                device_id = target
            else:
                device_id = "light_default"
            self.iot.execute_device_action(device_id, action or "noop")
            time.sleep(0.5)

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