#!/bin/bash
# ============================================================================
# 统一启动脚本（逻辑基准：paper_hot/start.sh，QuantLab / Quantlerning 共用同一套逻辑）
#
# 接口：
#   ./start.sh            等同 ./start.sh start（生产模式）
#   ./start.sh start      生产模式：构建前端产物 → 后端 → 独立前端进程
#   ./start.sh dev        开发模式：后端(+可选 reload) → 前端 HMR
#   ./start.sh stop       停止本项目服务（只终止工作目录属于本项目的进程）
#   ./start.sh restart    重启（默认沿用上次模式，可 restart dev / restart prod）
#   ./start.sh status     查看运行状态
#   ./start.sh help       帮助
#
# 状态文件：<项目根>/.runtime_ports（记录 mode / 实际端口 / PID，供 stop、status、restart 使用）
# 环境变量：FORCE_BUILD=1 强制重建前端；PORT_CONFLICT=kill|shift 预置端口冲突处理；PORT_MAX_TRIES=N 顺延上限
# ============================================================================

set -e

# ─────────────────── 项目配置区（各项目唯一的差异，改这里即可复用） ───────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT_NAME="QuantLab"

BACKEND_DIR_NAME="backend"
FRONTEND_DIR_NAME="frontend"
BACKEND_MODULE="app.main:app"          # uvicorn 目标（cwd = backend/）
HEALTH_PATH="/health"                  # 后端健康检查路径

# Python 解释器候选：项目内相对路径（目录或可执行文件均可），找不到再查 PATH
PYTHON_CANDIDATES=(".venv" "$CONDA_PREFIX/bin/python" "python3")

# dev 模式是否给 uvicorn 加 --reload（本项目一直启用，保持原行为）
BACKEND_RELOAD=1

# 端口来源：PORT_ENV_FILE 里首行匹配 PORT_ENV_KEY_* 的值；文件不存在或键缺失时用默认值
# （vite.config.js 也从同一个 .env 读取这两键作为 server.port 与 /api、/ws 代理目标）
PORT_ENV_FILE=".env"
PORT_ENV_KEY_BACKEND="BACKEND_PORT"
PORT_ENV_KEY_FRONTEND="FRONTEND_PORT"
PORT_DEFAULT_BACKEND=8101
PORT_DEFAULT_FRONTEND=3001

# 固定端口：前端代理目标在 vite.config.js 构建/启动期从 .env 读取，顺延会与之失配
ALLOW_PORT_SHIFT=0

BACKEND_LOG_REL="logs/backend.out"
FRONTEND_LOG_REL="logs/frontend.out"

# dev 启动前清理的前端产物/缓存（空 = 不清理；Vite 的依赖预构建缓存会自动失效）
DEV_CLEAN_TARGET=""

# 前端命令模板（%P = 前端端口）
DEV_FRONTEND_CMD=(npm run dev -- --host 0.0.0.0 --port "%P")
PROD_FRONTEND_CMD=(npm run preview -- --host 0.0.0.0 --port "%P")
PROD_BUILD_CMD=(npm run build)
PROD_BUILD_MARKER="frontend/dist/index.html"   # 存在则跳过构建（FORCE_BUILD=1 强制重建）

# Vite 由 vite.config.js 的 proxy 代理 /api，无需注入 BACKEND_API_URL
EXPORT_BACKEND_API_URL=0
# ──────────────────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

