import sys
import time
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} networkInterface")
        sys.exit(1)

    net_if = sys.argv[1]
    print(f"[TEST] init DDS interface = {net_if}")

    ChannelFactoryInitialize(0, net_if)

    audio_client = AudioClient()
    audio_client.SetTimeout(10.0)
    audio_client.Init()

    print("[TEST] GetVolume...")
    ret = audio_client.GetVolume()
    print("[TEST] GetVolume ret =", ret)

    print("[TEST] TTS...")
    ret = audio_client.TtsMaker("语音测试，G1局域网通信测试。", 0)
    print("[TEST] TtsMaker ret =", ret)

    time.sleep(3)

    print("[TEST] LED red...")
    ret = audio_client.LedControl(255, 0, 0)
    print("[TEST] LedControl ret =", ret)

    time.sleep(1)

    print("[TEST] LED green...")
    ret = audio_client.LedControl(0, 255, 0)
    print("[TEST] LedControl ret =", ret)

    print("[TEST] done")