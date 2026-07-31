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
        # 显式从项目根目录加载 .env（避免 uvicorn reload / 不同 CWD 启动时找不到）
        # config.py 路径：backend/app/core/config.py → 项目根 = parents[3]
        _project_root = Path(os.getenv("PROJECT_ROOT") or Path(__file__).resolve().parents[3])
        load_dotenv(_project_root / ".env", override=False)
        # 支持通过环境变量指定项目根目录（Docker 部署时需要）
        self.PROJECT_ROOT = _project_root
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
        self.opencodezen_api_key = os.getenv("OPENCODEZEN_API_KEY", "")

        # ---- 安全配置 ----
        self._app_env = os.getenv("APP_ENV", "development").lower()
        # AUTH_ENABLED 未显式设置时按环境判定：development 默认关，生产默认开
        auth_env = os.getenv("AUTH_ENABLED")
        if auth_env is not None:
            self._auth_enabled = auth_env.lower() in ("1", "true", "yes", "on")
        else:
            self._auth_enabled = self._app_env != "development"
        self._secret_key = os.getenv("SECRET_KEY", "change_this_to_random_string")
        self._admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
        self._admin_password_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
        # 登录限流：每分钟最多尝试次数
        self.login_rate_limit = os.getenv("LOGIN_RATE_LIMIT", "5/minute")

    @property
    def auth_enabled(self) -> bool:
        return self._auth_enabled

    @property
    def secret_key(self) -> str:
        return self._secret_key

    @property
    def admin_password(self) -> str:
        return self._admin_password

    @property
    def admin_password_hash(self) -> str:
        return self._admin_password_hash

    @property
    def app_env(self) -> str:
        return self._app_env

    @property
    def app_name(self) -> str:
        return self.app.get("name", "QuantLab")

    @property
    def app_version(self) -> str:
        return self.app.get("version", "0.0.0")

    @property
    def app_description(self) -> str:
        return self.app.get("description", "")

    @property
    def app_timezone(self) -> str:
        return self.app.get("timezone", "Asia/Shanghai")

    def validate_security(self) -> list[str]:
        """启动时校验安全配置，返回告警列表（非默认值则空）。"""
        warnings = []
        if not self._auth_enabled:
            warnings.append("AUTH_ENABLED=false：业务接口无鉴权，仅限本地开发使用")
        if self._secret_key == "change_this_to_random_string":
            warnings.append("SECRET_KEY 仍为默认值，token 可被伪造，请设置强随机值")
        if not self._admin_password_hash and self._admin_password == "admin123":
            warnings.append("ADMIN_PASSWORD 仍为默认值 admin123，请修改")
        return warnings

    def enforce_production_security(self) -> None:
        """生产环境强制安全闸门：默认密钥/口令时拒绝启动。

        仅在 APP_ENV 非 development 时生效。开发环境只告警不阻断。
        """
        if self._app_env == "development":
            return
        blockers = []
        if not self._auth_enabled:
            blockers.append("AUTH_ENABLED 必须为 true（生产环境）")
        if self._secret_key in ("change_this_to_random_string", "please_change_to_a_strong_random_string"):
            blockers.append("SECRET_KEY 不能使用默认值，请设置强随机串")
        if not self._admin_password_hash and self._admin_password == "admin123":
            blockers.append("ADMIN_PASSWORD 不能为默认 admin123，请修改或改用 ADMIN_PASSWORD_HASH")
        if blockers:
            raise RuntimeError("生产环境安全配置不达标，拒绝启动:\n  - " + "\n  - ".join(blockers))

    @property
    def db_path(self):
        return str(self.PROJECT_ROOT / self.data.get("db_path", "data/quantlab.db"))

    @property
    def qlib_provider_path(self):
        """qlib bin 数据目录的绝对路径"""
        return str(self.PROJECT_ROOT / self.quant.get("qlib_provider_uri", "data/qlib_bin/cn_data"))


settings = Settings()
