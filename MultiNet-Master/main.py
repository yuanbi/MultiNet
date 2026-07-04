"""
MultiNet Master - 主控服务
FastAPI + WebSocket 分布式网络探测平台
"""

import asyncio
import json
import re
import subprocess
import time
import uuid
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel

# ─────────────────────────────────────────────
# 应用初始化
# ─────────────────────────────────────────────
app = FastAPI(
    title="MultiNet 多节点综合网络探测平台",
    description="支持 IPv4/IPv6 双栈的分布式 Ping/TCPing/HTTP测速/IP归属查询平台",
    version="1.0.0",
)

templates = Jinja2Templates(directory="templates")

# ─────────────────────────────────────────────
# 在线节点池：{ node_id: {name, ws, last_seen} }
# ─────────────────────────────────────────────
online_nodes: Dict[str, dict] = {}

# ─────────────────────────────────────────────
# 任务结果缓存：{ task_id: [result, ...] }
# ─────────────────────────────────────────────
task_results: Dict[str, list] = {}

# ─────────────────────────────────────────────
# Pydantic 请求模型
# ─────────────────────────────────────────────

class PingRequest(BaseModel):
    target: str
    count: int = 4
    ip_version: int = 4          # 4 或 6
    node_ids: List[str] = []     # 空列表 = 仅本地

class TcpingRequest(BaseModel):
    target: str
    port: int = 80
    count: int = 4
    ip_version: int = 4
    node_ids: List[str] = []

class HttpSpeedRequest(BaseModel):
    url: str
    ip_version: int = 4
    node_ids: List[str] = []

class BatchProbeRequest(BaseModel):
    task_type: str               # ping / tcping / http_speed
    target: str
    port: Optional[int] = None
    count: int = 4
    ip_version: int = 4
    node_ids: List[str]

class IpQueryRequest(BaseModel):
    ip: str


# ─────────────────────────────────────────────
# 前端页面路由
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ─────────────────────────────────────────────
# Agent WebSocket 注册入口
# WS 地址: ws://<master>:8000/ws/agent
# ─────────────────────────────────────────────

@app.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket):
    await websocket.accept()
    node_id = None
    node_name = "未知节点"
    try:
        # 等待 Agent 发送注册消息
        reg_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        reg = json.loads(reg_raw)
        node_id = reg.get("node_id") or str(uuid.uuid4())
        node_name = reg.get("name", "未命名节点")

        online_nodes[node_id] = {
            "name": node_name,
            "ws": websocket,
            "last_seen": time.time(),
        }
        print(f"[REGISTER] {node_name} ({node_id})")

        # 持续接收 Agent 返回的任务结果
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "heartbeat":
                online_nodes[node_id]["last_seen"] = time.time()

            elif msg_type == "result":
                task_id = msg.get("task_id")
                if task_id and task_id in task_results:
                    task_results[task_id].append({
                        "node_id": node_id,
                        "node_name": node_name,
                        "output": msg.get("output", ""),
                        "error": msg.get("error", False),
                    })

    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if node_id and node_id in online_nodes:
            del online_nodes[node_id]
            print(f"[OFFLINE] {node_name} ({node_id})")


# ─────────────────────────────────────────────
# 前端 WebSocket：推送在线节点列表
# WS 地址: ws://<master>:8000/ws/nodes
# ─────────────────────────────────────────────

@app.websocket("/ws/nodes")
async def nodes_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            nodes_list = [
                {"id": nid, "name": info["name"]}
                for nid, info in online_nodes.items()
            ]
            await websocket.send_text(json.dumps(nodes_list))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass


# ─────────────────────────────────────────────
# 工具函数：本地执行 shell 命令
# ─────────────────────────────────────────────

