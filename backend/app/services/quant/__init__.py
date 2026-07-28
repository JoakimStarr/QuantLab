"""量化内核模块：基于 qlib 的因子评价、回测与组合分析。

qlib 为 CPU 密集型同步库，所有调用应通过 ProcessPool/worker 执行，
FastAPI 服务层仅做调度。qlib 采用懒加载，未安装时模块仍可导入，
仅在调用量化功能时抛出 QlibNotAvailableError。
"""
