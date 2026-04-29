from __future__ import annotations

import os
import sys


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    # 允许直接运行：把项目根目录加入 sys.path
    root = _project_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    from core.utils import ConfigLoader, setup_logger

    logger = setup_logger("scripts.test_speech")
    cfg = ConfigLoader()
    speech_cfg = cfg.get_nested("speech", default={}) or {}

    try:
        from modules.speech import SpeechManager
    except Exception as exc:
        print(f"[Speech][WARN] 无法导入 SpeechManager（依赖可能未安装）：{exc}")
        print("请先执行：pip install -r requirements.txt")
        return

    sm = SpeechManager(speech_cfg, logger=logger)
    if not sm.enabled:
        print("[Speech][WARN] speech.enabled=false，已退出")
        return

    if not sm.asr_available:
        print("[Speech][WARN] ASR 不可用，请检查麦克风/声卡/模型/依赖。")
        print("可尝试：speech.asr.model_size=tiny, device=cpu, compute_type=int8")

    print("=== Speech Test ===")
    try:
        tts_cfg = (speech_cfg.get("tts", {}) or {}) if isinstance(speech_cfg, dict) else {}
        print(
            "[SpeechTest] TTS 配置："
            f" enabled={tts_cfg.get('enabled', True)}"
            f" backend={tts_cfg.get('backend', 'print')}"
            f" voice={tts_cfg.get('voice', '')}"
            f" rate={tts_cfg.get('rate', '')}"
            f" volume={tts_cfg.get('volume', '')}"
        )
    except Exception:
        pass

    # 先做一次纯 TTS 测试，确保当前 backend 可用/可降级
    try:
        sm.speak("TTS 测试：如果你听到这句话，说明语音播报后端工作正常。")
    except Exception:
        pass

    print("按 Enter 开始录音，输入 q 退出。")
    while True:
        try:
            cmd = input("\n[SpeechTest] 按 Enter 录音 (q 退出): ").strip()
        except EOFError:
            cmd = "q"
        if cmd.lower() in ("q", "quit"):
            break

        text = sm.listen_once()
        if not text:
            print("[SpeechTest][WARN] 未识别到文本（ASR 可能不可用或环境无麦克风）。")
            continue

        print(f"[SpeechTest] 识别结果: {text}")
        sm.speak(f"我听到您说：{text}")

    try:
        sm.stop_speaking()
    except Exception:
        pass
    print("退出 SpeechTest。")


if __name__ == "__main__":
    main()

