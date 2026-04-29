from __future__ import annotations

from typing import Any, Optional


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


class TTSClient:
    """
    基础版 TTS：
    - 优先使用 pyttsx3
    - 初始化失败/播报失败自动降级为 print，不影响主控 FSM
    """

    def __init__(
        self,
        enabled: bool = True,
        backend: str = "pyttsx3",
        rate: int = 150,
        volume: float = 1.0,
        logger: Optional[Any] = None,
    ) -> None:
        self.logger = logger
        self.enabled = bool(enabled)
        self.backend = str(backend or "pyttsx3").strip() or "pyttsx3"
        self.rate = int(rate) if rate is not None else 150
        try:
            self.volume = float(volume)
        except Exception:
            self.volume = 1.0

        self._engine: Any = None
        self._available: bool = False

        if not self.enabled:
            self._available = False
            return
        if self.backend != "pyttsx3":
            _warn(self.logger, f"[Speech][WARN] 未支持的 TTS backend={self.backend}，将降级为 print")
            self._available = False
            return

        try:
            import pyttsx3  # type: ignore

            engine = pyttsx3.init()
            try:
                engine.setProperty("rate", self.rate)
            except Exception:
                pass
            try:
                engine.setProperty("volume", self.volume)
            except Exception:
                pass
            self._engine = engine
            self._available = True
            _log(self.logger, "[Speech] TTS 初始化成功（pyttsx3）")
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] TTS 初始化失败，将降级为 print：{exc}")
            self._engine = None
            self._available = False

    def speak(self, text: str) -> None:
        t = (text or "").strip()
        if not t:
            _warn(self.logger, "[Speech][WARN] TTS speak 文本为空，已忽略")
            return

        # 不可用或禁用：降级为 print（主控仍会 print，这里只是兜底）
        if not self.enabled or not self._available or self._engine is None:
            print(t)
            return

        try:
            self._engine.say(t)
            self._engine.runAndWait()
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] TTS 播报失败，已降级为 print：{exc}")
            try:
                print(t)
            except Exception:
                pass

    def stop(self) -> None:
        if not self._available or self._engine is None:
            return
        try:
            self._engine.stop()
        except Exception:
            return

