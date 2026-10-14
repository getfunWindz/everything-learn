import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pytest
from core.browserfetch import strip_json_prefix, parse_fetch_result, BrowserFetchError
import core.browserfetch as bf


def test_strip_json_prefix():
    raw = ')]}while(1);</x>{"success":true,"payload":{"value":{}}}'
    assert strip_json_prefix(raw).startswith("{")


def test_strip_plain_json():
    assert strip_json_prefix('{"a":1}') == '{"a":1}'


def test_parse_fetch_result_ok():
    data = parse_fetch_result({"status": 200, "text": '{"a":1}'})
    assert data == {"a": 1}


def test_parse_fetch_result_prefixed():
    data = parse_fetch_result({"status": 200, "text": ')]}while(1);</x>{"a":2}'})
    assert data == {"a": 2}


def test_parse_fetch_result_blocked():
    with pytest.raises(BrowserFetchError):
        parse_fetch_result({"status": 403, "text": "<html>Just a moment...</html>"})


def test_parse_fetch_result_attention_required():
    with pytest.raises(BrowserFetchError):
        parse_fetch_result({"status": 200, "text": "<html>Attention Required! | Cloudflare</html>"})


def test_parse_fetch_result_non_json():
    with pytest.raises(BrowserFetchError):
        parse_fetch_result({"status": 200, "text": "not json at all"})


def test_error_kind_mapping():
    assert BrowserFetchError("x").kind == "blocked"
    assert BrowserFetchError("x", kind="structure_changed").kind == "structure_changed"


def test_parse_non_json_kind():
    with pytest.raises(BrowserFetchError) as e:
        parse_fetch_result({"status": 200, "text": "not json at all"})
    assert e.value.kind == "structure_changed"


def _fake_launcher(record):
    class FakeCtx:
        def close(self):
            record.append("closed")

    class FakePage:
        def evaluate(self, js, *a):
            return {"status": 200, "text": '{"a":1}'}

    def launcher(browser, interactive):
        record.append(("launch", browser, interactive))
        return {"pw": None, "context": FakeCtx(), "page": FakePage()}
    return launcher


def test_session_singleton_and_mode_switch():
    record = []
    launcher = _fake_launcher(record)
    bf._ctx.update(pw=None, context=None, page=None, interactive=None)
    try:
        s1 = bf.BrowserSession("douyin", launcher=launcher)
        assert s1.page is s1.page                      # 会话内缓存
        s2 = bf.BrowserSession("douyin", interactive=True, launcher=launcher)
        assert s2.page is not None                     # 模式不同 → 重启
        launches = [r for r in record if r[0] == "launch"]
        assert launches == [("launch", "msedge", False), ("launch", "msedge", True)]
    finally:
        bf.shutdown()


def test_session_reuses_singleton_same_mode():
    record = []
    launcher = _fake_launcher(record)
    bf._ctx.update(pw=None, context=None, page=None, interactive=None)
    try:
        p1 = bf.BrowserSession("douyin", launcher=launcher).page
        p2 = bf.BrowserSession("xiaohongshu", launcher=launcher).page
        assert p1 is p2                                # 同模式 → 进程级复用
        assert len([r for r in record if r[0] == "launch"]) == 1
    finally:
        bf.shutdown()


def test_top_fetch_json_delegates_to_session(monkeypatch):
    calls = {}

    class FakeSession:
        def __init__(self, platform, **kw):
            calls["platform"] = platform

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch_json(self, url, warm_url=None, attempts=2, retry_wait=8.0):
            calls.update(url=url, warm=warm_url)
            return {"ok": 1}

    monkeypatch.setattr(bf, "BrowserSession", FakeSession)
    assert bf.fetch_json("u", warm_url="w") == {"ok": 1}
    assert calls == {"platform": "default", "url": "u", "warm": "w"}