BACKEND_LOG="$BACKEND_LOG_REL"
case "$BACKEND_LOG" in /*) ;; *) BACKEND_LOG="$PROJECT_DIR/$BACKEND_LOG" ;; esac
FRONTEND_LOG="$FRONTEND_LOG_REL"
case "$FRONTEND_LOG" in /*) ;; *) FRONTEND_LOG="$PROJECT_DIR/$FRONTEND_LOG" ;; esac
# 记录实际端口 / 进程 / 启动模式（stop、status、restart 用于定位）
RUNTIME_FILE="$PROJECT_DIR/.runtime_ports"
# 端口顺延最大次数（基础端口被占用后 +1 逐级寻找空闲端口）
PORT_MAX_TRIES=${PORT_MAX_TRIES:-100}

# ============================== 帮助 ==============================
usage() {
    echo "Usage: ./start.sh <command>"
    echo ""
    echo "Commands:"
    echo "  start      启动生产模式（默认）"
    echo "  dev        启动开发模式（热重载 / HMR）"
    echo "  stop       停止所有服务"
    echo "  restart    重启服务（默认沿用上次启动模式；可用 restart dev / restart prod 显式指定）"
    echo "  status     查看服务运行状态"
    echo "  help       -h --help  显示本帮助"
    echo ""
    echo "Examples:"
    echo "  ./start.sh           # 等同 ./start.sh start"
    echo "  ./start.sh dev"
    echo "  ./start.sh restart   # 沿用上次模式重启"
    echo "  ./start.sh restart dev"
    echo ""
    echo "端口说明："
    if [ -n "$PORT_ENV_FILE" ]; then
        echo "  默认后端 ${PORT_DEFAULT_BACKEND}、前端 ${PORT_DEFAULT_FRONTEND}（来源：${PORT_ENV_FILE}）。"
    else
        echo "  固定后端 ${PORT_DEFAULT_BACKEND}、前端 ${PORT_DEFAULT_FRONTEND}（同时写在"
        echo "  前端 vite.config 与后端 CORS 中，改端口需一并修改）。"
    fi
    if [ "$ALLOW_PORT_SHIFT" = "1" ]; then
        echo "  若端口被占用，启动时会先展示占用进程并询问处理方式：[k] kill 占用进程继续使用，"
        echo "  [n] 顺延 +1 使用新端口（${PORT_DEFAULT_FRONTEND} 被占用则尝试 $((PORT_DEFAULT_FRONTEND + 1)) 等），[q] 退出。"
        echo "  可用 PORT_CONFLICT=kill|shift 预置选择（非交互环境默认自动顺延）。"
    else
        echo "  本项目端口不自动顺延：被占用时会询问 [k] kill 占用进程或 [q] 退出；"
        echo "  也可用 PORT_CONFLICT=kill 预置（非交互环境默认放弃启动，不会自动终止进程）。"
    fi
    if [ "$EXPORT_BACKEND_API_URL" = "1" ]; then
        echo "  前端通过运行期代理把 /api 指向后端实际端口，因此后端端口变化无需重建前端。"
    fi
    echo ""
    echo "其他说明："
    echo "  - 启动前若检测到本项目的旧实例仍在运行，会先自动停止，避免双实例并存。"
    echo "  - 本机 systemd 常驻服务与本脚本共用端口；本脚本不会终止 systemd 托管的进程，"
    echo "    端口被常驻服务占用时会提示需要先停止的 unit（sudo systemctl stop <unit>）。"
    if [ -n "$DEV_CLEAN_TARGET" ]; then
        echo "  - dev 模式启动时会清空 ${DEV_CLEAN_TARGET##*/}（依赖或导入结构变更后旧产物会导致前端报错）。"
    fi
    echo "  - 健康检查为轮询等待（后端 30s / 前端 60s），替代旧的固定 sleep。"
}

# ───────────────────────── 停止服务 ─────────────────────────
# 本机 systemd 常驻服务与 start.sh 手动启动的服务共用同一批端口（见 README/部署说明）。
# start.sh 一律不触碰 systemd 托管的进程：它们由 Restart=always 守护，杀掉只会被立刻拉起、
# 与 start.sh 来回拉锯；需要释放端口时由用户显式 `sudo systemctl stop <unit>`。
systemd_unit_of() {
    local pid="$1"
    sed -n 's#.*/system\.slice/\([^/]*\.service\)$#\1#p' "/proc/$pid/cgroup" 2>/dev/null | head -1
}

# 端口占用者是 systemd 常驻服务时，返回 " [systemd: <unit>]" 供 status 提示
port_owner_note() {
    local port="$1" pid unit
    pid=$(lsof -t -i:"$port" 2>/dev/null | head -1)
    if [ -n "$pid" ]; then
        unit=$(systemd_unit_of "$pid")
        if [ -n "$unit" ]; then
            echo " [systemd: ${unit}]"
        fi
    fi
    return 0
}

