"""增量数据同步：下载 chenditc 增量包并合并到现有 qlib bin 目录

chenditc 提供 qlib_bin_1d.tar.gz 增量包（仅当日新增数据），
体积远小于全量包（~5MB vs ~500MB），适合日常增量更新。
"""
import os
import logging
import tarfile
import tempfile
import requests
from app.services.data.chenditc_client import get_latest_release_info

logger = logging.getLogger(__name__)

INCREMENTAL_ASSET = "qlib_bin_1d.tar.gz"


def get_incremental_release_info() -> dict:
    """获取增量包 release 信息"""
    info = get_latest_release_info()
    if "error" in info:
        return info
    # chenditc 增量包名可能不同，尝试多种命名
    # 通过 release assets 查找增量包
    try:
        resp = requests.get(
            "https://api.github.com/repos/chenditc/investment_data/releases/latest",
            timeout=30,
        )
        resp.raise_for_status()
        release = resp.json()
        assets = {a["name"]: a for a in release["assets"]}
        # 优先找增量包
        for name in [INCREMENTAL_ASSET, "qlib_bin_1d.tar.gz", "qlib_bin_daily.tar.gz"]:
            if name in assets:
                return {
                    "version": release["tag_name"],
                    "date": release["published_at"],
                    "size": assets[name]["size"],
                    "download_url": assets[name]["browser_download_url"],
                    "file": name,
                }
        # 没有增量包，返回需要全量的标志
        logger.info("未找到增量包，将使用全量同步")
        return {"error": "no_incremental", "message": "增量包不存在，请使用全量同步"}
    except Exception as e:
        logger.error(f"获取增量包信息失败: {e}")
        return {"error": str(e)}


def download_and_merge_incremental(target_dir: str) -> dict:
    """下载增量包并合并到现有 qlib bin 目录

    增量包仅包含当日新增/更新的文件，直接覆盖到目标目录即可。
    """
    info = get_incremental_release_info()
    if "error" in info:
        return info

    try:
        # 流式下载增量包
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="chenditc_inc_")
        os.close(tmp_fd)
        try:
            with requests.get(info["download_url"], stream=True, timeout=(30, 120)) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
            logger.info("增量包下载完成: %d MB", downloaded // 1024 // 1024)

            # 解压并覆盖到目标目录
            with tarfile.open(tmp_path, "r:gz") as tar:
                members = []
                for m in tar.getmembers():
                    if m.name.startswith("qlib_bin/"):
                        m.name = m.name[len("qlib_bin/"):]
                        if m.name:
                            members.append(m)
                tar.extractall(target_dir, members=members)

            # 统计
            day_txt = os.path.join(target_dir, "calendars", "day.txt")
            latest_date = None
            if os.path.exists(day_txt):
                with open(day_txt) as f:
                    lines = [l.strip() for l in f if l.strip()]
                    if lines:
                        latest_date = lines[-1]

            logger.info("增量同步完成: latest_date=%s", latest_date)
            return {
                "version": info["version"],
                "release_date": info["date"],
                "latest_date": latest_date,
                "incremental": True,
                "downloaded_mb": round(downloaded / 1024 / 1024, 1),
            }
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    except Exception as e:
        logger.error(f"增量同步失败: {e}")
        return {"error": str(e)}
