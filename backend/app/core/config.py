import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class CompatModel(BaseModel):
    """向后兼容层：让 Pydantic 模型同时支持属性访问（`m.universe`）和
    旧 dict 风格的 `m.get("universe", "csi300")` 调用，避免改动 22+ 个调用点。

    Pydantic v2 默认禁止对模型做 `obj["key"] = value` 修改（model_config frozen），
    但项目旧测试与脚本会直接修改 settings（如下调 topk 做实验），因此显式开启
    item assignment，允许运行时"借用" Pydantic 做轻量校验 + 修改。

    键名既支持字段名也支持 model_extra（v2 默认丢弃未声明字段，可通过 ConfigDict 保留）。
    """

    model_config = {"frozen": False, "extra": "ignore"}

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as e:
            raise KeyError(key) from e

    def __setitem__(self, key: str, value: Any) -> None:
        """允许 `settings.quant["topk"] = 30` 这种旧写法（运行时改字段）。"""
        # model_extra 未声明字段直接落到 __dict__
        if key in self.__class__.model_fields:
            setattr(self, key, value)
        else:
            # 未声明字段（dict 类型的子段）允许直接赋值
            # Pydantic v2 不支持 extra 写入，用私有 __dict__
            self.__dict__[key] = value


class AppSettings(CompatModel):
    name: str = "QuantLab"
    version: str = "0.0.0"
    description: str = ""
    timezone: str = "Asia/Shanghai"
    debug: bool = False


class DataSettings(CompatModel):
    db_path: str = "data/quantlab.db"
    models_dir: str = "models"
    processed_dir: str = "data/processed"
    raw_dir: str = "data/raw"


class AIProviderEndpoint(CompatModel):
    provider: str = ""
    base_url: str = ""
    model: str = ""
    timeout_seconds: int = 30
    max_tokens: int = 2048
    temperature: float = 0.3


class AIProviderSettings(CompatModel):
    primary: AIProviderEndpoint = Field(default_factory=AIProviderEndpoint)
    fallback: AIProviderEndpoint = Field(default_factory=AIProviderEndpoint)
    tertiary: AIProviderEndpoint = Field(default_factory=AIProviderEndpoint)
    force_json_output: bool = True
    cache_ttl: str = "day"
    retry_times: int = 1
    route_budget_seconds: int = 120
    total_timeout_seconds: int = 10


class APISettings(CompatModel):
    request_timeout: int = 30
    version: str = "v1"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )


class SchedulerSettings(CompatModel):
    quant_data_update_time: str = "18:00"


class LoggingSettings(CompatModel):
    dir: str = "logs"
    level: str = "INFO"


class QuantSettings(CompatModel):
    adjust: str = "qfq"
    benchmark: str = "SH000300"
    cost_buy: float = 0.0013
    cost_sell: float = 0.0023
    data_source: str = "chenditc"
    default_backtest_period: dict[str, str] = Field(
        default_factory=lambda: {
            "start": "2020-01-01",
            "end": "2024-12-31",
        }
    )
    fetch_interval_seconds: float = 1.2
    fetch_max_workers: int = 3
    include_bj: bool = False
    n_drop: int = 5
    portfolio_optimizer: dict[str, Any] = Field(
        default_factory=lambda: {
            "enabled": False,
            "max_industry_exposure": 0.2,
            "max_weight": 0.05,
            "method": "mean_variance",
            "risk_aversion": 0.5,
        }
    )
    qlib_provider_uri: str = "data/qlib_bin/cn_data"
    slippage_bps: int = 5
    smart_sync: dict[str, Any] = Field(
        default_factory=lambda: {
            "full_sync_threshold_days": 7,
            "include_intraday": True,
        }
    )
    sync_indices: list[str] = Field(
        default_factory=lambda: [
            "sh000001",
            "sh000300",
            "sh000016",
            "sh000905",
            "sh000852",
            "sz399001",
            "sz399006",
            "sh000688",
        ]
    )
    topk: int = 50
    universe: str = "csi300"
    auto_retry_sync: bool = False


