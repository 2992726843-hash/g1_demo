"""配置加载工具。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


BASE_DIR = Path(__file__).resolve().parent


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n"
            f"请先把 {path.name}.example 复制成 {path.name} 并填写。"
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误，根必须是映射: {path}")
    return data


def load_config() -> Dict[str, Any]:
    cfg_path = Path(os.environ.get("IOT_CONFIG", BASE_DIR / "config.yaml"))
    cfg = _load_yaml(cfg_path)
    cfg.setdefault("ha_url", "http://localhost:8123")
    cfg.setdefault("token", "")
    cfg.setdefault("server_host", "0.0.0.0")
    cfg.setdefault("server_port", 5000)
    cfg.setdefault("timeout", 5)
    cfg.setdefault("mock", True)
    cfg.setdefault("log_level", "INFO")
    return cfg


def load_devices() -> Dict[str, str]:
    dev_path = Path(os.environ.get("IOT_DEVICES", BASE_DIR / "devices.yaml"))
    data = _load_yaml(dev_path)
    # 全部强转为字符串
    return {str(k): str(v) for k, v in data.items()}
