"""
文本播报：优先 Piper 命令行生成 WAV 再播放；不可用时降级 pyttsx3。

说明：比赛阶段优先保证“能播报”，不是追求最佳音色。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from .audio_io import AudioIO

logger = logging.getLogger(__name__)


class TTSService:
    """Piper（子进程）+ 本地播放；失败则可选 pyttsx3。"""

    def __init__(
        self,
        piper_executable: str = "piper",
        model_path: str = "",
        use_fallback_pyttsx3: bool = True,
    ) -> None:
        self.piper_executable = piper_executable
        self.model_path = (model_path or "").strip()
        self.use_fallback_pyttsx3 = use_fallback_pyttsx3
        self._audio = AudioIO()

    def _piper_cmd_prefix(self) -> Optional[List[str]]:
        """解析可执行文件路径；找不到则返回 None。"""
        exe = self.piper_executable.strip() or "piper"
        if os.path.isfile(exe):
            return [exe]
        resolved = shutil.which(exe)
        if resolved:
            return [resolved]
        logger.warning("未找到 Piper 可执行文件: %r", self.piper_executable)
        return None

    def _try_piper(self, text: str) -> bool:
        if not self.model_path:
            logger.debug("Piper model_path 未配置，跳过 Piper")
            return False

        prefix = self._piper_cmd_prefix()
        if not prefix:
            return False

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            cmd = [*prefix, "--model", self.model_path, "--output_file", wav_path]
            completed = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=120,
                check=False,
            )
            if completed.returncode != 0:
                err = (completed.stderr or b"").decode("utf-8", errors="replace")
                logger.warning("Piper 执行失败 rc=%s stderr=%s", completed.returncode, err[:800])
                return False
            if not os.path.isfile(wav_path) or os.path.getsize(wav_path) == 0:
                logger.warning("Piper 未生成有效 WAV: %s", wav_path)
                return False
            self._audio.play_wav(wav_path)
            return True
        except FileNotFoundError:
            logger.warning("Piper 可执行文件不存在: %r", self.piper_executable)
            return False
        except subprocess.TimeoutExpired:
            logger.warning("Piper 执行超时")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("Piper 调用异常: %s", exc)
            return False
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def _try_pyttsx3(self, text: str) -> bool:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
            logger.info("pyttsx3 播报完成")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("pyttsx3 播报失败: %s", exc)
            return False

    def speak(self, text: str) -> bool:
        """
        播报文本。

        空文本返回 False；成功返回 True；全部失败返回 False（不抛异常）。
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return False

        if self._try_piper(cleaned):
            return True

        if self.use_fallback_pyttsx3:
            return self._try_pyttsx3(cleaned)

        logger.warning("Piper 不可用且已禁用 pyttsx3 降级")
        return False
