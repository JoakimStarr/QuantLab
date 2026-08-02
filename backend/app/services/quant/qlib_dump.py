"""qlib DumpDataAll 包装器。

将 qlib 官方 scripts/dump_bin.py 中的 DumpDataAll 暴露为 DumpAll，
方便在 data_adapter 中替换自研 bin 转储。

qlib 的 DumpDataAll 功能：
- 读取 CSV 目录，扫描所有 *.csv 文件
- 生成全局日历 calendars/day.txt
- 生成 instruments/all.txt（code\\tstart\\tend）
- 生成 features/<code>/<field>.day.bin（小端 float32）

DumpDataAll 的 bin 格式与本项目自研格式一致：
[start_index:float32] + [data:float32...]
"""
import sys
import os

# qlib 已以 editable 模式安装，scripts 目录在 qlib 包同级
import qlib as _qlib

_scripts_dir = os.path.join(os.path.dirname(_qlib.__file__), "..", "scripts")
_scripts_dir = os.path.abspath(_scripts_dir)

if os.path.isdir(_scripts_dir) and _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