# 仅当进程工作目录位于本项目内、且不由 systemd 托管时才终止（防止误杀其他项目或常驻服务）
# 启动时经 setsid 创建独立进程组，故先按进程组终止（可连带清理 npm/next/uvicorn 子进程树）
# 先发 SIGTERM 让 SQLite 有机会关闭 WAL 日志并刷盘，超时后才 SIGKILL 强制终止
kill_project_pids() {
    local pids="$1" name="$2"
    local pid cwd pgid unit pids_to_kill="" waited

    # 第一阶段：筛选属于本项目、且非 systemd 托管的进程
    for pid in $pids; do
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
        case "$cwd" in
            "$PROJECT_DIR"/*)
                unit=$(systemd_unit_of "$pid")
                if [ -n "$unit" ]; then
                    echo -e "   ${YELLOW}跳过 $name (PID: $pid)：由 systemd 托管（${unit}），需 sudo systemctl stop ${unit}${NC}"
                    continue
                fi
                pids_to_kill="$pids_to_kill $pid"
                ;;
        esac
    done

    # 第二阶段：SIGTERM（先杀进程组再杀进程本身；pgid==pid 说明该进程是 setsid 会话组长）
    for pid in $pids_to_kill; do
        pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
        if [ -n "$pgid" ] && [ "$pgid" = "$pid" ] && [ "$pgid" != "$$" ]; then
            kill -TERM -- "-$pgid" 2>/dev/null || true
        fi
        kill -TERM "$pid" 2>/dev/null || true
    done

    # 第三阶段：等待优雅退出（最多 5 秒）
    waited=0
    for pid in $pids_to_kill; do
        while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 5 ]; do
            sleep 1
            waited=$((waited + 1))
        done
    done

    # 第四阶段：仍未退出的强制终止
    for pid in $pids_to_kill; do
        pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
        if kill -0 "$pid" 2>/dev/null; then
            if [ -n "$pgid" ] && [ "$pgid" = "$pid" ] && [ "$pgid" != "$$" ]; then
                kill -KILL -- "-$pgid" 2>/dev/null || true
            fi
            kill -KILL "$pid" 2>/dev/null || true
        fi
        echo "   $name stopped (PID: $pid)"
    done
}

stop_services() {
    echo "🛑 Stopping ${PROJECT_NAME}..."
    load_ports
    load_runtime_ports

    # 1) 终止上次启动记录的进程（.runtime_ports 中记录的 PID）
    if [ -f "$RUNTIME_FILE" ]; then
        kill_project_pids "$(grep -E '^backend_pid=' "$RUNTIME_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' \r')" "Backend"
        kill_project_pids "$(grep -E '^frontend_pid=' "$RUNTIME_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' \r')" "Frontend"
    fi

    # 2) 兜底：按端口清理（同样校验属于本项目）
    kill_project_pids "$(lsof -t -i:$BACKEND_PORT 2>/dev/null || true)" "Backend(port $BACKEND_PORT)"
    kill_project_pids "$(lsof -t -i:$FRONTEND_PORT 2>/dev/null || true)" "Frontend(port $FRONTEND_PORT)"

    rm -f "$RUNTIME_FILE"
    echo ""
    echo "✅ ${PROJECT_NAME} has been stopped"
}

# ───────── 启动前自动清理本项目的旧实例 ─────────
# 背景：旧实例未停时再次 start 会端口顺延另起新进程，导致双实例并存、
# 浏览器连到陈旧的 dev server（典型症状：Loading CSS chunk ... failed）。
# 仅清理「记录在案且确实存活」的进程；记录的进程均已退出时只清掉过期记录。
stop_stale_instance() {
    [ -f "$RUNTIME_FILE" ] || return 0
    local bp fp pid alive=""
    bp=$(grep -E '^backend_pid=' "$RUNTIME_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
    fp=$(grep -E '^frontend_pid=' "$RUNTIME_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
    for pid in $bp $fp; do
        kill -0 "$pid" 2>/dev/null && alive="$alive $pid"
    done
    if [ -z "$alive" ]; then
        # 记录的进程均已退出：仅清理过期记录
        rm -f "$RUNTIME_FILE"
        return 0
    fi
    echo -e "${YELLOW}♻️  检测到本项目的旧实例仍在运行 (PID:${alive})，先自动停止以避免双实例${NC}"
    kill_project_pids "$bp" "Backend(stale)"
    kill_project_pids "$fp" "Frontend(stale)"
    # 兜底：端口上的占用者。uvicorn --reload / --workers 的子进程、npm 派生的 vite/next
    # 都不在记录里，只清记录会导致随后的端口冲突。
    kill_project_pids "$(lsof -t -i:$BACKEND_PORT 2>/dev/null || true)" "Backend(port $BACKEND_PORT)"
    kill_project_pids "$(lsof -t -i:$FRONTEND_PORT 2>/dev/null || true)" "Frontend(port $FRONTEND_PORT)"
    rm -f "$RUNTIME_FILE"
    sleep 1
}

# ───────────────────────── 查看状态 ─────────────────────────
status_services() {
    echo "📊 ${PROJECT_NAME} service status:"
    load_ports
    load_runtime_ports
    local mode
    mode=$(grep -E '^mode=' "$RUNTIME_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
    mode=${mode:-unknown}
    echo "   Last start mode: ${mode}"
    echo ""
    if lsof -t -i:$BACKEND_PORT >/dev/null 2>&1; then
        echo -e "   Backend  (port $BACKEND_PORT): ${GREEN}RUNNING${NC} (PID: $(lsof -t -i:$BACKEND_PORT | tr '\n' ' '))$(port_owner_note "$BACKEND_PORT")"
    else
        echo -e "   Backend  (port $BACKEND_PORT): ${RED}STOPPED${NC}"
    fi
    if lsof -t -i:$FRONTEND_PORT >/dev/null 2>&1; then
        echo -e "   Frontend (port $FRONTEND_PORT): ${GREEN}RUNNING${NC} (PID: $(lsof -t -i:$FRONTEND_PORT | tr '\n' ' '))$(port_owner_note "$FRONTEND_PORT")"
    else
        echo -e "   Frontend (port $FRONTEND_PORT): ${RED}STOPPED${NC}"
    fi
}

# ───────────────────────── 启动服务（统一实现） ─────────────────────────
# prod 与 dev 仅前端行为不同（构建产物 + 独立前端进程 vs HMR），后端启动逻辑完全一致。
resolve_python() {
    local cand path
    for cand in "${PYTHON_CANDIDATES[@]}"; do
        [ -n "$cand" ] || continue
        path="$cand"
        case "$path" in /*) ;; *) path="$PROJECT_DIR/$path" ;; esac
        # 项目内的虚拟环境目录 → 取其 bin/python
        if [ -x "$path/bin/python" ]; then
            PYTHON_BIN="$path/bin/python"
            return 0
        fi
        # 项目内的可执行文件（如 .venv/bin/python、./python）
        if [ -x "$path" ] && [ ! -d "$path" ]; then
            PYTHON_BIN="$path"
            return 0
        fi
        # 裸命令名（如 python3）→ 查 PATH
        case "$cand" in
            */*) ;;
            *)
                if command -v "$cand" >/dev/null 2>&1; then
                    PYTHON_BIN="$(command -v "$cand")"
                    return 0
                fi
                ;;
        esac
    done
    return 1
}

