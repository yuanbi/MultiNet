FROM python:3.11-slim

# 安装完整双栈网络工具（ping / ping6 / curl IPv6支持）
RUN apt-get update && apt-get install -y \
    iputils-ping \
    curl \
    iproute2 \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir websockets==12.0

COPY agent.py .

CMD ["python", "-u", "agent.py"]
