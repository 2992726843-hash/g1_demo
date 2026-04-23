"""
比赛 Demo 用语音子模块：PC 端录音 / 离线 ASR / TTS 播报。

不接入主程序入口，仅作为可独立测试的能力包。
"""

from .audio_io import AudioIO
from .asr_service import ASRService
from .speech_manager import SpeechManager
from .tts_service import TTSService

__all__ = [
    "AudioIO",
    "ASRService",
    "TTSService",
    "SpeechManager",
]
