import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsBaseModel(BaseModel):
    """子模型基类：提供 .get() / __getitem__ / __setitem__ 兼容层。

    替换旧 CompatModel，保留 dict 风格访问兼容性，避免改动 22+ 个调用点。
    Pydantic v2 默认禁止对模型做 obj["key"] = value 修改（model_config frozen），
    但项目旧测试与脚本会直接修改 settings（如下调 topk 做实验），因此显式开启
    item assignment，允许运行时"借用" Pydantic 做轻量校验 + 修改。
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
        """允许 settings.quant["topk"] = 30 这种旧写法（运行时改字段）。"""
        if key in self.__class__.model_fields:
            setattr(self, key, value)
        else:
            self.__dict__[key] = value


class AppSettings(SettingsBaseModel):
    name: str = "QuantLab"
    version: str = "0.0.0"
    description: str = ""
    timezone: str = "Asia/Shanghai"
    debug: bool = False


class DataSettings(SettingsBaseModel):
    db_path: str = "data/quantlab.db"
    models_dir: str = "models"
    processed_dir: str = "data/processed"
    raw_dir: str = "data/raw"


class AIProviderEndpoint(SettingsBaseModel):
    provider: str = ""
    base_url: str = ""
    model: str = ""
    timeout_seconds: int = 30
    max_tokens: int = 2048
    temperature: float = 0.3


class AIProviderSettings(SettingsBaseModel):
    primary: AIProviderEndpoint = Field(default_factory=AIProviderEndpoint)
    fallback: AIProviderEndpoint = Field(default_factory=AIProviderEndpoint)
    tertiary: AIProviderEndpoint = Field(default_factory=AIProviderEndpoint)
    force_json_output: bool = True
    cache_ttl: str = "day"
    retry_times: int = 1
    route_budget_seconds: int = 120
    total_timeout_seconds: int = 10


class APISettings(SettingsBaseModel):
    request_timeout: int = 30
    version: str = "v1"
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )


class SchedulerSettings(SettingsBaseModel):
    quant_data_update_time: str = "18:00"


class LoggingSettings(SettingsBaseModel):
    dir: str = "logs"
    level: str = "INFO"


class QuantSettings(SettingsBaseModel):
    adjust: str = "qfq"
    benchmark: str = "SH000300"
    cost_buy: float = 0.0013
    cost_sell: float = 0.0023
    data_source: str = "baostock"
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
    qlib_provider_uri: str = "data/qlib_bin/cn_data"
    slippage_bps: int = 5
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


class MiningSettings(SettingsBaseModel):
    automl: dict[str, Any] = Field(default_factory=lambda: {"combo_method": "lightgbm"})
    llm: dict[str, Any] = Field(
        default_factory=lambda: {
            "candidates_per_run": 10,
            "eval_timeout_seconds": 60,
            "ic_threshold": 0.03,
            "eval_horizon": 5,
            "significance_alpha": 0.05,
            "stability_threshold": 0.5,
            "positive_ratio_threshold": 0.55,
            "decay_threshold": -0.01,
            "diversity_threshold": 0.8,
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
                "$amount",
                "$change",
                "$turn",
                "$preclose",
                "$pe_ttm",
                "$pb_mrq",
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


class TaskSettings(SettingsBaseModel):
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


class SecuritySettings(SettingsBaseModel):
    """安全相关配置：来自环境变量，不在 yaml 中。"""

    app_env: str = "development"
    auth_enabled: bool = False
    secret_key: str = "change_this_to_random_string"
    admin_password: str = "admin123"
    admin_password_hash: str = ""
    login_rate_limit: str = "5/minute"
    access_token_expire_hours: int = 24


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


class Settings(BaseSettings):
    """全局配置：基于 pydantic-settings BaseSettings，自动加载 .env + 手动加载 config.yaml。

    pydantic-settings 自动从 .env 和环境变量读取字段值。
    子模型（app, quant, mining 等）从 config.yaml 加载。
    安全配置（security）从环境变量加载。

    访问模式：
        settings.app.name                 # 属性访问
        settings.quant.get("universe", "csi300")  # dict 风格访问（向后兼容）
        settings.security.auth_enabled    # 安全配置
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        arbitrary_types_allowed=True,
    )

    # -- 子模型（从 config.yaml 加载） --
    app: AppSettings = AppSettings()
    data: DataSettings = DataSettings()
    ai_provider: AIProviderSettings = AIProviderSettings()
    api: APISettings = APISettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    logging: LoggingSettings = LoggingSettings()
    quant: QuantSettings = QuantSettings()
    mining: MiningSettings = MiningSettings()
    task: TaskSettings = TaskSettings()

    # -- API keys（从 .env / 环境变量加载） --
    glm_api_key: str = ""
    siliconflow_api_key: str = ""
    opencodezen_api_key: str = ""

    # -- 安全配置（从环境变量加载，不在 yaml 中） --
    security: SecuritySettings = SecuritySettings()

    # -- 项目根目录 --
    PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]

    def model_post_init(self, __context: Any) -> None:
        """初始化后加载 yaml 和环境变量安全配置。"""
        # 显式从项目根目录加载 .env（避免 uvicorn reload / 不同 CWD 启动时找不到）
        _project_root = Path(os.getenv("PROJECT_ROOT") or self.PROJECT_ROOT)
        load_dotenv(_project_root / ".env", override=False)
        self.PROJECT_ROOT = _project_root

        self._load_yaml()
        self._load_security_from_env()

    def _load_yaml(self) -> None:
        """从 config.yaml 加载子模型配置。"""
        config_path = self.PROJECT_ROOT / "config.yaml"
        if not config_path.exists():
            return
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        section_map = {
            "app": "app",
            "data": "data",
            "ai_provider": "ai_provider",
            "api": "api",
            "scheduler": "scheduler",
            "logging": "logging",
            "quant": "quant",
            "mining": "mining",
            "task": "task",
        }
        for section, field_name in section_map.items():
            if section in cfg:
                current = getattr(self, field_name)
                updated = current.__class__(**(cfg[section]))
                setattr(self, field_name, updated)

    def _load_security_from_env(self) -> None:
        """从环境变量加载安全配置。"""
        app_env = os.getenv("APP_ENV", "development").lower()
        auth_env = os.getenv("AUTH_ENABLED")
        if auth_env is not None:
            auth_enabled = auth_env.lower() in ("1", "true", "yes", "on")
        else:
            auth_enabled = app_env != "development"
        self.security = SecuritySettings(
            app_env=app_env,
            auth_enabled=auth_enabled,
            secret_key=os.getenv("SECRET_KEY", "change_this_to_random_string"),
            admin_password=os.getenv("ADMIN_PASSWORD", "admin123"),
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", ""),
            login_rate_limit=os.getenv("LOGIN_RATE_LIMIT", "5/minute"),
            access_token_expire_hours=int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "24")),
        )

    # -- 便捷 property --

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
        warnings: list[str] = []
        if not self.security.auth_enabled:
            warnings.append("AUTH_ENABLED=false：业务接口无鉴权，仅限本地开发使用")
        if self.security.secret_key == "change_this_to_random_string":
            warnings.append("SECRET_KEY 仍为默认值，token 可被伪造，请设置强随机值")
        if not self.security.admin_password_hash and self.security.admin_password == "admin123":
            warnings.append("ADMIN_PASSWORD 仍为默认值 admin123，请修改")
        return warnings

    def enforce_production_security(self) -> None:
        if self.security.app_env == "development":
            return
        blockers: list[str] = []
        if not self.security.auth_enabled:
            blockers.append("AUTH_ENABLED 必须为 true（生产环境）")
        if self.security.secret_key in (
            "change_this_to_random_string",
            "change_this_to_a_strong_random_string",
        ):
            blockers.append("SECRET_KEY 不能使用默认值，请设置强随机串")
        if not self.security.admin_password_hash and self.security.admin_password == "admin123":
            blockers.append("ADMIN_PASSWORD 不能为默认 admin123，请修改或改用 ADMIN_PASSWORD_HASH")
        if blockers:
            raise RuntimeError("生产环境安全配置不达标，拒绝启动:\n  - " + "\n  - ".join(blockers))


settings = Settings()


def is_placeholder_api_key(value: str | None) -> bool:
    """检测 API Key 是否仍是占位符（用于 ai/provider_router）。"""
    return _is_placeholder_key(value or "")
