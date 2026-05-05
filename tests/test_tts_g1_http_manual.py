from __future__ import annotations

import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.speech.tts_client import TTSClient  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger("test_tts_g1_http_manual")

    tts = TTSClient(
        enabled=True,
        backend="g1_http",
        g1_speaker_id=0,
        fallback_backend="print",
        fallback_to_pc_tts=True,
        proxy_base_url="http://192.168.1.114:9001",
        proxy_timeout_s=3,
        logger=logger,
    )

    print("Calling TTSClient.speak() via g1_http ...")
    tts.speak("1语音播报测试成功")
    print("Done. If fake server is not running, this should have fallen back to print.")


if __name__ == "__main__":
    main()
