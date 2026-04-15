import logging
import os
import sys
import time
from typing import Any, Optional

from core.utils import ConfigLoader, setup_logger

try:
    import pyttsx3  # type: ignore
except ModuleNotFoundError:
    pyttsx3 = None  # type: ignore[assignment]


# Dynamically add Unitree SDK2 Python path
_SDK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "unitree_sdk2_python"))
if _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)

try:
    import unitree_sdk2py  # type: ignore[import-not-found]  # noqa: F401

    from unitree_sdk2py.core.channel import (  # type: ignore[import-not-found]
        ChannelFactoryInitialize,
    )
    from unitree_sdk2py.g1.loco.g1_loco_client import (  # type: ignore[import-not-found]
        LocoClient,
    )
    from unitree_sdk2py.g1.arm.g1_arm_action_client import (  # type: ignore[import-not-found]
        G1ArmActionClient,
    )

    _UNITREE_AVAILABLE = True
except Exception as e:  # defensive: any import error
    _UNITREE_AVAILABLE = False
    _UNITREE_IMPORT_ERROR: Exception = e


class RealG1Robot:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger: logging.Logger = logger or setup_logger("hardware.real_g1")

        cfg = ConfigLoader()
        self.net_if: str = str(cfg.get_nested("robot", "network_interface", default=""))

        self.tts: Any = None
        if pyttsx3 is None:
            self.logger.warning("pyttsx3 not installed; PC-TTS speak() will be disabled.")
        else:
            try:
                self.tts = pyttsx3.init()
            except Exception as e:
                self.logger.warning("Failed to init pyttsx3; PC-TTS disabled. Error: %s", e)
                self.tts = None

        self._connected: bool = False
        self._loco_client: Any = None
        self._arm_action_client: Any = None

        if not _UNITREE_AVAILABLE:
            self.logger.warning(
                "Unitree SDK2 python import failed; running in disconnected mode. Error: %s",
                getattr(sys.modules.get(__name__), "_UNITREE_IMPORT_ERROR", "unknown"),
            )
            return

        if not self.net_if:
            self.logger.warning(
                "Config robot.network_interface is empty; cannot initialize Unitree ChannelFactory."
            )
            return

        try:
            # The first parameter is domain id; keep default 0.
            ChannelFactoryInitialize(0, self.net_if)

            self._loco_client = LocoClient()
            self._loco_client.SetTimeout(10.0)
            self._loco_client.Init()

            self._arm_action_client = G1ArmActionClient()
            self._arm_action_client.SetTimeout(10.0)
            self._arm_action_client.Init()

            self._connected = True
            self.logger.info("RealG1Robot connected via interface: %s", self.net_if)
        except Exception as e:
            self._connected = False
            self.logger.warning("Failed to initialize Unitree clients; disconnected. Error: %s", e)

    def execute_action(self, action_id: int) -> bool:
        """
        Execute upper-body action (e.g., kiss, clap).
        """
        if not self._connected:
            self.logger.warning("execute_action(%s) ignored: robot not connected.", action_id)
            return False

        try:
            code = self._arm_action_client.ExecuteAction(int(action_id))
            if code != 0:
                self.logger.warning("Action execute failed. action_id=%s code=%s", action_id, code)
                return False
            self.logger.info("Action executed. action_id=%s", action_id)
            return True
        except Exception as e:
            self.logger.warning("Action execute exception. action_id=%s error=%s", action_id, e)
            return False

    def loco_control(self, cmd: str) -> bool:
        """
        Native posture control only (no navigation logic).

        Supported: stand_up, squat, sit, stop_move
        """
        if not self._connected:
            self.logger.warning("loco_control('%s') ignored: robot not connected.", cmd)
            return False

        normalized = cmd.strip().lower()
        try:
            if normalized == "stand_up":
                # For G1 loco client, common stand-up from lie/squat.
                code = self._loco_client.Lie2StandUp()
            elif normalized == "squat":
                code = self._loco_client.StandUp2Squat()
            elif normalized == "sit":
                code = self._loco_client.Sit()
            elif normalized == "stop_move":
                self._loco_client.StopMove()
                code = 0
            else:
                self.logger.warning("Unsupported loco_control cmd: %s", cmd)
                return False

            if code != 0:
                self.logger.warning("loco_control failed. cmd=%s code=%s", cmd, code)
                return False
            self.logger.info("loco_control ok. cmd=%s", cmd)
            return True
        except Exception as e:
            self.logger.warning("loco_control exception. cmd=%s error=%s", cmd, e)
            return False

    def speak(self, text: str) -> None:
        self.logger.info("[PC-TTS] 播报: %s", text)
        if self.tts is None:
            return
        try:
            self.tts.say(text)
            self.tts.runAndWait()
        except Exception as e:
            self.logger.warning("PC-TTS speak failed. error=%s", e)


if __name__ == "__main__":
    log = setup_logger("hardware.real_g1.test")
    bot = RealG1Robot(logger=log)

    bot.speak("Real G1 robot test started.")

    bot.loco_control("stand_up")
    time.sleep(2)
    bot.loco_control("sit")
    time.sleep(2)
    bot.loco_control("stand_up")
    time.sleep(2)
    bot.loco_control("squat")
    time.sleep(2)
    bot.loco_control("stop_move")

    bot.speak("Real G1 robot test finished.")
