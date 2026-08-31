# 快速开始（5 分钟跑通 QuantLab）

本文档帮助你在干净机器上从零跑起 QuantLab。

> 生产部署请参考 [DEPLOY.md](DEPLOY.md)。

---

## 一、前置依赖

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.11 | pyqlib 不支持 3.13，推荐 3.11/3.12 |
| Node.js | 18 | 前端构建，推荐 LTS |
| npm | 9 | 随 Node.js 安装 |
| PostgreSQL | 14 | 主数据库，需自行安装 |
| git | 任意 | 克隆代码 |

### 1.1 安装 Python 3.11

```bash
# Ubuntu / WSL
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev build-essential

# macOS
brew install python@3.12

# 或用 pyenv（推荐，可管理多版本）
curl -sSL https://pyenv.run | bash
pyenv install 3.11
pyenv local 3.11
```

### 1.2 安装 Node.js 18+

```bash
# 推荐 nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc
nvm install --lts
nvm use --lts
```

### 1.3 安装并配置 PostgreSQL

PostgreSQL 是主数据库，**必须先安装并建库**。

```bash
# Ubuntu / WSL
sudo apt install postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql

# macOS
brew install postgresql@14
brew services start postgresql@14
```

建库与建用户：

```bash
sudo -u postgres psql <<'SQL'
CREATE USER quantlab WITH PASSWORD 'quantlab';
CREATE DATABASE quantlab OWNER quantlab;
GRANT ALL PRIVILEGES ON DATABASE quantlab TO quantlab;
SQL
```

> 密码 `quantlab` 仅为示例，生产请用强密码。

---

## 二、三步跑通

### 第 1 步：克隆代码

```bash
git clone https://github.com/JoakimStarr/QuantLab.git
cd QuantLab
```

### 第 2 步：一键引导环境

```bash
./setup.sh
```

`setup.sh` 会自动完成：
1. 创建 `.venv` 虚拟环境
2. 安装 Python 依赖（`requirements.txt`）
3. 校验关键依赖（pyqlib / baostock / fastapi 等）
4. 安装前端依赖（`npm install`）
5. 创建数据目录（`data/` `models/` `logs/`）
6. 复制 `.env.example` → `.env`

> ⏱ 首次安装可能 5-15 分钟（pyqlib / lightgbm 等需编译）。

### 第 3 步：配置 .env

编辑 `.env`，填入实际配置：

```bash
# AI Provider（三选一即可，推荐 OPENCODEZEN）
OPENCODEZEN_API_KEY=sk-xxxxxxxx

# PostgreSQL（与上面建库时一致）
POSTGRES_USER=quantlab
POSTGRES_PASSWORD=quantlab          # 你的实际密码
POSTGRES_DB=quantlab
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# 安全（开发环境可默认，生产必须改）
SECRET_KEY=please_change_to_a_strong_random_string
ADMIN_PASSWORD=admin123
```

### 第 4 步：启动

```bash
./start.sh
```

启动后访问（端口在 `.env` 的 `BACKEND_PORT` / `FRONTEND_PORT` 配置）：
- 前端：http://localhost:3001
- 后端 API：http://localhost:8101
- Swagger 文档：http://localhost:8101/docs
- Prometheus 指标：http://localhost:8101/metrics

按 `Ctrl+C` 停止所有服务。

---

## 三、首次使用要做什么

1. **同步数据**：打开 `数据监控` 页面 → 选择回填年数 → 点 `开始同步`（baostock 全量回填）
   - 从最新交易日向旧逐日拉取全市场日K，写入 qlib bin + PG `stock_daily`
   - PG 幂等写入，重复执行只补缺口
2. **导入 Alpha158**：打开 `因子库` → 点 `导入 Alpha158` → 自动评价 158 个标准因子
3. **跑策略**：打开 `策略` → 选因子 → 点 `回测` → 看净值曲线

> 数据同步详见 [DATA_LAYER.md](DATA_LAYER.md)。

---

## 四、常见问题（FAQ）

### Q1: pip install 失败（编译错误）

```bash
# 缺编译工具
sudo apt install build-essential cmake

# 网络问题，换国内源
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

### Q2: npm install 很慢

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
```

### Q3: 后端启动报数据库连接失败

1. 确认 PostgreSQL 已启动：`sudo systemctl status postgresql`
2. 确认 `.env` 中 `POSTGRES_*` 配置正确
3. 测试连接：`psql -U quantlab -d quantlab -h localhost`
4. 若报 `password authentication failed`，重设密码：
   ```bash
   sudo -u postgres psql -c "ALTER USER quantlab WITH PASSWORD 'quantlab';"
   ```

### Q4: 前端能打开但接口 422/500

- 确认后端已启动（`http://localhost:8101/docs` 能访问，端口见 `.env` 的 `BACKEND_PORT`）
- 查看后端日志：`tail -f logs/quantlab.log`（同步问题看 `logs/sync.log`，错误看 `logs/error.log`）
- 数据未同步时部分接口会返回 503（qlib 不可用），先同步数据

### Q5: Windows 用户如何使用

Windows 用户请通过 WSL 运行：

```powershell
# 安装 WSL
wsl --install -d Ubuntu

# 进入 WSL 后按上述步骤操作
```

> ⚠️ 不要在 Windows 侧通过 `\\wsl$\` 编辑源码运行，会有换行符/权限问题。

### Q6: 想重置环境

```bash
rm -rf .venv frontend/node_modules
./setup.sh
```

---

## 五、下一步

- 生产部署：[DEPLOY.md](DEPLOY.md)
- 数据层详解：[DATA_LAYER.md](DATA_LAYER.md)
- 开发指南：[DEVELOPMENT.md](DEVELOPMENT.md)
- 技术架构：[TECHNICAL.md](TECHNICAL.md)
- API 参考：[API_REFERENCE.md](API_REFERENCE.md)

---

*文档版本：1.0 · 最后更新：2026-08-03*
