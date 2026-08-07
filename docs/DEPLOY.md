# 生产部署指南

本文档介绍 QuantLab 在生产环境的部署方式：纯本地 venv + systemd 托管 + Nginx 反向代理。

> 本地开发试用请参考 [QUICKSTART.md](QUICKSTART.md)。

---

## 一、系统要求

| 项 | 最低 | 推荐 |
|----|------|------|
| OS | Ubuntu 20.04 / Debian 11 / CentOS 8 | Ubuntu 22.04 LTS |
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+（qlib 回测内存占用高） |
| 磁盘 | 20 GB | 50 GB+（qlib bin 数据 + 模型产物） |
| Python | 3.11 | 3.11 |
| Node.js | 18 LTS | 20 LTS |
| PostgreSQL | 14 | 16 |

---

## 二、安装系统依赖

```bash
# 基础工具与编译依赖
sudo apt update
sudo apt install -y build-essential cmake git curl \
    python3.11 python3.11-venv python3.11-dev \
    nodejs npm \
    postgresql postgresql-contrib \
    nginx

# 若系统无 Python 3.11，用 deadsnakes PPA
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev
```

---

## 三、PostgreSQL 配置

### 3.1 初始化数据库

```bash
sudo systemctl enable --now postgresql

sudo -u postgres psql <<'SQL'
CREATE USER quantlab WITH PASSWORD '替换为强密码';
CREATE DATABASE quantlab OWNER quantlab;
GRANT ALL PRIVILEGES ON DATABASE quantlab TO quantlab;
SQL
```

### 3.2 生产安全加固（可选但推荐）

编辑 `/etc/postgresql/14/main/pgpostgresql.conf`：

```conf
listen_addresses = 'localhost'
password_encryption = scram-sha-256
```

编辑 `/etc/postgresql/14/main/pg_hba.conf`：

```conf
# 仅允许本机连接
host    quantlab    quantlab    127.0.0.1/32    scram-sha-256
host    quantlab    quantlab    ::1/128         scram-sha-256
```

重启：`sudo systemctl restart postgresql`

### 3.3 备份策略

```bash
# 每日全量备份（加入 crontab）
0 3 * * * pg_dump -U quantlab -Fc quantlab > /var/backups/quantlab_$(date +\%F).dump

# 保留 30 天
find /var/backups/ -name "quantlab_*.dump" -mtime +30 -delete
```

---

## 四、部署 QuantLab

### 4.1 创建运行用户与目录

```bash
sudo useradd -m -s /bin/bash quantlab
sudo mkdir -p /opt/quantlab
sudo chown quantlab:quantlab /opt/quantlab
```

### 4.2 克隆代码并引导环境

```bash
sudo -u quantlab -i

cd /opt/quantlab
git clone https://github.com/JoakimStarr/QuantLab.git .
./setup.sh
```

### 4.3 生产环境 .env 配置

```bash
cp .env.example .env
```

编辑 `.env`，**生产环境必须修改以下项**：

```bash
APP_ENV=production
AUTH_ENABLED=true

# JWT 密钥（用强随机串）
SECRET_KEY=$(openssl rand -hex 32)

# 管理员密码：推荐用 bcrypt 哈希
# 生成方式：python -c "import bcrypt; print(bcrypt.hashpw(b'你的密码', bcrypt.gensalt()).decode())"
ADMIN_PASSWORD_HASH=$2b$12$替换为生成的哈希
# 删除或注释明文 ADMIN_PASSWORD

# 登录限流（生产建议收紧）
LOGIN_RATE_LIMIT=5/minute

# PostgreSQL
POSTGRES_USER=quantlab
POSTGRES_PASSWORD=你的强密码
POSTGRES_DB=quantlab
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

# AI Provider
OPENCODEZEN_API_KEY=sk-xxxxxxxx

# 前端
VITE_API_BASE_URL=https://你的域名/api/v1
```

### 4.4 构建前端静态资源

```bash
cd /opt/quantlab/frontend
npm run build
# 产物在 frontend/dist
```

### 4.5 执行数据库迁移

```bash
cd /opt/quantlab
.venv/bin/python -m alembic upgrade head
```

### 4.6 初始化数据

首次部署需同步 qlib 数据：

```bash
# 通过 API 触发智能同步（或在前端页面操作）
curl -X POST http://localhost:8000/api/v1/quant/data/smart-sync
```

> 详见 [DATA_LAYER.md](DATA_LAYER.md)。

---

## 五、systemd 托管

### 5.1 后端服务

创建 `/etc/systemd/system/quantlab.service`：

