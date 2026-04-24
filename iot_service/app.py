"""IoT Service —— 家电联动小服务。

对外接口：
    GET  /iot/devices                列出 devices.yaml 中所有已配置设备
    POST /iot/device/on              通用开：按逻辑名开任意已配置设备
    POST /iot/device/off             通用关：按逻辑名关任意已配置设备
    POST /iot/device/set             通用属性设置：任意 HA 服务 + 任意属性键值对
    GET  /iot/light/on
    POST /iot/light/on
    POST /iot/light/off
    POST /iot/socket/on
    POST /iot/socket/off
    GET  /iot/status
    POST /iot/scene/night_mode
    POST /iot/scene/fall_alert
    POST /iot/scene/medicine_mode
    POST /iot/scene/{name}           自定义场景（在 config.yaml scenes 块中定义）

Mock 模式额外接口（config.yaml 中 mock: true 时开放）：
    GET    /mock/devices
    POST   /mock/devices         新增或覆盖一个模拟设备
    PATCH  /mock/devices/{eid}   修改状态 / 属性
    DELETE /mock/devices/{eid}
    POST   /mock/reset

OpenAPI 文档：
    Swagger UI -> /docs
    ReDoc      -> /redoc
    JSON       -> /openapi.json
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import load_config, load_devices
from ha_client import HAClient, HAError, MockHAClient


# --------------------------------------------------------------------------- #
# 初始化
# --------------------------------------------------------------------------- #
CONFIG = load_config()
DEVICES: Dict[str, str] = load_devices()  # logical_name -> entity_id

logging.basicConfig(
    level=getattr(logging, str(CONFIG.get("log_level", "INFO")).upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("iot.app")

USE_MOCK = bool(CONFIG.get("mock", True))
if USE_MOCK:
    ha = MockHAClient(preset_entities=list(DEVICES.values()))
    log.warning("运行在 MOCK 模式，使用内置模拟后端")
else:
    ha = HAClient(
        base_url=CONFIG["ha_url"],
        token=CONFIG["token"],
        timeout=float(CONFIG.get("timeout", 5)),
    )
    log.info("运行在真实 HA 模式，目标 %s", CONFIG["ha_url"])

app = FastAPI(
    title="IoT Service",
    description="家电联动小服务：封装 Home Assistant，提供单设备控制 + 场景联动。",
    version="1.0.0",
)


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
class DeviceRequest(BaseModel):
    name: str = Field(
        ...,
        description="设备逻辑名（devices.yaml 中的 key）或直接的 HA entity_id。",
        examples=["living_room_light", "switch.alarm_socket"],
    )


class LightOnRequest(DeviceRequest):
    brightness: Optional[int] = Field(
        None, ge=0, le=255, description="亮度 0-255（可选）"
    )
    rgb_color: Optional[List[int]] = Field(
        None, description="RGB，例如 [255,0,0]（可选，仅支持彩色灯）"
    )


class DeviceSetRequest(BaseModel):
    name: str = Field(
        ...,
        description="设备逻辑名（devices.yaml 中的 key）或直接的 HA entity_id。",
        examples=["living_room_light"],
    )
    service: str = Field(
        "turn_on",
        description="HA 服务名，同 domain 下的 service，如 turn_on / turn_off / toggle。",
        examples=["turn_on", "turn_off", "toggle"],
    )
    attributes: Optional[Dict[str, Any]] = Field(
        None,
        description="任意属性键值对，直接透传给 HA。支持所有 HA 原生属性，如 brightness、color_temp、effect、transition 等。",
        examples=[{"brightness": 200, "color_temp": 3000, "transition": 2}],
    )


class ApiResponse(BaseModel):
    ok: bool
    code: int
    action: str
    entity_id: str
    state: str
    message: str
    extra: Optional[Dict[str, Any]] = None


class MockDeviceBody(BaseModel):
    entity_id: str = Field(..., examples=["light.demo"])
    state: str = Field("off", examples=["on", "off"])
    attributes: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _resolve_entity(name: str) -> str:
    """把逻辑名解析为 entity_id，若本身就是 entity_id 则原样返回。"""
    if name in DEVICES:
        return DEVICES[name]
    if "." in name:  # 看起来是 entity_id
        return name
    raise HAError(f"未知设备: {name}", code=1010)


def _resp(
    ok: bool,
    action: str,
    entity_id: str,
    state: str = "unknown",
    code: int = 0,
    message: str = "success",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "code": code,
        "action": action,
        "entity_id": entity_id,
        "state": state,
        "message": message,
        "extra": extra,
    }


def _do(
    action: str,
    entity_id: str,
    func,
    *args,
    **kwargs,
) -> Dict[str, Any]:
    """包裹一次控制调用，做：日志 + 异常 + 控制后回读状态。"""
    log.info("请求 action=%s entity=%s args=%s kwargs=%s", action, entity_id, args, kwargs)
    try:
        result = func(entity_id, *args, **kwargs)
        # 闭环：再读一次真实状态
        try:
            verified = ha.get_state(entity_id)
            state = verified.get("state", "unknown")
        except HAError:
            state = result.get("state", "unknown") if isinstance(result, dict) else "unknown"
        log.info("完成 action=%s entity=%s state=%s", action, entity_id, state)
        return _resp(True, action, entity_id, state=state, extra=result if isinstance(result, dict) else None)
    except HAError as e:
        log.error("失败 action=%s entity=%s err=%s", action, entity_id, e)
        return _resp(False, action, entity_id, code=e.code, message=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("异常 action=%s entity=%s", action, entity_id)
        return _resp(False, action, entity_id, code=1999, message=f"internal error: {e}")


# --------------------------------------------------------------------------- #
# 单设备接口
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 通用设备接口
# --------------------------------------------------------------------------- #
@app.get("/iot/devices", tags=["device"])
def list_devices():
    """列出 devices.yaml 中所有已配置的设备及当前状态。"""
    items = []
    for logical, eid in DEVICES.items():
        try:
            st = ha.get_state(eid)
            items.append({
                "name": logical,
                "entity_id": eid,
                "state": st.get("state", "unknown"),
                "attributes": st.get("attributes", {}),
            })
        except HAError as e:
            items.append({"name": logical, "entity_id": eid, "state": "unknown", "error": str(e)})
    return {"ok": True, "code": 0, "count": len(items), "devices": items}


@app.post("/iot/device/on", response_model=ApiResponse, tags=["device"])
def device_on(req: LightOnRequest):
    """通用开：按逻辑名（或 entity_id）打开 devices.yaml 中任意已配置设备，可选亮度 / RGB。"""
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "device_on", req.name, code=e.code, message=str(e)))
    return _do(
        "device_on",
        eid,
        ha.turn_on,
        brightness=req.brightness,
        rgb_color=req.rgb_color,
    )


@app.post("/iot/device/off", response_model=ApiResponse, tags=["device"])
def device_off(req: DeviceRequest):
    """通用关：按逻辑名（或 entity_id）关闭 devices.yaml 中任意已配置设备。"""
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "device_off", req.name, code=e.code, message=str(e)))
    return _do("device_off", eid, ha.turn_off)


@app.post("/iot/device/set", response_model=ApiResponse, tags=["device"])
def device_set(req: DeviceSetRequest):
    """通用属性设置：对任意已配置设备调用指定 HA 服务，并透传任意属性键值对。

    示例（设置色温 + 渐变）：
    ```json
    {"name": "living_room_light", "service": "turn_on",
     "attributes": {"brightness": 180, "color_temp": 3000, "transition": 2}}
    ```
    """
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "device_set", req.name, code=e.code, message=str(e)))

    attrs = req.attributes or {}
    svc = req.service.lower()
    if svc in ("turn_on", "toggle"):
        fn = ha.turn_on
    elif svc == "turn_off":
        fn = ha.turn_off
    else:
        return JSONResponse(_resp(False, "device_set", eid, code=1011,
                                  message=f"不支持的 service: {svc}，可用: turn_on / turn_off / toggle"))
    return _do("device_set", eid, fn, **attrs)


@app.post("/iot/light/on", response_model=ApiResponse, tags=["device"])
def light_on(req: LightOnRequest):
    """打开灯，可选亮度 / RGB。"""
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "light_on", req.name, code=e.code, message=str(e)))
    return _do(
        "light_on",
        eid,
        ha.turn_on,
        brightness=req.brightness,
        rgb_color=req.rgb_color,
    )


@app.post("/iot/light/off", response_model=ApiResponse, tags=["device"])
def light_off(req: DeviceRequest):
    """关闭灯。"""
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "light_off", req.name, code=e.code, message=str(e)))
    return _do("light_off", eid, ha.turn_off)


@app.post("/iot/socket/on", response_model=ApiResponse, tags=["device"])
def socket_on(req: DeviceRequest):
    """打开插座。"""
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "socket_on", req.name, code=e.code, message=str(e)))
    return _do("socket_on", eid, ha.turn_on)


@app.post("/iot/socket/off", response_model=ApiResponse, tags=["device"])
def socket_off(req: DeviceRequest):
    """关闭插座。"""
    try:
        eid = _resolve_entity(req.name)
    except HAError as e:
        return JSONResponse(_resp(False, "socket_off", req.name, code=e.code, message=str(e)))
    return _do("socket_off", eid, ha.turn_off)


@app.get("/iot/status", tags=["device"])
def iot_status(
    name: Optional[str] = Query(
        None,
        description="设备逻辑名或 entity_id；不传则返回所有已配置设备状态。",
    ),
):
    """查询设备状态。"""
    if name:
        try:
            eid = _resolve_entity(name)
            st = ha.get_state(eid)
            return _resp(True, "status", eid, state=st.get("state", "unknown"), extra=st)
        except HAError as e:
            return _resp(False, "status", name, code=e.code, message=str(e))

    # 返回所有配置中的设备
    items = []
    for logical, eid in DEVICES.items():
        try:
            st = ha.get_state(eid)
            items.append(
                {
                    "name": logical,
                    "entity_id": eid,
                    "state": st.get("state", "unknown"),
                    "attributes": st.get("attributes", {}),
                }
            )
        except HAError as e:
            items.append(
                {
                    "name": logical,
                    "entity_id": eid,
                    "state": "unknown",
                    "error": str(e),
                }
            )
    return {"ok": True, "code": 0, "action": "status_all", "devices": items}


# --------------------------------------------------------------------------- #
# 场景接口
# --------------------------------------------------------------------------- #
def _safe_turn_on(logical: str, **attrs) -> Dict[str, Any]:
    """场景内部调用：设备缺失时不炸，记录到步骤里。
    若带属性的 turn_on 返回 400（设备不支持该属性），自动回退到无属性 turn_on。
    """
    if logical not in DEVICES:
        return {"name": logical, "ok": False, "message": "device_not_configured"}
    eid = DEVICES[logical]
    clean_attrs = {k: v for k, v in attrs.items() if v is not None}
    try:
        ha.turn_on(eid, **clean_attrs)
        st = ha.get_state(eid)
        return {"name": logical, "entity_id": eid, "ok": True, "state": st.get("state")}
    except HAError as e:
        # HA 400 通常是设备不支持 brightness/rgb 等属性 → 回退纯开关
        if e.code == 1007 and clean_attrs:
            log.warning("设备 %s 不支持属性 %s，回退到基础 turn_on", eid, list(clean_attrs))
            try:
                ha.turn_on(eid)
                st = ha.get_state(eid)
                return {"name": logical, "entity_id": eid, "ok": True, "state": st.get("state")}
            except HAError as e2:
                return {"name": logical, "entity_id": eid, "ok": False, "message": str(e2)}
        return {"name": logical, "entity_id": eid, "ok": False, "message": str(e)}


def _safe_turn_off(logical: str) -> Dict[str, Any]:
    if logical not in DEVICES:
        return {"name": logical, "ok": False, "message": "device_not_configured"}
    eid = DEVICES[logical]
    try:
        ha.turn_off(eid)
        st = ha.get_state(eid)
        return {"name": logical, "entity_id": eid, "ok": True, "state": st.get("state")}
    except HAError as e:
        return {"name": logical, "entity_id": eid, "ok": False, "message": str(e)}


@app.post("/iot/scene/night_mode", tags=["scene"])
def scene_night_mode():
    """夜间起夜辅助：主灯 + 路径灯带 + 小夜灯插座。"""
    log.info("场景: night_mode")
    steps = [
        _safe_turn_on("living_room_light", brightness=120),
        _safe_turn_on("path_strip", brightness=180),
        _safe_turn_on("night_light_socket"),
    ]
    ok = all(s.get("ok") for s in steps)
    return {
        "ok": ok,
        "code": 0 if ok else 2001,
        "action": "scene_night_mode",
        "message": "success" if ok else "partial_failure",
        "steps": steps,
    }


@app.post("/iot/scene/fall_alert", tags=["scene"])
def scene_fall_alert():
    """跌倒报警：灯带变红 + 报警插座。"""
    log.info("场景: fall_alert")
    steps = [
        _safe_turn_on("path_strip", rgb_color=[255, 0, 0], brightness=255),
        _safe_turn_on("alarm_socket"),
    ]
    ok = all(s.get("ok") for s in steps)
    return {
        "ok": ok,
        "code": 0 if ok else 2002,
        "action": "scene_fall_alert",
        "message": "success" if ok else "partial_failure",
        "steps": steps,
    }


@app.post("/iot/scene/medicine_mode", tags=["scene"])
def scene_medicine_mode():
    """用药提醒：主灯 + 药盒提示灯。"""
    log.info("场景: medicine_mode")
    steps = [
        _safe_turn_on("living_room_light", brightness=200),
        _safe_turn_on("medicine_light", brightness=255),
    ]
    ok = all(s.get("ok") for s in steps)
    return {
        "ok": ok,
        "code": 0 if ok else 2003,
        "action": "scene_medicine_mode",
        "message": "success" if ok else "partial_failure",
        "steps": steps,
    }


# --------------------------------------------------------------------------- #
# 自定义场景（从 config.yaml scenes 块动态注册）
# --------------------------------------------------------------------------- #
def _run_custom_scene(scene_name: str, steps_cfg: List[Dict[str, Any]]) -> Dict[str, Any]:
    """执行一个自定义场景的所有步骤。"""
    log.info("场景: %s (自定义)", scene_name)
    steps = []
    for step in steps_cfg:
        device = step.get("device", "")
        action = str(step.get("action", "on")).lower()
        attrs = {k: v for k, v in step.items() if k not in ("device", "action")}
        if action in ("on", "toggle"):
            steps.append(_safe_turn_on(device, **attrs))
        elif action == "off":
            steps.append(_safe_turn_off(device))
        else:
            steps.append({"name": device, "ok": False, "message": f"未知 action: {action}"})
    ok = all(s.get("ok") for s in steps)
    return {
        "ok": ok,
        "code": 0 if ok else 2100,
        "action": f"scene_{scene_name}",
        "message": "success" if ok else "partial_failure",
        "steps": steps,
    }


_BUILTIN_SCENES = {"night_mode", "fall_alert", "medicine_mode"}
_CUSTOM_SCENES: Dict[str, Dict[str, Any]] = CONFIG.get("scenes") or {}

for _sname, _scfg in _CUSTOM_SCENES.items():
    if _sname in _BUILTIN_SCENES:
        log.warning("自定义场景 '%s' 与内置场景同名，已跳过（内置优先）", _sname)
        continue
    _steps_cfg: List[Dict[str, Any]] = _scfg.get("steps", [])
    _desc: str = _scfg.get("description", f"自定义场景: {_sname}")

    def _make_scene_handler(name: str, steps: List[Dict[str, Any]]):
        def _handler():
            return _run_custom_scene(name, steps)
        _handler.__name__ = f"scene_{name}"
        _handler.__doc__ = _desc
        return _handler

    app.post(
        f"/iot/scene/{_sname}",
        tags=["scene"],
        summary=_desc,
    )(_make_scene_handler(_sname, _steps_cfg))
    log.info("已注册自定义场景: POST /iot/scene/%s", _sname)


# --------------------------------------------------------------------------- #
# Mock 管理接口（仅 mock 模式开放）
# --------------------------------------------------------------------------- #
if USE_MOCK:
    assert isinstance(ha, MockHAClient)
    mock_ha: MockHAClient = ha  # type: ignore[assignment]

    @app.get("/mock/devices", tags=["mock"])
    def mock_list():
        """列出所有模拟设备及状态。"""
        return {"ok": True, "devices": mock_ha.list_states()}

    @app.post("/mock/devices", tags=["mock"])
    def mock_upsert(body: MockDeviceBody):
        """新增或覆盖一个模拟设备的状态。"""
        snap = mock_ha.set_state(body.entity_id, body.state, body.attributes)
        return {"ok": True, "device": snap}

    @app.patch("/mock/devices/{entity_id}", tags=["mock"])
    def mock_patch(entity_id: str, body: MockDeviceBody):
        """修改模拟设备（entity_id 以路径为准）。"""
        snap = mock_ha.set_state(entity_id, body.state, body.attributes)
        return {"ok": True, "device": snap}

    @app.delete("/mock/devices/{entity_id}", tags=["mock"])
    def mock_delete(entity_id: str):
        """删除一个模拟设备。"""
        removed = mock_ha.remove_entity(entity_id)
        if not removed:
            raise HTTPException(status_code=404, detail="device not found")
        return {"ok": True, "removed": entity_id}

    @app.post("/mock/reset", tags=["mock"])
    def mock_reset():
        """重置所有模拟设备为 off。"""
        mock_ha.reset()
        return {"ok": True, "message": "all devices reset to off"}


# --------------------------------------------------------------------------- #
# 健康检查
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"])
def health():
    return {
        "ok": True,
        "mock": USE_MOCK,
        "devices": DEVICES,
        "time": time.time(),
    }


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=str(CONFIG.get("server_host", "0.0.0.0")),
        port=int(CONFIG.get("server_port", 5000)),
        log_level=str(CONFIG.get("log_level", "INFO")).lower(),
    )


if __name__ == "__main__":
    main()
