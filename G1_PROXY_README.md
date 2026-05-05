# G1 Robot HTTP Proxy

这些文件用于部署到 G1 本机的 `/home/unitree/mydemo/`，让 PC 主控通过 HTTP 调用 G1 本机代理，再由代理在 G1 本机通过 `eth0` 调用 `unitree_sdk2_python` / DDS。

目标目录：

```text
/home/unitree/mydemo/
├── g1_robot_proxy.py
├── start_robot_proxy.sh
├── test_g1_robot_proxy_local.py
├── logs/
└── unitree_sdk2_python/
```

`unitree_sdk2_python` 应放在：

```text
/home/unitree/mydemo/unitree_sdk2_python/
```

## 启动

在 G1 本机执行：

```bash
cd /home/unitree/mydemo
chmod +x start_robot_proxy.sh
./start_robot_proxy.sh
```

服务监听：

```text
0.0.0.0:9001
```

不需要在脚本里写死 G1 的 `wlan0` IP。PC 端配置里填写 G1 当前 `wlan0` IP。

## PC 端配置

PC 主控 `configs/system_config.yaml`：

```yaml
mode: "real"

robot:
  control_backend: "http_proxy"
  proxy_base_url: "http://<G1当前wlan0 IP>:9001"
  proxy_timeout_s: 3
```

在 G1 上查看 `wlan0` IP：

```bash
ip addr show wlan0
```

## G1 eth0 / DDS 检查

```bash
ip addr show eth0
ip route get 192.168.123.161
ip route get 239.255.0.1
```

`start_robot_proxy.sh` 会做这些网络准备：

- `cd /home/unitree/mydemo`
- 杀掉旧的 `g1_robot_proxy.py`
- `unset CYCLONEDDS_URI`
- `unset CYCLONEDDS_HOME`
- `sudo ip link set eth0 up`
- 如果缺少 `192.168.123.162/24`，则添加到 `eth0`
- `sudo ip route replace 239.255.0.0/16 dev eth0`
- 打印 `eth0` 地址和 DDS 路由检查
- `ping -c 1 -W 1 192.168.123.161`，失败只警告，不强制退出
- 启动 `python3 g1_robot_proxy.py`

## 本地测试

在 G1 本机或任意本地环境测试 HTTP 接口：

```bash
python3 test_g1_robot_proxy_local.py
```

默认只请求 `/health` 和 `/api/robot/status`，不会执行动作。需要单独测试一个手臂动作时显式传参：

```bash
python3 test_g1_robot_proxy_local.py http://<G1_IP>:9001 --action heart
```

如果代理不在本机：

```bash
python3 test_g1_robot_proxy_local.py http://<G1_IP>:9001
```

从 PC 测试：

```bash
curl http://<G1_IP>:9001/health
curl -X POST http://<G1_IP>:9001/api/robot/stop -H "Content-Type: application/json" -d '{}'
```

## HTTP 接口

- `GET /health`
- `GET /api/robot/status`
- `POST /api/robot/action`
- `POST /api/robot/loco`
- `POST /api/robot/stop`
- `POST /api/robot/speak`

所有响应统一为：

```json
{"ok": true, "data": {}}
```

或：

```json
{"ok": false, "error": "..."}
```

## 安全说明

初版只开放安全动作和 `safe_stop`。

开放的手臂动作白名单：

- `release_arm`: 99
- `two_hand_kiss`: 11
- `left_kiss`: 12
- `right_kiss`: 13
- `hands_up`: 15
- `clap`: 17
- `high_five`: 18
- `hug`: 19
- `heart`: 20
- `right_heart`: 21
- `reject`: 22
- `right_hand_up`: 23
- `x_ray`: 24
- `wave_face`: 25
- `wave_hand`: 26
- `shake_hand`: 27

手臂动作别名：

- `greet` -> `wave_hand`
- `say_hello` -> `wave_hand`
- `goodbye` -> `wave_face`
- `blow_kiss` -> `two_hand_kiss`

推荐优先测试幅度较小、适合作为业务反馈的动作：`heart`、`wave_face`、`x_ray`、`clap`。`hug`、`high_five` 幅度和交互距离更敏感，应确认周围安全后再单独测试。

`/api/robot/loco` 初版只开放：

- `stop` -> `LocoClient.StopMove()`
- `stand` / `high_stand` -> `LocoClient.HighStand()`

移动路线还没有开放，`move` / `navigate` / `turn` 等移动类动作仍会返回失败：

```text
loco move disabled in safe mode
```

危险或未验证动作仍然没有开放：

- 连续行走
- 高速移动
- 转向 / 复杂轨迹
- 跳舞
- `Damp`
- 趴下 / 坐下 / 低站姿
- 断电 / 零力矩

`/api/robot/stop` 是 `safe_stop`，不是硬急停。它会尽量停止移动、停止音频播放、释放手臂；不会默认调用 `Damp`、趴下、断电、零力矩等危险动作。
 