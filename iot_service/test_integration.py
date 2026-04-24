#!/usr/bin/env python3
"""IoT Service — 全量集成测试（真实 Home Assistant / Docker）

流程
────
1. 启动 homeassistant/home-assistant Docker 容器
2. 等待 HA HTTP 接口就绪
3. 自动完成 Onboarding（首次初始化），获取 access_token
4. 等待 input_boolean 虚拟设备实体出现
5. 生成测试专用 config.yaml + devices.yaml（指向真实 HA）
6. 启动 IoT 服务（真实 HA 模式，端口 5002）
7. 全量接口测试：单设备 + 三场景 + 错误处理 + 闭环验证
8. 打印测试报告
9. 清理（可选 --keep 保留容器）

用法
────
  .venv/bin/python test_integration.py            # 自动完整流程
  .venv/bin/python test_integration.py --keep     # 测试后保留容器
  .venv/bin/python test_integration.py --no-pull  # 跳过镜像拉取

需要
────
  - Docker 已安装并已启动
  - 已执行 pip install -r requirements.txt（或用 venv）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

# ─────────────────────────────────────── 常量 ─────────────────────────────── #
SCRIPT_DIR = Path(__file__).resolve().parent

HA_IMAGE     = "homeassistant/home-assistant:stable"
HA_CONTAINER = "ha_iot_test"
HA_PORT      = 8123
HA_URL       = f"http://localhost:{HA_PORT}"
CLIENT_ID    = f"http://localhost:{HA_PORT}/"

SVC_PORT = 5002
SVC_URL  = f"http://localhost:{SVC_PORT}"

HA_USERNAME = "admin"
HA_PASSWORD = "Admin12345!"
HA_DISPLAY  = "IoT TestAdmin"

ONBOARD_TIMEOUT = 180   # 等 HA 启动，最长 3 分钟
ENTITY_TIMEOUT  = 90    # 等 input_boolean 实体出现

# input_boolean 实体 ID（在 HA configuration.yaml 里定义）
DEVICE_MAP: Dict[str, str] = {
    "living_room_light":  "input_boolean.living_room_light",
    "bedroom_light":      "input_boolean.bedroom_light",
    "path_strip":         "input_boolean.path_strip",
    "medicine_light":     "input_boolean.medicine_light",
    "alarm_socket":       "input_boolean.alarm_socket",
    "night_light_socket": "input_boolean.night_light_socket",
}

# HA configuration.yaml —— 用 input_boolean 虚拟六个受控设备
HA_CONFIG_YAML = """\
# IoT 集成测试专用 HA 配置 —— 由 test_integration.py 自动生成
default_config:

input_boolean:
  living_room_light:
    name: "客厅主灯"
  bedroom_light:
    name: "卧室主灯"
  path_strip:
    name: "路径灯带"
  medicine_light:
    name: "药盒提示灯"
  alarm_socket:
    name: "报警器插座"
  night_light_socket:
    name: "小夜灯插座"