require_python() {
    if ! resolve_python; then
        echo -e "${RED}Error: 未找到可用的 Python 解释器（候选：${PYTHON_CANDIDATES[*]}）${NC}" >&2
        echo -e "${RED}       请先按项目文档创建虚拟环境并安装依赖${NC}" >&2
        exit 1
    fi
}

# 分离启动并取回真实 PID（结果写入 LAUNCH_PID）。
# 注意：本机 shell 开了作业控制，后台进程本身即进程组长，此时 setsid 会先 fork 再设新会话，
# 所以 `setsid ... &` 的 $! 只是随即退出的中间层，不能作为服务 PID（会导致 stop 找不到进程）。
# 这里改由内层 shell 先写 PID 再 exec（exec 不换 PID），取到的就是真正的服务进程，
# 同时也是新的会话/进程组长，便于事后按进程组整体终止。
# 参数：<日志文件> <命令...>
launch_detached() {
    local log="$1"
    shift
    local pidfile pid="" i=0
    mkdir -p "$(dirname "$log")"
    pidfile=$(mktemp -t "${PROJECT_NAME// /-}.XXXXXX") || return 1
    setsid nohup bash -c 'echo $$ > "$1"; shift; exec "$@"' _ "$pidfile" "$@" > "$log" 2>&1 &
    while [ "$i" -lt 25 ]; do
        if [ -s "$pidfile" ]; then
            pid=$(tr -d ' \r' < "$pidfile")
            break
        fi
        sleep 0.2
        i=$((i + 1))
    done
    rm -f "$pidfile"
    [ -n "$pid" ] || return 1
    LAUNCH_PID="$pid"
    return 0
}

