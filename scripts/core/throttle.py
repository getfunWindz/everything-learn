"""跨平台请求缓速（反爬基座）：平台级最小间隔 + 抖动；config.rate_limits 可精调。

- 适配器默认用 ThrottledSession("<platform>") 替代 requests.Session()；
  youtube/podcast 等直连 requests.get 的调用点用 throttle.wait("<platform>") 显式缓速。
- 默认值按风控风险分级；`rate_limits.<platform>.min_interval=0` 可关闭某平台缓速。
"""
import random
import threading
import time

import requests

_lock = threading.Lock()
_last = {}          # platform -> 上次请求时刻（monotonic）

# 每两次请求间的最小间隔（秒）：风控越高、值越大
_DEFAULTS = {"zhihu": 4.0, "douyin": 4.0, "weibo": 6.0, "xiaohongshu": 5.0,
             "medium": 10.0, "bilibili": 1.5, "youtube": 1.5, "podcast": 0.5, "default": 1.0}
_JITTER_DEFAULT = 0.5

# 测试可替换（便于注入时钟/睡眠）
_monotonic = time.monotonic
_sleep = time.sleep


def _interval(platform: str):
    """返回 (min_interval, jitter)：config.rate_limits.<platform> 覆盖默认值"""
    iv = _DEFAULTS.get(platform, _DEFAULTS["default"])
    jit = _JITTER_DEFAULT
    try:
        from core import config
        entry = ((config.load_config().get("rate_limits") or {}).get(platform) or {})
        iv = float(entry.get("min_interval", iv))
        jit = float(entry.get("jitter", jit))
    except Exception:
        pass
    return float(iv), float(jit)


def wait(platform: str) -> float:
    """阻塞到距上次请求 ≥ min_interval；返回本次实际等待秒数（含抖动）"""
    iv, jit = _interval(platform)
    if iv <= 0:
        return 0.0
    with _lock:
        now = _monotonic()
        due = _last.get(platform, 0.0) + iv
        delay = max(0.0, due - now)
    if delay > 0:
        delay += random.uniform(0, jit)
        _sleep(delay)
    with _lock:
        _last[platform] = _monotonic()
    return delay


def reset():
    """清空节流状态（测试/新运行使用）"""
    with _lock:
        _last.clear()


class ThrottledSession(requests.Session):
    """在每次请求前按平台缓速的 Session"""

    def __init__(self, platform: str):
        super().__init__()
        self.platform = platform

    def request(self, method, url, **kwargs):
        wait(self.platform)
        return super().request(method, url, **kwargs)