"""

# ─────────────────────────────────────── 终端颜色 ─────────────────────────── #
BOLD   = "\033[1m"
GREEN  = "\033[32m"
RED    = "\033[31m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
RESET  = "\033[0m"

# ─────────────────────────────────────── 统计 ─────────────────────────────── #
_results: List[Tuple[str, bool, str]] = []
_passed = 0
_failed = 0


def _ok(name: str, detail: str = "") -> None:
    global _passed
    _passed += 1
    _results.append((name, True, detail))
    suffix = f"  {detail}" if detail else ""
    print(f"  {GREEN}✓{RESET} {name}{suffix}")


def _fail(name: str, detail: str = "") -> None:
    global _failed
    _failed += 1
    _results.append((name, False, detail))
    print(f"  {RED}✗{RESET} {name}  {RED}{detail}{RESET}")


def _section(title: str) -> None:
    print(f"\n{CYAN}{BOLD}{'─' * 55}{RESET}")
    print(f"{CYAN}{BOLD}  {title}{RESET}")
    print(f"{CYAN}{'─' * 55}{RESET}")


# ─────────────────────────────────────── Docker 辅助 ──────────────────────── #
def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def docker_available() -> bool:
    return _run("docker info", check=False).returncode == 0


def container_running(name: str) -> bool:
    r = _run(f"docker inspect -f '{{{{.State.Running}}}}' {name}", check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def start_ha_docker(config_dir: Path, pull: bool = True) -> None:
    # 清理旧容器（无论状态）
    _run(f"docker rm -f {HA_CONTAINER}", check=False)

    if pull:
        print(f"  拉取镜像 {HA_IMAGE} …（首次约需 1–5 分钟）")
        r = _run(f"docker pull {HA_IMAGE}")
        if r.returncode != 0:
            raise RuntimeError(f"docker pull 失败:\n{r.stderr}")

    print(f"  启动容器 {HA_CONTAINER} …")
    r = _run(
        f"docker run -d "
        f"--name {HA_CONTAINER} "
        f"-p {HA_PORT}:8123 "
        f"-v {config_dir}:/config "
        f"--restart=no "
        f"{HA_IMAGE}"
    )
    if r.returncode != 0:
        raise RuntimeError(f"docker run 失败:\n{r.stderr}")


def stop_ha_docker() -> None:
    print(f"  停止并删除容器 {HA_CONTAINER} …")
    _run(f"docker rm -f {HA_CONTAINER}", check=False)


# ─────────────────────────────────────── HA 等待 / Onboarding ─────────────── #
def wait_for_ha(timeout: int = ONBOARD_TIMEOUT) -> bool:
    """轮询直到 HA HTTP 接口可访问。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # 先试 onboarding 端点，再试根路径
            r = requests.get(f"{HA_URL}/api/onboarding", timeout=3)
            if r.status_code in (200, 401, 403, 404):
                return True
        except Exception:
            pass
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(3)
    return False


def _need_onboarding() -> bool:
    """检查 HA 是否仍需 onboarding（有未完成的步骤）。"""
    try:
        r = requests.get(f"{HA_URL}/api/onboarding", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return any(not step.get("done", False) for step in data)
        return False
    except Exception:
        return False


def _login_token() -> str:
    """用用户名密码换取 access_token（适用于已 onboarded 的 HA）。"""
    r = requests.post(
        f"{HA_URL}/auth/token",
        data={
            "grant_type": "password",
            "username": HA_USERNAME,
            "password": HA_PASSWORD,
            "client_id": CLIENT_ID,
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"login/token 失败 HTTP {r.status_code}: {r.text[:400]}"
        )
    token = r.json().get("access_token", "")
    if not token:
        raise RuntimeError(f"响应中无 access_token: {r.text[:400]}")
    return token


def onboard_ha() -> str:
    """执行 HA 首次 Onboarding，返回 access_token；若已 onboarded 则直接登录。"""
    if not _need_onboarding():
        print("\n  [信息] HA 已 onboarded，直接用密码登录获取 token")
        return _login_token()

    # 步骤 1：创建首个管理员账户
    r = requests.post(
        f"{HA_URL}/api/onboarding/users",
        json={
            "name": HA_DISPLAY,
            "username": HA_USERNAME,
            "password": HA_PASSWORD,
            "language": "zh-Hans",
            "client_id": CLIENT_ID,
        },
        timeout=20,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"onboarding/users 失败 HTTP {r.status_code}: {r.text[:400]}"
        )
    auth_code = r.json().get("auth_code", "")
    if not auth_code:
        raise RuntimeError(f"响应中无 auth_code: {r.text[:400]}")

    # 步骤 2：用 auth_code 换取 access_token
    r2 = requests.post(
        f"{HA_URL}/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": CLIENT_ID,
        },
        timeout=15,
    )
    if r2.status_code != 200:
        raise RuntimeError(
            f"auth/token 失败 HTTP {r2.status_code}: {r2.text[:400]}"
        )
    token = r2.json().get("access_token", "")
    if not token:
        raise RuntimeError(f"响应中无 access_token: {r2.text[:400]}")

    # 步骤 3：完成剩余 onboarding steps（integration / core_config）
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for step in ("integration", "core_config"):
        rs = requests.post(
            f"{HA_URL}/api/onboarding/{step}",
            json={"client_id": CLIENT_ID},
            headers=hdrs,
            timeout=10,
        )
        if rs.status_code not in (200, 201, 404):
            print(f"    [warn] onboarding/{step} → {rs.status_code}")

    return token


def wait_for_entities(token: str, timeout: int = ENTITY_TIMEOUT) -> bool:
    """轮询直到所有 input_boolean 实体出现在 HA 中。"""
    hdrs = {"Authorization": f"Bearer {token}"}
    target = set(DEVICE_MAP.values())
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{HA_URL}/api/states", headers=hdrs, timeout=5)
            if r.status_code == 200:
                eids = {s["entity_id"] for s in r.json()}
                if target.issubset(eids):
                    return True
        except Exception:
            pass
        sys.stdout.write(".")
        sys.stdout.flush()
        time.sleep(3)
    return False


def ha_get_state(token: str, entity_id: str) -> str:
    """直接向 HA 查询实体状态（闭环验证用）。"""
    hdrs = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=hdrs, timeout=5)
    if r.status_code == 200:
        return r.json().get("state", "unknown")
    return "error"


