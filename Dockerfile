# ============================================
# OpenAi-AGBC Docker 镜像
# 完全容器隔离，不污染宿主机
# ============================================
FROM python:3.11-slim

# 系统依赖（Playwright Chromium 所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    wget curl unzip \
    libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libatspi2.0-0 libcups2 \
    libxkbcommon0 libgtk-3-0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN useradd -r -m -s /bin/bash agbc

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 设置 Playwright 浏览器安装路径（root 安装, agbc 用户运行时都能访问）
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir playwright \
    && playwright install chromium \
    && chmod -R o+rx /opt/pw-browsers

# 复制项目文件
COPY --chown=agbc:agbc . .

# 创建数据挂载点
RUN mkdir -p /app/data && chown agbc:agbc /app/data

# 启动脚本
COPY --chown=agbc:agbc docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 注: 不切换用户，以 root 运行避免挂载目录权限问题
# Docker 本身已提供进程隔离

EXPOSE 8501

ENTRYPOINT ["/app/docker-entrypoint.sh"]