start_backend() {
    echo "📦 Starting backend server..."
    cd "$PROJECT_DIR/$BACKEND_DIR_NAME"
    require_python
    local -a cmd=("$PYTHON_BIN" -m uvicorn "$BACKEND_MODULE" --host 0.0.0.0 --port "$BACKEND_PORT")
    if [ "$BACKEND_RELOAD" = "1" ]; then
        cmd+=("--reload")
    fi
    if ! launch_detached "$BACKEND_LOG" "${cmd[@]}"; then
        echo -e "${RED}Error: 后端启动失败（未取到进程 PID），请检查 ${BACKEND_LOG_REL}${NC}" >&2
        exit 1
    fi
    echo "backend_pid=${LAUNCH_PID}" >> "$RUNTIME_FILE"
    echo "   Backend started (PID: ${LAUNCH_PID})"
}

# 生产模式：先构建前端产物（失败则不启动任何服务），再起后端与前端
ensure_prod_build() {
    if [ -n "$PROD_BUILD_MARKER" ] && [ -f "$PROJECT_DIR/$PROD_BUILD_MARKER" ] && [ -z "${FORCE_BUILD:-}" ]; then
        echo "   Skip build (existing $PROD_BUILD_MARKER found, set FORCE_BUILD=1 to rebuild)"
        return 0
    fi
    cd "$PROJECT_DIR/$FRONTEND_DIR_NAME"
    echo "🔨 Building frontend (production)..."
    if ! "${PROD_BUILD_CMD[@]}"; then
        echo -e "${RED}Error: 前端构建失败，已中止启动（未启动任何服务）${NC}" >&2
        rm -f "$RUNTIME_FILE"
        return 1
    fi
}

# 前端命令模板里的 %P 替换为实际端口后执行
run_frontend_cmd() {
    local log="$1"
    shift
    local -a cmd=()
    local arg
    for arg in "$@"; do
        cmd+=("${arg//%P/$FRONTEND_PORT}")
    done
    if ! launch_detached "$log" "${cmd[@]}"; then
        echo -e "${RED}Error: 前端启动失败（未取到进程 PID），请检查 ${FRONTEND_LOG_REL}${NC}" >&2
        exit 1
    fi
    echo "frontend_pid=${LAUNCH_PID}" >> "$RUNTIME_FILE"
    echo "   Frontend started (PID: ${LAUNCH_PID})"
}

start_frontend_dev() {
    cd "$PROJECT_DIR/$FRONTEND_DIR_NAME"
    if [ -n "$DEV_CLEAN_TARGET" ]; then
        rm -rf "$PROJECT_DIR/$DEV_CLEAN_TARGET"
    fi
    echo ""
    echo "📱 Starting frontend server (dev with HMR)..."
    run_frontend_cmd "$FRONTEND_LOG" "${DEV_FRONTEND_CMD[@]}"
}

start_frontend_prod() {
    cd "$PROJECT_DIR/$FRONTEND_DIR_NAME"
    echo ""
    echo "📱 Starting frontend server (production)..."
    run_frontend_cmd "$FRONTEND_LOG" "${PROD_FRONTEND_CMD[@]}"
}

