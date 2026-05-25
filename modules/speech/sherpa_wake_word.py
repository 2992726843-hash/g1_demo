from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any, Dict, Optional


class SherpaWakeWordDetector:
    """
    Local sherpa-onnx KWS wake word detector.

    This class only detects the wake word. It does not call TTS and does not
    run the command ASR flow.
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        self.config = config or {}
        self.logger = logger or logging.getLogger(__name__)

        self.model_dir = Path(
            self.config.get(
                "model_dir",
                "models/kws/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
            )
        )
        self.keywords_file = Path(self.config.get("keywords_file", "configs/kws/keywords.txt"))

        self.sample_rate = int(self.config.get("sample_rate", 16000))
        self.num_threads = int(self.config.get("num_threads", 2))
        self.provider = str(self.config.get("provider", "cpu") or "cpu")
        self.max_active_paths = int(self.config.get("max_active_paths", 4))
        self.audio_block_size = float(self.config.get("audio_block_size", 0.1))
        self.tail_padding_seconds = float(self.config.get("tail_padding_seconds", 0.3))
        self.perf_log = bool(self.config.get("perf_log", True))

        self.encoder = self.model_dir / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        self.decoder = self.model_dir / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        self.joiner = self.model_dir / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
        self.tokens = self.model_dir / "tokens.txt"

        self._kws = None
        self._sherpa_onnx = None
        self._sd = None
        self._np = None

    def _log(self, msg: str, *args: Any) -> None:
        try:
            self.logger.info(msg, *args)
        except Exception:
            try:
                print(msg % args if args else msg)
            except Exception:
                pass

    def _require_imports(self) -> None:
        try:
            import sherpa_onnx  # type: ignore
        except Exception as exc:
            raise RuntimeError("未安装 sherpa-onnx，请执行 pip install sherpa-onnx") from exc

        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "未安装 sounddevice，请执行 pip install sounddevice；Ubuntu 可能还需要安装 libportaudio2 portaudio19-dev"
            ) from exc

        try:
            import numpy as np  # type: ignore
        except Exception as exc:
            raise RuntimeError("未安装 numpy，请执行 pip install numpy") from exc

        self._sherpa_onnx = sherpa_onnx
        self._sd = sd
        self._np = np

    def _check_files(self) -> None:
        required = [
            self.model_dir,
            self.encoder,
            self.decoder,
            self.joiner,
            self.tokens,
            self.keywords_file,
        ]
        missing = [str(p) for p in required if not p.exists()]
        if missing:
            raise FileNotFoundError(
                "sherpa-onnx KWS 模型或关键词文件缺失：\n"
                + "\n".join(f"- {p}" for p in missing)
                + "\n请执行 bash scripts/prepare_sherpa_kws.sh"
            )

    def _init_kws(self) -> None:
        if self._kws is not None:
            return

        self._require_imports()
        self._check_files()

        self._log("[WakeWord] 初始化 sherpa-onnx KWS")
        self._log("[WakeWord] model_dir = %s", self.model_dir)
        self._log("[WakeWord] keywords_file = %s", self.keywords_file)

        try:
            self._kws = self._sherpa_onnx.KeywordSpotter(
                tokens=str(self.tokens),
                encoder=str(self.encoder),
                decoder=str(self.decoder),
                joiner=str(self.joiner),
                keywords_file=str(self.keywords_file),
                num_threads=self.num_threads,
                sample_rate=self.sample_rate,
                max_active_paths=self.max_active_paths,
                provider=self.provider,
            )
        except Exception as exc:
            raise RuntimeError(f"sherpa-onnx KWS 初始化失败: {exc}") from exc

    def wait_for_wake(self) -> str:
        self._init_kws()
        if self._kws is None or self._sd is None or self._np is None:
            raise RuntimeError("sherpa-onnx KWS 未正确初始化")

        block_size = max(1, int(self.sample_rate * self.audio_block_size))
        tail_samples = max(0, int(self.sample_rate * self.tail_padding_seconds))
        stream = self._kws.create_stream()

        self._log("[WakeWord] 等待唤醒词，engine=sherpa_onnx")
        wait_start = time.perf_counter()
        try:
            with self._sd.InputStream(
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                blocksize=block_size,
            ) as mic:
                while True:
                    samples, overflowed = mic.read(block_size)
                    if overflowed:
                        self._log("[WakeWord][WARN] 麦克风输入溢出，继续监听")

                    audio = self._np.asarray(samples, dtype="float32").reshape(-1)
                    stream.accept_waveform(self.sample_rate, audio)

                    while self._kws.is_ready(stream):
                        self._kws.decode_stream(stream)

                    result = self._kws.get_result(stream)
                    if result:
                        if self.perf_log:
                            cost_ms = (time.perf_counter() - wait_start) * 1000.0
                            self._log("[PERF] sherpa_wake_wait cost=%.1f ms", cost_ms)
                        self._log("[WakeWord] detected raw result: %s", result)
                        self._kws.reset_stream(stream)
                        return result

                    if tail_samples > 0:
                        # Keep the stream moving through trailing blanks without
                        # adding a separate silence period that delays detection.
                        pass
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            raise RuntimeError(f"sherpa-onnx KWS 麦克风监听失败: {exc}") from exc

    def close(self) -> None:
        self._kws = None
