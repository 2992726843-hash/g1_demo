# 无 G1 真机 HTTP 代理链路测试指南

这套工具只验证 PC 端从 `G1HttpClient` / `ActionExecutor` 到 HTTP 请求这一段链路，不会 import `unitree_sdk2_python`，不会调用真实机器人 SDK。

## 1. 启动 Fake G1 Proxy

在第一个终端运行：

```bash
python3 tests/fake_g1_proxy_server.py
```

服务监听 `127.0.0.1:9001`，支持：

- `GET /health`
- `GET /api/robot/status`
- `POST /api/robot/action`
- `POST /api/robot/loco`
- `POST /api/robot/stop`
- `POST /api/robot/speak`

每次收到请求，终端会打印时间戳、method、path 和 JSON body。

## 2. 测试 G1HttpClient

在第二个终端运行：

```bash
python3 tests/test_g1_http_client_manual.py
```

预期 fake server 收到：

```text
GET /health
POST /api/robot/action {"action_id": 26, "action_name": "wave_hand"}
POST /api/robot/action {"action_id": 15, "action_name": "hands_up"}
POST /api/robot/loco {"action": "move", "target": "forward"}
POST /api/robot/stop {}
GET /api/robot/status
```

如果 fake server 没启动，脚本不会崩溃，返回值里应看到 `ok=false` 和连接失败信息。

## 3. 测试 ActionExecutor HTTP Proxy

继续保持 fake server 运行，然后在第二个终端运行：

```bash
python3 tests/test_action_executor_http_proxy_manual.py
```

该脚本会在进程内构造最小配置：

```yaml
mode: "real"
robot:
  control_backend: "http_proxy"
  proxy_base_url: "http://127.0.0.1:9001"
  proxy_timeout_s: 3
```

预期 fake server 收到：

```text
POST /api/robot/action {"action_id": 99, "action_name": "release_arm"}
POST /api/robot/action {"action_id": 26, "action_name": "wave_hand"}
POST /api/robot/action {"action_id": 99, "action_name": "release_arm"}
POST /api/robot/action {"action_id": 15, "action_name": "hands_up"}
POST /api/robot/stop {}
```

这一步用于确认 `ActionExecutor` 在 `real + http_proxy` 下不会走 `hardware.real_g1`，而是走 `G1HttpClient`。

## 4. 测试停止 / 跌倒 / 复位优先级

继续保持 fake server 运行，然后运行：

```bash
python3 tests/test_stop_reset_emergency_manual.py
```

该脚本会在进程内构造 `real + http_proxy` 最小配置，并直接调用 `main.py` 中的三个入口：

- `user_stop`：`_handle_high_priority_interrupt("停止")`
- `fall_alert`：`_handle_emergency_event({"is_fall": true})`
- `system_reset`：`_handle_system_reset_command("系统复位")`

预期 fake server 收到：

```text
POST /api/robot/stop {}
POST /api/robot/stop {}
POST /api/robot/stop {}
```

其中：

- 第一次来自 `user_stop`
- 第二次来自 `fall_alert`
- 第三次来自 `system_reset`

预期不会看到 `POST /api/robot/action {"action_id": 15, "action_name": "hands_up"}`，除非手动把配置改成：

```yaml
emergency:
  fall_robot_action_enabled: true
  fall_robot_action: "hands_up"
```

## 5. 临时用主程序验证

当前 `configs/system_config.yaml` 里的 `mode` 是 `mock` 时，不会测试 HTTP proxy 链路。要临时验证主程序，请改成：

```yaml
mode: "real"

robot:
  control_backend: "http_proxy"
  proxy_base_url: "http://127.0.0.1:9001"

speech:
  input_mode: "text"
  tts:
    backend: "print"
```

然后运行：

```bash
python3 main.py
```

输入“挥手”“举手”等指令，观察 fake server 是否收到 `/api/robot/action` 请求。输入“停止 / 停下 / 取消”时，应看到 `POST /api/robot/stop`。输入“系统复位 / 恢复默认 / 清空状态 / 复位”时，也应看到 `POST /api/robot/stop`，并且不应看到新的普通机器人动作。

## 6. 测试 g1_http TTS

### Fake Server 测试

继续保持 fake server 运行，然后运行：

```bash
python3 tests/test_tts_g1_http_manual.py
```

预期 fake server 收到：

```text
POST /api/robot/speak {"text": "G1 HTTP TTS 测试", "speaker_id": 0}
```

如果 fake server 没启动，脚本不会崩溃，应看到 `G1 HTTP TTS` 请求失败日志，并回退到 `print` 输出文本。

### 真 G1 测试

在 G1 上启动 `g1_robot_proxy.py`，并确认 PC 可以访问：

```bash
curl http://<G1_WLAN0_IP>:9001/health
curl -X POST http://<G1_WLAN0_IP>:9001/api/robot/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"G1 HTTP TTS 测试","speaker_id":0}'
```

真机发声成功后，PC 主控推荐配置为：

```yaml
mode: "mock"

robot:
  proxy_base_url: "http://192.168.1.114:9001"
  proxy_timeout_s: 3

speech:
  input_mode: "text"
  tts:
    backend: "g1_http"
    g1_speaker_id: 0
    fallback_backend: "print"
    fallback_to_pc_tts: true
```

这里 `mode: "mock"` 表示不测试机器人动作控制，只测试 PC 主控的语音交流链路；TTS 会通过 `robot.proxy_base_url` 发送到 G1 本体发声。

## 7. 验证代理不可达

关闭 fake server 后，再运行：

```bash
python3 tests/test_g1_http_client_manual.py
```

或运行：

```bash
python3 tests/test_tts_g1_http_manual.py
```

或在临时 `real + http_proxy` 配置下运行主程序。预期现象是请求返回 `ok=false` 或日志打印连接失败，但主程序不应崩溃。

## 优先级语义

- `fall_alert` 是最高优先级。触发后会停止 PC TTS，调用 `ActionExecutor.safe_stop_robot(reason="fall_alert")`，取消当前任务和动作队列，然后触发 IoT `fall_alert` 场景。默认不再提交 `hands_up`。
- `user_stop` 是高优先级用户中断。输入“停止 / 停下 / 取消 / 别说了 / 闭嘴 / 安静”时，只停止机器人和语音，不触发家电复位，不触发跌倒报警。
- `system_reset` 是测试后的清场指令。输入“系统复位 / 恢复默认 / 清空状态 / 复位”时，会停止 PC TTS，调用机器人 safe stop，清空紧急事件残留，恢复家电默认状态，一般不让机器人做新动作。

## Safe Stop 约束

当前 `stop` 的语义是 `safe_stop`，不是硬急停。G1 端未来实现 `/api/robot/stop` 时，应优先停止移动、停止语音、清理当前任务并尽量保持平衡；不要默认使用危险的 `Damp`、趴下、断电、零力矩等动作。
