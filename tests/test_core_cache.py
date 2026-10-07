import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
import core.cache as cache
from core.retry import retry_with_backoff


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    yield


def test_cache_key_platform_aware():
    """同 item_id 不同平台不串缓存"""
    cache.set_cached("bilibili", "BV1xx", "1", [{"start": 0, "end": 1, "text": "hi"}])
    assert cache.get_cached("youtube", "BV1xx", "1") is None
    assert cache.get_cached("bilibili", "BV1xx", "1")[0]["text"] == "hi"


def test_cache_without_sub_id():
    cache.set_cached("bilibili", "BV1yy", "", [{"start": 0, "end": 1, "text": "a"}])
    assert cache.get_cached("bilibili", "BV1yy")[0]["text"] == "a"
    assert cache.get_cached("bilibili", "BV1yy", "2") is None


def test_cache_corrupt_returns_none():
    p = os.path.join(cache.CACHE_DIR, "bilibili_BV1zz.json")
    open(p, "w", encoding="utf-8").write("{broken")
    assert cache.get_cached("bilibili", "BV1zz") is None


def test_cache_clear():
    cache.set_cached("bilibili", "A", "", [{"x": 1}])
    cache.set_cached("bilibili", "B", "", [{"x": 2}])
    assert cache.clear_cache("bilibili", "A") == 1
    assert cache.get_cached("bilibili", "A") is None


# ---------- retry ----------

def test_retry_succeeds_on_retry():
    n = {"i": 0}
    def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise RuntimeError("boom")
        return "ok"
    assert retry_with_backoff(flaky, attempts=4, base_delay=0) == "ok"


def test_retry_gives_up():
    def always_fail():
        raise ValueError("nope")
    with pytest.raises(ValueError):
        retry_with_backoff(always_fail, attempts=3, base_delay=0)


def test_retry_exponential_delays(monkeypatch):
    sleeps = []
    monkeypatch.setattr("core.retry.time.sleep", lambda s: sleeps.append(s))
    def flaky():
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        retry_with_backoff(flaky, attempts=4, base_delay=1)
    assert sleeps == [1, 2, 4]  # 指数退避（不含最后一次失败后的等待）