async def run_local(cmd: List[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    return stdout.decode(errors="replace")


def build_ping_cmd(target: str, count: int, ip_version: int) -> List[str]:
    """构建 ping 命令，支持 v4/v6"""
    if ip_version == 6:
        return ["ping6", "-c", str(count), target]
    return ["ping", "-c", str(count), target]


def build_tcping_cmd(target: str, port: int, count: int, ip_version: int) -> List[str]:
    """使用 curl 模拟 TCPing，支持 v4/v6"""
    proto_flag = "-6" if ip_version == 6 else "-4"
    # curl 连接计时：每次独立一次连接，循环 count 次通过脚本实现
    # 这里返回单次命令，批量由调用方循环
    return [
        "curl", proto_flag, "--connect-timeout", "5",
        "-o", "/dev/null", "-s", "-w",
        "connected=%{http_code} tcp_ms=%{time_connect_ms} total_ms=%{time_total_ms}\\n",
        f"http://{target}:{port}",
    ]


def build_http_speed_cmd(url: str, ip_version: int) -> List[str]:
    """构建 HTTP 测速命令，支持 v4/v6"""
    proto_flag = "-6" if ip_version == 6 else "-4"
    return [
        "curl", proto_flag,
        "--connect-timeout", "10",
        "-o", "/dev/null", "-s", "-w",
        (
            "dns_ms=%{time_namelookup_ms}\\n"
            "tcp_ms=%{time_connect_ms}\\n"
            "tls_ms=%{time_appconnect_ms}\\n"
            "ttfb_ms=%{time_starttransfer_ms}\\n"
            "total_ms=%{time_total_ms}\\n"
            "http_code=%{http_code}\\n"
        ),
        url,
    ]


# ─────────────────────────────────────────────
# 工具函数：向指定节点派发任务并等待结果
# ─────────────────────────────────────────────

async def dispatch_to_nodes(
    node_ids: List[str],
    task_payload: dict,
    timeout: float = 35.0,
) -> List[dict]:
    task_id = str(uuid.uuid4())
    task_results[task_id] = []

    # 过滤有效节点
    targets = {
        nid: online_nodes[nid]
        for nid in node_ids
        if nid in online_nodes
    }
    if not targets:
        del task_results[task_id]
        return []

    payload = {**task_payload, "task_id": task_id}

    # 并发派发
    send_tasks = []
    for info in targets.values():
        send_tasks.append(info["ws"].send_text(json.dumps(payload)))
    await asyncio.gather(*send_tasks, return_exceptions=True)

    # 等待所有节点返回结果（超时兜底）
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(task_results[task_id]) >= len(targets):
            break
        await asyncio.sleep(0.2)

    results = task_results.pop(task_id, [])
    return results


# ─────────────────────────────────────────────
# RESTful API - 节点管理
# ─────────────────────────────────────────────

@app.get("/api/nodes", summary="获取在线节点列表", tags=["节点管理"])
async def get_nodes():
    """
    返回当前所有在线探测节点信息。
    """
    return {
        "code": 0,
        "data": [
            {"id": nid, "name": info["name"]}
            for nid, info in online_nodes.items()
        ],
    }


# ─────────────────────────────────────────────
# RESTful API - 通用批量探测接口
# ─────────────────────────────────────────────

@app.post("/api/probe", summary="通用批量探测接口", tags=["批量探测"])
async def batch_probe(req: BatchProbeRequest):
    """
    向指定节点组并发下发探测任务，支持 ping/tcping/http_speed。

    - **task_type**: `ping` | `tcping` | `http_speed`
    - **ip_version**: `4` 或 `6`
    - **node_ids**: 节点 ID 列表（从 /api/nodes 获取）

    **curl IPv6 示例**:
    ```bash
    curl -X POST http://your-master:8000/api/probe \\
      -H "Content-Type: application/json" \\
      -d '{"task_type":"ping","target":"google.com","count":4,"ip_version":6,"node_ids":["node-id-xxx"]}'
    ```
    """
    payload = {
        "type": req.task_type,
        "target": req.target,
        "port": req.port,
        "count": req.count,
        "ip_version": req.ip_version,
    }
    results = await dispatch_to_nodes(req.node_ids, payload)
    return {"code": 0, "task_type": req.task_type, "ip_version": req.ip_version, "results": results}


# ─────────────────────────────────────────────
# RESTful API - 本地 Ping
# ─────────────────────────────────────────────

@app.post("/api/local/ping", summary="本地 Ping 测试", tags=["本地探测"])
async def local_ping(req: PingRequest):
    """
    主控本机执行 Ping，支持 IPv4/IPv6。

    **curl IPv6 示例**:
    ```bash
    curl -X POST http://your-master:8000/api/local/ping \\
      -H "Content-Type: application/json" \\
      -d '{"target":"2001:4860:4860::8888","count":4,"ip_version":6}'
    ```
    """
    cmd = build_ping_cmd(req.target, req.count, req.ip_version)
    try:
        output = await run_local(cmd)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Ping 超时")
    return {
        "code": 0,
        "ip_version": req.ip_version,
        "target": req.target,
        "output": output,
    }


# ─────────────────────────────────────────────
# RESTful API - 本地 TCPing
# ─────────────────────────────────────────────

@app.post("/api/local/tcping", summary="本地 TCP 端口延迟检测", tags=["本地探测"])
async def local_tcping(req: TcpingRequest):
    """
    主控本机执行 TCPing（基于 curl），支持 IPv4/IPv6。

    **curl IPv4 示例**:
    ```bash
    curl -X POST http://your-master:8000/api/local/tcping \\
      -H "Content-Type: application/json" \\
      -d '{"target":"example.com","port":443,"count":4,"ip_version":4}'
    ```
    """
    cmd = build_tcping_cmd(req.target, req.port, req.count, req.ip_version)
    lines = []
    for i in range(req.count):
        try:
            out = await run_local(cmd)
            lines.append(f"[{i+1}] {out.strip()}")
        except Exception as e:
            lines.append(f"[{i+1}] ERROR: {e}")
        await asyncio.sleep(0.3)
    return {
        "code": 0,
        "ip_version": req.ip_version,
        "target": req.target,
        "port": req.port,
        "output": "\n".join(lines),
    }


# ─────────────────────────────────────────────
# RESTful API - 本地 HTTP 测速
# ─────────────────────────────────────────────

@app.post("/api/local/http_speed", summary="本地网站响应测速", tags=["本地探测"])
async def local_http_speed(req: HttpSpeedRequest):
    """
    主控本机测量网站各阶段耗时，支持 IPv4/IPv6 强制协议栈。

    **curl IPv6 示例**:
    ```bash
    curl -X POST http://your-master:8000/api/local/http_speed \\
      -H "Content-Type: application/json" \\
      -d '{"url":"https://www.google.com","ip_version":6}'
    ```
    """
    cmd = build_http_speed_cmd(req.url, req.ip_version)
    try:
        output = await run_local(cmd)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="HTTP 测速超时")
    return {
        "code": 0,
        "ip_version": req.ip_version,
        "url": req.url,
        "output": output,
    }


# ─────────────────────────────────────────────
# RESTful API - IP 归属查询
# ─────────────────────────────────────────────

@app.get("/api/ip/query", summary="IP 归属地查询（IPv4/IPv6 自动识别）", tags=["IP查询"])
@app.post("/api/ip/query", summary="IP 归属地查询（IPv4/IPv6 自动识别）", tags=["IP查询"])
async def ip_query(req: IpQueryRequest = None, ip: str = None):
    """
    查询 IPv4 或 IPv6 地址的归属城市、地区、运营商、ASN、经纬度。
    自动识别地址类型，无需手动指定。

    **curl 示例 - IPv4**:
    ```bash
    curl "http://your-master:8000/api/ip/query?ip=8.8.8.8"
    ```
    **curl 示例 - IPv6**:
    ```bash
    curl "http://your-master:8000/api/ip/query?ip=2001:4860:4860::8888"
    ```
    """
    target_ip = (req.ip if req else None) or ip
    if not target_ip:
        raise HTTPException(status_code=422, detail="缺少 ip 参数")

    # 调用 ip-api.com（支持 IPv6，免费无需 key）
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"http://ip-api.com/json/{target_ip}",
                params={"fields": "status,message,country,regionName,city,isp,org,as,lat,lon,query"},
            )
            data = resp.json()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"查询失败: {e}")

    if data.get("status") != "success":
        raise HTTPException(status_code=400, detail=data.get("message", "查询失败"))

    # 自动判断 IP 版本
    is_v6 = ":" in target_ip
    return {
        "code": 0,
        "ip": data.get("query"),
        "ip_version": 6 if is_v6 else 4,
        "country": data.get("country"),
        "region": data.get("regionName"),
        "city": data.get("city"),
        "isp": data.get("isp"),
        "org": data.get("org"),
        "asn": data.get("as"),
        "latitude": data.get("lat"),
        "longitude": data.get("lon"),
    }


