#!/bin/bash
# QuantLab 启动脚本：同时启动前后端
# Usage: ./start.sh [dev|docker]   默认 dev

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-dev}"
BACKEND_PORT=8000
FRONTEND_PORT=3000

# Python 解释器：优先用项目 .venv，避免依赖系统环境
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# 颜色输出
red()    { echo -e "\033[31m$*\033[0m"; }
green()  { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
blue()   { echo -e "\033[34m$*\033[0m"; }

# 检查端口是否被占用，返回占用进程 PID
port_pid() {
    lsof -ti :"$1" 2>/dev/null || true
}

# 杀掉进程及其子进程（进程组）
kill_tree() {
    local pid=$1
    if [ -z "$pid" ]; then return; fi
    # 杀进程组（负 PID），确保子进程也被清理
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
}

cleanup() {
    echo ""
    yellow "正在停止所有服务..."
    [ -n "$BACKEND_PID" ]  && kill_tree "$BACKEND_PID"
    [ -n "$FRONTEND_PID" ] && kill_tree "$FRONTEND_PID"
    wait 2>/dev/null
    green "已停止"
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 等待端口就绪，超时返回非零
wait_for_port() {
    local port=$1
    local name=$2
    local timeout=${3:-30}
    local i=0
    while [ $i -lt "$timeout" ]; do
        if [ -n "$(port_pid "$port")" ]; then
            return 0
        fi
        printf "\r  等待 %s 启动... %ds/%ds" "$name" "$i" "$timeout"
        sleep 1
        i=$((i + 1))
    done
    echo ""
    red "错误: $name 启动超时 (port $port)"
    return 1
}

if [ "$MODE" = "dev" ]; then
    echo "========================================="
    echo "  QuantLab - 量化策略回测研究平台"
    echo "========================================="
    echo ""

    # 端口占用检查
    if [ -n "$(port_pid "$BACKEND_PORT")" ]; then
        red "端口 $BACKEND_PORT 已被占用 (PID: $(port_pid "$BACKEND_PORT"))，请先释放"
        exit 1
    fi
    if [ -n "$(port_pid "$FRONTEND_PORT")" ]; then
        red "端口 $FRONTEND_PORT 已被占用 (PID: $(port_pid "$FRONTEND_PORT"))，请先释放"
        exit 1
    fi

    # 检查依赖
    if ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
        red "未找到 uvicorn，请安装依赖: $PYTHON_BIN -m pip install uvicorn fastapi"
        exit 1
    fi
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
        yellow "前端依赖未安装，正在安装..."
        (cd "$SCRIPT_DIR/frontend" && npm install) || { red "npm install 失败"; exit 1; }
    fi

    # 启动后端
    blue "[1/2] 启动后端 (port $BACKEND_PORT)..."
    BACKEND_PID=""
    (
        cd "$SCRIPT_DIR/backend"
        exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
    ) &
    BACKEND_PID=$!

    # 启动前端
    blue "[2/2] 启动前端 (port $FRONTEND_PORT)..."
    FRONTEND_PID=""
    (
        cd "$SCRIPT_DIR/frontend"
        exec npm run dev -- --port "$FRONTEND_PORT"
    ) &
    FRONTEND_PID=$!

    # 等待服务就绪
    echo ""
    wait_for_port "$BACKEND_PORT"  "后端" 30 || exit 1
    echo ""
    wait_for_port "$FRONTEND_PORT" "前端" 30 || exit 1
    echo ""

    echo ""
    green "========================================="
    green "  所有服务已启动"
    green "========================================="
    echo "  Backend:  http://localhost:$BACKEND_PORT"
    echo "  API Docs: http://localhost:$BACKEND_PORT/docs"
    echo "  Frontend: http://localhost:$FRONTEND_PORT"
    echo "========================================="
    yellow "按 Ctrl+C 停止所有服务"
    echo ""

    wait

elif [ "$MODE" = "docker" ]; then
    echo "以 docker 模式启动..."
    docker-compose up --build
else
    echo "Usage: ./start.sh [dev|docker]"
    echo "  dev    - 本地开发模式（默认）"
    echo "  docker - Docker 容器模式"
    exit 1
fi