class MiningSettings(CompatModel):
    automl: dict[str, Any] = Field(default_factory=lambda: {"combo_method": "lightgbm"})
    llm: dict[str, Any] = Field(
        default_factory=lambda: {
            "candidates_per_run": 10,
            "eval_timeout_seconds": 60,
            "ic_threshold": 0.03,
            "allowed_ops": [
                "Ref",
                "Mean",
                "Std",
                "Max",
                "Min",
                "Sum",
                "Rank",
                "Corr",
                "Cov",
                "Delta",
                "Slope",
                "Resi",
                "WMA",
                "EMA",
                "$close",
                "$open",
                "$high",
                "$low",
                "$volume",
                "$factor",
            ],
        }
    )
    symbolic: dict[str, Any] = Field(
        default_factory=lambda: {
            "generations": 30,
            "ic_threshold": 0.03,
            "parsimony_coefficient": 0.001,
            "population": 1000,
            "tournament_size": 20,
        }
    )
    text: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_news_per_day": 50,
            "sentiment_labels": ["positive", "neutral", "negative"],
        }
    )


class TaskSettings(CompatModel):
    cpu_workers: int = 4
    io_workers: int = 8
    max_concurrent: int = 2
    task_timeout_seconds: int = 300
    timeouts: dict[str, int] = Field(
        default_factory=lambda: {
            "automl": 600,
            "llm": 300,
            "llm_hard_limit_seconds": 7200,
            "optimize": 600,
            "symbolic": 1800,
            "text": 900,
        }
    )


class SecuritySettings(CompatModel):
    """安全相关配置：来自环境变量，不在 yaml 中。"""

    app_env: str = "development"
    auth_enabled: bool = False
    secret_key: str = "change_this_to_random_string"
    admin_password: str = "admin123"
    admin_password_hash: str = ""
    login_rate_limit: str = "5/minute"


# 默认 SECRET_KEY 用于检测"未配置"
_DEFAULT_SECRET_KEY = "change_this_to_a_strong_random_string"
_PLACEHOLDER_ADMIN_PWD = "admin123"
_PLACEHOLDER_OPENCODEZEN = "your_opencodezen_api_key_here"
_PLACEHOLDER_GLM = "your_glm_api_key_here"
_PLACEHOLDER_SILICONFLOW = "your_siliconflow_api_key_here"
_PLACEHOLDER_GENERIC = "your_api_key_here"


def _is_placeholder_key(value: str) -> bool:
    if not value:
        return True
    return value in {
        _PLACEHOLDER_OPENCODEZEN,
        _PLACEHOLDER_GLM,
        _PLACEHOLDER_SILICONFLOW,
        _PLACEHOLDER_GENERIC,
    }


