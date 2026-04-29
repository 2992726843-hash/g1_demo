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


class FasterWhisperASR:
    """
    Faster-Whisper ASR（中文基础版）：
    - device="auto": 优先 cuda+float16；失败回退 cpu+int8
    - compute_type="auto": cuda->float16, cpu->int8
    - 初始化失败抛出异常（由 SpeechManager 捕获并降级）
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: str = "zh",
        logger: Optional[Any] = None,
    ) -> None:
        self.logger = logger
        self.model_size = str(model_size or "small").strip() or "small"
        self.language = str(language or "zh").strip() or "zh"
        self._device = str(device or "auto").strip() or "auto"
        self._compute_type = str(compute_type or "auto").strip() or "auto"

        self._model: Any = None
        self._init_model()

    def _init_model(self) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"faster-whisper 不可用：{exc}") from exc

        # auto 策略：先 cuda/float16，再 cpu/int8
        if self._device == "auto":
            try:
                _log(self.logger, f"[Speech] ASR 尝试加载模型：size={self.model_size} device=cuda compute=float16")
                self._model = WhisperModel(self.model_size, device="cuda", compute_type="float16")
                self._device = "cuda"
                self._compute_type = "float16"
                _log(self.logger, "[Speech] ASR 模型已使用 cuda+float16")
                return
            except Exception as exc:
                _warn(self.logger, f"[Speech][WARN] cuda 加载失败，回退 cpu+int8：err={exc}")
                try:
                    self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
                    self._device = "cpu"
                    self._compute_type = "int8"
                    _log(self.logger, "[Speech] ASR 模型已使用 cpu+int8")
                    return
                except Exception as exc2:
                    raise RuntimeError(f"ASR 模型加载失败（cuda/cpu 都失败）：{exc2}") from exc2

        # 指定 device：compute_type=auto 则按 device 推导
        device = self._device
        compute = self._compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"

        try:
            _log(self.logger, f"[Speech] ASR 加载模型：size={self.model_size} device={device} compute={compute}")
            self._model = WhisperModel(self.model_size, device=device, compute_type=compute)
            self._compute_type = compute
        except Exception as exc:
            raise RuntimeError(f"ASR 模型加载失败：{exc}") from exc

    def transcribe(self, wav_path: str) -> str:
        if not wav_path:
            return ""
        if self._model is None:
            return ""

        try:
            segments, _info = self._model.transcribe(
                wav_path,
                language=self.language,
                vad_filter=True,
            )
            parts: list[str] = []
            for seg in segments:
                try:
                    txt = getattr(seg, "text", "")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
                except Exception:
                    continue
            text = " ".join(parts)
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] ASR 识别失败：{exc}")
            return ""

        # 基础清洗：strip + 去掉多余空格
        text = (text or "").strip()
        text = " ".join(text.split())
        if text:
            _log(self.logger, f"[ASR] 识别结果: {text}")
        return text

