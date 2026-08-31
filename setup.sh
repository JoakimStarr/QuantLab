#!/bin/bash
# QuantLab 环境引导脚本：从干净机器 git clone 后执行一次
# 用法: ./setup.sh
#
# 本脚本仅负责 Python venv + 前端 npm 依赖 + 数据目录
# PostgreSQL 数据库需用户自行安装配置，详见 docs/QUICKSTART.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ============ 颜色输出 ============
red()    { echo -e "\033[31m$*\033[0m"; }
green()  { echo -e "\033[32m$*\033[0m"; }
yellow() { echo -e "\033[33m$*\033[0m"; }
blue()   { echo -e "\033[34m$*\033[0m"; }

# 交互确认：./setup.sh -y 或 SETUP_YES=1 时自动确认（非交互/CI 用）
AUTO_YES=0
if [ "$#" -gt 0 ] && { [ "$1" = "-y" ] || [ "$1" = "--yes" ]; }; then
    AUTO_YES=1
fi
[ -n "${SETUP_YES:-}" ] && AUTO_YES=1

confirm() {
    [ "$AUTO_YES" = "1" ] && return 0
    local msg="$1" ans
    read -r -p "$msg (y/N) " ans || ans="n"
    case "$ans" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

# ============ Python 版本检测 ============
# pyqlib 在 PyPI 上的所有版本均要求 Python < 3.13（最新约束 >=3.9,<3.13），
# 因此这里只接受 3.11/3.12，优先 3.11（项目钉定版本）；3.13/3.14 会装不上 pyqlib。
PYTHON_BIN=""
PYTHON_VERSION_STR=""

blue "[1/8] 检测 Python 解释器 (3.11/3.12，pyqlib 不支持 3.13+)..."

for cand in python3.11 python3.12 python3; do
    if ! command -v "$cand" >/dev/null 2>&1; then
        continue
    fi
    PY_VER="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
    if [ "$PY_VER" = "0.0" ]; then
        continue
    fi
    PY_MAJ="${PY_VER%%.*}"
    PY_MIN="${PY_VER#*.}"
    if [ "$PY_MAJ" -lt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 11 ]; }; then
        red "  ✗ $cand 版本 $PY_VER 过低（需 >= 3.11）"
        continue
    fi
    if [ "$PY_MAJ" -gt 3 ] || { [ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -gt 12 ]; }; then
        red "  ✗ $cand 版本 $PY_VER 过新（pyqlib 最高支持 3.12）"
        continue
    fi
    PYTHON_BIN="$(command -v "$cand")"
    PYTHON_VERSION_STR="$PY_VER"
    green "  ✓ $cand $PY_VER ($PYTHON_BIN)"
    break
done

if [ -z "$PYTHON_BIN" ]; then
    if command -v uv >/dev/null 2>&1; then
        yellow "  · 未找到系统 Python 3.11/3.12，将使用 uv 托管 Python 3.11（uv venv 会自动下载/复用缓存）"
    else
        red "未找到兼容的 Python（pyqlib 仅支持 3.11/3.12，不支持 3.13+）:"
        echo "  系统 python3 当前版本: $(python3 --version 2>/dev/null || echo '未知')"
        echo "  建议安装:"
        echo "    Ubuntu/WSL: sudo apt install python3.11 python3.11-venv"
        echo "    或 uv:      uv python install 3.11"
        exit 1
    fi
fi

# ============ 创建 venv（缺失/损坏时询问重建） ============
blue "[2/8] 创建虚拟环境 .venv..."