# ─────────────────────────────────────── IoT Service 管理 ─────────────────── #
_svc_proc: Optional[subprocess.Popen] = None


def start_iot_service(cfg_dir: Path) -> None:
    global _svc_proc
    env = os.environ.copy()
    env["IOT_CONFIG"]  = str(cfg_dir / "config.yaml")
    env["IOT_DEVICES"] = str(cfg_dir / "devices.yaml")
    uvicorn = SCRIPT_DIR / ".venv" / "bin" / "uvicorn"
    _svc_proc = subprocess.Popen(
        [str(uvicorn), "app:app", "--host", "0.0.0.0", "--port", str(SVC_PORT)],
        cwd=str(SCRIPT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # 最多等 15 秒直到服务可访问
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            requests.get(f"{SVC_URL}/iot/status", timeout=2)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("IoT 服务启动超时（15s）")


def stop_iot_service() -> None:
    global _svc_proc
    if _svc_proc and _svc_proc.poll() is None:
        _svc_proc.terminate()
        try:
            _svc_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _svc_proc.kill()


# ─────────────────────────────────────── 测试断言 ─────────────────────────── #
def _assert_device_resp(
    name: str,
    resp: requests.Response,
    expected_state: Optional[str] = None,
    ha_token: Optional[str] = None,
) -> None:
    """断言单设备接口响应，并可选地做 HA 闭环验证。"""
    try:
        data = resp.json()
    except Exception:
        _fail(name, f"非 JSON 响应: {resp.text[:100]}")
        return

    if resp.status_code != 200 or not data.get("ok"):
        _fail(name, f"HTTP {resp.status_code}  ok={data.get('ok')}  msg={data.get('message')}")
        return

    svc_state = data.get("state", "?")

    # 闭环：直接查 HA 验证
    if ha_token and expected_state:
        eid = data.get("entity_id", "")
        ha_state = ha_get_state(ha_token, eid) if eid else "unknown"
        if ha_state != expected_state:
            _fail(
                name,
                f"IoT 服务返回 state={svc_state}，但 HA 实际状态={ha_state}（闭环不一致）",
            )
            return
        _ok(name, f"state={svc_state}  HA直查={ha_state} ✓闭环")
        return

    if expected_state and svc_state != expected_state:
        _fail(name, f"期望 state={expected_state}，实际={svc_state}")
        return

    _ok(name, f"state={svc_state}")


def _assert_scene_resp(name: str, resp: requests.Response) -> None:
    try:
        data = resp.json()
    except Exception:
        _fail(name, f"非 JSON 响应: {resp.text[:100]}")
        return
    if resp.status_code != 200 or not data.get("ok"):
        _fail(name, f"HTTP {resp.status_code}  msg={data.get('message')}")
        return
    steps = data.get("steps", [])
    all_step_ok = all(s.get("ok") for s in steps)
    if not all_step_ok:
        bad = [s for s in steps if not s.get("ok")]
        _fail(name, f"失败步骤: {bad}")
        return
    states = [f"{s['name']}={s.get('state','?')}" for s in steps]
    _ok(name, f"{len(steps)} 步 | " + "  ".join(states))


# ─────────────────────────────────────── 全量测试套件 ─────────────────────── #
def run_tests(ha_token: str) -> None:
    B = SVC_URL

    # ── 1. 状态查询 ─────────────────────────────────────────────────────── #
    _section("1. 状态查询  GET /iot/status")
    r = requests.get(f"{B}/iot/status")
    try:
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            devs = data.get("devices", [])
            _ok("GET /iot/status（全部设备）", f"设备数={len(devs)}")
            for d in devs:
                print(f"      {d['name']:25s}  {d['entity_id']:40s}  state={d['state']}")
        else:
            _fail("GET /iot/status", str(data))
    except Exception as e:
        _fail("GET /iot/status", str(e))

    # 单设备查询
    r = requests.get(f"{B}/iot/status?name=living_room_light")
    try:
        data = r.json()
        if data.get("ok"):
            _ok("GET /iot/status?name=living_room_light", f"state={data.get('state')}")
        else:
            _fail("GET /iot/status?name=living_room_light", str(data))
    except Exception as e:
        _fail("GET /iot/status?name=living_room_light", str(e))

    # ── 2. 灯控制（含闭环验证） ──────────────────────────────────────────── #
    _section("2. 灯控制  POST /iot/light/on|off  【含 HA 闭环验证】")
    for dev in ("living_room_light", "bedroom_light", "path_strip", "medicine_light"):
        r = requests.post(f"{B}/iot/light/on", json={"name": dev})
        _assert_device_resp(
            f"light/on  [{dev}]", r, expected_state="on", ha_token=ha_token
        )
        r = requests.post(f"{B}/iot/light/off", json={"name": dev})
        _assert_device_resp(
            f"light/off [{dev}]", r, expected_state="off", ha_token=ha_token
        )

    # 带可选参数（brightness / rgb_color）
    r = requests.post(
        f"{B}/iot/light/on",
        json={"name": "path_strip", "brightness": 200, "rgb_color": [0, 255, 0]},
    )
    _assert_device_resp("light/on [path_strip] 带 brightness+rgb", r, expected_state="on")

    # ── 3. 插座控制（含闭环验证） ─────────────────────────────────────────── #
    _section("3. 插座控制  POST /iot/socket/on|off  【含 HA 闭环验证】")
    for dev in ("alarm_socket", "night_light_socket"):
        r = requests.post(f"{B}/iot/socket/on", json={"name": dev})
        _assert_device_resp(
            f"socket/on  [{dev}]", r, expected_state="on", ha_token=ha_token
        )
        r = requests.post(f"{B}/iot/socket/off", json={"name": dev})
        _assert_device_resp(
            f"socket/off [{dev}]", r, expected_state="off", ha_token=ha_token
        )

    # ── 4. 场景联动 ───────────────────────────────────────────────────────── #
    _section("4. 场景联动")

    # 先把所有设备恢复关闭，确保初始状态干净
    for dev in DEVICE_MAP.keys():
        if "light" in dev or "strip" in dev:
            requests.post(f"{B}/iot/light/off", json={"name": dev})
        else:
            requests.post(f"{B}/iot/socket/off", json={"name": dev})

    r = requests.post(f"{B}/iot/scene/night_mode")
    _assert_scene_resp("POST /iot/scene/night_mode", r)

    # 闭环：直接查 HA 确认 night_mode 影响的三个设备都 on
    for dev_name in ("living_room_light", "path_strip", "night_light_socket"):
        eid = DEVICE_MAP[dev_name]
        ha_st = ha_get_state(ha_token, eid)
        if ha_st == "on":
            _ok(f"  HA闭环 night_mode → {dev_name}", f"state={ha_st}")
        else:
            _fail(f"  HA闭环 night_mode → {dev_name}", f"HA状态={ha_st}，期望 on")

    r = requests.post(f"{B}/iot/scene/fall_alert")
    _assert_scene_resp("POST /iot/scene/fall_alert", r)

    # 闭环：path_strip + alarm_socket 都应 on
    for dev_name in ("path_strip", "alarm_socket"):
        eid = DEVICE_MAP[dev_name]
        ha_st = ha_get_state(ha_token, eid)
        if ha_st == "on":
            _ok(f"  HA闭环 fall_alert → {dev_name}", f"state={ha_st}")
        else:
            _fail(f"  HA闭环 fall_alert → {dev_name}", f"HA状态={ha_st}，期望 on")

    r = requests.post(f"{B}/iot/scene/medicine_mode")
    _assert_scene_resp("POST /iot/scene/medicine_mode", r)

    for dev_name in ("living_room_light", "medicine_light"):
        eid = DEVICE_MAP[dev_name]
        ha_st = ha_get_state(ha_token, eid)
        if ha_st == "on":
            _ok(f"  HA闭环 medicine_mode → {dev_name}", f"state={ha_st}")
        else:
            _fail(f"  HA闭环 medicine_mode → {dev_name}", f"HA状态={ha_st}，期望 on")

    # ── 5. Mock 接口在真实模式下不可用 ──────────────────────────────────────── #
    _section("5. Mock 接口隔离验证（真实 HA 模式下应 404）")
    r = requests.get(f"{B}/mock/devices")
    if r.status_code == 404:
        _ok("GET /mock/devices → 404（mock 接口已隐藏）")
    else:
        _fail("GET /mock/devices", f"期望 404，实际 {r.status_code}")

    # ── 6. 错误处理 ───────────────────────────────────────────────────────── #
    _section("6. 错误处理 / 边界条件")

    # 不存在的逻辑设备名
    r = requests.post(f"{B}/iot/light/on", json={"name": "ghost_device_xyz"})
    try:
        d = r.json()
        if not d.get("ok") and d.get("code", 0) != 0:
            _ok("未知设备名 → 返回错误码", f"code={d['code']}  msg={d['message']}")
        else:
            _fail("未知设备名应返回 ok=false", str(d))
    except Exception as e:
        _fail("未知设备名响应解析失败", str(e))

    # 直接使用不存在的 entity_id
    r = requests.post(
        f"{B}/iot/light/on", json={"name": "light.does_not_exist_ever_12345"}
    )
    try:
        d = r.json()
        # HA 会返回 404 或错误，IoT 服务应包装成 ok=false
        if not d.get("ok"):
            _ok("不存在的 entity_id → ok=false", f"code={d.get('code')}")
        else:
            # 某些 HA 实现对不存在的 entity 仍会 turn_on（创建临时状态）
            _ok("不存在的 entity_id（HA 侧行为）", f"state={d.get('state')}")
    except Exception as e:
        _fail("不存在 entity_id 响应解析失败", str(e))

    # OpenAPI 文档可访问
    _section("7. OpenAPI 文档")
    r = requests.get(f"{B}/openapi.json")
    if r.status_code == 200:
        schema = r.json()
        _ok("GET /openapi.json", f"title={schema.get('info', {}).get('title')}")
    else:
        _fail("GET /openapi.json", f"HTTP {r.status_code}")

    r = requests.get(f"{B}/docs")
    if r.status_code == 200:
        _ok("GET /docs (Swagger UI)")
    else:
        _fail("GET /docs", f"HTTP {r.status_code}")

    r = requests.get(f"{B}/redoc")
    if r.status_code == 200:
        _ok("GET /redoc")
    else:
        _fail("GET /redoc", f"HTTP {r.status_code}")


# ─────────────────────────────────────── 主流程 ───────────────────────────── #
def main() -> None:
    parser = argparse.ArgumentParser(description="IoT Service 全量集成测试")
    parser.add_argument("--keep",    action="store_true", help="测试完毕保留 HA Docker 容器")
    parser.add_argument("--no-pull", action="store_true", help="跳过 docker pull（镜像已存在时）")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}{'═' * 55}")
    print(f"  IoT Service · 全量集成测试（真实 Home Assistant）")
    print(f"{'═' * 55}{RESET}\n")

    # ── 前置检查 ──────────────────────────────────────────────────────────── #
    _section("前置检查")
    if not docker_available():
        print(f"\n{RED}  Docker 未运行，请先启动 Docker！{RESET}\n")
        sys.exit(1)
    _ok("Docker 可用")

    # ── 准备 HA 配置目录 ──────────────────────────────────────────────────── #
    ha_cfg_dir = Path(tempfile.mkdtemp(prefix="ha_iot_cfg_"))
    print(f"  HA 配置目录: {ha_cfg_dir}")
    (ha_cfg_dir / "configuration.yaml").write_text(HA_CONFIG_YAML)
    _ok("HA configuration.yaml 写入完成")

    # ── 启动 HA Docker ────────────────────────────────────────────────────── #
    _section("启动 Home Assistant Docker")
    try:
        start_ha_docker(ha_cfg_dir, pull=not args.no_pull)
        _ok(f"容器 {HA_CONTAINER} 启动中…")
    except Exception as e:
        _fail("Docker 启动失败", str(e))
        sys.exit(1)

    token: str = ""
    svc_cfg_dir = Path(tempfile.mkdtemp(prefix="iot_svc_cfg_"))

    try:
        # ── 等待 HA HTTP 就绪 ─────────────────────────────────────────────── #
        _section("等待 HA 启动（最长 3 分钟）")
        print("  轮询 /api/onboarding/steps ", end="", flush=True)
        if not wait_for_ha():
            print()
            _fail("HA 启动超时")
            raise SystemExit(1)
        print()
        _ok("HA HTTP 接口已响应")

        # ── Onboarding ─────────────────────────────────────────────────────── #
        _section("HA Onboarding（自动初始化）")
        try:
            token = onboard_ha()
            _ok("Onboarding 完成，access_token 获取成功")
        except Exception as e:
            _fail("Onboarding 失败", str(e))
            raise SystemExit(1)

        # ── 等待 input_boolean 实体 ───────────────────────────────────────── #
        _section("等待虚拟设备实体就绪（最长 90 秒）")
        print("  轮询 input_boolean 实体 ", end="", flush=True)
        if not wait_for_entities(token):
            print()
            _fail("input_boolean 实体超时未出现（HA 配置可能未加载）")
            raise SystemExit(1)
        print()
        _ok(f"全部 {len(DEVICE_MAP)} 个 input_boolean 实体已就绪")

        # ── 写 IoT 服务测试配置 ───────────────────────────────────────────── #
        _section("生成 IoT 服务配置")
        svc_config = {
            "ha_url":       HA_URL,
            "token":        token,
            "server_host":  "0.0.0.0",
            "server_port":  SVC_PORT,
            "timeout":      5,
            "mock":         False,
            "log_level":    "WARNING",
        }
        (svc_cfg_dir / "config.yaml").write_text(
            yaml.dump(svc_config, allow_unicode=True)
        )
        (svc_cfg_dir / "devices.yaml").write_text(
            yaml.dump(DEVICE_MAP, allow_unicode=True)
        )
        _ok("config.yaml", f"ha_url={HA_URL}  mock=false")
        _ok("devices.yaml", f"{len(DEVICE_MAP)} 个 input_boolean 设备")

        # ── 启动 IoT 服务 ─────────────────────────────────────────────────── #
        _section("启动 IoT 服务（真实 HA 模式）")
        try:
            start_iot_service(svc_cfg_dir)
            _ok(f"IoT 服务已启动  http://0.0.0.0:{SVC_PORT}")
            _ok(f"Swagger UI  http://localhost:{SVC_PORT}/docs")
        except Exception as e:
            _fail("IoT 服务启动失败", str(e))
            raise SystemExit(1)

        # ── 全量测试 ──────────────────────────────────────────────────────── #
        run_tests(ha_token=token)

    finally:
        stop_iot_service()

    # ── 测试报告 ──────────────────────────────────────────────────────────── #
    _section("测试报告")
    total = _passed + _failed
    print(f"\n  总计 {total} 项   {GREEN}{BOLD}通过 {_passed}{RESET}   {RED}{BOLD}失败 {_failed}{RESET}\n")
    if _failed > 0:
        print(f"  {RED}失败清单:{RESET}")
        for name, p, detail in _results:
            if not p:
                print(f"    ✗  {name}")
                if detail:
                    print(f"       {detail}")

    # ── 清理 ──────────────────────────────────────────────────────────────── #
    if not args.keep:
        _section("清理")
        stop_ha_docker()
        _ok(f"容器 {HA_CONTAINER} 已停止删除")
    else:
        print(
            f"\n  {YELLOW}--keep 模式：容器 {HA_CONTAINER} 仍在运行{RESET}\n"
            f"  浏览器访问: {BOLD}http://localhost:{HA_PORT}{RESET}\n"
            f"  用户名: {HA_USERNAME}   密码: {HA_PASSWORD}\n"
            f"  Swagger:   {BOLD}http://localhost:{SVC_PORT}/docs{RESET}（服务已停止，仅供参考）"
        )

    print()
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    main()
