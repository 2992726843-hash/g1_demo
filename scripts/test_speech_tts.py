from __future__ import annotations

import os
import sys


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    root = _project_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    text = " ".join(sys.argv[1:]).strip() or "我在"

    from core.utils import ConfigLoader, setup_logger
    from modules.speech import SpeechManager

    logger = setup_logger("scripts.test_speech_tts")
    cfg = ConfigLoader()
    speech_cfg = cfg.get_nested("speech", default={}) or {}
    robot_cfg = cfg.get_nested("robot", default={}) or {}
    tts_cfg = speech_cfg.get("tts", {}) or {}

    print("=== Speech TTS Test ===")
    print(f"input_mode = {speech_cfg.get('input_mode')}")
    print(f"tts.enabled = {tts_cfg.get('enabled', True)}")
    print(f"tts.backend = {tts_cfg.get('backend')}")
    print(f"robot.proxy_base_url = {robot_cfg.get('proxy_base_url')}")
    print(f"robot.proxy_timeout_s = {robot_cfg.get('proxy_timeout_s')}")
    print(f"request text = {text}")

    speech = SpeechManager(speech_cfg, logger=logger)
    speech.speak(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