NEED_VENV_CREATE=0
if [ ! -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    yellow "  · 未检测到 .venv 虚拟环境"
    if confirm "是否现在创建 .venv 并安装依赖？"; then
        NEED_VENV_CREATE=1
    else
        red "  ✗ 用户取消，无法继续"
        exit 1
    fi
else
    VENV_VER="$("$SCRIPT_DIR/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
    VENV_MAJ="${VENV_VER%%.*}"
    VENV_MIN="${VENV_VER#*.}"
    VENV_BAD=0
    if [ "$VENV_VER" = "0.0" ] || [ "$VENV_MAJ" -lt 3 ] \
       || { [ "$VENV_MAJ" -eq 3 ] && [ "$VENV_MIN" -gt 12 ]; }; then
        VENV_BAD=1
        red "  ✗ .venv Python $VENV_VER 不兼容（pyqlib 仅支持 3.11/3.12）"
    fi
    if [ "$VENV_BAD" = "0" ] && ! "$SCRIPT_DIR/.venv/bin/python" -c "import pyqlib" >/dev/null 2>&1; then
        VENV_BAD=1
        yellow "  · .venv 已存在但 pyqlib 未安装（环境不完整或从未装过依赖）"
    fi
    if [ "$VENV_BAD" = "0" ]; then
        green "  ✓ .venv Python $VENV_VER 可用"
    elif confirm "检测到 .venv 不可用，是否重建（rm -rf .venv + 重装依赖）？"; then
        NEED_VENV_CREATE=1
    else
        yellow "  · 跳过重建，继续使用现有 .venv（后续安装/启动可能失败）"
    fi
fi

if [ "$NEED_VENV_CREATE" = "1" ]; then
    if [ -e "$SCRIPT_DIR/.venv" ]; then
        yellow "  · 删除旧 .venv..."
        rm -rf "$SCRIPT_DIR/.venv"
    fi
    if command -v uv >/dev/null 2>&1; then
        yellow "  · 用 uv 创建 Python 3.11 venv（自动复用/下载缓存）..."
        if ! uv venv --python 3.11 "$SCRIPT_DIR/.venv"; then
            red "  ✗ uv venv 创建失败"
            exit 1
        fi
    else
        if [ -z "$PYTHON_BIN" ]; then
            red "  ✗ 无 uv 且无系统 Python 3.11/3.12，无法创建 venv"
            exit 1
        fi
        yellow "  · 用 $PYTHON_BIN 创建 venv..."
        "$PYTHON_BIN" -m venv "$SCRIPT_DIR/.venv" || {
            red "  ✗ venv 创建失败，可能缺少 python3-venv 包"
            echo "  Ubuntu/WSL: sudo apt install python3.11-venv"
            exit 1
        }
    fi
    green "  ✓ .venv 已创建"
fi

PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"

# ============ 升级 pip ============
blue "[3/8] 升级 pip / setuptools / wheel..."
if command -v uv >/dev/null 2>&1; then
    green "  ✓ 使用 uv 安装（跳过 pip 升级）"
else
    if "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1; then
        green "  ✓ pip 就绪"
    else
        yellow "  · pip 升级失败（网络问题？），继续使用内置版本"
    fi
fi

# ============ 安装 Python 依赖 ============
blue "[4/8] 安装 Python 依赖 (requirements.txt + requirements-dev.txt)..."
echo "  · 这一步可能耗时 5-15 分钟（pyqlib/lightgbm 等需编译）"
if command -v uv >/dev/null 2>&1; then
    if uv pip install --python "$PYTHON_BIN" -r requirements.txt -r requirements-dev.txt; then
        green "  ✓ Python 依赖安装完成 (uv)"
    else
        red "  ✗ uv pip install 失败，常见原因:"
        echo "  1) 缺少编译工具: sudo apt install build-essential"
        echo "  2) 网络问题: uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt"
        exit 1
    fi
else
    if "$PYTHON_BIN" -m pip install -r requirements.txt -r requirements-dev.txt; then
        green "  ✓ Python 依赖安装完成"
    else
        red "  ✗ pip install 失败，常见原因:"
        echo "  1) 缺少编译工具: sudo apt install build-essential"
        echo "  2) 网络问题: pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt"
        echo "  3) lightgbm 编译失败: sudo apt install cmake"
        exit 1
    fi
fi

# ============ 校验关键依赖 ============
blue "[5/8] 校验关键依赖可导入..."
CRITICAL_DEPS="uvicorn fastapi sqlalchemy pyqlib baostock akshare alphalens lightgbm gplearn sklearn"
MISSING=""
for mod in $CRITICAL_DEPS; do
    if ! "$PYTHON_BIN" -c "import $mod" >/dev/null 2>&1; then
        MISSING="$MISSING $mod"
    fi
done
if [ -n "$MISSING" ]; then
    yellow "  · 以下依赖导入失败（可能仍可用，但建议排查）:$MISSING"
else
    green "  ✓ 关键依赖全部可导入"
fi

# ============ Node.js / npm 检测 ============
blue "[6/8] 检测 Node.js / npm (>= 18)..."
if ! command -v node >/dev/null 2>&1; then
    red "  ✗ 未找到 node，请安装 Node.js >= 18:"
    echo "  推荐 nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash"
    echo "           nvm install --lts"
    echo "  或 apt:   sudo apt install nodejs npm"
    exit 1
fi
NODE_VERSION="$(node -v | sed 's/v//')"
NODE_MAJOR="$(echo "$NODE_VERSION" | cut -d. -f1)"
if [ "$NODE_MAJOR" -lt 18 ]; then
    red "  ✗ Node.js 版本 $NODE_VERSION 低于 18，请升级"
    exit 1
fi
green "  ✓ Node.js $NODE_VERSION / npm $(npm -v)"

# ============ 前端依赖 ============
blue "[7/8] 安装前端依赖 (frontend/npm install)..."
if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
    (cd "$SCRIPT_DIR/frontend" && npm install) || {
        red "  ✗ npm install 失败，可尝试:"
        echo "  cd frontend && npm install --registry=https://registry.npmmirror.com"
        exit 1
    }
    green "  ✓ 前端依赖安装完成"
else
    yellow "  · frontend/node_modules 已存在，跳过（如需重装请先删除）"
fi

# ============ 数据目录 + .env ============
blue "[8/8] 创建数据目录与配置文件..."
mkdir -p "$SCRIPT_DIR/data/raw" \
         "$SCRIPT_DIR/data/processed" \
         "$SCRIPT_DIR/data/qlib_bin/cn_data" \
         "$SCRIPT_DIR/models" \
         "$SCRIPT_DIR/logs"
green "  ✓ 数据目录已创建 (data/, models/, logs/)"

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    yellow "  · 已从 .env.example 复制 .env，请编辑填入实际配置"
else
    green "  ✓ .env 已存在"
fi

# ============ 完成 ============
echo ""
green "========================================="
green "  QuantLab 环境引导完成"
green "========================================="
echo ""
echo "下一步:"
echo "  1. 安装并配置 PostgreSQL（详见 docs/QUICKSTART.md）"
echo "  2. 编辑 .env 填入:"
echo "     - AI Provider API Key (GLM/SiliconFlow/OpenCodeZen 三选一)"
echo "     - POSTGRES_PASSWORD (你的 PostgreSQL 密码)"
echo "     - SECRET_KEY (生产环境务必修改)"
echo "  3. 启动服务: ./start.sh"
echo "  4. 访问: http://localhost:$(sed -n 's/^[[:space:]]*FRONTEND_PORT=//p' .env 2>/dev/null | tail -n1 | tr -d '[:space:]' | grep -E '^[0-9]+$' || echo 3001)"
echo ""
echo "文档:"
echo "  快速开始:     docs/QUICKSTART.md"
echo "  生产部署:     docs/DEPLOY.md"
echo "  数据层说明:   docs/DATA_LAYER.md"
echo "  开发指南:     docs/DEVELOPMENT.md"
echo ""
