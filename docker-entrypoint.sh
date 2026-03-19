#!/bin/bash
# ============================================
# OpenAi-AGBC 容器启动入口
# ============================================
set -e

# ---- 1. 启动 Xvfb 虚拟显示器 ----
Xvfb :99 -screen 0 1280x720x24 -ac -nolisten tcp &
export DISPLAY=:99

# 等待 Xvfb 就绪
sleep 1

# ---- 2. 初始化数据目录 ----
# config.json: 优先使用挂载的配置，不存在则从模板创建
if [ ! -f /app/data/config.json ]; then
    cp /app/config.example.json /app/data/config.json
    echo "[entrypoint] 已从模板创建 config.json → /app/data/config.json"
fi

# 软链接配置文件到工作目录
ln -sf /app/data/config.json /app/config.json

# 软链接数据库到持久化目录
if [ -f /app/data/data.db ]; then
    ln -sf /app/data/data.db /app/data.db
fi

# ---- 3. 初始化数据库 ----
python -c "from database import init_db; init_db(); print('[entrypoint] 数据库已初始化')"

# 确保数据库文件在持久化目录
if [ -f /app/data.db ] && [ ! -L /app/data.db ]; then
    mv /app/data.db /app/data/data.db
    ln -sf /app/data/data.db /app/data.db
fi

# ---- 4. 启动 Streamlit ----
echo "[entrypoint] 启动 Streamlit..."
exec streamlit run ui.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.maxUploadSize=5 \
    --browser.gatherUsageStats=false
