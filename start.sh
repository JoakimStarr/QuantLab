#!/bin/bash
# QuantLab 启动脚本：同时启动前后端
# Usage:
#   ./start.sh          静默启动（默认）：后台分离运行，脚本执行完自动退出，可关闭终端；停止用 ./start.sh stop
#   ./start.sh dev      开发模式：前台运行，Ctrl+C 停止（终端不可关闭）
#   ./start.sh stop     停止静默模式启动的服务
#
# Python 环境优先级：
#   1. conda env `quant`  （推荐，pyqlib/gplearn/LightGBM 等原生依赖齐备）
#   2. 项目 .venv
#   3. 系统 python3

set -u
# 不使用 -e：后台进程非零退出由 wait_for_port 处理

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-silent}"
BACKEND_PORT=8000
FRONTEND_PORT=3000

# ============ Python 解释器检测 ============
PYTHON_BIN=""
# 1. 优先：项目 .venv（setup.sh 创建）
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
# 2. 兜底：当前 conda 激活的环境
elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
    PYTHON_BIN="$CONDA_PREFIX/bin/python"
# 3. 兜底：系统 python3
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

# 杀掉进程及其子进程（先按进程组，再逐个兜底）
# 注意：当 PGID 组长已死（僵尸）时 kill -- -PGID 无效（孤儿进程组），
# 必须逐个 kill 目标进程本身。
kill_tree() {
    local pid=$1
    if [ -z "$pid" ]; then return; fi
    local pgid
    pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ -n "$pgid" ] && [ "$pgid" -gt 1 ]; then
        # 先尝试按进程组杀（覆盖 uvicorn/vite/npm/tee 整个启动链）
        kill -- -"$pgid" 2>/dev/null || true
    fi
    # 再逐个杀目标进程本身（兜底：孤儿进程组 / 组长已死的情况）
    kill "$pid" 2>/dev/null || true
    kill -9 "$pid" 2>/dev/null || true
}

# 端口冲突处理：显示占用进程并询问用户是否杀掉重启。
# 返回 0=已杀掉并释放端口；返回 1=用户选择不杀（由调用方决定退出）。
ask_kill_port() {
    local port=$1
    local name=$2
    local pids
    pids=$(port_pid "$port")
    [ -z "$pids" ] && return 0

    echo ""
    yellow "端口 $port ($name) 已被以下进程占用:"
    for p in $pids; do
        ps -w -o pid=,cmd= -p "$p" 2>/dev/null | sed 's/^/    /' || echo "    PID $p (进程不存在?)"
    done

    while true; do
        printf "\033[33m是否杀掉这些进程并重新启动 $name？[y/N] \033[0m"
        if ! read -r answer; then
            # 非交互环境（stdin 非 TTY/EOF），无法确认 → 安全起见不杀
            red "非交互环境无法确认，请手动释放端口 $port 后重试。"
            return 1
        fi
        case "${answer:-N}" in
            y|Y|yes|YES)
                for p in $pids; do
                    kill_tree "$p"
                done
                # 等待端口真正释放（最多 10s）
                local i=0
                while [ -n "$(port_pid "$port")" ] && [ $i -lt 10 ]; do
                    sleep 1
                    i=$((i + 1))
                done
                if [ -n "$(port_pid "$port")" ]; then
                    red "端口 $port 未能释放（仍有进程占用），请手动处理后再试。"
                    return 1
                fi
                green "端口 $port 已释放"
                return 0
                ;;
            n|N|no|NO|"")
                yellow "已跳过，端口 $port ($name) 保持占用。"
                return 1
                ;;
            *)
                echo "请输入 y 或 n"
                ;;
        esac
    done
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
# 静默模式正常完成标记：置 1 后 cleanup 不再杀已分离的服务
SILENT_OK=0

