"""把 scripts/ 加入 sys.path，使 `core` / `providers` 以顶层模块导入。

这样既支持 `python scripts/el.py`，也支持 `pipx install --editable .` 后的
`everything-learn` console script（以及非 editable 安装）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
