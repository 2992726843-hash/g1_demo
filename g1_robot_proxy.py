from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Tuple


HOST = "0.0.0.0"
PORT = 9001
DDS_INTERFACE = "eth0"
SDK_DIR = "/home/unitree/mydemo/unitree_sdk2_python"
LOCAL_SDK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unitree_sdk2_python")
SAFE_MODE = True
RELEASE_BEFORE_ARM_ACTION = True
TTS_COOLDOWN_S = 8.0
AUDIO_APP_NAME = "g1_robot_proxy"

SAFE_ARM_ACTIONS: Dict[str, int] = {
    "release_arm": 99,
    "two_hand_kiss": 11,
    "left_kiss": 12,
    "right_kiss": 13,
    "hands_up": 15,
    "clap": 17,
    "high_five": 18,
    "hug": 19,
    "heart": 20,
    "right_heart": 21,
    "reject": 22,
    "right_hand_up": 23,
    "x_ray": 24,
    "wave_face": 25,
    "wave_hand": 26,
    "shake_hand": 27,
}

ARM_ACTION_ALIASES: Dict[str, str] = {
    "greet": "wave_hand",
    "say_hello": "wave_hand",
    "goodbye": "wave_face",
    "blow_kiss": "two_hand_kiss",
}


def _log(message: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {message}", flush=True)


def _json_response(ok: bool, data: Any = None, error: str = "") -> Dict[str, Any]:
    if ok:
        return {"ok": True, "data": data if data is not None else {}}
    return {"ok": False, "error": str(error or "unknown error")}


class RobotProxyState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sdk_initialized = False
        self.init_error = ""
        self.dds_interface = DDS_INTERFACE
        self.safe_mode = SAFE_MODE
        self.arm_client: Any = None
        self.loco_client: Any = None
        self.audio_client: Any = None
        self.last_tts_ts = 0.0

    def initialize_sdk(self) -> None:
        try:
            for path in (SDK_DIR, LOCAL_SDK_DIR):
                if path and os.path.isdir(path) and path not in sys.path:
                    sys.path.insert(0, path)
                    _log(f"Added SDK path: {path}")

            from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # type: ignore
            from unitree_sdk2py.g1.arm.g1_arm_action_client import G1ArmActionClient  # type: ignore
            from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient  # type: ignore
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient  # type: ignore

            _log(f"Initializing DDS on interface: {self.dds_interface}")
            ChannelFactoryInitialize(0, self.dds_interface)

            arm_client = G1ArmActionClient()
            arm_client.SetTimeout(10.0)
            arm_client.Init()

            loco_client = LocoClient()
            loco_client.SetTimeout(10.0)
            loco_client.Init()

            audio_client = AudioClient()
            audio_client.SetTimeout(10.0)
            audio_client.Init()

            self.arm_client = arm_client
            self.loco_client = loco_client
            self.audio_client = audio_client
            self.sdk_initialized = True
            self.init_error = ""
            _log("SDK initialized: G1ArmActionClient, LocoClient, AudioClient")
        except Exception as exc:  # noqa: BLE001
            self.sdk_initialized = False
            self.init_error = str(exc)
            _log(f"SDK initialization failed: {exc}")
            _log(traceback.format_exc())

    def status(self) -> Dict[str, Any]:
        return {
            "sdk_initialized": self.sdk_initialized,
            "dds_interface": self.dds_interface,
            "safe_mode": self.safe_mode,
            "init_error": self.init_error,
            "safe_arm_actions": dict(SAFE_ARM_ACTIONS),
            "arm_action_aliases": dict(ARM_ACTION_ALIASES),
            "tts_cooldown_s": TTS_COOLDOWN_S,
        }


STATE = RobotProxyState()


def _require_sdk() -> Tuple[bool, str]:
    if not STATE.sdk_initialized:
        return False, STATE.init_error or "SDK not initialized"
    return True, ""


def _call_sdk_step(name: str, fn: Callable[[], Any]) -> Dict[str, Any]:
    try:
        result = fn()
        return {"step": name, "ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        _log(f"SDK step failed: {name} err={exc}")
        _log(traceback.format_exc())
        return {"step": name, "ok": False, "error": str(exc)}


def handle_health() -> Dict[str, Any]:
    return _json_response(True, {"service": "g1_robot_proxy", "status": "running"})


def handle_status() -> Dict[str, Any]:
    return _json_response(True, STATE.status())


def handle_action(body: Dict[str, Any]) -> Dict[str, Any]:
    ok, error = _require_sdk()
    if not ok:
        return _json_response(False, error=error)

    requested_name = str(body.get("action_name") or "").strip()
    action_name = ARM_ACTION_ALIASES.get(requested_name, requested_name)
    if action_name not in SAFE_ARM_ACTIONS:
        return _json_response(False, error=f"action_name not allowed in safe mode: {requested_name!r}")
    if action_name != requested_name:
        _log(f"Mapped arm action alias: {requested_name} -> {action_name}")

    expected_id = int(SAFE_ARM_ACTIONS[action_name])
    try:
        requested_id = int(body.get("action_id"))
    except Exception:
        requested_id = expected_id
    if requested_id != expected_id:
        _log(
            "WARNING: action_id mismatch; using whitelist id. "
            f"action_name={action_name} requested_id={requested_id} whitelist_id={expected_id}"
        )

    steps = []
    with STATE.lock:
        if action_name != "release_arm" and RELEASE_BEFORE_ARM_ACTION:
            _log(f"Running release_arm before action: {action_name}")
            steps.append(_call_sdk_step("release_arm", lambda: STATE.arm_client.ExecuteAction(SAFE_ARM_ACTIONS["release_arm"])))
        steps.append(_call_sdk_step(action_name, lambda: STATE.arm_client.ExecuteAction(expected_id)))

    return _json_response(True, {"action_name": action_name, "action_id": expected_id, "steps": steps})


def handle_loco(body: Dict[str, Any]) -> Dict[str, Any]:
    ok, error = _require_sdk()
    if not ok:
        return _json_response(False, error=error)

    action = str(body.get("action") or "").strip().lower()
    target = str(body.get("target") or "").strip()
    with STATE.lock:
        if action == "stop":
            step = _call_sdk_step("StopMove", lambda: STATE.loco_client.StopMove())
            return _json_response(True, {"action": action, "target": target, "steps": [step]})
        if action in {"stand", "high_stand"}:
            step = _call_sdk_step("HighStand", lambda: STATE.loco_client.HighStand())
            return _json_response(True, {"action": action, "target": target, "steps": [step]})

    if action in {"move", "navigate", "forward", "backward", "left", "right", "turn"}:
        return _json_response(False, error="loco move disabled in safe mode")
    return _json_response(False, error=f"loco action not allowed in safe mode: {action!r}")


def handle_stop() -> Dict[str, Any]:
    ok, error = _require_sdk()
    if not ok:
        return _json_response(False, error=error)

    steps = []
    with STATE.lock:
        steps.append(_call_sdk_step("LocoClient.StopMove", lambda: STATE.loco_client.StopMove()))
        steps.append(_call_sdk_step("AudioClient.PlayStop", lambda: STATE.audio_client.PlayStop(AUDIO_APP_NAME)))
        steps.append(_call_sdk_step("G1ArmActionClient.release_arm", lambda: STATE.arm_client.ExecuteAction(SAFE_ARM_ACTIONS["release_arm"])))

    return _json_response(True, {"safe_stop": True, "steps": steps})


def handle_speak(body: Dict[str, Any]) -> Dict[str, Any]:
    ok, error = _require_sdk()
    if not ok:
        return _json_response(False, error=error)

    text = str(body.get("text") or "").strip()
    if not text:
        return _json_response(False, error="text is empty")
    try:
        speaker_id = int(body.get("speaker_id", 0))
    except Exception:
        speaker_id = 0

    now = time.monotonic()
    elapsed = now - STATE.last_tts_ts
    if elapsed < TTS_COOLDOWN_S:
        return _json_response(False, error=f"TTS cooldown active: wait {TTS_COOLDOWN_S - elapsed:.1f}s")

    with STATE.lock:
        step = _call_sdk_step("AudioClient.TtsMaker", lambda: STATE.audio_client.TtsMaker(text, speaker_id))
        if step.get("ok"):
            STATE.last_tts_ts = time.monotonic()
    return _json_response(True, {"text": text, "speaker_id": speaker_id, "step": step})


class G1RobotProxyHandler(BaseHTTPRequestHandler):
    server_version = "G1RobotProxy/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self) -> None:
        body = self._read_json_body()
        _log(f"HTTP request method={self.command} path={self.path} body={json.dumps(body, ensure_ascii=False)}")
        try:
            if self.command == "GET" and self.path == "/health":
                payload = handle_health()
            elif self.command == "GET" and self.path == "/api/robot/status":
                payload = handle_status()
            elif self.command == "POST" and self.path == "/api/robot/action":
                payload = handle_action(body)
            elif self.command == "POST" and self.path == "/api/robot/loco":
                payload = handle_loco(body)
            elif self.command == "POST" and self.path == "/api/robot/stop":
                payload = handle_stop()
            elif self.command == "POST" and self.path == "/api/robot/speak":
                payload = handle_speak(body)
            else:
                payload = _json_response(False, error=f"unknown route: {self.command} {self.path}")
        except Exception as exc:  # noqa: BLE001
            _log(f"Unhandled request error: {exc}")
            _log(traceback.format_exc())
            payload = _json_response(False, error=str(exc))

        _log(f"HTTP response path={self.path} payload={json.dumps(payload, ensure_ascii=False)}")
        self._send_json(payload, status=200)

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()


def main() -> None:
    _log("Starting g1_robot_proxy")
    STATE.initialize_sdk()
    httpd = ThreadingHTTPServer((HOST, PORT), G1RobotProxyHandler)
    _log(f"Listening on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("Received Ctrl+C, stopping")
    finally:
        httpd.server_close()
        _log("g1_robot_proxy stopped")


if __name__ == "__main__":
    main()
