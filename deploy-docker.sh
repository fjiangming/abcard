#!/bin/bash
# ============================================
# OpenAi-AGBC 一键 Docker 部署 (Linux)
#
# 用法:
#   sudo bash deploy-docker.sh            # 部署
#   sudo bash deploy-docker.sh --update   # 更新
#   sudo bash deploy-docker.sh --uninstall # 卸载
#   sudo bash deploy-docker.sh --status    # 查看状态
# ============================================
set -e

COMPOSE_FILE="docker-compose.yml"
DATA_DIR="./data"

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- 卸载 ----
do_uninstall() {
    echo ""
    echo "============================="
    echo "  🗑️  卸载 OpenAi-AGBC"
    echo "============================="
    echo ""

    if command -v docker compose &>/dev/null; then
        docker compose down --rmi local --volumes 2>/dev/null || true
    elif command -v docker-compose &>/dev/null; then
        docker-compose down --rmi local --volumes 2>/dev/null || true
    fi

    read -p "是否删除数据目录 ${DATA_DIR}? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$DATA_DIR"
        ok "数据目录已删除"
    else
        info "数据目录已保留: ${DATA_DIR}"
    fi

    ok "卸载完成"
    exit 0
}

# ---- 状态 ----
do_status() {
    echo ""
    echo "============================="
    echo "  📊 OpenAi-AGBC 状态"
    echo "============================="
    echo ""

    if command -v docker compose &>/dev/null; then
        docker compose ps
    elif command -v docker-compose &>/dev/null; then
        docker-compose ps
    else
        error "未检测到 docker compose"
        exit 1
    fi

    echo ""
    info "查看日志: docker compose logs -f"
    exit 0
}

# ---- 参数解析 ----
case "${1:-}" in
    --uninstall) do_uninstall ;;
    --status)    do_status ;;
    --update)    UPDATE_MODE=true ;;
    "")          UPDATE_MODE=false ;;
    *)           echo "用法: $0 [--update|--uninstall|--status]"; exit 1 ;;
esac

# ---- 自动检测 prod 模式 ----
if [ -f "docker-compose.prod.yml" ] && ! [ -f "Dockerfile" ]; then
    COMPOSE_FILE="docker-compose.prod.yml"
else
    COMPOSE_FILE="docker-compose.yml"
fi

# ---- 主流程 ----
echo ""
echo "============================="
echo "  🚀 OpenAi-AGBC Docker 部署"
echo "============================="
echo ""

# ---- 1. 检测 Docker ----
info "[1/4] 检测 Docker..."
if ! command -v docker &>/dev/null; then
    warn "Docker 未安装，正在自动安装..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    ok "Docker 已安装并启动"
else
    ok "Docker 已就绪: $(docker --version)"
fi

# 检测 compose 命令
COMPOSE_CMD=""
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    warn "安装 Docker Compose 插件..."
    apt-get update -qq && apt-get install -y -qq docker-compose-plugin 2>/dev/null || true
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    else
        error "无法安装 Docker Compose，请手动安装"
        exit 1
    fi
fi
ok "Compose 已就绪: $COMPOSE_CMD"

# ---- 2. 初始化数据目录 ----
info "[2/4] 初始化数据目录..."
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/config.json" ]; then
    cp config.example.json "$DATA_DIR/config.json"
    warn "已创建 config.json，请编辑: ${DATA_DIR}/config.json"
else
    ok "config.json 已存在"
fi

# ---- 3. 构建/拉取镜像 ----
info "[3/4] 准备 Docker 镜像... (使用 $COMPOSE_FILE)"
if [ "$COMPOSE_FILE" = "docker-compose.prod.yml" ]; then
    # prod 模式: 从 GHCR 拉取预构建镜像
    $COMPOSE_CMD -f $COMPOSE_FILE pull
    ok "镜像拉取完成"
else
    # dev 模式: 本地构建
    if [ "$UPDATE_MODE" = true ]; then
        $COMPOSE_CMD -f $COMPOSE_FILE build --no-cache
    else
        $COMPOSE_CMD -f $COMPOSE_FILE build
    fi
    ok "镜像构建完成"
fi

# ---- 4. 启动容器 ----
info "[4/4] 启动容器..."
$COMPOSE_CMD -f $COMPOSE_FILE down 2>/dev/null || true
$COMPOSE_CMD -f $COMPOSE_FILE up -d
ok "容器已启动"

# ---- 完成 ----
echo ""
echo "============================="
echo "  ✅ 部署完成!"
echo "============================="
echo ""
echo "  📦 数据目录:   $(realpath $DATA_DIR)"
echo "  📝 配置文件:   $(realpath $DATA_DIR)/config.json"
echo "  🌐 访问地址:   http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8501"
echo ""
echo "  常用命令:"
echo "    查看状态:    $COMPOSE_CMD ps"
echo "    查看日志:    $COMPOSE_CMD logs -f"
echo "    重启服务:    $COMPOSE_CMD restart"
echo "    停止服务:    $COMPOSE_CMD -f $COMPOSE_FILE down"
echo "    更新升级:    sudo bash deploy-docker.sh --update"
echo "    卸载清理:    sudo bash deploy-docker.sh --uninstall"
echo ""
