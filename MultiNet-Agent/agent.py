"""
MultiNet Agent - 异地探测节点
主动 WebSocket 长连接到主控，执行探测任务并返回结果。
断线自动重连，支持 IPv4/IPv6 双栈探测。
"""

import asyncio
import json
import os
import subprocess
import uuid

import websockets

# ─────────────────────────────────────────────
# ★ 可配置项（优先读取环境变量，也可直接在此修改）
# ─────────────────────────────────────────────
MASTER_WS = os.environ.get("MASTER_WS", "ws://YOUR_MASTER_IP:8000/ws/agent")
NODE_NAME  = os.environ.get("NODE_NAME",  "未命名节点")
NODE_ID    = str(uuid.uuid4())   # 每次启动自动生成唯一 ID，重启后 ID 不变（如需固定可改为读文件）

RECONNECT_INTERVAL = 5   # 断线后重连间隔（秒）


# ─────────────────────────────────────────────
# 工具函数：执行系统命令并返回输出字符串
# ─────────────────────────────────────────────
async def run_cmd(cmd: list, timeout: float = 30.0) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        return "ERROR: 命令执行超时"
    except FileNotFoundError as e:
        return f"ERROR: 命令不存在 - {e}"
    except Exception as e:
        return f"ERROR: {e}"


# ─────────────────────────────────────────────
# 构建探测命令
# ─────────────────────────────────────────────
def build_ping_cmd(target: str, count: int, ip_version: int) -> list:
    """IPv6 使用 ping6，IPv4 使用 ping"""
    if ip_version == 6:
        return ["ping6", "-c", str(count), target]
    return ["ping", "-c", str(count), target]


def build_tcping_cmd(target: str, port: int, ip_version: int) -> list:
    """使用 curl 模拟 TCPing，-4/-6 强制协议栈"""
    proto = "-6" if ip_version == 6 else "-4"
    return [
        "curl", proto, "--connect-timeout", "5",
        "-o", "/dev/null", "-s", "-w",
        "connected=%{http_code} tcp_ms=%{time_connect_ms} total_ms=%{time_total_ms}\n",
        f"http://{target}:{port}",
    ]


def build_http_speed_cmd(url: str, ip_version: int) -> list:
    """HTTP 测速，-4/-6 强制协议栈，拆分各阶段耗时"""
    proto = "-6" if ip_version == 6 else "-4"
    return [
        "curl", proto, "--connect-timeout", "10",
        "-o", "/dev/null", "-s", "-w",
        (
            "dns_ms=%{time_namelookup_ms}\n"
            "tcp_ms=%{time_connect_ms}\n"
            "tls_ms=%{time_appconnect_ms}\n"
            "ttfb_ms=%{time_starttransfer_ms}\n"
            "total_ms=%{time_total_ms}\n"
            "http_code=%{http_code}\n"
        ),
        url,
    ]


# ─────────────────────────────────────────────
# 处理单个任务，返回输出字符串
# ─────────────────────────────────────────────
async def handle_task(msg: dict) -> tuple[str, bool]:
    """返回 (output_str, is_error)"""
    task_type  = msg.get("type", "")
    target     = msg.get("target", "")
    port       = msg.get("port") or 80
    count      = min(int(msg.get("count") or 4), 20)   # 最多 20 次，防滥用
    ip_version = int(msg.get("ip_version") or 4)

    if not target:
        return ("ERROR: 缺少 target 参数", True)

    # ── Ping ──
    if task_type == "ping":
        cmd = build_ping_cmd(target, count, ip_version)
        output = await run_cmd(cmd, timeout=count * 3 + 5)
        return (output, output.startswith("ERROR"))

    # ── TCPing ──
    elif task_type == "tcping":
        cmd = build_tcping_cmd(target, port, ip_version)
        lines = []
        for i in range(count):
            out = await run_cmd(cmd, timeout=10)
            lines.append(f"[{i+1}] {out.strip()}")
            await asyncio.sleep(0.3)
        output = "\n".join(lines)
        return (output, False)

    # ── HTTP 测速 ──
    elif task_type == "http_speed":
        cmd = build_http_speed_cmd(target, ip_version)
        output = await run_cmd(cmd, timeout=30)
        return (output, output.startswith("ERROR"))

    else:
        return (f"ERROR: 不支持的任务类型 {task_type!r}", True)


# ─────────────────────────────────────────────
# 主 WebSocket 客户端逻辑
# ─────────────────────────────────────────────
async def run_agent():
    while True:
        try:
            print(f"[CONNECT] 连接主控: {MASTER_WS}")
            async with websockets.connect(
                MASTER_WS,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                # 发送注册消息
                reg = json.dumps({"node_id": NODE_ID, "name": NODE_NAME})
                await ws.send(reg)
                print(f"[REGISTER] 已注册为: {NODE_NAME} ({NODE_ID})")

                # 心跳任务（独立协程）
                async def heartbeat():
                    while True:
                        await asyncio.sleep(15)
                        try:
                            await ws.send(json.dumps({"type": "heartbeat"}))
                        except Exception:
                            break

                hb_task = asyncio.create_task(heartbeat())

                try:
                    # 持续接收主控下发的任务
                    async for raw in ws:
                        msg = json.loads(raw)
                        msg_type = msg.get("type")

                        if msg_type in ("ping", "tcping", "http_speed"):
                            task_id = msg.get("task_id", "")
                            print(f"[TASK] {msg_type} -> {msg.get('target')} (v{msg.get('ip_version',4)})")

                            # 异步执行探测，不阻塞接收
                            async def do_task(m=msg, tid=task_id):
                                output, is_err = await handle_task(m)
                                result = json.dumps({
                                    "type":    "result",
                                    "task_id": tid,
                                    "output":  output,
                                    "error":   is_err,
                                })
                                await ws.send(result)

                            asyncio.create_task(do_task())

                finally:
                    hb_task.cancel()

        except (
            websockets.ConnectionClosed,
            ConnectionRefusedError,
            OSError,
        ) as e:
            print(f"[DISCONNECT] 连接断开: {e}，{RECONNECT_INTERVAL}s 后重连...")
        except Exception as e:
            print(f"[ERROR] 未知错误: {e}，{RECONNECT_INTERVAL}s 后重连...")

        await asyncio.sleep(RECONNECT_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_agent())
