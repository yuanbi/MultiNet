FROM python:3.11-slim

# 安装完整双栈网络工具
RUN apt-get update && apt-get install -y \
    iputils-ping \
    curl \
    iproute2 \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
