import os
import yaml
from pathlib import Path
from dotenv import load_dotenv


class Settings:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        load_dotenv()
        # 支持通过环境变量指定项目根目录（Docker 部署时需要）
        self.PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT") or Path(__file__).resolve().parent.parent.parent.parent)
        config_path = self.PROJECT_ROOT / "config.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self.app = cfg.get("app", {})
        self.data = cfg.get("data", {})
        self.ai_provider = cfg.get("ai_provider", {})
        self.api = cfg.get("api", {})
        self.scheduler = cfg.get("scheduler", {})
        self.logging = cfg.get("logging", {})
        self.quant = cfg.get("quant", {})
        self.mining = cfg.get("mining", {})
        self.task = cfg.get("task", {"max_concurrent": 2, "task_timeout_seconds": 300})
        self.glm_api_key = os.getenv("GLM_API_KEY", "")
        self.siliconflow_api_key = os.getenv("SILICONFLOW_API_KEY", "")

    @property
    def db_path(self):
        return str(self.PROJECT_ROOT / self.data.get("db_path", "data/quantlab.db"))

    @property
    def qlib_provider_path(self):
        """qlib bin 数据目录的绝对路径"""
        return str(self.PROJECT_ROOT / self.quant.get("qlib_provider_uri", "data/qlib_bin/cn_data"))


settings = Settings()
