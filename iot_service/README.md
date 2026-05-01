# IoT Service（家电联动子系统）

比赛项目中的 **IoT 模块**。封装 Home Assistant，向主控程序提供统一、稳定的 HTTP 接口，支持：

- 通用设备控制：按逻辑名开 / 关 / 任意属性设置任意已配置设备
- 专用接口：灯（含亮度 / RGB）、插座
- 内置三个场景：夜间起夜 / 跌倒报警 / 用药提醒
- **自定义场景**：在 `config.yaml` 里添加即可，服务启动时自动注册接口
- 控制后回读状态（闭环验证）
- 内置 **Mock 后端**，无需真实 Home Assistant 也能联调
- **OpenAPI** 自动文档：Swagger UI / ReDoc

---

## 一、目录结构

```
iot_service/
├── app.py                  # FastAPI 主入口，所有接口
├── ha_client.py            # HAClient（真实 HA）+ MockHAClient（模拟）
├── config.py               # 读取 yaml 配置
├── config.yaml             # 服务运行配置（含自定义场景）
├── config.yaml.example     # 配置模板
├── devices.yaml            # 设备逻辑名 -> HA entity_id 映射
├── devices.yaml.example    # 设备映射模板
├── test_integration.py     # 集成测试（自动拉起 Docker HA，41 个用例）
├── requirements.txt
└── README.md
```

---

## 二、快速开始

```bash
cd iot_service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 拷贝配置（首次）
cp config.yaml.example  config.yaml
cp devices.yaml.example devices.yaml

# 启动（macOS 端口 5000 被 AirPlay 占用，推荐用 5001）
uvicorn app:app --host 0.0.0.0 --port 5001
```

服务默认监听 `http://0.0.0.0:5001`（由 `config.yaml` 的 `server_port` 决定）。

| 地址 | 说明 |
|------|------|
| `http://localhost:5001/docs` | Swagger UI（可直接在线调用） |
| `http://localhost:5001/redoc` | ReDoc 文档 |
| `http://localhost:5001/openapi.json` | OpenAPI JSON |
| `http://localhost:5001/health` | 健康检查 |

> `config.yaml` 里 `mock: true` 时走内置模拟后端，零依赖，可直接联调。  
> 切到真实环境：`mock: false` + 填好 `ha_url` 和 `token`，代码无需改动。

---

## 三、配置文件

### config.yaml

```yaml
ha_url: "http://localhost:8123"
token: "YOUR_LONG_LIVED_TOKEN"   # HA 长期令牌

server_host: "0.0.0.0"
server_port: 5001
timeout: 5

mock: false     # true = 模拟后端；false = 真实 HA
log_level: "INFO"

# 自定义场景（可选，见第六节）
scenes:
  morning_mode:
    description: "早安模式"
    steps:
      - device: living_room_light
        action: on
        brightness: 255
        color_temp: 3000
        transition: 2
```

