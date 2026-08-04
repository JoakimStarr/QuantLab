"""日志定期清理单测（无需 DB）：验证过期备份删除、当前文件保留、错误日志更长保留、并发锁。"""
import os
import time

from app.core.logging_config import cleanup_old_logs


def _make_log_dir(tmp_path, files):
    """创建日志目录与文件，返回目录路径。

    files: {name: age_days}，age_days 为 None 表示当前文件（mtime=now）。
    """
    d = tmp_path / "logs"
    d.mkdir()
    now = time.time()
    for name, age_days in files.items():
        p = d / name
        p.write_text("log line\n")
        if age_days is not None:
            os.utime(p, (now - age_days * 86400, now - age_days * 86400))
    return d


def test_cleanup_deletes_old_keeps_current_and_error(tmp_path):
    d = _make_log_dir(tmp_path, {
        "quantlab.log": None,          # 当前文件，永不删除
        "quantlab.log.1": 30,          # 超 7 天 → 删除
        "quantlab.log.2": 3,           # 7 天内 → 保留
        "error.log": None,             # 当前文件，永不删除
        "error.log.1": 20,             # 超 15 天 → 删除
        "error.log.2": 10,             # 15 天内 → 保留
        "audit.jsonl.1": 40,           # 超 7 天 → 删除
    })

    result = cleanup_old_logs(d, retention_days=7, error_retention_days=15)

    assert result["deleted_count"] == 3
    assert set(result["deleted"]) == {"quantlab.log.1", "error.log.1", "audit.jsonl.1"}
    assert result["freed_bytes"] > 0
    # 当前文件 + 未过期备份保留
    remaining = {p.name for p in d.iterdir()}
    assert {"quantlab.log", "quantlab.log.2", "error.log", "error.log.2"} <= remaining


def test_cleanup_error_retained_longer_than_normal(tmp_path):
    """同样 10 天旧的备份：普通日志被删、错误日志保留（不同保留期）。"""
    d = _make_log_dir(tmp_path, {
        "quantlab.log.1": 10,   # >7 天 → 删除
        "error.log.1": 10,      # <15 天 → 保留
    })
    result = cleanup_old_logs(d, retention_days=7, error_retention_days=15)
    assert result["deleted"] == ["quantlab.log.1"]
    assert (d / "error.log.1").exists()


def test_cleanup_no_dir_returns_empty(tmp_path):
    result = cleanup_old_logs(tmp_path / "nope", retention_days=7, error_retention_days=15)
    assert result == {"deleted": [], "freed_bytes": 0, "deleted_count": 0, "skipped": False}


def test_cleanup_skips_when_locked(tmp_path):
    """已有实例持有 .cleanup.lock 时，本次清理直接跳过。"""
    import fcntl

    d = _make_log_dir(tmp_path, {"quantlab.log.1": 30})
    lock = open(d / ".cleanup.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = cleanup_old_logs(d, retention_days=7, error_retention_days=15)
        assert result["skipped"] is True
        assert result["deleted"] == []
        # 被锁时不应删除任何文件
        assert (d / "quantlab.log.1").exists()
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
        (d / ".cleanup.lock").unlink(missing_ok=True)


def test_cleanup_releases_lock(tmp_path):
    """清理完成后锁应释放，可再次执行。"""
    d = _make_log_dir(tmp_path, {"quantlab.log.1": 30})
    cleanup_old_logs(d, retention_days=7, error_retention_days=15)
    assert not (d / ".cleanup.lock").exists()
    # 锁文件释放后可再次成功清理（无残留）
    result = cleanup_old_logs(d, retention_days=7, error_retention_days=15)
    assert result["skipped"] is False
