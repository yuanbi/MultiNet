# MultiNet · 多节点综合网络探测平台

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)
![Docker](https://img.shields.io/badge/docker-compose-blue.svg)
![IPv6](https://img.shields.io/badge/IPv6-supported-brightgreen.svg)

**主控 Master + 多异地 Agent 分布式网络探测平台**
支持 IPv4 / IPv6 双栈 · Ping · TCPing · HTTP 测速 · IP 归属查询

</div>

---

## 功能特性

| 功能 | 描述 |
|------|------|
| **Ping 延迟测试** | IPv4/IPv6 双栈，逐包延迟+丢包率，持续 Ping 实时折线图 |
| **TCPing 端口探测** | 基于 curl，区分端口连通/超时，握手延迟统计 |
| **HTTP 测速** | DNS / TCP / TLS / TTFB / 总耗时五阶段可视化进度条 |
| **IP 归属查询** | 自动识别 IPv4/IPv6，返回城市、运营商、ASN、经纬度 |
| **多节点并发探测** | 多台 Agent 同时探测，结果独立展示 |
| **持续 Ping 图表** | 实时滚动条形图，绿色=连通/高度=延迟，红色=丢包 |
| **开放 RESTful API** | FastAPI 自动生成 Swagger 文档，支持第三方调用 |

---

## 架构概览

```
┌─────────────────────────────────────────┐
│              公网主控服务器              │
│  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │  前端页面 │  │ FastAPI  │  │ 本机  │ │
│  │  Web UI  │  │ REST API │  │ 探测  │ │
│  └──────────┘  └────┬─────┘  └───────┘ │
│                     │ WebSocket         │
└─────────────────────┼───────────────────┘
                       │  (出站长连接，无需开放入站端口)
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ Agent    │  │ Agent    │  │ Agent    │
  │ 香港节点  │  │ 美国节点  │  │ 日本节点  │
  └──────────┘  └──────────┘  └──────────┘
```

- **Master** 部署于公网，承载 Web 页面 + RESTful API + WebSocket 调度中心
- **Agent** 部署于全球各地，主动出站连接 Master，异地机器**无需开放任何入站端口**
- 全服务 Docker Compose 一键部署，容器开启 `NET_RAW` 权限保证 ping 正常

---

## 目录结构

```
MultiNet/
├── MultiNet-Master/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   ├── main.py                 # FastAPI 后端
│   └── templates/
│       └── index.html          # 前端页面（纯 HTML/CSS/JS）
└── MultiNet-Agent/
    ├── Dockerfile
    ├── docker-compose.yml
    └── agent.py                # Agent 探测节点脚本
```

---

## 快速部署

### 环境要求

- Docker >= 20.10
- Docker Compose >= 2.0
- 主控服务器需有公网 IP，开放 TCP 8000 端口

---

### 一、部署主控 Master

```bash
# 克隆或上传项目
git clone https://github.com/yourname/MultiNet.git
cd MultiNet/MultiNet-Master

# 一键构建并启动
docker compose up -d --build

# 查看运行日志
docker logs -f multinet-master
```

**访问地址**

| 地址 | 说明 |
|------|------|
| `http://YOUR_IP:8000` | Web 可视化界面 |
| `http://YOUR_IP:8000/docs` | Swagger API 文档 |

> 云服务器安全组需放行 TCP `8000` 端口（IPv4 + IPv6）

---

### 二、部署异地 Agent 节点

**1. 修改配置**（编辑 `MultiNet-Agent/docker-compose.yml`）

```yaml
environment:
  - MASTER_WS=ws://YOUR_MASTER_IP:8000/ws/agent   # ← 改为主控公网 IP
  - NODE_NAME=节点-香港-阿里云                      # ← 自定义节点名
```

**2. 启动**

```bash
cd MultiNet/MultiNet-Agent
docker compose up -d --build

# 看到以下日志说明注册成功
# [REGISTER] 节点-香港-阿里云 (xxxxxxxx)
docker logs -f multinet-agent
```

> Agent 仅需出站访问主控端口，**无需开放任何入站端口**，可部署在 NAT/内网机器上

**3. 多节点扩展**：复制 `MultiNet-Agent/` 目录到任意服务器，修改 `NODE_NAME` 即可新增节点，数量不限。

---

### 三、直接 Python 部署主控 Master（无 Docker）

适用于不方便使用 Docker 的环境。

**系统依赖（Linux）**

```bash
# Debian / Ubuntu
apt-get update && apt-get install -y iputils-ping curl

# CentOS / AlmaLinux
yum install -y iputils curl
```

**Python 环境**

```bash
# 要求 Python >= 3.11
cd MultiNet-Master

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

**启动服务**

```bash
# 前台运行（测试用）
uvicorn main:app --host 0.0.0.0 --port 8000

# 后台运行
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > multinet.log 2>&1 &
echo $! > multinet.pid

# 查看日志
tail -f multinet.log

# 停止服务
kill $(cat multinet.pid)
```

**使用 systemd 开机自启（推荐生产环境）**

```bash
# 创建 systemd 服务文件
cat > /etc/systemd/system/multinet-master.service << EOF
[Unit]
Description=MultiNet Master
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/MultiNet-Master
ExecStart=/opt/MultiNet-Master/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动
systemctl daemon-reload
systemctl enable multinet-master
systemctl start multinet-master

# 查看状态
systemctl status multinet-master
```

> 访问 `http://YOUR_IP:8000`，确保防火墙放行 8000 端口：
> ```bash
> ufw allow 8000/tcp   # Ubuntu
> firewall-cmd --permanent --add-port=8000/tcp && firewall-cmd --reload  # CentOS
> ```

---

### 四、直接 Python 部署 Agent 节点（无 Docker）

**系统依赖**

```bash
# Debian / Ubuntu
apt-get update && apt-get install -y iputils-ping curl

# CentOS / AlmaLinux
yum install -y iputils curl
```

**Python 环境**

```bash
cd MultiNet-Agent

python3 -m venv venv
source venv/bin/activate

# Agent 只依赖 websockets
pip install websockets==12.0
```

**修改连接配置**（编辑 `agent.py` 顶部两处）

```python
# agent.py 第 14~15 行
MASTER_WS = "ws://YOUR_MASTER_IP:8000/ws/agent"   # ← 改为主控公网 IP
NODE_NAME  = "节点-香港-阿里云"                     # ← 改为自定义节点名
```

**启动节点**

```bash
# 前台运行（测试）
python3 agent.py

# 后台运行
nohup python3 agent.py > agent.log 2>&1 &
echo $! > agent.pid

# 查看日志（出现 [REGISTER] 即注册成功）
tail -f agent.log

# 停止
kill $(cat agent.pid)
```

**使用 systemd 开机自启**

```bash
cat > /etc/systemd/system/multinet-agent.service << EOF
[Unit]
Description=MultiNet Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/MultiNet-Agent
ExecStart=/opt/MultiNet-Agent/venv/bin/python3 agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable multinet-agent
systemctl start multinet-agent

# 查看状态
systemctl status multinet-agent
```

> Agent 无需开放任何入站端口，仅需能访问主控 IP:8000 即可。

### Ping 延迟测试

- 逐包展示 TTL + 延迟徽章（绿 <50ms · 橙 <150ms · 红 ≥150ms）
- 汇总卡片：最低 / 平均 / 最高 / 丢包率
- **持续 Ping 模式**：实时滚动条形图，每秒一包，绿色柱高=延迟，红色=丢包

### TCPing 端口探测

- 每次探测显示 `OPEN` / `FAIL` 状态徽章 + 握手延迟
- 汇总：平均握手时间 + 失败率

### HTTP 测速

- HTTP 状态码徽章（2xx 绿 / 3xx 橙 / 失败 红）
- 五阶段横向进度条：DNS解析 · TCP连接 · TLS握手 · TTFB · 总耗时

### IP 归属查询

- 自动识别 IPv4 / IPv6，无需手动选择
- 展示：国家 / 地区 / 城市 / ISP / 组织 / ASN / 经纬度

---

## API 文档

所有接口返回标准 JSON，完整文档见 `http://YOUR_IP:8000/docs`

### 常用接口示例

**获取在线节点列表**

```bash
curl http://YOUR_IP:8000/api/nodes
```

**批量 Ping（IPv6）**

```bash
curl -X POST http://YOUR_IP:8000/api/probe \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "ping",
    "target": "google.com",
    "count": 4,
    "ip_version": 6,
    "node_ids": ["NODE_ID_1", "NODE_ID_2"]
  }'
```

**本地 HTTP 测速（强制 IPv6）**

```bash
curl -X POST http://YOUR_IP:8000/api/local/http_speed \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.google.com", "ip_version": 6}'
```

**IP 归属查询**

```bash
# IPv4
curl "http://YOUR_IP:8000/api/ip/query?ip=8.8.8.8"

# IPv6
curl "http://YOUR_IP:8000/api/ip/query?ip=2001:4860:4860::8888"
```

---

## 常见问题

<details>
<summary><b>ping 报错 Operation not permitted</b></summary>

确认 `docker-compose.yml` 包含以下配置，然后重建容器：

```yaml
cap_add:
  - NET_RAW
  - NET_ADMIN
```

```bash
docker compose down && docker compose up -d --build
```
</details>

<details>
<summary><b>IPv6 探测失败</b></summary>

检查宿主机 IPv6 是否启用：

```bash
# 0 = 已开启，1 = 已禁用
cat /proc/sys/net/ipv6/conf/all/disable_ipv6

# 临时开启
sysctl -w net.ipv6.conf.all.disable_ipv6=0

# 永久开启
echo "net.ipv6.conf.all.disable_ipv6=0" >> /etc/sysctl.conf && sysctl -p
```
</details>

<details>
<summary><b>Agent 节点离线 / 无法注册</b></summary>

```bash
# 检查 Agent 日志
docker logs multinet-agent

# 在 Agent 机器上测试主控端口连通性
curl -v http://YOUR_MASTER_IP:8000/api/nodes

# 确认主控安全组已放行 8000 端口
```
</details>

<details>
<summary><b>容器启动失败</b></summary>

```bash
# 查看详细报错
docker compose logs

# 检查端口是否被占用
ss -tlnp | grep 8000
```
</details>

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11 · FastAPI · uvicorn · websockets |
| 前端 | 原生 HTML / CSS / JS（无框架） |
| 探测工具 | iputils-ping（ping/ping6） · curl（支持 -4/-6） |
| 通信 | WebSocket 长连接（Agent → Master） |
| 部署 | Docker · Docker Compose |

---

## License

MIT
