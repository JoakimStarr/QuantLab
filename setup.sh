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

# ============ Python 版本检测 ============
PYTHON_MIN_VERSION="3.11"
PYTHON_BIN=""

blue "[1/8] 检测 Python 解释器 (>= $PYTHON_MIN_VERSION)..."

if command -v python3 >/dev/null 2>&1; then
    PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
    PY_OK="$(python3 -c "import sys; print(1 if sys.version_info >= (3, 11) else 0)" 2>/dev/null || echo 0)"
    if [ "$PY_OK" = "1" ]; then
        PYTHON_BIN="$(command -v python3)"
        green "  ✓ python3 $PY_VERSION ($PYTHON_BIN)"
    else
        red "  ✗ python3 版本 $PY_VERSION 低于 $PYTHON_MIN_VERSION"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    red "未找到 Python >= $PYTHON_MIN_VERSION，请先安装:"
    echo "  Ubuntu/WSL: sudo apt update && sudo apt install python3 python3-venv python3-dev"
    echo "  macOS:      brew install python@3.12"
    echo "  或使用 pyenv: pyenv install 3.11 && pyenv local 3.11"
    exit 1
fi

# ============ 创建 venv ============
blue "[2/8] 创建虚拟环境 .venv..."
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    yellow "  · .venv 已存在，跳过创建"
else
    "$PYTHON_BIN" -m venv .venv || {
        red "  ✗ venv 创建失败，可能缺少 python3-venv 包"
        echo "  Ubuntu/WSL: sudo apt install python3-venv"
        exit 1
    }
    green "  ✓ .venv 已创建"
fi
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"

# ============ 升级 pip ============
blue "[3/8] 升级 pip / setuptools / wheel..."
if "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1; then
    green "  ✓ pip 就绪"
else
    yellow "  · pip 升级失败（网络问题？），继续使用内置版本"
fi

# ============ 安装 Python 依赖 ============
blue "[4/8] 安装 Python 依赖 (requirements.txt)..."
echo "  · 这一步可能耗时 5-15 分钟（pyqlib/lightgbm 等需编译）"
if "$PYTHON_BIN" -m pip install -r requirements.txt; then
    green "  ✓ Python 依赖安装完成"
else
    red "  ✗ pip install 失败，常见原因:"
    echo "  1) 缺少编译工具: sudo apt install build-essential"
    echo "  2) 网络问题: 换源 pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt"
    echo "  3) lightgbm 编译失败: sudo apt install cmake"
    exit 1
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
echo "  4. 访问: http://localhost:3000"
echo ""
echo "文档:"
echo "  快速开始:     docs/QUICKSTART.md"
echo "  生产部署:     docs/DEPLOY.md"
echo "  数据层说明:   docs/DATA_LAYER.md"
echo "  开发指南:     docs/DEVELOPMENT.md"
echo ""
