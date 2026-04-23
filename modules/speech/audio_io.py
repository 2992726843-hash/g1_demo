"""
PC 端简单音频输入输出。

说明：这是比赛 MVP 版，不做复杂实时音频处理（无 VAD、无流式、无回声消除）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AudioIO:
    """使用 sounddevice 采集/回放，soundfile 读写 WAV。"""

    def record_wav(self, output_path: str, seconds: int = 4, sample_rate: int = 16000) -> str:
        """
        从默认麦克风录制指定秒数并保存为 WAV。

        成功返回 output_path；失败返回空字符串并记录日志。
        """
        try:
            import sounddevice as sd
            import soundfile as sf
        except Exception as exc:  # noqa: BLE001 — 比赛场景兜底
            logger.exception("录音依赖缺失或导入失败（需要 sounddevice / soundfile）: %s", exc)
            return ""

        frames = int(seconds * sample_rate)
        if frames <= 0:
            logger.warning("录音时长无效: seconds=%s sample_rate=%s", seconds, sample_rate)
            return ""

        try:
            recording = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32")
            sd.wait()
            sf.write(output_path, recording, sample_rate)
            logger.info("录音完成: path=%s seconds=%s sr=%s", output_path, seconds, sample_rate)
            return output_path
        except Exception as exc:  # noqa: BLE001
            logger.exception("录音失败（检查麦克风权限与设备）: %s", exc)
            return ""

    def play_wav(self, wav_path: str) -> None:
        """播放 WAV 文件；异常仅记录日志，不向调用方抛出。"""
        try:
            import sounddevice as sd
            import soundfile as sf
        except Exception as exc:  # noqa: BLE001
            logger.exception("播放依赖缺失或导入失败: %s", exc)
            return

        try:
            data, sr = sf.read(wav_path, always_2d=False)
            sd.play(data, sr)
            sd.wait()
        except Exception as exc:  # noqa: BLE001
            logger.exception("播放失败: wav_path=%s err=%s", wav_path, exc)
