"""日志管理 API：查询日志文件、按级别/关键词/request_id/时间过滤、清除日志。"""
import json
import logging
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Query
import app.core.logging_config as logging_config
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])

# 允许查询的静态日志文件（与 logging_config.setup_logging / audit_log 实际产物一致）
_ALLOWED_STATIC = {"error.log", "quantlab.log", "audit.jsonl"}
# sync_worker 日志：sync_worker_<kind>.log（kind 与 sync_worker CLI 保持一致）
_SYNC_WORKER_KINDS = {"backfill", "eod", "repair", "indices", "fundamental"}

# 文本日志行正则: "2026-07-28 15:34:23,123 [INFO] app.module: message [req=xxx]"
# 或 sync_worker 格式 "2026-07-28 15:34:23,123 INFO app.module: message"（无方括号）
_TEXT_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+) \[?(\w+)\]? ([^:]+): (.*)$"
)
_REQ_RE = re.compile(r"\[req=([a-f0-9]+)\]")
_NUMBER_RE = re.compile(r"^[-+]?\d+(\.\d+)?$")

# 级别阈值：structlog JSON 日志是小写（"info"/"error"），文本日志是大写，统一转小写比较
_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def _allowed_file(name: str) -> bool:
    """日志文件白名单校验（含动态 sync_worker 日志）。"""
    if name in _ALLOWED_STATIC:
        return True
    kind = name[len("sync_worker_"):-len(".log")]
    return name.startswith("sync_worker_") and name.endswith(".log") and kind in _SYNC_WORKER_KINDS


def _log_files():
    """日志文件列表：静态 3 个 + 实际存在的 sync_worker_*.log。"""
    for name in sorted(_ALLOWED_STATIC):
        yield name
    for fp in sorted(logging_config.log_dir.glob("sync_worker_*.log")):
        if fp.name not in _ALLOWED_STATIC:
            yield fp.name


@router.get("/files")
async def list_log_files():
    """获取日志文件列表（含轮转备份数量/大小，供清除确认弹窗展示）。"""
    items = []
    for name in sorted(_log_files()):
        fp = logging_config.log_dir / name
        if fp.exists():
            stat = fp.stat()
            backups = sorted(p for p in logging_config.log_dir.glob(f"{name}.*") if p.is_file())
            backup_size = sum(p.stat().st_size for p in backups)
            items.append({
                "name": name,
                "size": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "backup_count": len(backups),
                "backup_size": backup_size,
                "backup_size_human": _human_size(backup_size),
                "modified": str(int(stat.st_mtime)),
            })
        else:
            items.append({
                "name": name, "size": 0, "size_human": "0 B",
                "backup_count": 0, "backup_size": 0, "backup_size_human": "0 B",
                "modified": None,
            })
    return ApiResponse(ok=True, data={"items": items})


