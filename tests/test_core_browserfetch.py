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


class FakeResp:
    def __init__(self, url, data=None, bad=False):
        self.url = url
        self._data = data
        self._bad = bad

    def json(self):
        if self._bad:
            raise ValueError("no body")
        return self._data


class FakeEventPage:
    def __init__(self):
        self.handlers = []
        self.removed = []
        self.timeouts = []

    def on(self, event, handler):
        self.handlers.append((event, handler))

    def remove_listener(self, event, handler):
        self.removed.append((event, handler))
        self.handlers.remove((event, handler))

    def wait_for_timeout(self, ms):
        self.timeouts.append(ms)


def _session_with_page(page):
    s = bf.BrowserSession("douyin")
    s._page = page
    return s


def test_tap_json_collects_and_filters():
    page = FakeEventPage()
    s = _session_with_page(page)

    def action():
        for _, h in list(page.handlers):
            h(FakeResp("https://www.douyin.com/aweme/v1/web/aweme/post/?a=1",
                       {"aweme_list": [{"aweme_id": "1"}]}))
            h(FakeResp("https://other.example/api/feed", {"nope": True}))
            h(FakeResp("https://www.douyin.com/aweme/v1/web/aweme/post/?a=2", bad=True))

    out = s.tap_json(r"/aweme/v1/web/aweme/post/", action=action)
    assert out == [{"aweme_list": [{"aweme_id": "1"}]}]
    assert page.handlers == []                    # 监听器已移除
    assert page.removed                        # remove_listener 被调用


def test_tap_json_respects_limit():
    page = FakeEventPage()
    s = _session_with_page(page)

    def action():
        for i in range(3):
            for _, h in list(page.handlers):
                h(FakeResp(f"https://d.com/list?i={i}", {"i": i}))

    out = s.tap_json(r"/list", action=action, limit=2)
    assert len(out) == 2


def test_tap_json_no_match_raises_structure_changed():
    page = FakeEventPage()
    s = _session_with_page(page)
    with pytest.raises(BrowserFetchError) as e:
        s.tap_json(r"/never/", action=lambda: None, settle_ms=0)
    assert e.value.kind == "structure_changed"
    assert page.handlers == []


class FakeScrollPage:
    def __init__(self, html=""):
        self.evals = []
        self._html = html

    def evaluate(self, js):
        self.evals.append(js)

    def content(self):
        return self._html


def test_scroll_steps_and_throttle(monkeypatch):
    calls = []
    monkeypatch.setattr(bf.throttle, "wait", lambda platform: calls.append(platform))
    page = FakeScrollPage()
    s = _session_with_page(page)
    s.scroll(times=3, step_px=500)
    assert page.evals == ["window.scrollBy(0, 500)"] * 3
    assert calls == ["douyin"] * 3


def test_scroll_zero_times(monkeypatch):
    calls = []
    monkeypatch.setattr(bf.throttle, "wait", lambda platform: calls.append(platform))
    page = FakeScrollPage()
    s = _session_with_page(page)
    s.scroll(times=0)
    assert page.evals == [] and calls == []


def test_login_probe_markers():
    s = _session_with_page(FakeScrollPage('<html><button>登录</button>ok</html>'))
    assert s.login_probe(bad_marker="登录") is False        # 命中未登录标记
    assert s.login_probe(ok_marker="ok") is True
    assert s.login_probe(ok_marker="不存在") is False
    assert s.login_probe() is True                          # 无标记 → 保守放行


def test_launch_args_visible_vs_offscreen():
    """回归：可见模式必须显式指定屏内位置（profile 会记忆上一次的离屏位置）"""
    off = bf._launch_args(False)
    vis = bf._launch_args(True)
    assert any(a.startswith("--window-position=-") for a in off)
    assert "--window-position=80,80" in vis
    assert not any(a.startswith("--window-position=-") for a in vis)
    assert "--disable-blink-features=AutomationControlled" in vis


class FakeCtxCookies:
    def __init__(self, names):
        self._names = names

    def cookies(self):
        return [{"name": n, "value": "v"} for n in self._names]


class FakeClickPage(FakeScrollPage):
    def __init__(self, html=""):
        super().__init__(html)
        self.clicks = []

    def click(self, selector, timeout=5000):
        self.clicks.append((selector, timeout))


def test_session_click_html_has_cookie():
    page = FakeClickPage("<html>ok</html>")
    page.context = FakeCtxCookies(["sessionid"])
    s = _session_with_page(page)
    s.click("text=收藏", timeout=8000)
    assert page.clicks == [("text=收藏", 8000)]
    assert s.html() == "<html>ok</html>"
    assert s.has_cookie("sessionid") is True
    assert s.has_cookie("nope") is False
