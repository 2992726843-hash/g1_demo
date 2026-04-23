"""
离线语音识别服务（批处理），优先 faster-whisper。

说明：比赛阶段先做离线批处理识别，不做流式识别与唤醒。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ASRService:
    """封装 faster-whisper；不可用时返回空串，不抛异常。"""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        # None：未尝试加载；False：已确认不可用；WhisperModel：可用
        self._model: Any = None

    def _load_model(self) -> bool:
        if self._model is False:
            return False
        if self._model is not None:
            return True
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info(
                "faster-whisper 模型加载成功: size=%s device=%s compute_type=%s",
                self.model_size,
                self.device,
                self.compute_type,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("faster-whisper 不可用或加载失败，识别将返回空字符串: %s", exc)
            self._model = False
            return False

    def transcribe(self, wav_path: str) -> str:
        """
        对 WAV 做离线转写。

        失败或依赖缺失时返回空字符串，不抛异常。
        """
        if not wav_path:
            logger.warning("transcribe: wav_path 为空")
            return ""

        if not self._load_model():
            return ""

        try:
            segments, _info = self._model.transcribe(wav_path, beam_size=5)
            parts: list[str] = []
            for seg in segments:
                parts.append(seg.text)
            text = "".join(parts).strip()
            logger.info("ASR 识别结果: %r", text)
            return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("ASR 识别失败，返回空字符串: %s", exc)
            return ""
