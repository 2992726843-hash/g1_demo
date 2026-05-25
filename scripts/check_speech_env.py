from __future__ import annotations

import importlib.util
import multiprocessing as mp
import os
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _print_import_status(name: str) -> None:
    print(f"{name}: {'OK' if importlib.util.find_spec(name) else 'MISSING'}")


def _query_sounddevice_devices(queue: "mp.Queue[str]") -> None:
    try:
        import sounddevice as sd  # type: ignore

        queue.put(str(sd.query_devices()))
    except Exception as exc:
        queue.put(f"SOUNDDEVICE_ERROR: {exc!r}")


def main() -> int:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    print(f"python: {sys.executable}")
    print(f"sys.prefix: {sys.prefix}")
    print(f"sys.base_prefix: {sys.base_prefix}")
    print(f"venv: {sys.prefix != sys.base_prefix}")
    print(f"conda_prefix: {os.environ.get('CONDA_PREFIX', '')}")

    for name in ("sherpa_onnx", "sounddevice", "numpy", "yaml"):
        _print_import_status(name)

    print("=== sounddevice input devices ===")
    queue: "mp.Queue[str]" = mp.Queue()
    proc = mp.Process(target=_query_sounddevice_devices, args=(queue,))
    proc.start()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=1)
        print("SOUNDDEVICE_ERROR: query_devices timeout after 5s")
    elif not queue.empty():
        print(queue.get())
    else:
        print("SOUNDDEVICE_ERROR: query_devices returned no output")

    from core.utils import ConfigLoader

    cfg = ConfigLoader()
    sherpa_cfg = cfg.get_nested("speech", "wake_word", "sherpa_onnx", default={}) or {}
    model_dir = root / str(
        sherpa_cfg.get(
            "model_dir",
            "models/kws/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01",
        )
    )
    keywords_file = root / str(sherpa_cfg.get("keywords_file", "configs/kws/keywords.txt"))

    required = [
        model_dir / "tokens.txt",
        model_dir / "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        model_dir / "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        model_dir / "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
        keywords_file,
    ]

    print("=== sherpa KWS files ===")
    for path in required:
        print(f"{path}: {'OK' if path.exists() else 'MISSING'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
