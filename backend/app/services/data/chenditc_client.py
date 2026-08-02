"""chenditc/investment_data 预构建 qlib tarball 数据客户端。

替代 akshare 逐只爬取方案：直接下载 GitHub Releases 上每日构建的 qlib_bin.tar.gz，
解压到 qlib provider 目录，与现有 qlib bin 格式（calendars/instruments/features）完全一致。

数据源：https://github.com/chenditc/investment_data
每日 release 资产：qlib_bin.tar.gz（约 500MB+，内含顶层 qlib_bin/ 目录前缀）
"""
import os
import shutil
import tarfile
import tempfile
import logging
import requests

logger = logging.getLogger(__name__)

# chenditc/investment_data 最新 release 查询 API
GITHUB_RELEASE_API = "https://api.github.com/repos/chenditc/investment_data/releases/latest"
# 目标资产文件名
TARGET_ASSET = "qlib_bin.tar.gz"
# GitHub API 查询超时（秒）
API_TIMEOUT = 30
# 大文件下载超时（秒）：连接建立与读取均受限，实际进度由 stream chunk 控制
DOWNLOAD_CONNECT_TIMEOUT = 30
DOWNLOAD_READ_TIMEOUT = 600
# tarball 内顶层目录前缀层数（qlib_bin/...），解压时剥离 1 层
STRIP_COMPONENTS = 1
# 下载缓冲块大小（1MB）
CHUNK_SIZE = 1024 * 1024


def get_latest_release_info() -> dict:
    """查询 chenditc/investment_data 最新 release 信息（不下载）。

    Returns:
        {"version": tag_name, "date": published_at, "file": asset_name,
         "size": asset_size_bytes, "download_url": browser_download_url}
    Raises:
        RuntimeError: API 请求失败或未找到 qlib_bin.tar.gz 资产
    """
    logger.info("查询 chenditc 最新 release: %s", GITHUB_RELEASE_API)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "QuantLab/2.0",
    }
    try:
        resp = requests.get(GITHUB_RELEASE_API, headers=headers, timeout=API_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"查询 chenditc release 失败: {e}") from e

    data = resp.json()
    tag = data.get("tag_name")
    published = data.get("published_at")
    assets = data.get("assets", []) or []
    asset = next((a for a in assets if a.get("name") == TARGET_ASSET), None)
    if asset is None:
        # 列出可用资产名，便于排查
        available = [a.get("name") for a in assets]
        raise RuntimeError(
            f"release {tag} 未找到资产 {TARGET_ASSET}，可用资产: {available}"
        )

    info = {
        "version": tag,
        "date": published,
        "file": asset.get("name"),
        "size": asset.get("size", 0),
        "download_url": asset.get("browser_download_url"),
    }
    logger.info(
        "最新 release: version=%s date=%s size=%.1fMB url=%s",
        info["version"], info["date"],
        info["size"] / 1024 / 1024, info["download_url"],
    )
    return info


