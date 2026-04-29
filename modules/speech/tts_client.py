from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
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
    TTSClient：
    - backend: print / pyttsx3 / edge_tts
    - 任意后端失败都不抛异常到上层（主控必须稳定）
    - edge_tts 失败自动降级为 print
    """

    def __init__(
        self,
        enabled: bool = True,
        backend: str = "print",
        rate: Any = 150,
        volume: Any = 1.0,
        voice: str = "zh-CN-XiaoxiaoNeural",
        logger: Optional[Any] = None,
    ) -> None:
        self.logger = logger
        self.enabled = bool(enabled)
        self.backend = str(backend or "print").strip().lower() or "print"
        self.voice = str(voice or "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural"

        self.rate_raw = rate
        self.volume_raw = volume
        self._last_text: str = ""

        self._engine: Any = None
        self._available: bool = False
        self._fallback_backend: str = "print"

        # edge-tts 输出文件
        self._edge_output_path = str(Path("/tmp") / "g1_tts_output.mp3")
        # edge-tts 期望 rate/volume 为 "+0%" / "-10%" 这种格式
        self.edge_rate = self._to_edge_percent(rate, default="+0%")
        self.edge_volume = self._to_edge_percent(volume, default="+0%")

        # pyttsx3 期望 rate=int, volume=float(0-1)
        self.pyttsx3_rate: int = 150
        self.pyttsx3_volume: float = 1.0

        if not self.enabled:
            self._available = False
            return

        if self.backend == "print":
            self._available = True
            _log(self.logger, "[Speech] TTS 初始化成功（print）")
            return

        if self.backend == "edge_tts":
            # edge_tts 不需要预初始化；播放时再生成并播放
            self._available = True
            _log(
                self.logger,
                f"[Speech] TTS 初始化成功（edge_tts，voice={self.voice}, rate={self.edge_rate}, volume={self.edge_volume}）",
            )
            return

        if self.backend == "pyttsx3":
            try:
                import pyttsx3  # type: ignore

                engine = pyttsx3.init()
                self.pyttsx3_rate = self._to_pyttsx3_rate(rate, engine=engine, default=150)
                self.pyttsx3_volume = self._to_pyttsx3_volume(volume, engine=engine, default=1.0)
                try:
                    engine.setProperty("rate", self.pyttsx3_rate)
                except Exception:
                    pass
                try:
                    engine.setProperty("volume", self.pyttsx3_volume)
                except Exception:
                    pass
                self._engine = engine
                self._available = True
                _log(
                    self.logger,
                    f"[Speech] TTS 初始化成功（pyttsx3，rate={self.pyttsx3_rate}, volume={self.pyttsx3_volume}）",
                )
                return
            except Exception as exc:
                _warn(self.logger, f"[Speech][WARN] TTS 初始化失败（pyttsx3），将降级为 print：{exc}")
                self.backend = self._fallback_backend
                self._engine = None
                self._available = True
                return

        _warn(self.logger, f"[Speech][WARN] 未支持的 TTS backend={self.backend}，将降级为 print")
        self.backend = self._fallback_backend
        self._available = True

    @staticmethod
    def _to_edge_percent(v: Any, default: str = "+0%") -> str:
        """
        edge-tts rate/volume: "+0%" / "-10%" 形式。
        - 若传入已是百分号字符串，尽量规范化并返回
        - 若传入数字：当作 0（不变）处理
        """
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            if s.endswith("%"):
                # edge-tts 允许 "0%"，也允许 "+0%"；这里尽量补齐符号
                if s[0] not in ("+", "-"):
                    # "10%" -> "+10%"
                    s = f"+{s}"
                return s
            # 兼容老配置："1.0" / "150" 这类字符串，edge 后端无法直接解释，按默认
            return default
        return default

    @staticmethod
    def _try_parse_percent(s: str) -> Optional[float]:
        """
        将 "+10%" / "-10%" / "10%" 解析为 0.10 / -0.10。
        """
        try:
            t = (s or "").strip()
            if not t.endswith("%"):
                return None
            t = t[:-1].strip()  # 去掉 %
            if not t:
                return None
            # "10" / "+10" / "-10"
            p = float(t) / 100.0
            return p
        except Exception:
            return None

    def _to_pyttsx3_rate(self, v: Any, engine: Any, default: int = 150) -> int:
        """
        pyttsx3 rate:
        - int: 直接使用
        - "-10%": 按当前 engine rate 进行比例调整
        """
        try:
            if isinstance(v, int):
                return int(v)
            if isinstance(v, float):
                return int(v)
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return default
                pct = self._try_parse_percent(s if s[0] in ("+", "-") else f"+{s}")
                if pct is not None:
                    try:
                        base = int(engine.getProperty("rate") or default)
                    except Exception:
                        base = default
                    return max(50, int(round(base * (1.0 + pct))))
                return int(float(s))
        except Exception:
            return default
        return default

    def _to_pyttsx3_volume(self, v: Any, engine: Any, default: float = 1.0) -> float:
        """
        pyttsx3 volume:
        - float 0..1: 直接使用
        - "+0%"/"-10%": 按当前 engine volume 比例调整并 clamp 到 0..1
        """
        def _clamp(x: float) -> float:
            return max(0.0, min(1.0, x))

        try:
            if isinstance(v, (int, float)):
                return _clamp(float(v))
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return default
                pct = self._try_parse_percent(s if s[0] in ("+", "-") else f"+{s}")
                if pct is not None:
                    try:
                        base = float(engine.getProperty("volume") or default)
                    except Exception:
                        base = default
                    return _clamp(base * (1.0 + pct))
                return _clamp(float(s))
        except Exception:
            return default
        return default

    def speak(self, text: str) -> None:
        try:
            t = (text or "").strip()
            if not t:
                _warn(self.logger, "[Speech][WARN] TTS speak 文本为空，已忽略")
                return
            self._last_text = t

            if not self.enabled:
                return

            # 永远可用：print
            if self.backend == "print" or not self._available:
                self._print_backend(t)
                return

            if self.backend == "pyttsx3":
                if self._engine is None:
                    self._print_backend(t)
                    return
                try:
                    self._engine.say(t)
                    self._engine.runAndWait()
                except Exception as exc:
                    _warn(self.logger, f"[Speech][WARN] TTS 播报失败（pyttsx3），已降级为 print：{exc}")
                    self._print_backend(t)
                return

            if self.backend == "edge_tts":
                ok = self._edge_tts_speak_blocking(t)
                if not ok:
                    self._print_backend(t)
                return

            # 未知 backend：降级
            _warn(self.logger, f"[Speech][WARN] 未支持的 TTS backend={self.backend}，已降级为 print")
            self._print_backend(t)
        except Exception as exc:
            # 绝不让异常冒泡
            _warn(self.logger, f"[Speech][WARN] TTS speak 异常已忽略：{exc}")
            try:
                self._print_backend((text or "").strip())
            except Exception:
                pass

    def _print_backend(self, text: str) -> None:
        try:
            print(f"[TTS] {text}")
        except Exception:
            return

    async def _edge_tts_generate(self, text: str, output_path: str) -> None:
        import edge_tts  # type: ignore

        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.edge_rate,
            volume=self.edge_volume,
        )
        await communicate.save(output_path)

    def _play_audio_file(self, path: str) -> bool:
        """
        尽量用系统播放器播放，避免额外依赖。
        优先级：
        - ffplay
        - mpg123
        """
        try:
            if shutil.which("ffplay"):
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    check=False,
                )
                return True
            if shutil.which("mpg123"):
                subprocess.run(["mpg123", "-q", path], check=False)
                return True
        except Exception:
            return False
        return False

    def _edge_tts_speak_blocking(self, text: str) -> bool:
        """
        同步 speak：内部调用 edge-tts 的 async 生成流程。
        如果当前环境已有 event loop，基础版直接降级为 print（不崩溃）。
        """
        try:
            # 避免在已有 loop 中 asyncio.run 抛异常导致主控崩溃
            try:
                asyncio.get_running_loop()
                _warn(self.logger, "[Speech][WARN] 检测到运行中的 event loop，edge_tts 将降级为 print")
                return False
            except RuntimeError:
                pass  # no running loop

            out = self._edge_output_path
            # 确保目录存在
            try:
                os.makedirs(os.path.dirname(out), exist_ok=True)
            except Exception:
                pass

            asyncio.run(self._edge_tts_generate(text, out))
            if not os.path.exists(out):
                _warn(self.logger, "[Speech][WARN] edge_tts 生成音频失败：输出文件不存在")
                return False

            if not self._play_audio_file(out):
                _warn(self.logger, "[Speech][WARN] 未找到可用播放器（ffplay/mpg123），edge_tts 将降级为 print")
                return False

            return True
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] edge_tts 播报失败，将降级为 print：{exc}")
            return False

    def stop(self) -> None:
        try:
            if self.backend == "pyttsx3" and self._engine is not None:
                self._engine.stop()
        except Exception:
            return