```ini
[Unit]
Description=QuantLab Backend (FastAPI + uvicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=quantlab
Group=quantlab
WorkingDirectory=/opt/quantlab/backend
EnvironmentFile=/opt/quantlab/.env
ExecStart=/opt/quantlab/.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --no-access-log
Restart=always
RestartSec=5
StandardOutput=append:/opt/quantlab/logs/quantlab.log
StandardError=append:/opt/quantlab/logs/quantlab-error.log

# 资源限制
LimitNOFILE=65536
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

> ⚠️ `workers=1`：APScheduler 单例 + 内存进度跟踪不宜多 worker。如需横向扩展需引入外部调度与分布式进度存储。

启用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now quantlab
sudo systemctl status quantlab
```

### 5.2 日志轮转

应用自身用 `RotatingFileHandler` 管理日志（`quantlab.log`/`error.log`/`sync.log`，各 100MB×5，每日 03:30 按天数清理过期备份）。systemd 的 `StandardOutput` 仅捕获 uvicorn 启动瞬间的 stdout，会与文件日志少量重复，可去掉 `StandardOutput`/`StandardError` 两行。

如仍需保留外部 logrotate（仅兜底），创建 `/etc/logrotate.d/quantlab`：

```conf
/opt/quantlab/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 quantlab quantlab
    sharedscripts
    postrotate
        systemctl reload quantlab >/dev/null 2>&1 || true
    endscript
}
```
> ⚠️ 应用内 `RotatingFileHandler` 轮转时会把当前文件 rename 成 `.1` 并新建，与外部 logrotate 的 rename 方式不同但互不冲突；两者都做时保留期以更短者为准。

---

## 六、Nginx 反向代理

### 6.1 配置文件

创建 `/etc/nginx/sites-available/quantlab.conf`：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 静态前端
    root /opt/quantlab/frontend/dist;
    index index.html;

    # gzip 压缩
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
    gzip_min_length 1000;

    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # API 反代后端
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # WebSocket（同步进度推送）
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;
    }

    # Swagger 文档
    location /docs {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    # Prometheus 指标（限内网访问）
    location /metrics {
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }

    # 健康检查
    location /health {
        proxy_pass http://127.0.0.1:8000;
    }

    # SPA 路由回退
    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### 6.2 启用

```bash
sudo ln -s /etc/nginx/sites-available/quantlab.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 6.3 HTTPS（推荐）

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

certbot 会自动修改 Nginx 配置并配置证书自动续期。

---

## 七、健康检查与监控

### 7.1 健康端点

```bash
curl http://localhost:8000/health
```

返回：

```json
{
  "status": "ok",
  "checks": {"database": "ok", "qlib": "ok"}
}
```

### 7.2 Prometheus 指标

`http://localhost:8000/metrics` 暴露 Prometheus 格式指标，可接入 Grafana 监控。

### 7.3 日志查看

```bash
# 应用日志
journalctl -u quantlab -f

# 文件日志
tail -f /opt/quantlab/logs/app.log
tail -f /opt/quantlab/logs/error.log
```

---

## 八、运维操作

### 8.1 更新代码

```bash
sudo -u quantlab -i
cd /opt/quantlab
git pull
.venv/bin/pip install -r requirements.txt   # 依赖有变更时
cd frontend && npm install && npm run build && cd ..
.venv/bin/python -m alembic upgrade head    # 有迁移时
exit
sudo systemctl restart quantlab
```

### 8.2 重启服务

```bash
sudo systemctl restart quantlab
sudo systemctl reload nginx
```

### 8.3 查看状态

```bash
sudo systemctl status quantlab
sudo systemctl status nginx
sudo systemctl status postgresql
```

---

## 九、安全清单

- [ ] `APP_ENV=production`，`AUTH_ENABLED=true`
- [ ] `SECRET_KEY` 已改为强随机串
- [ ] `ADMIN_PASSWORD_HASH` 已设置 bcrypt 哈希，明文已删除
- [ ] PostgreSQL 仅监听 `localhost`，`pg_hba.conf` 限制访问
- [ ] Nginx `/metrics` 限内网访问
- [ ] 已配置 HTTPS（certbot）
- [ ] 防火墙仅开放 80/443，未开放 8000/5432
- [ ] 日志轮转已配置
- [ ] 数据库每日备份已配置

```bash
# 防火墙示例
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

---

## 十、故障排查

| 现象 | 排查 |
|------|------|
| 后端 502 | `systemctl status quantlab`，查 `logs/error.log` |
| 数据库连接失败 | `systemctl status postgresql`，测 `psql -U quantlab -d quantlab -h localhost` |
| 前端接口 422 | 确认 `.env` 中 `VITE_API_BASE_URL` 与 Nginx 路径一致 |
| qlib 503 | 数据未同步，触发智能同步 |
| 同步进度不更新 | 单实例部署下进度走内存轮询，确认未启多 worker |

---

*文档版本：1.0 · 最后更新：2026-08-03*
