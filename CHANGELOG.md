# 更新日志 CHANGELOG

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [未发布 Unreleased]

### 新增
- **宏观指标模块**：新增 `POST /api/v1/macro/sync`（EastMoney 数据源同步 PMI/CPI/PPI/GDP）、
  `GET /api/v1/macro/indicators`（含 PIT 锚点 `available_date`）、`GET /api/v1/macro/status`。
  数据落 PG 窄表 `macro_indicator`（indicator/report_date/field_name 唯一）并广播写入 qlib
  `features/*/{field}.day.bin`（`pmi/pmi_nm/cpi/ppi/gdp`），支持因子表达式直接引用 `$pmi` 等宏字段。
- **宏观指标前端**：新增 `/quant/macro` 页（Macro.vue）：
  - PMI 图同时叠加「制造业PMI」与「非制造业PMI」两条线（常驻图例，制造=蓝、非制造=橙）；
  - 荣枯线（=50）加粗深红虚线 + 加粗标签；
  - 50 上/下方分别淡绿/淡红背景分区；穿越荣枯线处绘 markPoint 标记点（突破=红、跌破=绿）；
  - 时间范围切换（1Y/3Y/5Y/全部，默认 5Y）；CPI/PPI/GDP 单指标图。
  - 首页 Dashboard 新增「宏观指标快照」卡片，含「详情」跳转 /quant/macro。
- **数据完整性校验与一键修复**：新增 `GET /quant/data/validate`（bin/DB 字段、日历、覆盖一致性报告）、
  `POST /quant/data/repair`（按差异重建 day.txt/bin/instruments；可选 `include_baostock` 补拉缺失交易日）、
  `POST /quant/data/sync-calendar`（以 DB stock_daily 为准重建 qlib 日历）。`integrity_check` 对空数据友好返回。
  前端 DataStatus 页接入校验/修复/日历对齐操作与 EOD 结果轮询。
- **日志系统增强**：日志改为按大小轮转（RotatingFileHandler）+ 每日 03:30 定期清理（`quantlab.log`/`audit.jsonl`
  备份保留 7 天、`error.log` 保留 15 天，可通过 config/logging 配置）；错误日志检索兼容 structlog 的 `event` 字段。

### 变更 / 修复
- **数据同步迁移至独立 worker 子进程（关键修复，根治 reload 卡死）**：
  - 此前 baostock 全量回填、EOD 增量、repair 通过 FastAPI `BackgroundTasks` 在 web 进程内运行；
    uvicorn `--reload` 触发重启时会「等待后台任务完成」，而全量回填动辄数十分钟，导致 new worker
    起不来、端口被半死进程占用、前端 ECONNREFUSED。
  - 现改为独立子进程执行（`python -m app.services.data.sync_worker`，`start_new_session` 脱离 web 进程组）：
    reload 重启立即退出，不再等待后台任务，也不误杀正在进行的同步。
  - 状态写 DB（`stock_data_status`，新增 `sync_trigger=manual/auto`）、进度镜像共享文件
    `data/sync_progress.json`（含 `worker_pid`），web 进程据此判断同步真实活跃性
    （`sync_is_active`：worker 进程已死则视为不活跃，允许重新触发）。
  - `/quart/data/sync`、`/eod-sync`、`/sync-indices`、`/repair`、`/fallback-sync` 均改为拉起独立 worker；
    `/eod-result` 改为读取共享结果文件 `data/eod_last_result.json`（不再依赖进程内存）。
  - 启动时 `recover_stale_sync` 回收卡死同步：worker 进程已死而 DB 残留 syncing 的立即标记 failed；
    自动触发的同步超 30 分钟标记 failed。`start.sh` 补充说明 `--reload` 现已安全。
- **baostock 增量回填优化**：写盘改为多线程并写（`_write_pool`）+ 下载/写盘流水线并发；
  跳过已下载日期（基于已有数据区间作种子，保留最早历史）；重建日历/生成
  `features/*/{field}.day.bin`；修复 instruments 代码大小写（baostock→qlib 转小写，如 `sh600000`）。
- **修复 DataStatus「一键补齐」UI**：待修复差异框补充「bin 长度异常 / 疑似损坏 / bin 无 DB 记录」
  标签（此前仅缺 field/db/range 类型时框为空但按钮出现）；补齐提交后正确显示进度条；
  完成提示按任务类型显示（不再误显示已删除的「智能同步」）；补齐完成后自动重新校验刷新报告；
  409 冲突不再重复弹提示；进度轮询连续无数据时自动停止避免空转。

### 其他
- 删除冗余 smoke 脚本 `backend/scripts/_smoke_backfill.py`。
- 新增/更新测试：macro_api、macro_sync、validation、repair、logging_cleanup、logging_error_retrieval、
  baostock_backfill、eod_incremental 等。