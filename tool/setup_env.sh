#!/usr/bin/env bash
# ═══════════════════════════════════════════════════
# OpenAi-AGBC 环境初始化脚本 (Linux / WSL)
# 用法: bash setup_env.sh
# ═══════════════════════════════════════════════════
set -e

echo "══════════════════════════════════════"
echo "  OpenAi-AGBC 环境初始化"
echo "══════════════════════════════════════"

# ── 1. 安装 Python 依赖 ──
echo ""
echo "[1/5] 安装 Python 依赖..."
if [ -f requirements.txt ]; then
    pip install -r requirements.txt -q 2>/dev/null || pip3 install -r requirements.txt -q
fi
# Playwright 是绑卡功能必需的
pip install playwright -q 2>/dev/null || pip3 install playwright -q 2>/dev/null || true

# ── 2. 安装 Playwright 浏览器 + 系统依赖 ──
echo "[2/5] 安装 Playwright Chromium 及系统依赖..."
python3 -m playwright install chromium 2>/dev/null || python -m playwright install chromium 2>/dev/null || true
# 安装 Chromium 运行所需的系统库 (解决 libnspr4.so 等缺失问题)
if command -v apt-get &>/dev/null; then
    echo "  安装 Chromium 系统依赖 (需要 sudo)..."
    sudo npx -y playwright install-deps chromium 2>/dev/null || \
    sudo apt-get install -y --no-install-recommends \
        libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
        libasound2 libxshmfence1 xvfb 2>/dev/null || true
fi

# ── 3. 启动 Xvfb (虚拟显示器, 绑卡浏览器需要) ──
echo "[3/5] 检查 Xvfb..."
if pgrep -f "Xvfb :99" >/dev/null 2>&1; then
    echo "  Xvfb :99 已在运行 ✓"
else
    echo "  启动 Xvfb :99..."
    Xvfb :99 -screen 0 1920x1080x24 -ac &>/dev/null &
    sleep 1
    if pgrep -f "Xvfb :99" >/dev/null 2>&1; then
        echo "  Xvfb :99 启动成功 ✓"
    else
        echo "  ⚠️ Xvfb 启动失败，绑卡功能可能不可用"
    fi
fi
export DISPLAY=:99

# ── 4. 验证 Chrome 可运行 ──
echo "[4/5] 验证 Chrome..."
CHROME_BIN=""
# 优先 Playwright 自带 Chrome
for f in ~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome; do
    [ -f "$f" ] && CHROME_BIN="$f" && break
done
# 备选: 系统 Chrome
[ -z "$CHROME_BIN" ] && [ -f /opt/google/chrome/chrome ] && CHROME_BIN="/opt/google/chrome/chrome"
[ -z "$CHROME_BIN" ] && [ -f /usr/bin/google-chrome-stable ] && CHROME_BIN="/usr/bin/google-chrome-stable"
[ -z "$CHROME_BIN" ] && [ -f /usr/bin/chromium-browser ] && CHROME_BIN="/usr/bin/chromium-browser"

if [ -z "$CHROME_BIN" ]; then
    echo "  ❌ 未找到 Chrome/Chromium, 请运行: python3 -m playwright install chromium"
else
    echo "  Chrome: $CHROME_BIN"
    # 快速启动测试
    if DISPLAY=:99 timeout 5 "$CHROME_BIN" --headless=new --no-sandbox --disable-gpu --dump-dom about:blank >/dev/null 2>&1; then
        echo "  Chrome 可正常运行 ✓"
    else
        echo "  ⚠️ Chrome 启动异常，可能缺少系统库，请运行: sudo npx -y playwright install-deps chromium"
    fi
fi

# ── 5. 检测 WSL 代理连通性 ──
echo "[5/5] 检测网络..."
# 获取 Windows 宿主 IP (仅 WSL 环境)
WIN_IP=""
if grep -qi microsoft /proc/version 2>/dev/null; then
    WIN_IP=$(ip route show default 2>/dev/null | awk '{print $3}')
    echo "  WSL 环境, Windows 宿主 IP: ${WIN_IP:-未知}"
    echo ""
    echo "  ╔═══════════════════════════════════════════════╗"
    echo "  ║ WSL 代理配置提示:                             ║"
    echo "  ║                                               ║"
    echo "  ║   在 UI「代理」栏填写:                        ║"
    echo "  ║   http://${WIN_IP:-<Windows-IP>}:<代理端口>             ║"
    echo "  ║                                               ║"
    echo "  ║   例如: http://${WIN_IP:-172.x.x.x}:7897                ║"
    echo "  ║                                               ║"
    echo "  ║   ⚠️ Windows 代理软件需开启「允许局域网连接」 ║"
    echo "  ╚═══════════════════════════════════════════════╝"

    # 自动测试常见代理端口
    for PORT in 7897 7890 10809 1080; do
        if curl -s --max-time 2 --proxy "http://${WIN_IP}:${PORT}" https://httpbin.org/ip >/dev/null 2>&1; then
            echo ""
            echo "  ✅ 检测到可用代理: http://${WIN_IP}:${PORT}"
            break
        fi
    done
else
    echo "  非 WSL 环境, 直连测试..."
    if curl -s --max-time 5 https://js.stripe.com/v3/ >/dev/null 2>&1; then
        echo "  可直连 Stripe ✓"
    else
        echo "  ⚠️ 无法直连 Stripe, 请配置代理"
    fi
fi

echo ""
echo "══════════════════════════════════════"
echo "  ✅ 环境初始化完成"
echo ""
echo "  启动项目: streamlit run ui.py"
echo "══════════════════════════════════════"
