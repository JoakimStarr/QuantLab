"""日志管理 API 单测（无需 DB）：验证 since/before 时间过滤、尾部反向扫描、白名单、清除日志。"""
import json
from datetime import datetime, timezone

import pytest

from app.api.logs import _allowed_file, _normalize_bound, _scan_log, _ts_key


def _write_json_log(fp, entries):
    """entries: [{ts, level, msg}]，按 ts 升序写入，模拟单写者追加。"""
    with open(fp, "w", encoding="utf-8") as f:
        for e in entries:
            line = {
                "timestamp": e["ts"],
                "level": e["level"],
                "event": e["msg"],
            }
            f.write(json.dumps(line) + "\n")
    return fp


def _json_entries():
    return [
        {"ts": "2026-08-01T01:00:00.000Z", "level": "info", "msg": "a"},
        {"ts": "2026-08-02T01:00:00.000Z", "level": "error", "msg": "b"},
        {"ts": "2026-08-03T01:00:00.000Z", "level": "info", "msg": "c"},
    ]


def test_scan_json_since_tail(tmp_path):
    """since 触发尾部反向扫描：只返回下界之后条目，最新在前。"""
    fp = _write_json_log(tmp_path / "quantlab.log", _json_entries())
    items, total = _scan_log(fp, is_json=True, since="2026-08-02T01:00:00.000Z")
    assert total == 2
    assert [e["message"] for e in items] == ["c", "b"]  # 最新在前


def test_scan_json_since_with_limit_offset(tmp_path):
    fp = _write_json_log(tmp_path / "quantlab.log", _json_entries())
    items, total = _scan_log(fp, is_json=True, since="2026-08-02T01:00:00.000Z", limit=1, offset=0)
    assert [e["message"] for e in items] == ["c"]
    assert total == 2


def test_scan_json_since_with_level(tmp_path):
    fp = _write_json_log(tmp_path / "quantlab.log", _json_entries())
    items, total = _scan_log(fp, is_json=True, since="2026-08-02T01:00:00.000Z", level="error")
    assert [e["message"] for e in items] == ["b"]


def test_scan_json_before_full_scan(tmp_path):
    """仅 before（无 since）回退全文件扫描，过滤上界后仍最新在前。"""
    fp = _write_json_log(tmp_path / "quantlab.log", _json_entries())
    items, total = _scan_log(fp, is_json=True, before="2026-08-03T01:00:00.000Z")
    assert total == 2
    assert [e["message"] for e in items] == ["b", "a"]


def test_scan_text_traceback_merge(tmp_path):
    """文本日志多行 traceback 合并回归。"""
    text = (
        "2026-07-28 15:34:23,123 [INFO] app.module: hello [req=abc123]\n"
        "2026-07-28 15:34:24,456 [ERROR] app.module: boom\n"
        '  File "/x/y.py", line 10\n'
        "    raise RuntimeError()\n"
    )
    fp = tmp_path / "worker.log"
    fp.write_text(text, encoding="utf-8")
    items, total = _scan_log(fp, is_json=False)
    assert total == 2
    # 最新在前：error 条目带合并后的 traceback
    assert items[0]["level"] == "ERROR"
    assert "RuntimeError" in items[0]["traceback"]
    assert items[1]["request_id"] == "abc123"


def test_allowed_file_whitelist():
    assert _allowed_file("error.log")
    assert _allowed_file("quantlab.log")
    assert _allowed_file("audit.jsonl")
    assert _allowed_file("sync_worker_repair.log")
    assert _allowed_file("sync_worker_backfill.log")
    assert not _allowed_file("sync_worker_evil.log")
    assert not _allowed_file("passwd.log")
    assert not _allowed_file("quantlab.log.1")


def test_ts_key_and_normalize_bound():
    assert _ts_key("2026-08-05T09:00:00.123Z") == "2026-08-05T09:00:00.123"
    assert _ts_key("2026-07-28 15:34:23,123") == "2026-07-28T15:34:23.123"
    # unix 秒归一化为 ISO key
    ts = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
    assert _normalize_bound(str(int(ts.timestamp()))) == "2026-08-05T09:00:00.000000"


async def test_clear_logs(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.logging_config.log_dir", tmp_path)
    (tmp_path / "error.log").write_text("x" * 10, encoding="utf-8")
    (tmp_path / "error.log.1").write_text("y" * 5, encoding="utf-8")

    from app.api.logs import clear_logs

    res = await clear_logs("error.log")
    assert res.ok is True
    assert res.data["freed_bytes"] == 15
    assert res.data["deleted_backups"] == 1
    assert (tmp_path / "error.log").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "error.log.1").exists()


async def test_clear_logs_invalid_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.logging_config.log_dir", tmp_path)
    from app.api.logs import clear_logs

    res = await clear_logs("passwd.log")
    assert res.ok is False
    assert res.error["code"] == "INVALID_FILE"
