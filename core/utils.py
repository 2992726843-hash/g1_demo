from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Mapping, Optional


class ConfigLoader:
    """
    Singleton YAML config loader.

    Loads `configs/system_config.yaml` by default. If the file is missing or the
    YAML cannot be parsed, raises an exception (callers should treat this as fatal).
    """

    _instance: Optional["ConfigLoader"] = None
    _instance_lock: Lock = Lock()

    def __new__(cls, config_path: str | Path = "configs/system_config.yaml") -> "ConfigLoader":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self, config_path: str | Path = "configs/system_config.yaml") -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._config_path: Path = Path(config_path)
        self._config: Dict[str, Any] = self._load_yaml(self._config_path)

    @property
    def config_path(self) -> Path:
        return self._config_path

    def get_config(self) -> Mapping[str, Any]:
        return self._config

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self._config
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    @staticmethod
    def _load_yaml(path: Path) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as e:
            raise RuntimeError(
                "Missing dependency 'pyyaml'. Install it first, e.g.:\n"
                "  pip install PyYAML\n"
                "or (recommended) from project root:\n"
                "  pip install -r requirements.txt"
            ) from e

        if not path.exists():
            raise FileNotFoundError(
                f"Config file not found: {path.resolve()}. "
                f"Expected a YAML file at 'configs/system_config.yaml'."
            )

        try:
            raw_text: str = path.read_text(encoding="utf-8")
        except Exception as e:
            raise RuntimeError(f"Failed to read config file: {path.resolve()}. Error: {e}") from e

        try:
            data: Any = yaml.safe_load(raw_text)
        except Exception as e:
            raise RuntimeError(f"Failed to parse YAML config: {path.resolve()}. Error: {e}") from e

        if data is None:
            return {}
        if not isinstance(data, dict):
            raise RuntimeError(
                f"Invalid config root type in {path.resolve()}: expected mapping/dict, got {type(data).__name__}"
            )
        return data


@dataclass(frozen=True)
class _Ansi:
    RESET: str = "\033[0m"
    GRAY: str = "\033[90m"
    GREEN: str = "\033[32m"
    YELLOW: str = "\033[33m"
    RED: str = "\033[31m"


class _ConsoleColorFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        ts: str = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        module_name: str = record.name
        level: str = record.levelname
        msg: str = record.getMessage()

        color: str
        if record.levelno >= logging.ERROR:
            color = _Ansi.RED
        elif record.levelno >= logging.WARNING:
            color = _Ansi.YELLOW
        else:
            color = _Ansi.GREEN

        header = f"[{ts}] [{module_name}] [{level}]"
        return f"{_Ansi.GRAY}{header}{_Ansi.RESET} {color}- {msg}{_Ansi.RESET}"


class _PlainFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        ts: str = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        module_name: str = record.name
        level: str = record.levelname
        msg: str = record.getMessage()
        return f"[{ts}] [{module_name}] [{level}] - {msg}"


def setup_logger(name: str) -> logging.Logger:
    """
    Create or return a logger that outputs to both console and `logs/system.log`.

    Format: [time] [module] [level] - message
    Console output is colorized by level (INFO/WARNING/ERROR).
    """

    logger: logging.Logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid adding duplicate handlers if called multiple times.
    if any(getattr(h, "_g1_smarthome", False) for h in logger.handlers):
        return logger

    logs_dir: Path = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file: Path = logs_dir / "system.log"

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(_ConsoleColorFormatter())
    setattr(console_handler, "_g1_smarthome", True)

    file_handler = logging.FileHandler(filename=str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(_PlainFormatter())
    setattr(file_handler, "_g1_smarthome", True)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


if __name__ == "__main__":
    cfg = ConfigLoader().get_config()
    print("Loaded mode:", cfg.get("mode"))
    print("Robot IP:", cfg.get("robot", {}).get("ip"))

    log = setup_logger("core.utils")
    log.info("This is an INFO test log.")
    log.warning("This is a WARNING test log.")
    log.error("This is an ERROR test log.")