cleanup() {
    local exit_code=$?
    # 避免 cleanup 被嵌套调用（cleanup 本身也会触发 EXIT trap）
    if [ "${CLEANUP_RUNNING:-0}" = "1" ]; then return; fi
    CLEANUP_RUNNING=1

    if [ "${SILENT_OK:-0}" = "1" ]; then
        # 静默模式正常完成：服务已分离运行，交由 ./start.sh stop 管理
        exit 0
    fi
    # 本脚本未启动任何服务（silent 启动失败 / stop 命令等）→ 静默退出，不打扰
    if [ -z "$BACKEND_PID" ] && [ -z "$FRONTEND_PID" ]; then
        exit "$exit_code"
    fi

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
trap 'cleanup' SIGINT SIGTERM SIGHUP EXIT

# ============ 静默启动（默认模式） ============
# 分离运行：setsid 新会话 + stdin 断开 + 输出重定向，脚本执行完即退出，终端可关闭。
# PID 写 logs/backend.pid / frontend.pid，用 ./start.sh stop 停止。
start_silent() {
    echo "========================================="
    echo "  QuantLab - 静默启动"
    echo "========================================="
    blue "Python: $PYTHON_BIN"
    echo ""

    # 端口检查（非交互，不询问；若为旧实例请先 stop）
    if [ -n "$(port_pid "$BACKEND_PORT")" ]; then
        red "端口 $BACKEND_PORT (后端) 已被占用。若为旧实例，请先运行 ./start.sh stop。"
        exit 1
    fi
    if [ -n "$(port_pid "$FRONTEND_PORT")" ]; then
        red "端口 $FRONTEND_PORT (前端) 已被占用。若为旧实例，请先运行 ./start.sh stop。"
        exit 1
    fi

    # 依赖检查（与 dev 模式一致）
    if ! "$PYTHON_BIN" -c "import uvicorn, fastapi" >/dev/null 2>&1; then
        red "未找到 uvicorn/fastapi，请先运行 ./setup.sh 安装依赖。"
        exit 1
    fi
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
        yellow "前端依赖未安装，正在安装..."
        (cd "$SCRIPT_DIR/frontend" && npm install) || { red "npm install 失败"; exit 1; }
    fi

    mkdir -p "$SCRIPT_DIR/logs"

    # 启动后端：结构化日志由应用写 quantlab.log/error.log，stdout 丢弃避免重复，
    # stderr 收 backend.out 便于排查启动失败（import 错误等）。
    # 注意：不用 set -m（否则子 shell 是组长，setsid 会 fork 导致 $! 失效）；
    # 无组长 + exec setsid → 原地新会话，PID 即 $!，kill_tree 可精确停整个进程组。
    blue "[1/2] 启动后端 (port $BACKEND_PORT)..."
    (
        cd "$SCRIPT_DIR/backend"
        if command -v setsid >/dev/null 2>&1; then
            exec setsid "$PYTHON_BIN" -u -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" \
                </dev/null 2>>"$SCRIPT_DIR/logs/backend.out" >/dev/null
        else
            exec nohup "$PYTHON_BIN" -u -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" \
                </dev/null 2>>"$SCRIPT_DIR/logs/backend.out" >/dev/null
        fi
    ) &
    BACKEND_PID=$!
    echo "$BACKEND_PID" > "$SCRIPT_DIR/logs/backend.pid"

    echo ""
    wait_for_port "$BACKEND_PORT" "后端" 60 "http://localhost:$BACKEND_PORT/health" || {
        red "后端启动失败，查看日志: tail -f logs/quantlab.log logs/backend.out"
        exit 1
    }
    echo ""

    # 启动前端（Vite 输出收 frontend.out）
    blue "[2/2] 启动前端 (port $FRONTEND_PORT)..."
    (
        cd "$SCRIPT_DIR/frontend"
        if command -v setsid >/dev/null 2>&1; then
            exec setsid npm run dev -- --port "$FRONTEND_PORT" \
                </dev/null >>"$SCRIPT_DIR/logs/frontend.out" 2>&1
        else
            exec nohup npm run dev -- --port "$FRONTEND_PORT" \
                </dev/null >>"$SCRIPT_DIR/logs/frontend.out" 2>&1
        fi
    ) &
    FRONTEND_PID=$!
    echo "$FRONTEND_PID" > "$SCRIPT_DIR/logs/frontend.pid"

    wait_for_port "$FRONTEND_PORT" "前端" 30 || {
        red "前端启动失败，查看日志: tail -f logs/frontend.out"
        exit 1
    }

    SILENT_OK=1

    echo ""
    green "========================================="
    green "  服务已静默启动（可关闭本终端）"
    green "========================================="
    echo "  Backend:  http://localhost:$BACKEND_PORT"
    echo "  API Docs: http://localhost:$BACKEND_PORT/docs"
    echo "  Frontend: http://localhost:$FRONTEND_PORT"
    echo "  PID:      后端 $BACKEND_PID / 前端 $FRONTEND_PID (logs/*.pid)"
    echo "  停止:     ./start.sh stop"
    echo "  Logs:     logs/quantlab.log logs/error.log logs/sync.log"
    echo "            logs/backend.out(启动 stderr) logs/frontend.out(前端)"
    green "========================================="
    exit 0
}

# ============ 停止静默服务 ============
stop_services() {
    echo "正在停止 QuantLab 服务..."
    local found=0
    for pf in "$SCRIPT_DIR/logs/backend.pid" "$SCRIPT_DIR/logs/frontend.pid"; do
        [ -f "$pf" ] || continue
        local pid
        pid="$(cat "$pf" 2>/dev/null || true)"
        rm -f "$pf"
        [ -n "$pid" ] || continue
        if ps -p "$pid" >/dev/null 2>&1; then
            found=1
            kill_tree "$pid"
            yellow "  已停止 PID $pid ($(basename "$pf"))"
        else
            yellow "  PID $pid 已不存在 ($(basename "$pf"))，清理记录"
        fi
    done

    local leftovers=""
    for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
        if [ -n "$(port_pid "$port")" ]; then
            leftovers="$leftovers $port"
        fi
    done
    if [ -n "$leftovers" ]; then
        red "端口$leftovers 仍有进程占用（PID 文件可能丢失），可手动:"
        echo "  lsof -ti :$BACKEND_PORT -ti :$FRONTEND_PORT | xargs kill"
    fi

    if [ "$found" = "1" ]; then
        green "QuantLab 已停止"
    else
        yellow "未找到运行中的 QuantLab 服务（无有效 PID 文件）"
    fi
}

# ============ 模式分发 ============
case "$MODE" in
    silent)
        start_silent
        ;;
    dev)
    echo "========================================="
    echo "  QuantLab - 量化策略回测研究平台"
    echo "========================================="
    blue "Python: $PYTHON_BIN"
    echo ""

    # 端口占用检查：冲突时询问用户是否杀掉重启
    if [ -n "$(port_pid "$BACKEND_PORT")" ]; then
        if ! ask_kill_port "$BACKEND_PORT" "后端"; then
            red "已取消启动，请先释放端口 $BACKEND_PORT"
            exit 1
        fi
    fi
    if [ -n "$(port_pid "$FRONTEND_PORT")" ]; then
        if ! ask_kill_port "$FRONTEND_PORT" "前端"; then
            red "已取消启动，请先释放端口 $FRONTEND_PORT"
            exit 1
        fi
    fi

    # 后端依赖检查
    if ! "$PYTHON_BIN" -c "import uvicorn, fastapi" >/dev/null 2>&1; then
        red "未找到 uvicorn/fastapi，请安装依赖:"
        echo "  $PYTHON_BIN -m pip install -r requirements.txt"
        exit 1
    fi
    # 关键第三方库检查（避免运行时才发现缺失）
    if ! "$PYTHON_BIN" -c "import fastapi_users_db_sqlalchemy, empyrical, alphalens, tenacity, structlog, prometheus_fastapi_instrumentator, cachetools, zxcvbn" >/dev/null 2>&1; then
        red "后端依赖缺失，请先安装:"
        echo "  $PYTHON_BIN -m pip install -r requirements.txt"
        exit 1
    fi

    # 前端依赖检查
    if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
        yellow "前端依赖未安装，正在安装..."
        (cd "$SCRIPT_DIR/frontend" && npm install) || { red "npm install 失败"; exit 1; }
    fi

    # 启动后端（结构化日志由应用写入 logs/quantlab.log + logs/error.log，
    # console handler 默认同步输出到终端；不再 tee 到 backend.log，
    # 避免同一内容写三份：终端 + quantlab.log + backend.log）
    # 注：长同步任务（baostock 全量回填/EOD/repair）已迁移到独立 worker 子进程
    # （.venv/bin/python -m app.services.data.sync_worker，start_new_session 脱离本进程组），
    # 因此 --reload 触发重启时会立即退出，不会像以前那样"等待后台任务完成"而卡死。
    # 所有 worker 统一写 logs/sync.log（JSON，worker_kind 字段区分任务类型）。
    blue "[1/2] 启动后端 (port $BACKEND_PORT)..."
    mkdir -p "$SCRIPT_DIR/logs"
    set -m
    (
        cd "$SCRIPT_DIR/backend"
        "$PYTHON_BIN" -u -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT"
    ) &
    BACKEND_PID=$!
    set +m

    # 等后端健康检查通过后，再启动前端（避免前端启动时后端尚未就绪）
    echo ""
    wait_for_port "$BACKEND_PORT"  "后端" 60 "http://localhost:$BACKEND_PORT/health" || {
        red "后端启动失败，查看日志: tail -f logs/quantlab.log"
        exit 1
    }
    echo ""

    # 启动前端（Vite 输出直接到终端；前端页面错误可在浏览器 DevTools 查看）
    blue "[2/2] 启动前端 (port $FRONTEND_PORT)..."
    set -m
    (
        cd "$SCRIPT_DIR/frontend"
        npm run dev -- --port "$FRONTEND_PORT"
    ) &
    FRONTEND_PID=$!
    set +m

    # 前端只检查端口
    wait_for_port "$FRONTEND_PORT" "前端" 30 || {
        red "前端启动失败，查看上方终端输出"
        exit 1
    }
    echo ""

    green "========================================="
    green "  所有服务已启动"
    green "========================================="
    echo "  Backend:  http://localhost:$BACKEND_PORT"
    echo "  API Docs: http://localhost:$BACKEND_PORT/docs"
    echo "  Frontend: http://localhost:$FRONTEND_PORT"
    echo "  Logs:     logs/quantlab.log  logs/error.log  logs/sync.log  (前端日志页可视化查看)"
    green "========================================="
    yellow "按 Ctrl+C 停止所有服务"
    echo ""

    wait
        ;;
    stop)
        stop_services
        ;;
    *)
        echo "Usage: ./start.sh [silent|dev|stop]"
        echo "  silent - 静默启动（默认）：后台分离运行，脚本退出后终端可关闭；停止用 ./start.sh stop"
        echo "  dev    - 本地开发模式：前台运行，Ctrl+C 停止（终端不可关闭）"
        echo "  stop   - 停止静默模式启动的服务"
        exit 1
        ;;
esac