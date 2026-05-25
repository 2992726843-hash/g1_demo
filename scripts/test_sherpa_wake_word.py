from __future__ import annotations

import os
import sys


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    root = _project_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    from core.utils import ConfigLoader, setup_logger
    from modules.speech.sherpa_wake_word import SherpaWakeWordDetector

    logger = setup_logger("scripts.test_sherpa_wake_word")
    cfg = ConfigLoader()
    speech_cfg = cfg.get_nested("speech", default={}) or {}
    wake_cfg = speech_cfg.get("wake_word", {}) or {}
    sherpa_cfg = wake_cfg.get("sherpa_onnx", {}) or {}

    detector = SherpaWakeWordDetector(sherpa_cfg, logger=logger)
    print("请说：小航小航。按 Ctrl+C 退出。")
    try:
        while True:
            result = detector.wait_for_wake()
            if result:
                print(f"[TEST] detected: {result}")
    except KeyboardInterrupt:
        print("\n[TEST] 已退出。")
        return 0
    except Exception as exc:
        print(f"[TEST][ERROR] {exc}")
        return 1
    finally:
        detector.close()


if __name__ == "__main__":
    raise SystemExit(main())
