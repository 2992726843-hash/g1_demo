"""
语音统一入口：录音 -> 离线 ASR -> 文本；TTS 播报。

供比赛 Demo 独立测试，不依赖项目 main。
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile

# 允许直接运行该文件：把项目根目录加入 sys.path（与 modules/llm_agent/qwen_client.py 一致）
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from modules.speech.asr_service import ASRService  # noqa: E402
from modules.speech.audio_io import AudioIO  # noqa: E402
from modules.speech.tts_service import TTSService  # noqa: E402

logger = logging.getLogger(__name__)


class SpeechManager:
    """组合 AudioIO + ASRService + TTSService。"""

    def __init__(
        self,
        audio: AudioIO | None = None,
        asr: ASRService | None = None,
        tts: TTSService | None = None,
        keep_audio_debug: bool = False,
    ) -> None:
        self.audio = audio or AudioIO()
        # 比赛演示阶段优先速度：tiny 模型更快；更适合短指令识别
        self.asr = asr or ASRService(model_size="tiny")
        self.tts = tts or TTSService()
        self.keep_audio_debug = keep_audio_debug

    def listen_once(self, seconds: int = 4) -> str:
        """
        录音 -> 识别 -> 返回文本。

        任意步骤失败返回空字符串，不抛异常。
        """
        path: str | None = None
        try:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            out = self.audio.record_wav(path, seconds=seconds)
            if not out:
                logger.warning("listen_once: 录音未生成有效文件")
                return ""
            return self.asr.transcribe(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("listen_once 异常（已兜底）: %s", exc)
            return ""
        finally:
            if path:
                if self.keep_audio_debug:
                    logger.info("调试模式：保留录音文件 %s", path)
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def speak(self, text: str) -> bool:
        """TTS 播报；内部异常兜底为 False。"""
        try:
            return self.tts.speak(text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("speak 异常（已兜底）: %s", exc)
            return False


def _run_cli_demo() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    mgr = SpeechManager()
    try:
        # 比赛演示阶段优先速度：2 秒录音更适合短指令识别，减少等待时间
        s = input("输入 r 并回车开始录音（默认 2 秒），直接回车也可… ").strip().lower()
        if s not in ("", "r"):
            print("已取消录音。")
            return
    except EOFError:
        logger.info("无交互输入（EOF），直接开始录音")
    text = mgr.listen_once(2)
    if not text:
        print("识别文本: <空>")
        logger.warning("未识别到有效语音输入")
    else:
        print("识别文本:", text)
    ok = mgr.speak("我已收到您的指令")
    print("TTS 结果:", "成功" if ok else "失败")


if __name__ == "__main__":
    try:
        _run_cli_demo()
    except Exception as exc:  # noqa: BLE001
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        logger.exception("speech_manager __main__ 顶层异常（已兜底，不崩溃）: %s", exc)