class Settings:
    """全局配置：单例模式，懒加载 .env + config.yaml。

    使用 Pydantic 模型做字段类型校验与默认值兜底，
    对外保留 `settings.<section>` 的字典式访问（`get(key, default)`）以维持向后兼容。

    访问模式：
        settings.app.name            # Pydantic 字段
        settings.quant.get("universe", "csi300")  # 字典访问（向后兼容）
    """

    _instance: "Settings | None" = None

    def __new__(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._load()

    # -- 加载 --

    def _load(self) -> None:
        # 显式从项目根目录加载 .env（避免 uvicorn reload / 不同 CWD 启动时找不到）
        _project_root = Path(os.getenv("PROJECT_ROOT") or Path(__file__).resolve().parents[3])
        load_dotenv(_project_root / ".env", override=False)
        self.PROJECT_ROOT = _project_root

        config_path = _project_root / "config.yaml"
        cfg: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

        # 各 section 用 Pydantic 校验（缺失字段自动补默认，类型错误抛出清晰异常）
        self.app = AppSettings(**(cfg.get("app") or {}))
        self.data = DataSettings(**(cfg.get("data") or {}))
        self.ai_provider = AIProviderSettings(**(cfg.get("ai_provider") or {}))
        self.api = APISettings(**(cfg.get("api") or {}))
        self.scheduler = SchedulerSettings(**(cfg.get("scheduler") or {}))
        self.logging = LoggingSettings(**(cfg.get("logging") or {}))
        self.quant = QuantSettings(**(cfg.get("quant") or {}))
        self.mining = MiningSettings(**(cfg.get("mining") or {}))
        self.task = TaskSettings(**(cfg.get("task") or {}))

        # AI Provider API keys：环境变量覆盖 yaml
        self.glm_api_key = os.getenv("GLM_API_KEY", "")
        self.siliconflow_api_key = os.getenv("SILICONFLOW_API_KEY", "")
        self.opencodezen_api_key = os.getenv("OPENCODEZEN_API_KEY", "")

        # 安全配置（仅环境变量，不写 yaml）
        app_env = os.getenv("APP_ENV", "development").lower()
        auth_env = os.getenv("AUTH_ENABLED")
        if auth_env is not None:
            auth_enabled = auth_env.lower() in ("1", "true", "yes", "on")
        else:
            auth_enabled = app_env != "development"
        secret_key_value = os.getenv("SECRET_KEY", "change_this_to_random_string")
        admin_pwd_value = os.getenv("ADMIN_PASSWORD", _PLACEHOLDER_ADMIN_PWD)
        admin_pwd_hash_value = os.getenv("ADMIN_PASSWORD_HASH", "")
        self.security = SecuritySettings(
            app_env=app_env,
            auth_enabled=auth_enabled,
            secret_key=secret_key_value,
            admin_password=admin_pwd_value,
            admin_password_hash=admin_pwd_hash_value,
            login_rate_limit=os.getenv("LOGIN_RATE_LIMIT", "5/minute"),
        )
        # 保留旧私有属性以兼容旧测试与反射访问（auth._auth_enabled 等）
        self._auth_enabled = auth_enabled
        self._secret_key = secret_key_value
        self._admin_password = admin_pwd_value
        self._admin_password_hash = admin_pwd_hash_value
        self._app_env = app_env

    # -- 向后兼容 property --

    @property
    def auth_enabled(self) -> bool:
        # 兼容 monkeypatch.setattr(settings, "_auth_enabled", ...) 的旧测试
        return getattr(self, "_auth_enabled", self.security.auth_enabled)

    @property
    def secret_key(self) -> str:
        return getattr(self, "_secret_key", self.security.secret_key)

    @property
    def admin_password(self) -> str:
        return getattr(self, "_admin_password", self.security.admin_password)

    @property
    def admin_password_hash(self) -> str:
        return getattr(
            self,
            "_admin_password_hash",
            self.security.admin_password_hash,
        )

    @property
    def app_env(self) -> str:
        return getattr(self, "_app_env", self.security.app_env)

    @property
    def app_name(self) -> str:
        return self.app.name

    @property
    def app_version(self) -> str:
        return self.app.version

    @property
    def app_description(self) -> str:
        return self.app.description

    @property
    def app_timezone(self) -> str:
        return self.app.timezone

    @property
    def db_path(self) -> str:
        return str(self.PROJECT_ROOT / self.data.db_path)

    @property
    def qlib_provider_path(self) -> str:
        return str(self.PROJECT_ROOT / self.quant.qlib_provider_uri)

    @property
    def login_rate_limit(self) -> str:
        return self.security.login_rate_limit

    # -- 安全校验 --

    def validate_security(self) -> list[str]:
        # 读取兼容层：monkeypatch 可改 _auth_enabled / _secret_key 等
        auth_enabled = self.auth_enabled
        secret_key = self.secret_key
        admin_password = self.admin_password
        admin_password_hash = self.admin_password_hash

        warnings: list[str] = []
        if not auth_enabled:
            warnings.append("AUTH_ENABLED=false：业务接口无鉴权，仅限本地开发使用")
        if secret_key == "change_this_to_random_string":
            warnings.append("SECRET_KEY 仍为默认值，token 可被伪造，请设置强随机值")
        if not admin_password_hash and admin_password == _PLACEHOLDER_ADMIN_PWD:
            warnings.append("ADMIN_PASSWORD 仍为默认值 admin123，请修改")
        return warnings

    def enforce_production_security(self) -> None:
        # 读取兼容层
        auth_enabled = self.auth_enabled
        secret_key = self.secret_key
        admin_password = self.admin_password
        admin_password_hash = self.admin_password_hash
        app_env = self.app_env

        if app_env == "development":
            return
        blockers: list[str] = []
        if not auth_enabled:
            blockers.append("AUTH_ENABLED 必须为 true（生产环境）")
        if secret_key in (
            "change_this_to_random_string",
            _DEFAULT_SECRET_KEY,
        ):
            blockers.append("SECRET_KEY 不能使用默认值，请设置强随机串")
        if not admin_password_hash and admin_password == _PLACEHOLDER_ADMIN_PWD:
            blockers.append("ADMIN_PASSWORD 不能为默认 admin123，请修改或改用 ADMIN_PASSWORD_HASH")
        if blockers:
            raise RuntimeError("生产环境安全配置不达标，拒绝启动:\n  - " + "\n  - ".join(blockers))


settings = Settings()


def is_placeholder_api_key(value: str | None) -> bool:
    """检测 API Key 是否仍是占位符（用于 ai/provider_router）。"""
    return _is_placeholder_key(value or "")