start_services() {
    local mode="${1:-prod}"
    local label="Production" color="$GREEN"
    if [ "$mode" = "dev" ]; then
        label="Development"; color="$YELLOW"
    fi
    echo -e "🚀 Starting ${PROJECT_NAME} (${color}${label}${NC} Mode)..."

    load_ports
    stop_stale_instance
    resolve_ports "$mode"
    echo "   Ports: backend=${BACKEND_PORT}, frontend=${FRONTEND_PORT}"

    if [ "$mode" != "dev" ]; then
        ensure_prod_build || exit 1
    fi

    start_backend

    # 等后端健康后再启前端：避免前端先就绪、浏览器打开即吃到代理 500（socket hang up）
    wait_for_http "http://localhost:${BACKEND_PORT}${HEALTH_PATH}" "Backend" "${BACKEND_LOG_REL}" 30 || true

    if [ "$mode" = "dev" ]; then
        start_frontend_dev
        echo -e "${YELLOW}⚠️  DEV MODE: Hot reload enabled, not for production use${NC}"
    else
        start_frontend_prod
    fi

    print_urls "$label"
    health_check
}

# ───────────────────────── 重启服务 ─────────────────────────
# 默认沿用上次启动模式（.runtime_ports 中的 mode= 记录，缺省 prod）；
# 也可显式指定：./start.sh restart dev | ./start.sh restart prod
restart_services() {
    local target="${1:-}"
    if [ -z "$target" ]; then
        target=$(grep -E '^mode=' "$RUNTIME_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
        target=${target:-prod}
    fi
    stop_services
    echo ""
    if [ "$target" = "dev" ]; then
        start_services dev
    else
        start_services prod
    fi
}

# ───────────────────────── 端口处理 ─────────────────────────
# ───────── 从 PORT_ENV_FILE 读取端口配置（配置缺失时用默认值） ─────────
load_ports() {
    BACKEND_PORT=""
    FRONTEND_PORT=""
    if [ -n "$PORT_ENV_FILE" ] && [ -f "$PROJECT_DIR/$PORT_ENV_FILE" ]; then
        local env_file="$PROJECT_DIR/$PORT_ENV_FILE"
        BACKEND_PORT=$(grep -E "^${PORT_ENV_KEY_BACKEND}=" "$env_file" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
        FRONTEND_PORT=$(grep -E "^${PORT_ENV_KEY_FRONTEND}=" "$env_file" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
    fi
    BACKEND_PORT=${BACKEND_PORT:-$PORT_DEFAULT_BACKEND}
    FRONTEND_PORT=${FRONTEND_PORT:-$PORT_DEFAULT_FRONTEND}
}

# ───────── 仅 stop/status 使用：采用上次实际运行端口（仅当该端口确有进程监听时才采信） ─────────
load_runtime_ports() {
    [ -f "$RUNTIME_FILE" ] || return 0
    local rb rf
    rb=$(grep -E '^backend_port=' "$RUNTIME_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
    rf=$(grep -E '^frontend_port=' "$RUNTIME_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' \r')
    if [ -n "$rb" ] && port_in_use "$rb"; then
        BACKEND_PORT=$rb
    fi
    if [ -n "$rf" ] && port_in_use "$rf"; then
        FRONTEND_PORT=$rf
    fi
}

# 判定端口当前是否有进程监听
port_in_use() {
    lsof -t -i:"$1" >/dev/null 2>&1
}

# 交互式处理端口冲突：让用户决定 kill 占用进程、（允许时）顺延新端口或退出
# 返回值：0 = 端口已释放可继续使用；1 = 放弃启动；2 = 顺延下一个端口（仅 ALLOW_PORT_SHIFT=1）
# 可用 PORT_CONFLICT=kill|shift 预置选择
handle_port_conflict() {
    local port="$1" name="$2"
    local choice pid_list pid_display
    local conflict_mode="${PORT_CONFLICT:-ask}"

    # 每行一个 PID（不转成空格分隔，避免 zsh/bash 对未加引号变量分词行为不一致）
    pid_list=$(lsof -t -i:"$port" 2>/dev/null)
    pid_display=$(echo "$pid_list" | tr '\n' ' ')

    echo "" >&2
    echo -e "${YELLOW}⚠️  ${name} 端口 ${port} 已被占用${NC}" >&2
    lsof -i:"$port" 2>/dev/null | tail -n +2 | sed 's/^/     /' >&2
    # 占用者若由 systemd 托管（Restart=always），kill 只会被立刻拉回，故直接放弃并给出该停的 unit
    local oc_pid oc_unit oc_units=""
    for oc_pid in $pid_list; do
        oc_unit=$(systemd_unit_of "$oc_pid")
        if [ -n "$oc_unit" ]; then
            case " $oc_units " in
                *" $oc_unit "*) ;;
                *) oc_units="$oc_units $oc_unit" ;;
            esac
        fi
    done
    if [ -n "$oc_units" ]; then
        echo "" >&2
        echo -e "${RED}   端口 ${port} 由 systemd 常驻服务占用，start.sh 不会终止它。${NC}" >&2
        echo -e "${RED}   需要手动调试请先释放端口：${NC}" >&2
        for oc_unit in $oc_units; do
            echo -e "${RED}     sudo systemctl stop ${oc_unit}${NC}" >&2
        done
        echo -e "${YELLOW}   （长期改由 start.sh 管理：sudo systemctl disable --now <unit>；"
        echo -e "     调试完恢复常驻：sudo systemctl start <unit>）${NC}" >&2
        return 1
    fi

    if [ "$ALLOW_PORT_SHIFT" = "1" ]; then
        case "$conflict_mode" in
            kill)
                choice="k" ;;
            shift|new)
                choice="n" ;;
            *)
                if [ ! -t 0 ]; then
                    echo -e "${YELLOW}   非交互环境（无终端），默认顺延新端口；可用 PORT_CONFLICT=kill|shift 预设行为${NC}" >&2
                    choice="n"
                else
                    while true; do
                        echo "" >&2
                        echo "   请选择处理方式：" >&2
                        echo "     [k] kill 占用进程，继续使用该端口" >&2
                        echo "     [n] 顺延使用新端口（+1 递增）" >&2
                        echo "     [q] 退出启动" >&2
                        read -r -p "   请输入 [k/n/q]: " choice
                        case "$choice" in
                            [kKnNqQ]) break ;;
                            *) echo -e "${RED}   无效输入，请输入 k / n / q${NC}" >&2 ;;
                        esac
                    done
                    choice=$(echo "$choice" | tr 'A-Z' 'a-z')
                fi
                ;;
        esac
    else
        # 固定端口：顺延会与前端写死的端口/代理配置失配，故不提供顺延
        case "$conflict_mode" in
            kill)
                choice="k" ;;
            *)
                if [ ! -t 0 ]; then
                    echo -e "${RED}   本项目的端口为固定端口，非交互环境不会自动终止占用进程；${NC}" >&2
                    echo -e "${RED}   请先释放端口 ${port}，或设 PORT_CONFLICT=kill 明确授权终止${NC}" >&2
                    return 1
                else
                    while true; do
                        echo "" >&2
                        echo "   请选择处理方式：" >&2
                        echo "     [k] kill 占用进程，继续使用该端口" >&2
                        echo "     [q] 退出启动（端口固定，不支持顺延）" >&2
                        read -r -p "   请输入 [k/q]: " choice
                        case "$choice" in
                            [kKqQ]) break ;;
                            *) echo -e "${RED}   无效输入，请输入 k / q${NC}" >&2 ;;
                        esac
                    done
                    choice=$(echo "$choice" | tr 'A-Z' 'a-z')
                fi
                ;;
        esac
    fi

    case "$choice" in
        k)
            echo -e "   Killing process(es) on port $port: ${pid_display:-无}" >&2
            # 先优雅终止，未成功再强制
            for pid in $pid_list; do
                kill "$pid" 2>/dev/null || true
            done
            sleep 1
            for pid in $pid_list; do
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                fi
            done
            if port_in_use "$port"; then
                echo -e "${RED}Error: ${name} 端口 ${port} 的占用进程无法终止${NC}" >&2
                return 1
            fi
            echo -e "${GREEN}   端口 ${port} 已释放${NC}" >&2
            return 0
            ;;
        n)
            return 2
            ;;
        q)
            echo -e "${RED}Aborted by user${NC}" >&2
            return 1
            ;;
    esac
}

