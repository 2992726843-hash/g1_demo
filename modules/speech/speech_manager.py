from __future__ import annotations

from typing import Any, Optional

from .audio_recorder import AudioRecorder
from .asr_client import FasterWhisperASR
from .tts_client import TTSClient


def _log(logger: Any, msg: str) -> None:
    try:
        if logger is not None and hasattr(logger, "info"):
            logger.info(msg)
            return
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


def _warn(logger: Any, msg: str) -> None:
    try:
        if logger is not None and hasattr(logger, "warning"):
            logger.warning(msg)
            return
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


class SpeechManager:
    """
    SpeechManager：基础版语音闭环管理器（PC 优先）。

    设计原则：
    - 任何异常都不抛到 main.py（主控必须稳定）
    - ASR/TTS 任一不可用都要自动降级
    """

    def __init__(self, speech_config: dict, logger: Optional[Any] = None):
        self.logger = logger
        self.cfg = speech_config or {}

        self.enabled: bool = bool(self.cfg.get("enabled", True))
        self.input_mode: str = str(self.cfg.get("input_mode", "hybrid") or "hybrid").strip().lower()

        self.asr_available: bool = False

        # recorder
        self.recorder: Optional[AudioRecorder] = None
        # asr
        self.asr: Optional[FasterWhisperASR] = None
        # tts
        self.tts: Optional[TTSClient] = None

        if not self.enabled:
            _warn(self.logger, "[Speech][WARN] speech.enabled=false，语音模块禁用")
            return

        try:
            sample_rate = int(self.cfg.get("sample_rate", 16000))
            channels = int(self.cfg.get("channels", 1))
            record_seconds = int(self.cfg.get("record_seconds", 4))
            input_device = self.cfg.get("input_device", None)
            if input_device is not None:
                try:
                    input_device = int(input_device)
                except Exception:
                    input_device = None

            self.recorder = AudioRecorder(
                sample_rate=sample_rate,
                channels=channels,
                record_seconds=record_seconds,
                input_device=input_device,
                logger=self.logger,
            )
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] 录音器初始化失败：{exc}")
            self.recorder = None

        # ASR
        try:
            asr_cfg = self.cfg.get("asr", {}) or {}
            backend = str(asr_cfg.get("backend", "faster_whisper") or "faster_whisper").strip()
            if backend != "faster_whisper":
                raise RuntimeError(f"未支持的 ASR backend={backend}")

            model_size = str(asr_cfg.get("model_size", "small") or "small").strip()
            device = str(asr_cfg.get("device", "auto") or "auto").strip()
            compute_type = str(asr_cfg.get("compute_type", "auto") or "auto").strip()
            language = str(self.cfg.get("language", "zh") or "zh").strip()

            self.asr = FasterWhisperASR(
                model_size=model_size,
                device=device,
                compute_type=compute_type,
                language=language,
                logger=self.logger,
            )
            self.asr_available = True
        except Exception as exc:
            self.asr = None
            self.asr_available = False
            _warn(self.logger, f"[Speech][WARN] ASR 不可用，将回退到文本输入：{exc}")

        # TTS
        try:
            tts_cfg = self.cfg.get("tts", {}) or {}
            tts_enabled = bool(tts_cfg.get("enabled", True))
            backend = str(tts_cfg.get("backend", "pyttsx3") or "pyttsx3").strip()
            rate = int(tts_cfg.get("rate", 150))
            volume = float(tts_cfg.get("volume", 1.0))
            self.tts = TTSClient(
                enabled=tts_enabled,
                backend=backend,
                rate=rate,
                volume=volume,
                logger=self.logger,
            )
        except Exception as exc:
            self.tts = TTSClient(enabled=False, logger=self.logger)
            _warn(self.logger, f"[Speech][WARN] TTS 初始化失败，将降级为 print：{exc}")

        _log(self.logger, "[Speech] 语音模块初始化成功")

    def listen_once(self) -> str:
        """
        录一次音并识别：
        - ASR 可用才执行；不可用返回空串
        - 出现任何异常都吞掉并返回空串，保证主控稳定
        """
        if not self.enabled:
            return ""
        if not self.asr_available or self.asr is None or self.recorder is None:
            return ""

        try:
            input("🎤 按 Enter 开始录音... ")
        except Exception:
            # 不影响流程：继续尝试录音
            pass

        text = ""
        try:
            wav_path = self.recorder.record_to_file()
            text = self.asr.transcribe(wav_path)
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] listen_once 失败：{exc}")
            return ""

        text = self.normalize_text(text)
        if text:
            _log(self.logger, f"[Speech] 归一化后文本: {text}")
        return text

    def speak(self, text: str) -> None:
        if not self.enabled or self.tts is None:
            return
        try:
            self.tts.speak(text)
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] speak 失败已忽略：{exc}")

    def stop_speaking(self) -> None:
        if self.tts is None:
            return
        try:
            self.tts.stop()
        except Exception:
            return

    def normalize_text(self, text: str) -> str:
        """
        基础中文归一化（只做简单 replace，不做复杂 NLP）。
        """
        s = (text or "").strip()
        if not s:
            return ""

        # 常见 ASR 误识别纠正
        replacements = [
            ("七夜", "起夜"),
            ("去厕所", "去卫生间"),
            ("上厕所", "去卫生间"),
            ("找药", "帮我找药"),
            ("吃药", "帮我找药"),
            ("关灯灯", "关灯"),
        ]
        for a, b in replacements:
            try:
                s = s.replace(a, b)
            except Exception:
                continue

        # 清理重复空格
        s = " ".join(s.split())
        return s