# ─────────────────────────────────────────────
# 内置"主控本机"节点：前端选 local 时走此路由
# ─────────────────────────────────────────────

@app.post("/api/local/probe", summary="主控本机综合探测（供前端本地节点调用）", tags=["本地探测"])
async def local_probe(req: BatchProbeRequest):
    """前端选择「主控本机」节点时，实际调用此接口。"""
    if req.task_type == "ping":
        cmd = build_ping_cmd(req.target, req.count, req.ip_version)
        try:
            output = await run_local(cmd)
        except asyncio.TimeoutError:
            output = "ERROR: 超时"
    elif req.task_type == "tcping":
        port = req.port or 80
        cmd = build_tcping_cmd(req.target, port, req.count, req.ip_version)
        lines = []
        for i in range(req.count):
            try:
                out = await run_local(cmd)
                lines.append(f"[{i+1}] {out.strip()}")
            except Exception as e:
                lines.append(f"[{i+1}] ERROR: {e}")
            await asyncio.sleep(0.3)
        output = "\n".join(lines)
    elif req.task_type == "http_speed":
        cmd = build_http_speed_cmd(req.target, req.ip_version)
        try:
            output = await run_local(cmd)
        except asyncio.TimeoutError:
            output = "ERROR: 超时"
    else:
        raise HTTPException(status_code=400, detail="不支持的 task_type")

    return {
        "code": 0,
        "results": [
            {
                "node_id": "local",
                "node_name": "主控本机",
                "output": output,
                "error": output.startswith("ERROR"),
            }
        ],
    }