# 解析可用端口：基础端口可用则直接使用；被占用时按 handle_port_conflict 的结果处理
# （ALLOW_PORT_SHIFT=0 时不会 +1，顺延分支不可达）
next_free_port() {
    local base="$1" name="$2"
    local port="$base"
    local n="$PORT_MAX_TRIES"
    local i=0
    while port_in_use "$port"; do
        i=$((i + 1))
        if [ "$i" -gt "$n" ]; then
            echo -e "${RED}Error: ${name} 端口冲突处理超过 ${n} 次仍不可用${NC}" >&2
            return 1
        fi
        if [ "$port" -eq "$base" ]; then
            handle_port_conflict "$port" "$name"
            case $? in
                1) return 1 ;;
                2) port=$((port + 1)) ;;   # 顺延（仅 ALLOW_PORT_SHIFT=1 时可能返回）
            esac
            continue
        fi
        if [ "$ALLOW_PORT_SHIFT" != "1" ]; then
            echo -e "${RED}Error: ${name} 端口固定为 ${base}，不可顺延${NC}" >&2
            return 1
        fi
        port=$((port + 1))
    done
    if [ "$port" -ne "$base" ]; then
        echo -e "${YELLOW}⚠️  ${name} 端口 ${base} 已被占用，顺延使用空闲端口 ${port}${NC}" >&2
    fi
    echo "$port"
    return 0
}

