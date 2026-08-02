#!/bin/bash
# QuantLab 启动脚本：同时启动前后端
# Usage: ./start.sh [dev|docker]   默认 dev
#
# Python 环境优先级：
#   1. conda env `quant`  （推荐，pyqlib/gplearn/LightGBM 等原生依赖齐备）
#   2. 项目 .venv
#   3. 系统 python3

set -u
# 不使用 -e：后台进程非零退出由 wait_for_port 处理

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-dev}"
BACKEND_PORT=8000
FRONTEND_PORT=3000

# ============ Python 解释器检测 ============
PYTHON_BIN=""
# 1. 优先：miniconda 的 quant 环境（项目主用环境）
if [ -x "/home/joakim/miniconda3/envs/quant/bin/python" ]; then
    PYTHON_BIN="/home/joakim/miniconda3/envs/quant/bin/python"
# 2. 兜底：当前 conda 激活的环境
elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
    PYTHON_BIN="$CONDA_PREFIX/bin/python"
# 3. 兜底：项目 .venv
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
# 4. 兜底：系统 python3
else
    PYTHON_BIN="$(command -v python3)"
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo -e "\033[31m[ERROR] 未找到可用的 Python 解释器\033[0m" >&2
    exit 1
fi

# ============ 颜色输出 ============
red()    { echo -e "\033[31m$*\033[0m"; }
green()  { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
blue()   { echo -e "\033[34m$*\033[0m"; }

# ============ 工具函数 ============
port_pid() {
    lsof -ti :"$1" 2>/dev/null || true
}

# 杀掉进程及其子进程（使用进程组）
kill_tree() {
    local pid=$1
    if [ -z "$pid" ]; then return; fi
    # 杀进程组（负 PID），确保子进程也被清理
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
}

# 等待端口真正就绪：端口有进程 + HTTP 探测（仅 backend）
wait_for_port() {
    local port=$1
    local name=$2
    local timeout=${3:-30}
    local probe_url="${4:-}"
    local i=0
    while [ $i -lt "$timeout" ]; do
        if [ -n "$(port_pid "$port")" ]; then
            # 如果提供了 HTTP 探测 URL，等真正能响应才返回
            if [ -n "$probe_url" ]; then
                if curl -fsS -m 2 "$probe_url" >/dev/null 2>&1; then
                    return 0
                fi
            else
                return 0
            fi
        fi
        printf "\r  等待 %s 启动... %ds/%ds" "$name" "$i" "$timeout"
        sleep 1
        i=$((i + 1))
    done
    echo ""
    red "错误: $name 启动超时 (port $port)"
    return 1
}

# ============ 进程清理 ============
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    local exit_code=$?
    # 避免 cleanup 被嵌套调用（cleanup 本身也会触发 EXIT trap）
    if [ "${CLEANUP_RUNNING:-0}" = "1" ]; then return; fi
    CLEANUP_RUNNING=1

    echo ""
    yellow "正在停止所有服务..."
    [ -n "$BACKEND_PID"  ] && kill_tree "$BACKEND_PID"
    [ -n "$FRONTEND_PID" ] && kill_tree "$FRONTEND_PID"
    # 给子进程 2 秒退出时间，再强杀
    sleep 2
    [ -n "$BACKEND_PID"  ] && kill -9 "$BACKEND_PID"  2>/dev/null || true
    [ -n "$FRONTEND_PID" ] && kill -9 "$FRONTEND_PID" 2>/dev/null || true
    wait 2>/dev/null
    green "已停止"
    exit "$exit_code"
}
trap 'cleanup' SIGINT SIGTERM EXIT

# ============ 模式分发 ============
if [ "$MODE" = "dev" ]; then
    echo "========================================="
    echo "  QuantLab - 量化策略回测研究平台"
    echo "========================================="
    blue "Python: $PYTHON_BIN"
    echo ""

    # 端口占用检查（必须在 trap 之前）
    if [ -n "$(port_pid "$BACKEND_PORT")" ]; then
        red "端口 $BACKEND_PORT 已被占用 (PID: $(port_pid "$BACKEND_PORT"))，请先释放"
        exit 1
    fi
    if [ -n "$(port_pid "$FRONTEND_PORT")" ]; then
        red "端口 $FRONTEND_PORT 已被占用 (PID: $(port_pid "$FRONTEND_PORT"))，请先释放"
        exit 1
    fi

    # 后端依赖检查
    if ! "$PYTHON_BIN" -c "import uvicorn, fastapi" >/dev/null 2>&1; then
        red "未找到 uvicorn/fastapi，请安装依赖:"
        echo "  $PYTHON_BIN -m pip install -r requirements.txt"
        exit 1
    fi
    # 关键第三方库检查（避免运行时才发现缺失）
    if ! "$PYTHON_BIN" -c "import fastapi_users_db_sqlalchemy, empyrical, alphalens, pypfopt, tenacity, structlog, prometheus_fastapi_instrumentator, cachetools, zxcvbn" >/dev/null 2>&1; then
        red "后端依赖缺失，请先安装:"
        echo "  $PYTHON_BIN -m pip install -r requirements.txt"
        exit 1
    fi

    # 前端依赖检查
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
        yellow "前端依赖未安装，正在安装..."
        (cd "$SCRIPT_DIR/frontend" && npm install) || { red "npm install 失败"; exit 1; }
    fi

    # 启动后端（带日志重定向，set -m 创建进程组使 kill -- -PID 能工作）
    blue "[1/2] 启动后端 (port $BACKEND_PORT)..."
    mkdir -p "$SCRIPT_DIR/logs"
    set -m
    (
        cd "$SCRIPT_DIR/backend"
        exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
    ) > "$SCRIPT_DIR/logs/backend.log" 2>&1 &
    BACKEND_PID=$!
    set +m

    # 启动前端
    blue "[2/2] 启动前端 (port $FRONTEND_PORT)..."
    set -m
    (
        cd "$SCRIPT_DIR/frontend"
        exec npm run dev -- --port "$FRONTEND_PORT"
    ) > "$SCRIPT_DIR/logs/frontend.log" 2>&1 &
    FRONTEND_PID=$!
    set +m

    # 等待服务真正就绪（后端做 HTTP 健康检查，前端只检查端口）
    echo ""
    wait_for_port "$BACKEND_PORT"  "后端" 60 "http://localhost:$BACKEND_PORT/health" || {
        red "后端启动失败，查看日志: tail -f logs/backend.log"
        exit 1
    }
    echo ""
    wait_for_port "$FRONTEND_PORT" "前端" 30 || {
        red "前端启动失败，查看日志: tail -f logs/frontend.log"
        exit 1
    }
    echo ""

    green "========================================="
    green "  所有服务已启动"
    green "========================================="
    echo "  Backend:  http://localhost:$BACKEND_PORT"
    echo "  API Docs: http://localhost:$BACKEND_PORT/docs"
    echo "  Frontend: http://localhost:$FRONTEND_PORT"
    echo "  Logs:     logs/backend.log  logs/frontend.log"
    green "========================================="
    yellow "按 Ctrl+C 停止所有服务"
    echo ""

    wait

elif [ "$MODE" = "docker" ]; then
    echo "以 docker 模式启动..."
    if ! command -v docker-compose >/dev/null 2>&1; then
        red "未找到 docker-compose，请先安装"
        exit 1
    fi
    docker-compose up --build
else
    echo "Usage: ./start.sh [dev|docker]"
    echo "  dev    - 本地开发模式（默认）"
    echo "  docker - Docker 容器模式"
    exit 1
fi