| 字段 | 说明 |
|------|------|
| `ha_url` | Home Assistant 地址 |
| `token` | HA Long-Lived Access Token |
| `server_host` / `server_port` | 本服务监听地址 |
| `timeout` | 请求 HA 超时（秒） |
| `mock` | `true` 模拟后端；`false` 真实 HA |
| `log_level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `scenes` | 自定义场景，见第六节 |

### devices.yaml

左边是**逻辑名**（接口调用时使用），右边是 HA 中的 **entity_id**：

```yaml
living_room_light: "light.living_room_main"
bedroom_light:     "light.bedroom_main"
path_strip:        "light.path_strip"
medicine_light:    "light.medicine_box_light"
alarm_socket:      "switch.alarm_socket"
night_light_socket:"switch.night_light_socket"
```

主控只认逻辑名；更换设备只需改此文件，接口层无需改动。

---

## 四、设备实体清单（默认）

| 逻辑名 | entity_id | 作用 |
|--------|-----------|------|
| `living_room_light` | `light.living_room_main` | 客厅主灯：夜间照明 / 用药提醒 |
| `bedroom_light` | `light.bedroom_main` | 卧室主灯 |
| `path_strip` | `light.path_strip` | 路径灯带：起夜引导 / 跌倒时变红 |
| `medicine_light` | `light.medicine_box_light` | 药盒提示灯 |
| `alarm_socket` | `switch.alarm_socket` | 报警器 / 蜂鸣器插座 |
| `night_light_socket` | `switch.night_light_socket` | 小夜灯插座 |

---

## 五、接口说明

### 统一返回格式

```json
{
  "ok": true,
  "code": 0,
  "action": "device_on",
  "entity_id": "light.living_room_main",
  "state": "on",
  "message": "success",
  "extra": {}
}
```

**错误码**：

| code | 含义 |
|------|------|
| 0 | 成功 |
| 1001 | HA 通用请求错误 |
| 1002 | 请求超时 |
| 1003 | 无法连接 HA |
| 1005 | Token 无效 |
| 1006 | 设备 / 资源不存在 |
| 1010 | 未知逻辑设备名 |
| 1011 | 不支持的 service 名称 |
| 1999 | 内部未知异常 |
| 2001–2003 | 内置场景部分失败 |
| 2100 | 自定义场景部分失败 |

---

### 5.1 通用设备接口

| Method | Path | 说明 |
|--------|------|------|
| GET | `/iot/devices` | 列出 devices.yaml 中所有设备及实时状态 |
| POST | `/iot/device/on` | 按逻辑名开任意设备（可选 brightness / rgb_color） |
| POST | `/iot/device/off` | 按逻辑名关任意设备 |
| POST | `/iot/device/set` | 任意服务 + 任意属性（最灵活，见下） |
| GET | `/iot/status?name=xxx` | 查询单个或全部设备状态 |

#### `/iot/device/set` — 任意属性设置

发送任意 HA 原生属性（`brightness`、`color_temp`、`rgb_color`、`effect`、`transition` 等）：

```json
{
  "name": "living_room_light",
  "service": "turn_on",
  "attributes": {
    "brightness": 180,
    "color_temp": 3000,
    "transition": 2
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 逻辑名或 entity_id |
| `service` | string | `turn_on` / `turn_off` / `toggle`（默认 `turn_on`） |
| `attributes` | object | 任意 HA 属性键值对，透传给 HA |

---

### 5.2 专用设备接口

| Method | Path | Body 示例 | 说明 |
|--------|------|-----------|------|
| POST | `/iot/light/on` | `{"name":"living_room_light","brightness":120,"rgb_color":[255,0,0]}` | 开灯 |
| POST | `/iot/light/off` | `{"name":"living_room_light"}` | 关灯 |
| POST | `/iot/socket/on` | `{"name":"alarm_socket"}` | 开插座 |
| POST | `/iot/socket/off` | `{"name":"alarm_socket"}` | 关插座 |

`name` 支持**逻辑名**（推荐）或直接 **entity_id**。

---

### 5.3 内置场景接口

| Method | Path | 行为 |
|--------|------|------|
| POST | `/iot/scene/night_mode` | 主灯（低亮）+ 路径灯带 + 小夜灯插座 |
| POST | `/iot/scene/fall_alert` | 灯带切红色满亮 + 报警器插座 |
| POST | `/iot/scene/medicine_mode` | 主灯（中亮）+ 药盒提示灯 |

返回示例：

```json
{
  "ok": true,
  "code": 0,
  "action": "scene_night_mode",
  "message": "success",
  "steps": [
    {"name":"living_room_light","entity_id":"light.living_room_main","ok":true,"state":"on"},
    {"name":"path_strip","entity_id":"light.path_strip","ok":true,"state":"on"},
    {"name":"night_light_socket","entity_id":"switch.night_light_socket","ok":true,"state":"on"}
  ]
}
```

---

### 5.4 Mock 管理接口（`mock: true` 时开放）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/mock/devices` | 列出所有模拟设备 |
| POST | `/mock/devices` | 新增 / 覆盖设备，body: `{"entity_id":"light.x","state":"off","attributes":{}}` |
| PATCH | `/mock/devices/{entity_id}` | 修改状态或属性 |
| DELETE | `/mock/devices/{entity_id}` | 删除 |
| POST | `/mock/reset` | 全部设备重置为 `off` |

---

## 六、自定义场景

在 `config.yaml` 的 `scenes:` 块中添加场景，**服务启动时自动注册为 `POST /iot/scene/{name}`**，Swagger UI 里立即可见。

```yaml
scenes:
  morning_mode:
    description: "早安模式：全亮暖白光"
    steps:
      - device: living_room_light   # devices.yaml 逻辑名或 entity_id
        action: on                  # on / off / toggle
        brightness: 255
        color_temp: 3000
        transition: 2
      - device: bedroom_light
        action: on
        brightness: 200

  good_night:
    description: "晚安模式：关主灯，留小夜灯"
    steps:
      - device: living_room_light
        action: off
      - device: bedroom_light
        action: off
      - device: night_light_socket
        action: on
```

**步骤字段说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `device` | ✅ | 逻辑名或 entity_id |
| `action` | ✅ | `on` / `off` / `toggle` |
| 其余字段 | — | 任意 HA 属性，透传给 HA（如 `brightness`、`color_temp`、`rgb_color`、`transition`、`effect`） |

> **限制**：`night_mode`、`fall_alert`、`medicine_mode` 为内置保留名，自定义场景同名时以内置为准并在日志中警告。

---

## 七、切换到真实 Home Assistant

1. 获取 HA Long-Lived Token：HA Web UI → 个人资料 → 底部「长期访问令牌」→ 创建令牌
2. 修改 `config.yaml`：
   ```yaml
   mock: false
   ha_url: "http://<HA机器IP>:8123"
   token: "<你的长期令牌>"
   ```
3. 确认 `devices.yaml` 中 entity_id 与 HA 一致（HA → 开发者工具 → 状态 可查全部 entity_id）
4. 重启服务，接口行为完全一致，主控无需改动

---

## 八、集成测试

自动拉起 Docker HA、完成初始化、跑完 41 个用例：

```bash
# 首次（拉取 HA 镜像约 2.5 GB）
.venv/bin/python test_integration.py --keep

# 镜像已存在，跳过拉取
.venv/bin/python test_integration.py --keep --no-pull
```

- `--keep`：测试结束后保留 HA 容器，方便手动探查
- `--no-pull`：跳过 `docker pull`

测试覆盖：设备状态查询、灯 / 插座开关（HA 闭环）、三个内置场景、Mock 隔离、错误处理、OpenAPI 文档可达性。

---

## 九、主控调用示例

```python
import requests
BASE = "http://<IoT服务所在机器>:5001"

# 内置场景
requests.post(f"{BASE}/iot/scene/night_mode").json()

# 通用开灯（逻辑名）
requests.post(f"{BASE}/iot/device/on", json={"name": "living_room_light", "brightness": 200}).json()

# 通用属性设置（色温渐变）
requests.post(f"{BASE}/iot/device/set", json={
    "name": "living_room_light",
    "service": "turn_on",
    "attributes": {"brightness": 180, "color_temp": 3000, "transition": 2}
}).json()

# 查询所有设备状态
requests.get(f"{BASE}/iot/devices").json()

# 查询单个设备
requests.get(f"{BASE}/iot/status", params={"name": "path_strip"}).json()

# 自定义场景（在 config.yaml 中已定义 morning_mode）
requests.post(f"{BASE}/iot/scene/morning_mode").json()
```

---

## 十、日志

- 每个请求记录：`action / entity / 参数 / 结果 / 最终状态`
- `log_level: DEBUG` 可看到更详细的 HTTP 信息
- 场景步骤失败时整体 `ok: false`，`steps` 里每一步单独说明成功 / 失败原因


---

## 一、目录结构

```
iot_service/
├── app.py                  # FastAPI 主入口，所有接口都在这里
├── ha_client.py            # HAClient（真实）+ MockHAClient（模拟）
├── config.py               # 读取 yaml 配置
├── config.yaml.example     # 服务 & HA 配置模板
├── devices.yaml.example    # 设备逻辑名 -> HA entity_id 映射
├── requirements.txt
└── README.md
```

---

## 二、快速开始

```bash
cd iot_service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 拷贝配置
cp config.yaml.example  config.yaml
cp devices.yaml.example devices.yaml

# 启动
python app.py
```

服务默认监听 `http://0.0.0.0:5000`。

- Swagger UI：`http://localhost:5000/docs`
- ReDoc：`http://localhost:5000/redoc`
- OpenAPI JSON：`http://localhost:5000/openapi.json`
- 健康检查：`GET /health`

> `config.yaml` 里 `mock: true` 时走内置模拟后端，零依赖、可以直接开始联调。
> 切到真实环境时把 `mock` 改为 `false`、填好 `ha_url` 和 `token` 即可，代码无需改动。

---

## 三、配置文件

### config.yaml

| 字段 | 说明 |
|------|------|
| `ha_url` | Home Assistant 地址，如 `http://localhost:8123` |
| `token` | HA Long-Lived Access Token |
| `server_host` / `server_port` | 本服务监听地址 |
| `timeout` | 请求 HA 的超时（秒） |
| `mock` | `true` 启用模拟后端；`false` 走真实 HA |
| `log_level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

### devices.yaml

左边是 **逻辑名**（主控调用时使用的名字），右边是 HA 中的 **entity_id**：

```yaml
living_room_light: "light.living_room_main"
bedroom_light:     "light.bedroom_main"
path_strip:        "light.path_strip"
medicine_light:    "light.medicine_box_light"
alarm_socket:      "switch.alarm_socket"
night_light_socket:"switch.night_light_socket"
```

主控以后只认逻辑名，换设备只需要改此文件。

---

## 四、设备实体清单（默认）

| 逻辑名 | entity_id | 作用 |
|--------|-----------|------|
| `living_room_light` | `light.living_room_main` | 客厅主灯：夜间照明 / 用药提醒 |
| `bedroom_light` | `light.bedroom_main` | 卧室主灯 |
| `path_strip` | `light.path_strip` | 路径灯带：起夜引导 / 跌倒时变红 |
| `medicine_light` | `light.medicine_box_light` | 药盒提示灯 |
| `alarm_socket` | `switch.alarm_socket` | 报警器 / 蜂鸣器插座 |
| `night_light_socket` | `switch.night_light_socket` | 小夜灯插座 |

---

## 五、接口说明

所有接口统一返回（成功 / 失败格式一致）：

```json
{
  "ok": true,
  "code": 0,
  "action": "light_on",
  "entity_id": "light.living_room_main",
  "state": "on",
  "message": "success",
  "extra": { "attributes": { "brightness": 120 } }
}
```

错误码约定：

| code | 含义 |
|------|------|
| 0 | 成功 |
| 1001 | HA 通用请求错误 |
| 1002 | 请求超时 |
| 1003 | 无法连接 HA |
| 1005 | Token 无效 |
| 1006 | 设备 / 资源不存在 |
| 1010 | 未知逻辑设备名 |
| 1999 | 内部未知异常 |
| 2001-2003 | 对应三个场景的部分失败 |

### 1. 单设备

| Method | Path | Body | 说明 |
|--------|------|------|------|
| POST | `/iot/light/on` | `{ "name": "living_room_light", "brightness": 120, "rgb_color": [255,0,0] }` | 开灯（亮度 / 颜色可选） |
| POST | `/iot/light/off` | `{ "name": "living_room_light" }` | 关灯 |
| POST | `/iot/socket/on` | `{ "name": "alarm_socket" }` | 开插座 |
| POST | `/iot/socket/off` | `{ "name": "alarm_socket" }` | 关插座 |
| GET | `/iot/status?name=living_room_light` | — | 查询单个或全部设备 |

`name` 支持两种写法：**逻辑名**（推荐）或直接 **entity_id**。

### 2. 场景接口

| Method | Path | 行为 |
|--------|------|------|
| POST | `/iot/scene/night_mode` | 主灯 + 路径灯带 + 小夜灯插座 |
| POST | `/iot/scene/fall_alert` | 灯带切红色 + 报警器插座 |
| POST | `/iot/scene/medicine_mode` | 主灯 + 药盒提示灯 |

返回示例：

```json
{
  "ok": true,
  "code": 0,
  "action": "scene_night_mode",
  "message": "success",
  "steps": [
    {"name":"living_room_light","entity_id":"light.living_room_main","ok":true,"state":"on"},
    {"name":"path_strip","entity_id":"light.path_strip","ok":true,"state":"on"},
    {"name":"night_light_socket","entity_id":"switch.night_light_socket","ok":true,"state":"on"}
  ]
}
```

### 3. Mock 管理接口（`mock: true` 时开放）

方便联调：启动服务后可以通过接口增删模拟设备、观察状态变化。

| Method | Path | 说明 |
|--------|------|------|
| GET | `/mock/devices` | 列出所有模拟设备 |
| POST | `/mock/devices` | 新增 / 覆盖一个设备，body: `{"entity_id":"light.x","state":"off","attributes":{}}` |
| PATCH | `/mock/devices/{entity_id}` | 修改状态或属性 |
| DELETE | `/mock/devices/{entity_id}` | 删除 |
| POST | `/mock/reset` | 把全部设备状态重置为 `off` |

---

## 六、主控调用示例

```python
import requests
BASE = "http://<IoT服务所在机器>:5000"

# 夜间场景
requests.post(f"{BASE}/iot/scene/night_mode").json()

# 单独开客厅灯
requests.post(f"{BASE}/iot/light/on", json={"name": "living_room_light", "brightness": 200}).json()

# 查询状态
requests.get(f"{BASE}/iot/status", params={"name": "path_strip"}).json()
```

---

## 七、联调 / 测试（Mock 模式下可直接跑）

```bash
# 启动
python app.py

# 场景：夜间
curl -X POST http://localhost:5000/iot/scene/night_mode

# 场景：跌倒
curl -X POST http://localhost:5000/iot/scene/fall_alert

# 场景：用药
curl -X POST http://localhost:5000/iot/scene/medicine_mode

# 查全部状态
curl http://localhost:5000/iot/status

# Mock 模式下观察
curl http://localhost:5000/mock/devices
```

---

## 八、切换到真实 Home Assistant

1. 在 HA Web UI → 个人资料 → 底部「长期访问令牌」生成 token
2. 把 `config.yaml` 改为：

   ```yaml
   mock: false
   ha_url: "http://<HA机器IP>:8123"
   token: "<你的长期令牌>"
   ```

3. 确认 `devices.yaml` 中的 entity_id 与 HA 中一致（HA → 开发者工具 → 状态 可查看所有 entity_id）
4. 重启本服务。接口行为完全一致，主控无需做任何改动。

---

## Mock / Real Home Assistant 一键切换

### 1) Mock 模式如何配置

编辑 `iot_service/config.yaml`：

```yaml
ha_url: "http://队友电脑IP:8123"
token: "your_home_assistant_token_here"

server_host: "0.0.0.0"
server_port: 5001

mock: true
log_level: "INFO"
```

- `mock: true` 时服务会使用 `MockHAClient`，**不会访问真实 Home Assistant**。
- Mock 启动时会读取 `devices.yaml`，自动创建所有 entity_id 的模拟状态，默认 `off`。

`iot_service/devices.yaml` 继续使用 input_boolean 映射，例如：

```yaml
living_room_light: "input_boolean.living_room_light"
bedroom_light: "input_boolean.bedroom_light"
path_strip: "input_boolean.path_strip"
medicine_light: "input_boolean.medicine_light"
alarm_socket: "input_boolean.alarm_socket"
night_light_socket: "input_boolean.night_light_socket"
```

### 2) Real 模式如何配置

把 `iot_service/config.yaml` 的 `mock` 改为 `false`，并填真实 HA 地址和长期令牌：

```yaml
ha_url: "http://队友电脑IP:8123"
token: "your_home_assistant_token_here"
mock: false
```

### 3) 启动命令

```bash
cd iot_service
uvicorn app:app --host 0.0.0.0 --port 5001
```

### 4) 测试命令

```bash
python3 test_iot_modes.py
```

### 5) Real 模式需要注意

- `ha_url` 要改成队友电脑的 Home Assistant 地址
- `token` 要换成真实长期访问令牌
- `devices.yaml` 里的 entity_id 必须和队友 HA 里一致
- 两台电脑必须在同一局域网
- 防火墙不能拦截 8123 和 5001

---

## 九、日志

- 启动后在终端打印
- 每个请求记录：`action / entity / 参数 / 结果 / 最终状态`
- `log_level: DEBUG` 可看到更详细 HTTP 信息
