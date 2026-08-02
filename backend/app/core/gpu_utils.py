"""GPU 检测工具：判断当前环境是否可用 GPU 加速。

仅在显式导入时检查，避免 torch 加载副作用。
"""
import logging

logger = logging.getLogger(__name__)


def is_gpu_available() -> bool:
    """检测 GPU 是否可用。

    优先检查 torch.cuda，其次检查 nvidia-smi。
    """
    try:
        import torch
        available = torch.cuda.is_available()
        if available:
            device_count = torch.cuda.device_count()
            device_name = torch.cuda.get_device_name(0) if device_count > 0 else "unknown"
            logger.info("GPU 可用: %s (%d 个设备), 设备: %s", available, device_count, device_name)
        else:
            logger.info("CUDA 不可用 (torch.cuda.is_available()=False)")
        return available
    except ImportError:
        logger.debug("torch 未安装，无法使用 GPU")
    except Exception as e:
        logger.debug("GPU 检测异常: %s", e)

    # 兜底：检查 nvidia-smi
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info("GPU 可用 (nvidia-smi): %s", result.stdout.strip()[:50])
            return True
    except Exception:
        pass

    logger.info("GPU 不可用，将使用 CPU")
    return False


def get_gpu_device() -> str:
    """获取 GPU 设备名称（可用于日志/监控）。"""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "cpu"


def get_device() -> str:
    """返回 'cuda' 或 'cpu'，用于 torch 设备选择。"""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"
