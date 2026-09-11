"""response_model 契约测试（golden-keys 安全网）。

背景：给高频 GET 端点补 response_model 时，最大的风险是 Pydantic 模型
漏字段/改类型导致响应 payload 变化（前端消费精确字段名）。本文件用
TestClient + mock（qlib/DB/log_dir）抓取每个端点**当前实际响应的全部键
（含嵌套）与 JSON 原始类型**，形成 golden 快照断言。

规则：
- 加 response_model 之前先写断言（golden 基线）；
- 加完 response_model 之后这些测试必须原样通过（字段一个都不能丢/改）。
- shape 断言比较 JSON 类型名（str/int/float/NoneType/list/dict），
  不比较具体数值。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.factor import router as factor_router
from app.api.logs import router as logs_router
from app.api.market import _kline_cache, _overview_cache
from app.api.market import router as market_router


def _shape(obj):
    """递归提取响应结构：dict→{key: shape}，list→[首元素 shape]，标量→类型名。"""
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shape(obj[0])] if obj else []
    return type(obj).__name__


@pytest.fixture
def client():
    """最小 FastAPI app：只挂 market/factor/logs 三个路由，不触发 lifespan/init_db。"""
    app = FastAPI()
    app.include_router(market_router)
    app.include_router(factor_router)
    app.include_router(logs_router)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_market_cache():
    """清空 market 模块的 TTL 缓存，保证 mock 数据每次都被读到。"""
    _overview_cache.clear()
    _kline_cache.clear()
    yield
    _overview_cache.clear()
    _kline_cache.clear()


# ---------------- market ----------------


def _mock_index_df():
    """模拟 qlib D.features 返回：3 个交易日 + 1 个 NaN 日历日（今日未发布）。"""
    idx = pd.MultiIndex.from_product(
        [["sh000300"], pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"])],
        names=["instrument", "datetime"],
    )
    return pd.DataFrame(
        {
            "$open": [4566.0, 4550.0, 4621.0, None],
            "$high": [4613.0, 4679.0, 4675.0, None],
            "$low": [4555.0, 4550.0, 4611.0, None],
            "$close": [4600.0, 4658.0, 4651.0, None],
            "$volume": [24207362048, 27692191744, 24751073280, None],
        },
        index=idx,
    )


@pytest.fixture
def market_client(client):
    with patch("app.api.market.is_qlib_available", new_callable=AsyncMock, return_value=True), \
         patch("app.api.market.init_qlib", return_value=True), \
         patch("qlib.data.D", MagicMock(features=MagicMock(return_value=_mock_index_df()))):
        yield client


def test_market_overview_golden_keys(market_client):
    res = market_client.get("/market/overview")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert _shape(body["data"]) == {
        "items": [
            {
                "code": "str",
                "name": "str",
                "price": "float",
                "pct_change": "float",
            }
        ]
    }


def test_market_indices_golden_keys(client):
    res = client.get("/market/indices")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert _shape(body["data"]) == {
        "items": [
            {
                "code": "str",
                "name": "str",
                "desc": "str",
                "qlib_code": "str",
            }
        ]
    }


def test_market_kline_golden_keys(market_client):
    res = market_client.get("/market/kline/SH000300")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    # NaN 日历日已被过滤：items 只有 3 条
    assert body["data"]["count"] == 3
    assert _shape(body["data"]) == {
        "index_code": "str",
        "index_name": "str",
        "period": "str",
        "count": "int",
        "items": [
            {
                "date": "str",
                "open": "float",
                "high": "float",
                "low": "float",
                "close": "float",
                "volume": "int",
                # 首条 pct_change 是 NaN→0（int），后续为 float；首元素锁定为 int
                "pct_change": "int",
            }
        ],
    }


# ---------------- factor ----------------


def _factor_dict():
    """与 services.factor.library._to_dict 完全一致的 19 键结构。"""
    return {
        "id": 1,
        "name": "mom20",
        "expression": "$close / Ref($close, 20) - 1",
        "category": "momentum",
        "description": None,
        "ic": 0.05,
        "rank_ic": 0.06,
        "icir": 0.4,
        "ir": 0.35,
        "turnover": 0.12,
        "decay": [0.05, 0.04, 0.03],
        "ic_by_horizon": {"1": 0.03, "5": 0.05, "10": 0.04},
        "orthogonal_ic": 0.02,
        "eval_start": "2024-01-01",
        "eval_end": "2025-12-31",
        "evaluated_at": "2026-08-05T09:00:00",
        "status": "active",
        "source_task_id": None,
        "created_at": "2026-01-01T00:00:00",
    }


@pytest.fixture
def factor_client(client):
    with patch("app.api.factor.list_factors",
               new_callable=AsyncMock, return_value=([_factor_dict()], 1)), \
         patch("app.api.factor.get_factor",
               new_callable=AsyncMock, return_value=_factor_dict()):
        yield client


def test_factor_list_golden_keys(factor_client):
    res = factor_client.get("/factors")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert _shape(body["data"]) == {
        "items": [
            {
                "id": "int",
                "name": "str",
                "expression": "str",
                "category": "str",
                "description": "NoneType",
                "ic": "float",
                "rank_ic": "float",
                "icir": "float",
                "ir": "float",
                "turnover": "float",
                "decay": ["float"],
                "ic_by_horizon": {"1": "float", "5": "float", "10": "float"},
                "orthogonal_ic": "float",
                "eval_start": "str",
                "eval_end": "str",
                "evaluated_at": "str",
                "status": "str",
                "source_task_id": "NoneType",
                "created_at": "str",
            }
        ],
        "total": "int",
    }


def test_factor_detail_golden_keys(factor_client):
    res = factor_client.get("/factors/1")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    shape = _shape(body["data"])
    # 详情与列表项同构（_to_dict），19 键一个不能少
    assert set(shape) == {
        "id", "name", "expression", "category", "description",
        "ic", "rank_ic", "icir", "ir", "turnover",
        "decay", "ic_by_horizon", "orthogonal_ic",
        "eval_start", "eval_end", "evaluated_at",
        "status", "source_task_id", "created_at",
    }


def test_factor_detail_not_found_keeps_error_envelope(factor_client):
    """NOT_FOUND 业务错误路径（HTTP 200 + ok=False + error dict）不受 response_model 影响。"""
    with patch("app.api.factor.get_factor", new_callable=AsyncMock, return_value=None):
        res = factor_client.get("/factors/999")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"


# ---------------- logs ----------------


@pytest.fixture
def logs_client(client, tmp_path, monkeypatch):
    """把 log_dir 指到临时目录：quantlab.log（JSON 行）+ error.log（文本格式）。"""
    import app.core.logging_config as logging_config

    monkeypatch.setattr(logging_config, "log_dir", tmp_path)
    json_lines = [
        {"timestamp": "2026-08-01T01:00:00.000Z", "level": "info", "logger": "app.x",
         "event": "hello", "request_id": "abc", "worker_kind": "backfill"},
        {"timestamp": "2026-08-02T01:00:00.000Z", "level": "error", "logger": "app.y",
         "detail": "boom", "request_id": "", "exception": "Traceback..."},
    ]
    with open(tmp_path / "quantlab.log", "w", encoding="utf-8") as f:
        for ln in json_lines:
            f.write(__import__("json").dumps(ln) + "\n")
    (tmp_path / "error.log").write_text(
        "2026-07-28 15:34:23,123 [INFO] app.module: hello [req=abc123]\n",
        encoding="utf-8",
    )
    yield client


def test_logs_files_golden_keys(logs_client):
    res = logs_client.get("/logs/files")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert _shape(body["data"]) == {
        "items": [
            {
                "name": "str",
                "size": "int",
                "size_human": "str",
                "backup_count": "int",
                "backup_size": "int",
                "backup_size_human": "str",
                "modified": "str",
            }
        ]
    }


def test_logs_list_json_golden_keys(logs_client):
    """JSON 日志（当前 3 个受管文件的实际格式）：条目固定 8 键。"""
    res = logs_client.get("/logs", params={"file": "quantlab.log"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert _shape(body["data"]) == {
        "items": [
            {
                "timestamp": "str",
                "level": "str",
                "logger": "str",
                "message": "str",
                "request_id": "str",
                "traceback": "str",
                "detail": "str",
                "worker_kind": "str",
            }
        ],
        "total": "int",
        "file": "str",
    }


def test_logs_list_text_golden_keys(logs_client):
    """文本日志（兼容回退路径）：条目只有 6 键（无 detail/worker_kind）。

    response_model 的 LogEntry 模型不得给缺失键补 null —— 所以 detail/worker_kind
    走 extra="allow" 透传，不声明为带默认值的字段。
    """
    res = logs_client.get("/logs", params={"file": "error.log"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert _shape(body["data"]) == {
        "items": [
            {
                "timestamp": "str",
                "level": "str",
                "logger": "str",
                "message": "str",
                "request_id": "str",
                "traceback": "str",
            }
        ],
        "total": "int",
        "file": "str",
    }


# ---------------- openapi ----------------


def test_openapi_declares_response_schemas(client):
    """第一批 7 个端点必须在 OpenAPI 里声明 payload schema（$ref 到 ApiResponse[...]）。"""
    spec = client.get("/openapi.json").json()
    expected = [
        ("/market/overview", "get"),
        ("/market/indices", "get"),
        ("/market/kline/{index_code}", "get"),
        ("/factors", "get"),
        ("/factors/{factor_id}", "get"),
        ("/logs/files", "get"),
        ("/logs", "get"),
    ]
    for path, method in expected:
        op = spec["paths"][path][method]
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in schema, f"{path} 未声明 response_model"
        assert "ApiResponse" in schema["$ref"], f"{path} 的响应模型不是 ApiResponse[...]"
