from __future__ import annotations

import logging
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Final, Optional, Tuple

from core.utils import ConfigLoader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ActionSpec:
    """
    动作配置项（内部使用）。

    - type: 底层链路类型
        - "arm_sdk": 手臂动作（Unitree SDK2 Arm Action）
        - "loco_sdk": 底盘/导航/运动（当前阶段做 Mock）
        - "cpp": 外部可执行进程
    - id: SDK 动作 ID（仅 arm_sdk 类型使用）
    - path: 可执行文件路径（仅 cpp 类型使用）
    - timeout_s: C++ 动作最大允许运行秒数（仅 cpp 类型使用）
    """

    type: str
    id: Optional[int] = None
    path: Optional[str] = None
    timeout_s: float = 60.0


class ActionExecutor:
    """
    ActionExecutor：动作执行器（单例）。

    设计目标：
    - 后台线程串行执行动作，避免并发控制硬件/进程带来的风险。
    - 提供可预期的中断接口（interrupt_current_action），并支持安全退出（shutdown）。
    - 支持 SDK 动作与 C++ 可执行动作两类（C++ 阶段性缺失时做防御性兼容）。
    """

    # 动作配置表（已按真实 SDK 校验字典全量替换）
    ACTION_MAP: Final[Dict[str, _ActionSpec]] = {
        # 🚶‍♂️ 移动类（底盘/导航，走 loco_sdk）
        "navigate": _ActionSpec(type="loco_sdk"),
        "move": _ActionSpec(type="loco_sdk"),
        "stop": _ActionSpec(type="loco_sdk"),

        # 🤖 手臂动作（Unitree SDK2 Arm Action / G1ArmActionClient）
        #
        # 重要（禁止混用）：
        # - arm_sdk 的 id 必须使用 `unitree_sdk2py/g1/arm/g1_arm_action_client.py` 的 action_map（ExecuteAction 的真实 ID）
        # - `hardware/unitree_sdk2_python/example/*` 里的 option_list id 只是示例菜单编号，不是 ExecuteAction ID
        # - 特别注意：不要把 g1_loco_client_example.py 的菜单编号（如 9/10/11）塞进 arm_sdk
        "release_arm": _ActionSpec(type="arm_sdk", id=99),

        "two_hand_kiss": _ActionSpec(type="arm_sdk", id=11),
        "left_kiss": _ActionSpec(type="arm_sdk", id=12),
        "right_kiss": _ActionSpec(type="arm_sdk", id=13),

        "hands_up": _ActionSpec(type="arm_sdk", id=15),
        "clap": _ActionSpec(type="arm_sdk", id=17),
        "high_five": _ActionSpec(type="arm_sdk", id=18),
        "hug": _ActionSpec(type="arm_sdk", id=19),
        "heart": _ActionSpec(type="arm_sdk", id=20),
        "right_heart": _ActionSpec(type="arm_sdk", id=21),
        "reject": _ActionSpec(type="arm_sdk", id=22),
        "right_hand_up": _ActionSpec(type="arm_sdk", id=23),
        "x_ray": _ActionSpec(type="arm_sdk", id=24),
        "wave_face": _ActionSpec(type="arm_sdk", id=25),
        "wave_hand": _ActionSpec(type="arm_sdk", id=26),
        "shake_hand": _ActionSpec(type="arm_sdk", id=27),

        # 🎯 语义别名（保持兼容历史/LLM 输出）
        "blow_kiss": _ActionSpec(type="arm_sdk", id=11),
        "greet": _ActionSpec(type="arm_sdk", id=26),
        "say_hello": _ActionSpec(type="arm_sdk", id=26),
        "goodbye": _ActionSpec(type="arm_sdk", id=25),

        # 🧪 预留 C++ 动作
        "custom_dance": _ActionSpec(type="cpp", path="./bin/dance_action", timeout_s=60.0),
    }

    _instance: Optional["ActionExecutor"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "ActionExecutor":
        # 双重检查 + 锁，保证单例线程安全
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # 防止单例被多次初始化
        if getattr(self, "_initialized", False):
            return

        # 基础组件初始化（严格按需求）
        self._queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self._control_mode: str = "idle"
        self._current_process: Optional[subprocess.Popen[bytes]] = None
        self._interrupt_event: threading.Event = threading.Event()
        # mock/real 切换：mock 模式下延迟创建并复用虚拟机器人实例，避免重复打印“已上线”
        self._mock_robot: Optional[Any] = None

        # 额外内部控制：用于安全退出 worker（不暴露给外部）
        self._shutdown_event: threading.Event = threading.Event()
        self._sentinel: Tuple[str, str] = ("__shutdown__", "")

        # 后台线程（注意 daemon=False）
        self._worker_thread = threading.Thread(
            name="_worker_thread",
            target=self._worker_loop,
            daemon=False,
        )
        self._worker_thread.start()

        self._initialized = True
        logger.info("ActionExecutor 初始化完成：worker 线程已启动（daemon=False）。")

    # -----------------------------
    # 对外接口
    # -----------------------------
    def submit_action(self, action_name: str, target: str = "") -> None:
        """
        提交动作到队列。

        - **安全的队列覆盖策略**：
          如果 action_name 属于 ["navigate", "move", "stop"]，必须在入队前安全清空队列，
          防止旧的移动/导航指令滞留导致行为不可控。
        """
        if action_name in ["navigate", "move", "stop"]:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            logger.info("覆盖策略触发：已清空队列后提交动作：%s target=%s", action_name, target)
        else:
            logger.info("提交动作：%s target=%s", action_name, target)

        self._queue.put((action_name, target))

    def interrupt_current_action(self) -> None:
        """
        绝对干净的中断接口：
        - 清空队列（不再执行排队动作）
        - 设置中断事件，通知正在执行的动作尽快停止
        """
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._interrupt_event.set()
        logger.warning("收到中断请求：已清空队列并设置中断标志。")

    def clear_interrupt(self) -> None:
        """
        清除中断标志，允许后续新动作正常执行。

        仅应在确认当前动作已停止（如 robot_stop 完成后）再调用。
        """
        self._interrupt_event.clear()
        logger.info("中断标志已清除，后续新动作可正常执行。")

    def shutdown(self, timeout_s: float = 5.0) -> None:
        """
        安全退出主程序。

        行为：
        - 设置 shutdown 标志
        - 触发中断（尽快停止当前动作）
        - 向队列推入 sentinel 唤醒 worker 的阻塞读取
        - join worker 线程（可设置超时）
        """
        if self._shutdown_event.is_set():
            return

        logger.info("ActionExecutor 正在 shutdown：timeout_s=%.2f", timeout_s)
        self._shutdown_event.set()

        # 先中断当前动作，并尽力清空队列
        self.interrupt_current_action()

        # 唤醒 worker：避免其永久阻塞在 get()
        self._queue.put(self._sentinel)

        self._worker_thread.join(timeout=timeout_s)
        if self._worker_thread.is_alive():
            logger.warning("shutdown 超时：worker 线程仍在运行（可能仍在等待进程退出/被强杀）。")
        else:
            logger.info("shutdown 完成：worker 线程已退出。")

    # -----------------------------
    # 内部执行逻辑
    # -----------------------------
    def _worker_loop(self) -> None:
        """
        后台执行循环：
        - 阻塞读取队列
        - 按 ACTION_MAP 查表执行
        - SDK 动作：hardware.real_g1.play_action
        - C++ 动作：subprocess.Popen + 轮询 + 中断/超时强杀
        """
        logger.info("worker loop 已启动。")

        while not self._shutdown_event.is_set():
            try:
                action_name, target = self._queue.get(block=True)
            except Exception:
                logger.exception("worker 阻塞读取队列异常，继续循环。")
                continue
            try:
                # shutdown sentinel：允许 worker 退出
                if (action_name, target) == self._sentinel:
                    logger.info("worker 收到 shutdown sentinel，即将退出。")
                    break

                spec = self.ACTION_MAP.get(action_name)
                if spec is None:
                    logger.warning("未知动作：%s（ACTION_MAP 未配置），已跳过。", action_name)
                    continue

                # 若在取到动作后已被中断：直接跳过该动作
                #
                # 重要：这里不清除中断标志。原因是：
                # - interrupt_current_action() 的语义是“停止当前动作并丢弃队列”
                # - 如果这里提前 clear()，可能会让正在执行的 C++ 动作错过中断信号（竞态）
                if self._interrupt_event.is_set():
                    logger.warning("动作 %s 在执行前检测到中断标志，已跳过。", action_name)
                    continue

                if spec.type == "arm_sdk":
                    self._run_arm_action(action_name=action_name, action_id=spec.id)
                elif spec.type == "loco_sdk":
                    self._run_loco_action(action_name=action_name, target=target)
                elif spec.type == "cpp":
                    self._run_cpp_action(
                        action_name=action_name,
                        target=target,
                        path=spec.path,
                        timeout_s=spec.timeout_s,
                    )
                else:
                    logger.warning("动作 %s 配置 type=%s 不支持，已跳过。", action_name, spec.type)
            finally:
                # 标准 queue 语义：无论成功/跳过/异常，都标记该 item 已处理
                try:
                    self._queue.task_done()
                except Exception:
                    # queue.get() 的 item 可能并非来自同一个 queue 实例等极端情况，保持鲁棒
                    pass

        # worker 退出前：尽量恢复状态
        self._control_mode = "idle"
        self._interrupt_event.clear()
        logger.info("worker loop 已退出。")

    def _run_arm_action(self, action_name: str, action_id: Optional[int]) -> None:
        """
        手臂动作（Unitree SDK2 Arm Action）。

        极其关键：发送真实动作前必须先 play_action(99) 释放手臂占用，
        并等待 0.1s，再发送实际 action_id。
        """
        if action_id is None:
            logger.warning("arm_sdk 动作 %s 缺少 id 配置，已跳过。", action_name)
            return

        self._control_mode = "arm_sdk"
        logger.info("开始执行 arm_sdk 动作：%s id=%s", action_name, action_id)
        try:
            # 比赛优先：根据 system_config.yaml 的 mode 做 mock/real 切换
            mode = str(ConfigLoader().get_nested("mode", default="mock") or "mock").strip().lower()

            if mode == "mock":
                logger.info("当前为 MOCK 模式，使用虚拟机器人执行动作。")
                # 运行时导入，避免开发/部署环境缺包导致启动失败
                from hardware.mock_g1 import MockG1Robot  # type: ignore

                if self._mock_robot is None:
                    self._mock_robot = MockG1Robot()
                mock_robot = self._mock_robot

                # 特殊动作：release_arm（99）只执行一次，避免连续释放两次
                if int(action_id) == 99:
                    try:
                        ok = bool(mock_robot.execute_action(99))
                        if not ok:
                            logger.warning("MOCK release_arm 执行失败：action=%s id=99", action_name)
                    except Exception:
                        logger.exception("MOCK execute_action(99) 执行异常：%s", action_name)
                    return

                # Release 防护：先释放手臂占用
                try:
                    ok_release = bool(mock_robot.execute_action(99))
                    if not ok_release:
                        logger.warning("MOCK release_arm 执行失败：action=%s", action_name)
                except Exception:
                    logger.exception("MOCK execute_action(99) 执行异常：%s", action_name)
                time.sleep(0.1)
                try:
                    ok_act = bool(mock_robot.execute_action(int(action_id)))
                    if not ok_act:
                        logger.warning(
                            "MOCK arm action 执行失败：action=%s id=%s",
                            action_name,
                            action_id,
                        )
                except Exception:
                    logger.exception("MOCK execute_action(%s) 执行异常：%s", action_id, action_name)
            else:
                logger.info("当前为 REAL 模式，使用 Unitree SDK 执行动作。")
                # 避免在 import 阶段就强依赖硬件模块：运行时导入，便于开发阶段单测/缺依赖启动。
                from hardware.real_g1 import play_action  # type: ignore

                # 特殊动作：release_arm（99）只执行一次，避免连续释放两次
                if int(action_id) == 99:
                    ok = False
                    try:
                        ok = bool(play_action(99))  # type: ignore[call-arg]
                    except Exception:
                        logger.exception("REAL play_action(99) 异常：%s", action_name)
                    if not ok:
                        logger.warning("REAL release_arm 发送失败：action=%s id=99", action_name)
                    return

                # Release 防护：先释放手臂占用
                ok_release = False
                try:
                    ok_release = bool(play_action(99))  # type: ignore[call-arg]
                except Exception:
                    logger.exception("REAL play_action(99) 异常：%s", action_name)
                if not ok_release:
                    logger.warning("REAL release_arm 发送失败：action=%s", action_name)

                time.sleep(0.1)

                ok_act = False
                try:
                    ok_act = bool(play_action(int(action_id)))  # type: ignore[call-arg]
                except Exception:
                    logger.exception("REAL play_action(%s) 异常：%s", action_id, action_name)
                if not ok_act:
                    logger.warning("REAL arm action 发送失败：action=%s id=%s", action_name, action_id)

            logger.info("arm_sdk 动作完成：%s", action_name)
        except Exception:
            logger.exception("arm_sdk 动作执行异常：%s", action_name)
        finally:
            self._control_mode = "idle"
            # 动作执行完毕后：清理中断状态，避免影响后续动作
            self._interrupt_event.clear()
            time.sleep(0.3)  # 缓冲，避免动作切换过快导致硬件/SDK 不稳定

    def _run_loco_action(self, action_name: str, target: str) -> None:
        """
        底盘/导航/运动（loco_sdk）。

        比赛演示：按 system_config.yaml 的 mode 走 mock 闭环或 real 占位日志；不做真导航。

        重要（避免踩坑）：
        - `hardware/unitree_sdk2_python/example/g1/high_level/g1_loco_client_example.py` 的 option_list id
          只是示例菜单分支编号，并不是底层任务 ID
        - TODO: future real loco integration should call LocoClient methods directly, e.g. Move/StopMove/WaveHand/ShakeHand, not pass example menu IDs.
        """
        self._control_mode = "loco_sdk"
        try:
            mode = str(ConfigLoader().get_nested("mode", default="mock") or "mock").strip().lower()
            tgt = (target or "").strip()
            if action_name == "move" and tgt:
                cmd = f"move:{tgt}"
            elif action_name == "navigate" and tgt:
                cmd = f"navigate:{tgt}"
            elif action_name == "stop":
                cmd = "stop"
            else:
                cmd = action_name

            if mode == "mock":
                logger.info("当前为 MOCK 模式，使用虚拟机器人执行底盘动作。")
                from hardware.mock_g1 import MockG1Robot  # type: ignore

                if self._mock_robot is None:
                    self._mock_robot = MockG1Robot()
                mock_robot = self._mock_robot
                mock_robot.loco_control(cmd)
            else:
                logger.info(
                    "[REAL LOCO RESERVED] 真实底盘控制暂未接入：action=%s target=%s",
                    action_name,
                    target,
                )
        except Exception:
            logger.exception("loco_sdk 执行异常：action=%s target=%s", action_name, target)
        finally:
            self._control_mode = "idle"
            self._interrupt_event.clear()
            time.sleep(0.3)

    def _run_cpp_action(self, action_name: str, target: str, path: Optional[str], timeout_s: float) -> None:
        if not path:
            logger.warning("C++ 动作 %s 缺少 path 配置，已跳过。", action_name)
            return

        # 防御性实现：开发阶段可能没有可执行文件，直接 warning 并跳过
        if not os.path.isfile(path):
            logger.warning("C++ 动作可执行文件不存在：%s（动作=%s），已跳过。", path, action_name)
            return

        self._control_mode = "cpp"
        start_ts = time.monotonic()
        logger.info("开始执行 C++ 动作：%s path=%s target=%s timeout_s=%.2f", action_name, path, target, timeout_s)

        process: Optional[subprocess.Popen[bytes]] = None
        self._current_process = None
        try:
            cmd = [path]
            if target:
                cmd.append(target)

            # preexec_fn=os.setsid：为子进程建立新的进程组，方便后续 killpg 彻底强杀
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid,
            )
            self._current_process = process

            # 轮询：支持中断与超时
            while True:
                if process.poll() is not None:
                    # 进程正常结束
                    rc = process.returncode
                    logger.info("C++ 动作进程退出：%s returncode=%s", action_name, rc)
                    break

                if self._interrupt_event.is_set():
                    logger.warning("C++ 动作收到中断：%s，准备强制终止进程组。", action_name)
                    self._kill_process_group(process)
                    break

                elapsed = time.monotonic() - start_ts
                if elapsed > timeout_s:
                    logger.warning(
                        "C++ 动作超时：%s elapsed=%.2fs > timeout_s=%.2fs，准备强制终止进程组。",
                        action_name,
                        elapsed,
                        timeout_s,
                    )
                    self._kill_process_group(process)
                    break

                time.sleep(0.05)

        except Exception:
            logger.exception("C++ 动作执行异常：%s", action_name)
            if process is not None:
                try:
                    self._kill_process_group(process)
                except Exception:
                    logger.exception("异常后强杀进程组失败：%s", action_name)
        finally:
            self._current_process = None
            self._control_mode = "idle"
            self._interrupt_event.clear()
            time.sleep(0.3)  # 缓冲，避免动作切换过快

    def _kill_process_group(self, process: subprocess.Popen[bytes]) -> None:
        """
        彻底强杀：按需求使用 SIGKILL + killpg。
        需要确保子进程在独立进程组中（Popen preexec_fn=os.setsid）。
        """
        try:
            pgid = os.getpgid(process.pid)
        except Exception:
            logger.exception("获取进程组失败：pid=%s，退化为直接 kill。", getattr(process, "pid", None))
            try:
                process.kill()
            except Exception:
                logger.exception("直接 kill 也失败：pid=%s", getattr(process, "pid", None))
            return

        try:
            os.killpg(pgid, signal.SIGKILL)
            logger.warning("已强杀进程组：pgid=%s pid=%s", pgid, process.pid)
        except ProcessLookupError:
            logger.info("进程组已不存在（可能已退出）：pgid=%s pid=%s", pgid, process.pid)
        except Exception:
            logger.exception("强杀进程组失败：pgid=%s pid=%s", pgid, process.pid)