@router.get("")
async def get_logs(
    file: str = Query("error.log"),
    level: str = Query(None, description="ERROR/WARNING/INFO/DEBUG/CRITICAL"),
    search: str = Query(None, description="关键词模糊搜索"),
    request_id: str = Query(None, description="按 request_id 精确过滤"),
    since: str = Query(None, description="仅返回该时间之后的条目（ISO 时间或 unix 秒）"),
    before: str = Query(None, description="仅返回该时间之前的条目（ISO 时间或 unix 秒）"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    """查询日志条目（按时间倒序返回，total 为匹配总数）。"""
    if not _allowed_file(file):
        return ApiResponse(ok=False, error={
            "code": "INVALID_FILE", "message": f"不允许的文件名: {file}", "status": 400
        })

    fp = logging_config.log_dir / file
    if not fp.exists():
        return ApiResponse(ok=True, data={"items": [], "total": 0, "file": file})

    is_json = _detect_json(fp)
    items, total = _scan_log(fp, is_json, level=level, search=search,
                             request_id=request_id, limit=limit, offset=offset,
                             since=since, before=before)
    page = items[offset:offset + limit]
    return ApiResponse(ok=True, data={"items": page, "total": total, "file": file})


@router.post("/clear")
async def clear_logs(file: str = Query("error.log")):
    """清空指定日志文件：截断当前文件 + 删除其轮转备份，返回释放的字节数。"""
    if not _allowed_file(file):
        return ApiResponse(ok=False, error={
            "code": "INVALID_FILE", "message": f"不允许的文件名: {file}", "status": 400
        })
    fp = logging_config.log_dir / file
    if not fp.exists():
        return ApiResponse(ok=True, data={"freed_bytes": 0, "deleted_backups": 0, "file": file})

    freed = 0
    deleted_backups = 0
    try:
        freed += fp.stat().st_size
        # 截断当前文件：日志 handler 以 append 模式持有同一 inode，
        # 截断后继续写入会从头追加，不会损坏后续日志
        with open(fp, "w", encoding="utf-8"):
            pass
        for backup in sorted(logging_config.log_dir.glob(f"{file}.*")):
            try:
                freed += backup.stat().st_size
                backup.unlink()
                deleted_backups += 1
            except OSError:
                continue
    except OSError as e:
        return ApiResponse(ok=False, error={
            "code": "CLEAR_FAILED", "message": str(e), "status": 500
        })
    logger.info("日志已清除: %s 释放 %.2f MB (%d 个备份)",
                file, freed / 1048576.0, deleted_backups)
    return ApiResponse(ok=True, data={
        "freed_bytes": freed, "deleted_backups": deleted_backups, "file": file,
    })


def _detect_json(fp: Path) -> bool:
    """探测日志文件是否为 JSON 行格式（.jsonl 直接判定，其余看首行）。"""
    if fp.name.endswith(".jsonl"):
        return True
    if fp.stat().st_size == 0:
        return False
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        first = f.readline().strip()
    return first.startswith("{") and first.endswith("}")


def _ts_key(ts: str) -> str:
    """归一化时间戳为可字典序比较的 key（忽略 UTC 后缀/文本格式差异）。"""
    if not ts:
        return ""
    t = ts.strip()
    # 文本格式 "2026-07-28 15:34:23,123" → ISO 风格
    if " " in t and "," in t and "T" not in t:
        t = t.replace(" ", "T").replace(",", ".")
    if t.endswith("Z"):
        t = t[:-1]
    return t


def _normalize_bound(value) -> str | None:
    """把 since/before 参数归一化为可比较的 ISO key。

    支持 ISO 时间字符串（含/不含 Z）与 unix 秒（数字/数字字符串）。
    """
    if value is None or value == "":
        return None
    s = str(value).strip()
    if _NUMBER_RE.match(s):
        return datetime.fromtimestamp(float(s), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    return _ts_key(s)


def _passes_window(ts: str, since_key: str | None, before_key: str | None) -> bool:
    """时间窗口过滤：半开区间 [since, before)，since 含、before 不含。"""
    if not since_key and not before_key:
        return True
    k = _ts_key(ts)
    if since_key and k < since_key:
        return False
    if before_key and k >= before_key:
        return False
    return True


def _entry_from_json(obj: dict) -> dict:
    return {
        "timestamp": obj.get("timestamp", ""),
        "level": obj.get("level") or obj.get("levelname", ""),
        "logger": obj.get("logger", ""),
        # structlog 默认用 "event" 作为消息键；audit.jsonl 用 "action"
        "message": obj.get("message") or obj.get("event") or obj.get("action", ""),
        "request_id": obj.get("request_id", ""),
        "traceback": obj.get("exception", ""),
    }


def _reverse_lines(fp, chunk_size: int = 64 * 1024):
    """从文件尾部按逻辑行反向迭代（最新一行在前），适配大文件尾部扫描。

    二进制分块读取，跨块拼接，避免整文件驻留内存；用 errors=replace 容忍
    被截断的多字节 UTF-8 字符。
    """
    fp.seek(0, os.SEEK_END)
    pos = fp.tell()
    buf = b""
    while pos > 0:
        size = min(chunk_size, pos)
        pos -= size
        fp.seek(pos)
        buf = fp.read(size) + buf
        parts = buf.split(b"\n")
        buf = parts[0]
        for ln in reversed(parts[1:]):
            line = ln.decode("utf-8", errors="replace").strip()
            if line:
                yield line
    if buf.strip():
        yield buf.decode("utf-8", errors="replace").strip()


def _scan_log(
    fp: Path,
    is_json: bool,
    level: str = None,
    search: str = None,
    request_id: str = None,
    limit: int = 100,
    offset: int = 0,
    since: str = None,
    before: str = None,
) -> tuple[list[dict], int]:
    """扫描日志文件，过滤后只保留最新的 offset+limit 条。

    返回 (items, total)：items 按时间倒序（最新在前），total 为匹配总数。

    性能优化：JSON 行日志 + 给定 since 下界时（追加写入、时间有序），
    从文件尾部反向扫描，遇到早于 since 的行即停止，避免大文件全量扫描；
    其余情况回退全文件流式扫描（deque 限长，内存有界）。
    """
    min_level = _LEVEL_ORDER.get(level.lower()) if level else None
    search_l = search.lower() if search else None
    since_key = _normalize_bound(since)
    before_key = _normalize_bound(before) if before else None

    if is_json and since_key:
        return _scan_log_tail(fp, min_level, search_l, request_id,
                              limit, offset, since_key, before_key)

    keep = deque(maxlen=offset + limit)
    total = 0

    current = None  # 文本日志当前条目（用于合并多行 traceback）
    traceback_lines = []
    with open(fp, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line:
                continue
            if is_json:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry = _entry_from_json(obj)
                if _passes_window(entry["timestamp"], since_key, before_key) \
                        and _accept(entry, min_level, search_l, request_id):
                    total += 1
                    keep.append(entry)
            else:
                match = _TEXT_LOG_RE.match(line)
                if match:
                    # 新条目开始，先结算上一条（含 traceback 合并）
                    if current is not None:
                        if traceback_lines:
                            current["traceback"] = "\n".join(traceback_lines)
                        if _passes_window(current["timestamp"], since_key, before_key) \
                                and _accept(current, min_level, search_l, request_id):
                            total += 1
                            keep.append(current)
                        traceback_lines = []
                    ts, lvl, lgr, msg = match.groups()
                    req_match = _REQ_RE.search(msg)
                    req_id = req_match.group(1) if req_match else ""
                    if req_match:
                        msg = msg[:req_match.start()].rstrip()
                    current = {
                        "timestamp": ts,
                        "level": lvl,
                        "logger": lgr,
                        "message": msg,
                        "request_id": req_id,
                        "traceback": "",
                    }
                elif current is not None and line.strip():
                    # traceback 的延续行
                    traceback_lines.append(line)

    # 结算最后一个文本条目
    if current is not None:
        if traceback_lines:
            current["traceback"] = "\n".join(traceback_lines)
        if _passes_window(current["timestamp"], since_key, before_key) \
                and _accept(current, min_level, search_l, request_id):
            total += 1
            keep.append(current)

    items = list(keep)
    items.reverse()  # 文件末尾是最新条目，反转后最新在前
    return items, total


def _scan_log_tail(fp, min_level, search, request_id, limit, offset, since_key, before_key):
    """JSON 日志尾部反向扫描：时间有序，遇到早于 since 的行即停止。"""
    # 反向迭代最新在前：前 offset+limit 条匹配即是最新的那批，直接收集；
    # total 需数完 since 窗口内的全部匹配
    keep = []
    total = 0
    with open(fp, "rb") as f:
        for line in _reverse_lines(f):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry = _entry_from_json(obj)
            if _ts_key(entry["timestamp"]) < since_key:
                break
            if _passes_window(entry["timestamp"], since_key, before_key) \
                    and _accept(entry, min_level, search, request_id):
                total += 1
                if len(keep) < offset + limit:
                    keep.append(entry)
    # 已是时间倒序（最新在前）
    return keep, total


def _accept(entry: dict, min_level: int, search: str, request_id: str) -> bool:
    """判断条目是否匹配全部过滤条件。"""
    if min_level and _LEVEL_ORDER.get(entry["level"].lower(), 0) < min_level:
        return False
    if search and search not in (entry["message"] or "").lower():
        return False
    if request_id and entry["request_id"] != request_id:
        return False
    return True


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