def _download_stream(url: str, dest_path: str, total_size: int) -> None:
    """流式下载文件，支持断点续传和进度跟踪。"""
    from app.services.data.sync_progress import update_progress
    import time as _time

    logger.info("开始下载: %s", url)
    headers = {}
    existing_size = 0
    # 断点续传：如果临时文件已存在且有部分数据，从断点继续
    if os.path.exists(dest_path):
        existing_size = os.path.getsize(dest_path)
        if existing_size > 0 and (total_size == 0 or existing_size < total_size):
            headers["Range"] = f"bytes={existing_size}-"
            logger.info("断点续传: 从 %d bytes 继续 (%.1f%%)", existing_size, existing_size * 100 / max(total_size, 1))

    timeout = (DOWNLOAD_CONNECT_TIMEOUT, DOWNLOAD_READ_TIMEOUT)
    start_time = _time.time()

    with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
        if resp.status_code == 416:
            logger.info("文件已完整下载")
            update_progress(pct=100.0, status="done", message="下载已完成")
            return
        resp.raise_for_status()
        # 206 = 服务器支持 Range 续传；200 = 从头开始
        if resp.status_code == 206:
            mode = "ab"
            downloaded = existing_size
        else:
            mode = "wb"
            downloaded = 0
        last_log_pct = -10
        with open(dest_path, mode) as fp:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    fp.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = downloaded * 100 / total_size
                        if pct >= last_log_pct + 10:
                            last_log_pct = int(pct)
                            elapsed = _time.time() - start_time
                            speed = (downloaded - existing_size) / 1024 / 1024 / max(elapsed, 0.1)
                            logger.info(
                                "下载进度: %d%% (%d/%d MB, %.1f MB/s)",
                                last_log_pct,
                                downloaded // 1024 // 1024,
                                total_size // 1024 // 1024,
                                speed,
                            )
                            # 更新进度 API
                            update_progress(
                                pct=pct,
                                downloaded_mb=downloaded / 1024 / 1024,
                                speed_mbps=speed,
                                message=f"下载中 {last_log_pct}%",
                            )
    logger.info("下载完成: %d MB", downloaded // 1024 // 1024)
    update_progress(pct=100.0, downloaded_mb=downloaded / 1024 / 1024, status="extracting", message="正在解压...")


def _clear_dir(target_dir: str) -> None:
    """清空目标目录中的所有内容（保留目录本身）。

    解压前调用，避免新旧数据混杂。
    """
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
        return
    for name in os.listdir(target_dir):
        path = os.path.join(target_dir, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except Exception as e:
            logger.warning("清理旧文件失败 %s: %s", path, e)


def _extract_tarball(tarball_path: str, target_dir: str) -> None:
    """解压 tarball 到 target_dir，剥离顶层目录前缀（strip components=1）。

    使用标准库 tarfile，逐成员重写路径后 extractall。
    qlib_bin.tar.gz 内部结构为 qlib_bin/{calendars,instruments,features}，
    剥离后得到 target_dir/{calendars,instruments,features}。
    """
    logger.info("解压 %s -> %s (strip components=%d)",
                tarball_path, target_dir, STRIP_COMPONENTS)
    os.makedirs(target_dir, exist_ok=True)
    with tarfile.open(tarball_path, "r:gz") as tar:
        members_to_extract = []
        for member in tar.getmembers():
            # 安全检查：拒绝绝对路径或路径穿越
            if member.name.startswith("/") or ".." in member.name.split("/"):
                logger.warning("跳过不安全成员: %s", member.name)
                continue
            # 剥离顶层目录前缀（按 / 分割去掉第一段）
            if "/" not in member.name:
                # 顶层目录本身（如 qlib_bin），跳过
                continue
            stripped = member.name.split("/", STRIP_COMPONENTS)[STRIP_COMPONENTS]
            if not stripped or stripped.startswith("/") or ".." in stripped.split("/"):
                continue
            member.name = stripped
            # 同步处理符号/硬链接目标（qlib bin 数据通常无链接，此处防御性处理）
            if member.issym() or member.islnk():
                if "/" in member.linkname:
                    member.linkname = member.linkname.split("/", STRIP_COMPONENTS)[STRIP_COMPONENTS]
            members_to_extract.append(member)
        tar.extractall(target_dir, members=members_to_extract)
    logger.info("解压完成: %s", target_dir)


def download_qlib_bin(target_dir: str) -> dict:
    """下载 chenditc 最新 qlib_bin.tar.gz 并解压到 target_dir。

    流程（先下载到临时文件，解压到暂存目录，校验后再替换目标目录，避免中途失败污染现有数据）：
      1. 查询最新 release
      2. 流式下载 qlib_bin.tar.gz 到临时文件
      3. 解压到暂存目录（strip components=1）
      4. 校验 calendars/day.txt 存在
      5. 备份并替换目标目录
      6. 清理临时文件与备份

    Args:
        target_dir: qlib bin 数据目标目录（如 .../data/qlib_bin/cn_data）
    Returns:
        {"version": release_tag, "date": release_date,
         "file": filename, "target_dir": target_dir}
    Raises:
        RuntimeError: 查询/下载/解压/校验失败
    """
    info = get_latest_release_info()
    download_url = info["download_url"]
    file_size = info.get("size", 0)

    # 1. 下载到临时文件
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="chenditc_qlib_")
    os.close(tmp_fd)
    try:
        max_retries = 5
        # 指数退避：5s/10s/20s/30s/60s，应对 GitHub Releases 大文件下载中断
        backoff_seconds = [5, 10, 20, 30, 60]
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    "下载尝试 %d/%d (url=%s, size=%d MB)",
                    attempt, max_retries, download_url, file_size // 1024 // 1024,
                )
                _download_stream(download_url, tmp_path, file_size)
                logger.info("下载成功（第 %d/%d 次尝试）", attempt, max_retries)
                break
            except Exception as e:
                if attempt == max_retries:
                    logger.error(
                        "下载彻底失败：已重试 %d 次仍不成功，最后错误: %s",
                        max_retries, e,
                    )
                    raise
                wait = backoff_seconds[attempt - 1]
                logger.warning(
                    "下载失败（第 %d/%d 次）: %s: %s，%ds 后重试",
                    attempt, max_retries, type(e).__name__, e, wait,
                )
                import time
                time.sleep(wait)

        # 2. 解压到暂存目录（与 target_dir 同级，保证同文件系统可原子 rename）
        parent = os.path.dirname(target_dir.rstrip("/")) or "."
        staging_dir = os.path.join(parent, os.path.basename(target_dir.rstrip("/")) + ".new")
        if os.path.exists(staging_dir):
            _clear_dir(staging_dir)
        os.makedirs(staging_dir, exist_ok=True)
        _extract_tarball(tmp_path, staging_dir)

        # 3. 校验解压结果
        day_txt = os.path.join(staging_dir, "calendars", "day.txt")
        if not os.path.exists(day_txt):
            raise RuntimeError(
                f"解压后未找到 calendars/day.txt，数据可能不完整: {staging_dir}"
            )

        # 4. 备份并替换目标目录
        backup_dir = os.path.join(parent, os.path.basename(target_dir.rstrip("/")) + ".old")
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        if os.path.exists(target_dir):
            os.rename(target_dir, backup_dir)
        os.rename(staging_dir, target_dir)

        # 5. 清理备份
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
    finally:
        # 清理临时 tarball
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    result = {
        "version": info["version"],
        "date": info["date"],
        "file": info["file"],
        "target_dir": target_dir,
    }
    logger.info("chenditc 数据就绪: %s", result)
    return result
