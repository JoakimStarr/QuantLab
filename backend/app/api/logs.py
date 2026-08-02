"""日志管理 API：查询日志文件、按级别/关键词/request_id 过滤日志条目。"""
import json
import re
import logging
from pathlib import Path
from fastapi import APIRouter, Query
from app.core.logging_config import log_dir
from app.schemas.common import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])

# 允许查询的日志文件白名单
_ALLOWED_FILES = {"app.log", "error.log", "quantlab.log", "api.jsonl", "perf.jsonl", "audit.jsonl"}

# 文本日志行正则: "2026-07-28 15:34:23,123 [INFO] app.module: message [req=xxx]"
_TEXT_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+) \[(\w+)\] ([^:]+): (.*)$"
)
_REQ_RE = re.compile(r"\[req=([a-f0-9]+)\]")


@router.get("/files")
async def list_log_files():
    """获取日志文件列表。"""
    items = []
    for name in sorted(_ALLOWED_FILES):
        fp = log_dir / name
        if fp.exists():
            stat = fp.stat()
            items.append({
                "name": name,
                "size": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "modified": str(int(stat.st_mtime)),
            })
        else:
            items.append({"name": name, "size": 0, "size_human": "0 B", "modified": None})
    return ApiResponse(ok=True, data={"items": items})


@router.get("")
async def get_logs(
    file: str = Query("error.log"),
    level: str = Query(None, description="ERROR/WARNING/INFO/DEBUG"),
    search: str = Query(None, description="关键词模糊搜索"),
    request_id: str = Query(None, description="按 request_id 精确过滤"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    """查询日志条目（从文件末尾向前读取，按时间倒序返回）。"""
    if file not in _ALLOWED_FILES:
        return ApiResponse(ok=False, error={
            "code": "INVALID_FILE", "message": f"不允许的文件名: {file}", "status": 400
        })

    fp = log_dir / file
    if not fp.exists():
        return ApiResponse(ok=True, data={"items": [], "total": 0, "file": file})

    is_json = file.endswith(".jsonl")
    # 探测文件头部是否为 JSON 行（structlog JSON 格式化写出的 .log 文件同样适用）
    if not is_json and fp.exists() and fp.stat().st_size > 0:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
        is_json = first.startswith("{") and first.endswith("}")
    entries = _read_log_file(fp, is_json, limit + offset)

    # 过滤
    level_order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    min_level = level_order.get(level.upper()) if level else None

    filtered = []
    for entry in entries:
        if min_level and level_order.get(entry.get("level", ""), 0) < min_level:
            continue
        if search and search.lower() not in entry.get("message", "").lower():
            continue
        if request_id and entry.get("request_id", "") != request_id:
            continue
        filtered.append(entry)

    total = len(filtered)
    page = filtered[offset:offset + limit]
    return ApiResponse(ok=True, data={"items": page, "total": total, "file": file})


def _read_log_file(fp: Path, is_json: bool, max_lines: int) -> list[dict]:
    """从文件末尾向前读取日志，返回按时间倒序的条目列表。"""
    entries = []
    # 对于小文件直接全量读取；大文件从末尾按块读取
    size = fp.stat().st_size
    if size < 5 * 1024 * 1024:  # < 5MB 全量读取
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    else:
        # 从末尾按块读取
        lines = _tail_read(fp, max_lines * 3)  # 多读一些以防多行 traceback

    # 倒序处理
    lines.reverse()

    if is_json:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entries.append({
                    "timestamp": obj.get("timestamp", ""),
                    "level": obj.get("level", ""),
                    "logger": obj.get("logger", ""),
                    "message": obj.get("message", ""),
                    "request_id": obj.get("request_id", ""),
                    "traceback": obj.get("exception", ""),
                })
            except json.JSONDecodeError:
                continue
            if len(entries) >= max_lines:
                break
    else:
        # 文本日志: 合并多行 traceback
        current = None
        traceback_lines = []
        for line in lines:
            match = _TEXT_LOG_RE.match(line.rstrip("\n"))
            if match:
                # 保存上一个条目
                if current:
                    if traceback_lines:
                        current["traceback"] = "\n".join(reversed(traceback_lines))
                    entries.append(current)
                    if len(entries) >= max_lines:
                        return entries
                    traceback_lines = []
                ts, lvl, lgr, msg = match.groups()
                req_match = _REQ_RE.search(msg)
                req_id = req_match.group(1) if req_match else ""
                # 去掉消息尾部的 [req=xxx]
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
            else:
                # 多行 traceback 的延续行
                if current and line.strip():
                    traceback_lines.append(line.rstrip("\n"))
        # 保存最后一个条目
        if current:
            if traceback_lines:
                current["traceback"] = "\n".join(reversed(traceback_lines))
            entries.append(current)

    return entries


def _tail_read(fp: Path, max_lines: int) -> list[str]:
    """从文件末尾向前按块读取，返回倒序的行列表。"""
    chunk_size = 64 * 1024
    lines = []
    with open(fp, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        remainder = b""
        while pos > 0 and len(lines) < max_lines:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size) + remainder
            parts = chunk.split(b"\n")
            remainder = parts[0]  # 不完整的行留到下一轮
            for part in reversed(parts[1:]):
                if part.strip():
                    lines.append(part.decode("utf-8", errors="replace"))
                if len(lines) >= max_lines:
                    break
        # 处理最后剩余的行
        if remainder.strip() and len(lines) < max_lines:
            lines.append(remainder.decode("utf-8", errors="replace"))
    return lines


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