# 仅用于启动流程：按顺序解析实际可用端口，并导出前端运行时所需的后端地址
# mode 参数（dev|prod）写入 .runtime_ports，供 restart 沿用上次模式
resolve_ports() {
    local mode="${1:-prod}"
    BACKEND_PORT=$(next_free_port "$BACKEND_PORT" "backend") || exit 1
    FRONTEND_PORT=$(next_free_port "$FRONTEND_PORT" "frontend") || exit 1
    if [ "$EXPORT_BACKEND_API_URL" = "1" ]; then
        # 前端（Next rewrites）在运行期据此代理 /api，仅需注入后端实际地址
        export BACKEND_API_URL="http://localhost:${BACKEND_PORT}"
    fi
    # 记录实际端口与模式，供本次启动后的 stop / status / restart 使用
    cat > "$RUNTIME_FILE" <<EOF
mode=${mode}
backend_port=${BACKEND_PORT}
frontend_port=${FRONTEND_PORT}
EOF
}

print_urls() {
    MODE=$1
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "✅ ${PROJECT_NAME} ${MODE} is running!"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "📱 Frontend:  http://localhost:${FRONTEND_PORT}"
    echo "🔧 Backend:   http://localhost:${BACKEND_PORT}"
    echo "📚 API Docs:  http://localhost:${BACKEND_PORT}/docs"
    echo ""
    echo "To stop:  ./start.sh stop"
    echo "Status:   ./start.sh status"
    echo ""
}

# ───────── 就绪等待（轮询重试，替代固定 sleep） ─────────
# dev 首次编译、后端 reload 都可能超过固定等待时长；轮询直至就绪或超时。
# 返回 0 = 就绪；1 = 超时（调用方用 || true 兜底，不影响 set -e）
wait_for_http() {
    local url="$1" name="$2" log_hint="$3" tries="${4:-30}"
    local i=0
    while [ "$i" -lt "$tries" ]; do
        if curl -sf --max-time 5 -o /dev/null "$url" 2>/dev/null; then
            echo -e "${GREEN}✅ ${name} 就绪 (${url})${NC}"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    echo -e "${RED}⚠️  ${name} 在 ${tries}s 内未就绪（${url}），请检查 ${log_hint}${NC}"
    return 1
}

# 健康检查：后端 /health + 前端首页，均带重试等待
health_check() {
    wait_for_http "http://localhost:${BACKEND_PORT}${HEALTH_PATH}" "Backend" "${BACKEND_LOG_REL}" 30 || true
    wait_for_http "http://localhost:${FRONTEND_PORT}" "Frontend" "${FRONTEND_LOG_REL}" 60 || true
}

# ───────────────────────── 命令分发 ─────────────────────────
COMMAND="${1:-start}"

case "$COMMAND" in
    start)
        start_services prod
        ;;
    dev)
        start_services dev
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services "${2:-}"
        ;;
    status)
        status_services
        ;;
    help|-h|--help)
        usage
        ;;
    *)
        echo -e "${RED}Unknown command: $COMMAND${NC}"
        echo ""
        usage
        exit 1
        ;;
esac
