import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core import throttle


@pytest.fixture(autouse=True)
def _clean_state():
    throttle.reset()
    yield
    throttle.reset()


def test_first_call_no_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(throttle, "_sleep", lambda s: slept.append(s))
    monkeypatch.setattr(throttle, "_monotonic", lambda: 100.0)
    throttle.wait("zhihu")
    assert slept == []


def test_second_call_waits(monkeypatch):
    slept, now = [], {"t": 100.0}
    monkeypatch.setattr(throttle, "_sleep", lambda s: (slept.append(s), now.update(t=now["t"] + s)))
    monkeypatch.setattr(throttle, "_monotonic", lambda: now["t"])
    monkeypatch.setattr(throttle, "_interval", lambda platform: (4.0, 0.0))  # 便于断言
    throttle.wait("zhihu")          # t=100，无等待
    now["t"] = 101.0                # 1s 后再请求 → 应等 3s
    throttle.wait("zhihu")
    assert slept == [3.0]


def test_config_override(monkeypatch):
    from core import config as cfg
    monkeypatch.setattr(cfg, "load_config",
                        lambda: {"rate_limits": {"zhihu": {"min_interval": 8, "jitter": 0}}})
    assert throttle._interval("zhihu") == (8.0, 0.0)


def test_zero_interval_disables(monkeypatch):
    monkeypatch.setattr(throttle, "_interval", lambda platform: (0.0, 0.0))
    slept = []
    monkeypatch.setattr(throttle, "_sleep", lambda s: slept.append(s))
    throttle.wait("x")
    throttle.wait("x")
    assert slept == []


def test_throttled_session_waits_before_request(monkeypatch):
    import requests
    from core.throttle import ThrottledSession
    calls = []
    monkeypatch.setattr(throttle, "wait", lambda platform: calls.append(platform))
    sent = []
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: sent.append(url) or "OK")
    s = ThrottledSession("zhihu")
    assert s.request("GET", "https://x/1") == "OK"
    assert calls == ["zhihu"] and sent == ["https://x/1"]


def test_medium_default_is_conservative(monkeypatch):
    """Medium（Cloudflare 高风控）默认间隔显著大于普通平台"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {})
    iv, jit = throttle._interval("medium")
    assert iv >= 8.0 and jit > 0


def test_weibo_default_is_conservative(monkeypatch):
    """微博（432 敏感）默认间隔 >= 5s"""
    from core import config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {})
    iv, jit = throttle._interval("weibo")
    assert iv >= 5.0 and jit > 0
