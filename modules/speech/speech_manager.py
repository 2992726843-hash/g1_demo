from __future__ import annotations

from collections import Counter
import re
import time
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


# 核心关键词仅用于日志提示，不再作为短句拦截条件。
_CORE_KEYWORDS: tuple[str, ...] = (
    "开灯",
    "关灯",
    "灯",
    "卧室",
    "客厅",
    "起夜",
    "卫生间",
    "厕所",
    "找药",
    "吃药",
    "药",
    "报警",
    "跌倒",
    "摔倒",
    "挥手",
    "鼓掌",
    "飞吻",
    "停止",
    "不要",
    "睡觉",
    "睡了",
    "休息",
    "晚安",
    "太暗",
    "有点冷",
    "不舒服",
    "帮帮我",
    "害怕",
)

_STRIP_EDGE_PUNCT = "，,。.!！?？、；;:\"\"''「」『』（）()[]【】…·—-"

_NOISE_CHARS = frozenset("嗯啊哦呃欸喂呀哈呵咦")
_NOISE_WORDS = frozenset(("嗯", "啊", "哦", "呃", "喂", "你好", "哈", "哈哈", "谢谢"))
_ASR_HALLUCINATION_PREFIXES = ("字幕由", "谢谢观看", "请不吝点赞", "欢迎订阅")


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
        self.wake_word_config: dict = self.cfg.get("wake_word", {}) or {}
        self.wake_cfg: dict = self.wake_word_config
        self.wake_engine: str = str(self.wake_word_config.get("engine", "sherpa_onnx") or "sherpa_onnx").strip().lower()
        self.wake_keyword: str = str(self.wake_word_config.get("keyword", "小航小航") or "小航小航").strip()
        self.wake_reply: str = str(self.wake_word_config.get("reply", "我在") or "我在").strip()
        self.command_record_seconds: float = self._get_float_config(
            self.wake_word_config, "command_record_seconds", 6.0
        )
        self.wake_reply_grace_seconds: float = self._get_float_config(
            self.wake_word_config, "wake_reply_grace_seconds", 0.2
        )
        self.wake_record_seconds: float = self._get_float_config(self.wake_word_config, "wake_record_seconds", 2.0)
        self.cooldown_sec: float = self._get_float_config(self.wake_word_config, "cooldown_sec", 0.3)
        self._sherpa_wake_detector: Any = None
        try:
            from core.utils import ConfigLoader  # type: ignore

            cfg_loader = ConfigLoader()
            self.perf_log = bool(cfg_loader.get_nested("debug", "perf_log", default=self.cfg.get("perf_log", True)))
            self.verbose_log = bool(cfg_loader.get_nested("debug", "verbose_log", default=True))
            self.asr_detail_log = bool(cfg_loader.get_nested("debug", "asr_detail_log", default=self.verbose_log))
        except Exception:
            self.perf_log = bool(self.cfg.get("perf_log", True))
            self.verbose_log = True
            self.asr_detail_log = True
        self.last_perf: dict[str, float] = {}
        self.last_wake_detected_ts: Optional[float] = None

        self.asr_available: bool = False

        self.recorder: Optional[AudioRecorder] = None
        self.asr: Optional[FasterWhisperASR] = None
        self.tts: Optional[TTSClient] = None

        self._opencc_t2s: Any = None
        self._opencc_failed: bool = False
        self._opencc_warned: bool = False

        if not self.enabled:
            _warn(self.logger, "[Speech][WARN] speech.enabled=false，语音模块禁用")
            return

        try:
            sample_rate = int(self.cfg.get("sample_rate", 16000))
            channels = int(self.cfg.get("channels", 1))
            record_seconds = int(self.cfg.get("record_seconds", 5))
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

        try:
            asr_cfg = self.cfg.get("asr", {}) or {}
            backend = str(asr_cfg.get("backend", "faster_whisper") or "faster_whisper").strip()
            if backend != "faster_whisper":
                raise RuntimeError(f"未支持的 ASR backend={backend}")

            model_size = str(asr_cfg.get("model_size", "small") or "small").strip()
            device = str(asr_cfg.get("device", "auto") or "auto").strip()
            compute_type = str(asr_cfg.get("compute_type", "auto") or "auto").strip()
            language = str(self.cfg.get("language", "zh") or "zh").strip()
            task = str(asr_cfg.get("task", "transcribe") or "transcribe").strip()
            beam_size = int(asr_cfg.get("beam_size", 5))
            best_of = int(asr_cfg.get("best_of", 5))
            vad_filter = bool(asr_cfg.get("vad_filter", True))
            condition_on_previous_text = bool(asr_cfg.get("condition_on_previous_text", False))
            initial_prompt = asr_cfg.get("initial_prompt", None)
            if initial_prompt is not None:
                initial_prompt = str(initial_prompt).strip() or None

            self.asr = FasterWhisperASR(
                model_size=model_size,
                device=device,
                compute_type=compute_type,
                language=language,
                logger=self.logger,
                task=task,
                beam_size=beam_size,
                best_of=best_of,
                vad_filter=vad_filter,
                condition_on_previous_text=condition_on_previous_text,
                initial_prompt=initial_prompt,
            )
            self.asr_available = True
        except Exception as exc:
            self.asr = None
            self.asr_available = False
            _warn(self.logger, f"[Speech][WARN] ASR 不可用，将回退到文本输入：{exc}")

        try:
            tts_cfg = self.cfg.get("tts", {}) or {}
            tts_enabled = bool(tts_cfg.get("enabled", True))
            backend = str(tts_cfg.get("backend", "pyttsx3") or "pyttsx3").strip()
            rate = tts_cfg.get("rate", 150)
            volume = tts_cfg.get("volume", 1.0)
            voice = tts_cfg.get("voice", "zh-CN-XiaoxiaoNeural")
            proxy_base_url = ""
            proxy_timeout_s = 3
            try:
                from core.utils import ConfigLoader  # type: ignore

                cfg_loader = ConfigLoader()
                proxy_base_url = str(cfg_loader.get_nested("robot", "proxy_base_url", default="") or "").strip()
                proxy_timeout_s = cfg_loader.get_nested("robot", "proxy_timeout_s", default=3)
            except Exception:
                proxy_base_url = ""
                proxy_timeout_s = 3
            self.tts = TTSClient(
                enabled=tts_enabled,
                backend=backend,
                rate=rate,
                volume=volume,
                voice=voice,
                g1_speaker_id=tts_cfg.get("g1_speaker_id", 0),
                fallback_backend=tts_cfg.get("fallback_backend", "print"),
                fallback_to_pc_tts=bool(tts_cfg.get("fallback_to_pc_tts", True)),
                cooldown_retry_ms=tts_cfg.get("cooldown_retry_ms", 0),
                proxy_base_url=proxy_base_url,
                proxy_timeout_s=proxy_timeout_s,
                logger=self.logger,
            )
            _log(
                self.logger,
                "[Speech][TTS] init config: "
                f"input_mode={self.input_mode} enabled={tts_enabled} backend={backend} "
                f"proxy_base_url={proxy_base_url} timeout_s={proxy_timeout_s} "
                f"fallback_backend={tts_cfg.get('fallback_backend', 'print')} "
                f"fallback_to_pc_tts={bool(tts_cfg.get('fallback_to_pc_tts', True))}",
            )
        except Exception as exc:
            self.tts = TTSClient(enabled=False, logger=self.logger)
            _warn(self.logger, f"[Speech][WARN] TTS 初始化失败，将降级为 print：{exc}")

        _log(self.logger, f"[Speech] input_mode = {self.input_mode}")
        if self.input_mode == "wake_word":
            _log(self.logger, f"[WakeWord] keyword = {self.wake_keyword}")
            _log(self.logger, f"[WakeWord] engine = {self.wake_engine}")
        _log(self.logger, "[Speech] 语音模块初始化成功")

    @staticmethod
    def _get_float_config(config: dict, key: str, default: float) -> float:
        try:
            return float(config.get(key, default))
        except Exception:
            return float(default)

    @staticmethod
    def _ms(start: float) -> float:
        return (time.perf_counter() - start) * 1000.0

    def _perf(self, stage: str, start: float) -> float:
        cost_ms = self._ms(start)
        self.last_perf[stage] = cost_ms
        if self.perf_log:
            _log(self.logger, f"[PERF] {stage} cost={cost_ms:.1f} ms")
        return cost_ms

    @staticmethod
    def _fullwidth_to_halfwidth(s: str) -> str:
        out: list[str] = []
        for ch in s:
            code = ord(ch)
            if code == 0x3000:
                out.append(" ")
            elif 0xFF01 <= code <= 0xFF5E:
                out.append(chr(code - 0xFEE0))
            else:
                out.append(ch)
        return "".join(out)

    def _convert_traditional_to_simplified(self, text: str) -> str:
        if not text:
            return text
        if self._opencc_failed:
            return text
        if self._opencc_t2s is not None:
            try:
                return str(self._opencc_t2s.convert(text))
            except Exception:
                return text
        try:
            from opencc import OpenCC  # type: ignore

            self._opencc_t2s = OpenCC("t2s")
            return str(self._opencc_t2s.convert(text))
        except Exception:
            if not self._opencc_warned:
                self._opencc_warned = True
                _warn(self.logger, "[Speech][WARN] OpenCC 不可用，跳过繁简转换。")
            self._opencc_failed = True
            return text

    @staticmethod
    def is_low_confidence_text(text: str) -> bool:
        s = (text or "").strip()
        if not s:
            return True
        if len(s) < 2:
            return True
        compact = re.sub(r"\s+", "", s)
        if compact in _NOISE_WORDS:
            return True
        if compact and all((c in _NOISE_CHARS) for c in compact):
            return True
        if compact.startswith(_ASR_HALLUCINATION_PREFIXES):
            return True
        if compact and all((not c.isalnum()) for c in compact):
            return True
        if compact.isdigit():
            return True
        if re.fullmatch(r"[A-Za-z]+", compact) is not None:
            return True
        if len(compact) >= 5:
            if len(set(compact)) == 1:
                return True
            most = Counter(compact).most_common(1)[0][1]
            if most >= max(5, int(len(compact) * 0.65) + 1):
                return True
        return False

    @staticmethod
    def _contains_core_keyword(text: str) -> bool:
        return any(kw in text for kw in _CORE_KEYWORDS)

    def normalize_text(self, text: str) -> str:
        """基础清洗 + 高确定性口语归一化（不做过度意图改写）。"""
        s = (text or "").strip()
        if not s:
            return ""
        s = " ".join(s.split())
        s = self._fullwidth_to_halfwidth(s)
        s = s.strip(_STRIP_EDGE_PUNCT).strip()
        s = " ".join(s.split())

        before_cc = s
        s = self._convert_traditional_to_simplified(s)
        if before_cc and self.asr_detail_log:
            _log(self.logger, f"[Speech] 繁简转换后文本: {s}")

        replacements = [
            ("去厕所", "去卫生间"),
            ("上厕所", "去卫生间"),
            ("洗手间", "卫生间"),
            ("找要", "找药"),
            ("吃要", "吃药"),
            ("七夜", "起夜"),
            ("起页", "起夜"),
            ("喂手", "挥手"),
            ("辉手", "挥手"),
            ("古掌", "鼓掌"),
            ("股掌", "鼓掌"),
            ("关灯灯", "关灯"),
            ("卧石", "卧室"),
            ("握时", "卧室"),
            ("盖片", "钙片"),
            ("酱鸭要", "降压药"),
            ("酱鸭药", "降压药"),
            ("酱鸭又", "降压药"),
        ]
        for a, b in replacements:
            try:
                s = s.replace(a, b)
            except Exception:
                continue

        s = " ".join(s.split())
        if s and self.asr_detail_log:
            _log(self.logger, f"[Speech] 归一化后文本: {s}")
        return s

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
            pass

        text = ""
        try:
            wav_path = self.recorder.record_to_file()
            text = self.asr.transcribe(wav_path)
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] listen_once 失败：{exc}")
            return ""

        normalized = self.normalize_text(text)
        if self.is_low_confidence_text(normalized):
            _warn(self.logger, f"[Speech][WARN] 识别结果可信度低，请用户重说：{normalized!r}")
            _warn(self.logger, f"[Speech][WARN] 低质量识别，已忽略: {normalized!r}")
            try:
                self.speak("我没有听清楚，请您再说一遍。")
            except Exception:
                pass
            return ""

        if normalized and self.verbose_log and not self._contains_core_keyword(normalized):
            _log(self.logger, f"[Speech] 正常短句，无核心关键词，交给上层意图解析: {normalized}")

        return normalized

    def transcribe_once_no_prompt(self, record_seconds: Optional[float] = None, perf_prefix: str = "command") -> str:
        """
        不等待 Enter，直接录音并识别一次。

        用于 wake_word 模式的自动短录音监听和唤醒后的正式指令录制。
        """
        return self._transcribe_once_no_prompt(
            record_seconds=record_seconds,
            speak_on_low_confidence=False,
            perf_prefix=perf_prefix,
        )

    def _transcribe_once_no_prompt(
        self,
        record_seconds: Optional[float] = None,
        *,
        speak_on_low_confidence: bool = False,
        perf_prefix: str = "command",
    ) -> str:
        if not self.enabled:
            return ""
        if not self.asr_available or self.asr is None or self.recorder is None:
            _warn(self.logger, "[Speech][WARN] ASR 不可用，wake_word/voice 无法录音识别；请切回 text/manual 模式。")
            return ""

        old_record_seconds = getattr(self.recorder, "record_seconds", None)
        if record_seconds is not None:
            try:
                self.recorder.record_seconds = max(1, int(round(float(record_seconds))))
            except Exception:
                pass

        try:
            record_start = time.perf_counter()
            wav_path = self.recorder.record_to_file()
            self._perf(f"{perf_prefix}_record", record_start)
            if self.perf_log:
                _log(
                    self.logger,
                    f"[PERF] {perf_prefix}_record configured_seconds={getattr(self.recorder, 'record_seconds', '')}",
                )
            asr_start = time.perf_counter()
            text = self.asr.transcribe(wav_path)
            self._perf(f"{perf_prefix}_asr", asr_start)
        except Exception as exc:
            _warn(self.logger, f"[Speech][WARN] transcribe_once_no_prompt 失败：{exc}")
            return ""
        finally:
            if record_seconds is not None and old_record_seconds is not None:
                try:
                    self.recorder.record_seconds = old_record_seconds
                except Exception:
                    pass

        normalize_start = time.perf_counter()
        normalized = self.normalize_text(text)
        self._perf(f"{perf_prefix}_normalize", normalize_start)
        if self.is_low_confidence_text(normalized):
            _warn(self.logger, f"[Speech][WARN] 低质量识别，已忽略: {normalized!r}")
            if speak_on_low_confidence:
                try:
                    self.speak("我没有听清楚，请您再说一遍。")
                except Exception:
                    pass
            return ""

        if normalized and self.verbose_log and not self._contains_core_keyword(normalized):
            _log(self.logger, f"[Speech] 正常短句，无核心关键词，交给上层意图解析: {normalized}")

        return normalized

    def _is_wake_word_detected(self, text: str, keyword: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact:
            return False

        aliases = [keyword, "小杭小杭", "小行小行"]
        configured_aliases = getattr(self, "wake_cfg", {}).get("aliases", None)
        if isinstance(configured_aliases, list):
            aliases.extend(str(v).strip() for v in configured_aliases if str(v).strip())

        for alias in aliases:
            a = re.sub(r"\s+", "", alias or "")
            if a and a in compact:
                return True

        # “小航”较短，避免在长句里宽松命中导致误唤醒。
        return compact in {"小航", "小杭", "小行"}

    def _get_sherpa_wake_detector(self) -> Any:
        if self._sherpa_wake_detector is not None:
            return self._sherpa_wake_detector
        from modules.speech.sherpa_wake_word import SherpaWakeWordDetector

        sherpa_config = dict(self.wake_word_config.get("sherpa_onnx", {}) or {})
        sherpa_config.setdefault("perf_log", self.perf_log)
        self._sherpa_wake_detector = SherpaWakeWordDetector(sherpa_config, logger=self.logger)
        return self._sherpa_wake_detector

    def _wait_for_wake_word_simple_asr(self) -> bool:
        if not self.asr_available or self.asr is None or self.recorder is None:
            _warn(self.logger, "[WakeWord][WARN] ASR 不可用，simple_asr 唤醒无法启动；请切回 text/manual 模式。")
            return False

        _log(self.logger, "[WakeWord] 开始监听")
        while True:
            _log(self.logger, f"[WakeWord] 等待唤醒词: {self.wake_keyword}")
            text = self.transcribe_once_no_prompt(record_seconds=self.wake_record_seconds, perf_prefix="wake_simple_asr")
            if text:
                _log(self.logger, f"[WakeWord] 识别片段: {text}")

            if self._is_wake_word_detected(text, self.wake_keyword):
                _log(self.logger, f"[WakeWord] detected: {self.wake_keyword}")
                return True

            if self.cooldown_sec > 0:
                time.sleep(self.cooldown_sec)

    def _wait_for_wake_word_mock(self) -> bool:
        while True:
            try:
                text = input(f"[WakeWord][mock] 请输入唤醒词 {self.wake_keyword} (q 退出): ").strip()
            except EOFError:
                raise KeyboardInterrupt
            if text.lower() in ("q", "quit", "exit"):
                raise KeyboardInterrupt
            if self._is_wake_word_detected(text, self.wake_keyword):
                _log(self.logger, f"[WakeWord] detected: {self.wake_keyword}")
                return True

    def wait_for_wake_word(self) -> bool:
        if not self.enabled:
            return False
        if not bool(self.wake_word_config.get("enabled", True)):
            _warn(self.logger, "[WakeWord][WARN] wake_word.enabled=false，跳过唤醒词监听")
            return False

        engine = self.wake_engine
        wait_start = time.perf_counter()
        if self.perf_log:
            _log(self.logger, "[PERF] wake_word_wait start")
        if engine == "sherpa_onnx":
            try:
                detector = self._get_sherpa_wake_detector()
                result = detector.wait_for_wake()
                if result:
                    self._perf("wake_word_wait", wait_start)
                    _log(self.logger, f"[WakeWord] detected: {self.wake_keyword}")
                    return True
                return False
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                _warn(self.logger, f"[WakeWord][ERROR] sherpa_onnx 唤醒失败: {exc}")
                _warn(self.logger, "[WakeWord][WARN] 回退到 simple_asr 唤醒")
                return self._wait_for_wake_word_simple_asr()

        if engine == "simple_asr":
            ok = self._wait_for_wake_word_simple_asr()
            if ok:
                self._perf("wake_word_wait", wait_start)
            return ok

        if engine == "mock":
            ok = self._wait_for_wake_word_mock()
            if ok:
                self._perf("wake_word_wait", wait_start)
            return ok

        _warn(self.logger, f"[WakeWord][WARN] 未知 engine={engine}，回退 simple_asr")
        ok = self._wait_for_wake_word_simple_asr()
        if ok:
            self._perf("wake_word_wait", wait_start)
        return ok

    def wait_wake_and_transcribe(self) -> str:
        self.last_perf = {}
        self.last_wake_detected_ts = None
        _log(self.logger, f"[WakeWord] keyword = {self.wake_keyword}")
        ok = self.wait_for_wake_word()
        if not ok:
            return ""

        self.last_wake_detected_ts = time.perf_counter()
        after_wake_start = self.last_wake_detected_ts
        _log(self.logger, f"[Speech] 播报: {self.wake_reply}")
        tts_start = time.perf_counter()
        self.speak(self.wake_reply)
        self._perf("wake_reply_tts", tts_start)
        if self.wake_reply_grace_seconds > 0:
            _log(
                self.logger,
                f"[WakeWord] wake reply grace sleep {self.wake_reply_grace_seconds:.2f}s before command recording",
            )
            time.sleep(self.wake_reply_grace_seconds)

        _log(self.logger, "[ASR] 开始录制唤醒后的正式指令")
        text = self.transcribe_once_no_prompt(record_seconds=self.command_record_seconds)

        if not text:
            self._perf("wait_wake_and_transcribe_total", after_wake_start)
            _warn(self.logger, "[ASR][WARN] 唤醒后未识别到有效指令，回到唤醒词监听")
            return ""

        _log(self.logger, f"[ASR] 指令识别结果: {text}")
        self._perf("wait_wake_and_transcribe_total", after_wake_start)
        return text

    def speak(self, text: str) -> None:
        if not self.enabled or self.tts is None:
            _warn(self.logger, "[Speech][TTS][WARN] fallback to print because: SpeechManager disabled or TTS not initialized")
            try:
                print(f"[TTS] {(text or '').strip()}")
            except Exception:
                pass
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
