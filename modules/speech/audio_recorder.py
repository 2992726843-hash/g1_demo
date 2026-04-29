from __future__ import annotations

from dataclasses import dataclass
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


def list_audio_devices() -> None:
    """
    打印当前音频设备列表，方便在 Ubuntu / G1 上排查麦克风设备名与 index。
    """
    try:
        import sounddevice as sd  # type: ignore

        devices = sd.query_devices()
        print("=== Audio Devices ===")
        for idx, d in enumerate(devices):
            name = d.get("name")
            hostapi = d.get("hostapi")
            max_in = d.get("max_input_channels")
            max_out = d.get("max_output_channels")
            print(f"[{idx}] name={name!r} hostapi={hostapi} in={max_in} out={max_out}")
        try:
            default_in = sd.default.device[0]
            default_out = sd.default.device[1]
            print(f"default_input={default_in} default_output={default_out}")
        except Exception:
            pass
        print("=====================")
    except Exception as exc:
        print(f"[Speech][WARN] 无法列出音频设备：{exc}")


@dataclass
class AudioRecorder:
    """
    PC 端基础录音器：
    - 使用 sounddevice 录音
    - 使用 soundfile 写入 wav
    - 输出固定路径 /tmp/g1_speech_input.wav（便于排障）
    """

    sample_rate: int = 16000
    channels: int = 1
    record_seconds: int = 4
    input_device: Optional[int] = None
    logger: Any = None

    def record_to_file(self) -> str:
        """
        录音并写入 wav 文件，返回 wav 路径。

        录音失败时抛出异常（由上层 SpeechManager 捕获并降级），本方法不负责退出程序。
        """
        wav_path = "/tmp/g1_speech_input.wav"
        try:
            import numpy as np  # type: ignore
            import sounddevice as sd  # type: ignore
            import soundfile as sf  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"录音依赖不可用（需要 sounddevice/soundfile/numpy）：{exc}") from exc

        if int(self.sample_rate) <= 0:
            raise ValueError(f"sample_rate 非法: {self.sample_rate}")
        if int(self.channels) <= 0:
            raise ValueError(f"channels 非法: {self.channels}")
        if int(self.record_seconds) <= 0:
            raise ValueError(f"record_seconds 非法: {self.record_seconds}")

        _log(self.logger, "[Speech] 开始录音，请说话...")
        try:
            frames = int(self.sample_rate) * int(self.record_seconds)
            audio = sd.rec(
                frames=frames,
                samplerate=int(self.sample_rate),
                channels=int(self.channels),
                dtype="float32",
                device=self.input_device,
            )
            sd.wait()
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] 录音失败：{exc}")
            raise RuntimeError(f"录音失败：{exc}") from exc

        _log(self.logger, "[Speech] 录音结束，正在识别...")
        try:
            # soundfile 支持 float32 直接写入 WAV（内部会按 subtype 处理）
            data = np.asarray(audio, dtype="float32")
            sf.write(wav_path, data, int(self.sample_rate))
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] 写入 wav 失败：{exc}")
            raise RuntimeError(f"写入 wav 失败：{exc}") from exc

        return wav_path

