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
    assert cache.get_cached("bilibili", "BV1xx", "1")["items"][0]["text"] == "hi"


def test_cache_without_sub_id():
    cache.set_cached("bilibili", "BV1yy", "", [{"start": 0, "end": 1, "text": "a"}])
    assert cache.get_cached("bilibili", "BV1yy")["items"][0]["text"] == "a"
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


def test_cache_v2_file_invalidated():
    """B3：v2 旧缓存（无 provenance/language）不再命中（schema v3）"""
    assert cache.CACHE_VERSION == 3
    p = os.path.join(cache.CACHE_DIR, "bilibili_BVv2.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"kind": "timeline", "items": [], "_cache_v": 2}, f)
    assert cache.get_cached("bilibili", "BVv2") is None


def test_cache_payload_keeps_provenance_language_recheck():
    """B3：payload 原样保存 provenance/language/recheck（写入端透明）"""
    cache.set_cached("youtube", "abc", "1", {
        "kind": "timeline", "items": [{"start": 0, "end": 1, "text": "hi"}],
        "provenance": "whisper", "language": "en",
        "recheck": {"tier": 1}, "recheck_report": {"tier": 1},
    })
    got = cache.get_cached("youtube", "abc", "1")
    assert got["provenance"] == "whisper" and got["language"] == "en"
    assert got["recheck"] == {"tier": 1} and got["recheck_report"] == {"tier": 1}
    assert got["_cache_v"] == cache.CACHE_VERSION


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


def test_cache_version_bumps_invalidate_legacy(tmp_path, monkeypatch):
    from core import cache
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.set_cached("demo", "item1", "", {"kind": "timeline", "items": [{"start": 0, "end": 1, "text": "x"}]})
    got = cache.get_cached("demo", "item1", "")
    assert got and got["items"][0]["text"] == "x"
    # 模拟旧版缓存（无 _cache_v）→ 视为未命中
    import json, os
    p = os.path.join(str(tmp_path), "demo_item2.json")
    json.dump({"kind": "timeline", "items": [{"start": 0, "end": 1, "text": "old"}]},
              open(p, "w", encoding="utf-8"))
    assert cache.get_cached("demo", "item2", "") is None
    # 旧格式（纯 list）同样失效
    p3 = os.path.join(str(tmp_path), "demo_item3.json")
    json.dump([{"start": 0, "end": 1, "text": "legacy"}], open(p3, "w", encoding="utf-8"))
    assert cache.get_cached("demo", "item3", "") is None


def test_cache_path_sanitizes_windows_illegal_chars():
    """Art19 GUID 含 gid:// 与 /，Windows 下必须可写且可回读"""
    gid = "gid://art19-episode-locator/V0/DI_C_mWtVmdV4it"
    cache.set_cached("podcast", gid, "1", [{"start": 0, "end": 1, "text": "hi"}])
    got = cache.get_cached("podcast", gid, "1")
    assert got and got["items"][0]["text"] == "hi"
    name = os.path.basename(cache._cache_path("podcast", gid, "1"))
    assert not any(ch in name for ch in ':?*"<>|')


def test_cache_path_distinct_for_colliding_sanitized_ids():
    """净化后同形但不同 id 不应串缓存"""
    a, b = "gid://x/a", "gid://x_a"
    cache.set_cached("podcast", a, "1", [{"start": 0, "end": 1, "text": "A"}])
    cache.set_cached("podcast", b, "1", [{"start": 0, "end": 1, "text": "B"}])
    assert cache.get_cached("podcast", a, "1")["items"][0]["text"] == "A"
    assert cache.get_cached("podcast", b, "1")["items"][0]["text"] == "B"
