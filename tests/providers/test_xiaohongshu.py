import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import json
import pytest
from core.adapter import SearchNeeded
from providers.xiaohongshu import XiaohongshuAdapter, parse_user_notes, parse_collect_payload
from tests.contract.harness import run_contract


@pytest.fixture(autouse=True)
def _isolate_token_cache(tmp_path, monkeypatch):
    """token 缓存为进程级文件：每个用例隔离到 tmp，避免污染真实缓存"""
    import providers.xiaohongshu as x
    monkeypatch.setattr(x, "_TOKEN_CACHE", str(tmp_path / "xhs_tokens.json"))


def test_match_forms():
    a = XiaohongshuAdapter(session=object())
    assert a.match("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980?xsec_token=ABC&xsec_source=pc_share")
    assert a.match("https://www.xiaohongshu.com/discovery/item/6acde40b000000001b02c980?xsec_token=ABC")
    assert a.match("https://xhslink.cn/o/5HWLkuHJZ7x")
    assert a.match("https://xhslink.com/a/AbCdEf123")
    assert not a.match("https://www.xiaohongshu.com/explore")
    assert not a.match("https://example.com/explore/6acde40b000000001b02c980")


def test_resolve_direct_keeps_token():
    a = XiaohongshuAdapter(session=object())
    ref = a.resolve("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980"
                    "?xsec_token=ABC123=&xsec_source=pc_share")
    assert ref.platform == "xiaohongshu"
    assert ref.item_id == "xhs-6acde40b000000001b02c980"
    assert ref.extra["kind"] == "note"
    assert ref.extra["token"] == "ABC123="
    assert "xsec_token=ABC123=" in ref.url


def test_resolve_short_link(monkeypatch):
    class R:
        url = ("https://www.xiaohongshu.com/discovery/item/6acde40b000000001b02c980"
               "?xsec_token=CBTYel6EB4CeoNIadZD8vJaSTjjgiC9ytiUWXkw6Y-r24=&xsec_source=app_share")

        def close(self):
            pass

    class S:
        headers = {}
        def get(self, url, **kw):
            return R()
    a = XiaohongshuAdapter(session=S())
    ref = a.resolve("https://xhslink.cn/o/5HWLkuHJZ7x")
    assert ref.item_id == "xhs-6acde40b000000001b02c980"
    assert ref.extra["token"].startswith("CBTYel6EB4")


def test_resolve_name_needs_search():
    a = XiaohongshuAdapter(session=object())
    with pytest.raises(SearchNeeded):
        a.resolve("一篇小红书笔记")


def test_list_items_and_contract():
    a = XiaohongshuAdapter(session=object())
    ref = a.resolve("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980?xsec_token=ABC")
    assert a.list_items(ref) == [ref]
    assert run_contract(a) == []


FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "xiaohongshu")


def _fx_text(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def _fx_json(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def _note_html(state):
    return "<html><head></head><body><script>window.__INITIAL_STATE__ = " + \
        json.dumps(state, ensure_ascii=False) + "</script></body></html>"


class _Resp:
    def __init__(self, text="", url="", status=200):
        self.text, self.url, self.status_code = text, url, status

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        pass


class _SeqSession:
    """按顺序返回预设响应；记录请求 URL"""
    def __init__(self, resps):
        self._resps = list(resps)
        self.calls = []
        self.headers = {}

    def get(self, url, **kw):
        self.calls.append(url)
        return self._resps.pop(0)


def _state(kind="normal"):
    note = _fx_json(f"note_{kind}.json")
    return {"note": {"noteDetailMap": {note["noteId"]: {"note": note}}}}


def test_parse_normal_note_meta_and_content():
    nid = "6acde40b000000001b02c980"
    html = _note_html(_state("normal"))
    url = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token=ABC&xsec_source=pc_share"
    a = XiaohongshuAdapter(session=_SeqSession([_Resp(text=html, url=url)]))
    ref = a.resolve(url)
    meta = a.fetch_meta(ref)
    assert meta.author == "NANA"
    assert meta.published
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status == "ok"
    assert any("冰美式" in b["text"] for b in res.blocks)
    assert len(res.images) == 8
    assert res.images[0].startswith("https://sns-webpic-qc.xhscdn.com/")


def test_video_note_transcribed(monkeypatch):
    nid = "6ab0a6b9000000002b027713"
    html = _note_html(_state("video"))
    url = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token=ABC&xsec_source=pc_share"
    a = XiaohongshuAdapter(session=_SeqSession([_Resp(text=html, url=url)]))
    ref = a.resolve(url)
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url",
                        lambda url, **kw: [{"start": 0.0, "end": 1.0, "text": "测试口播"}])
    res = a.fetch_content(ref)
    assert any(b["heading"] == "视频口播内容" and "测试口播" in b["text"] for b in res.blocks)
    assert res.video_lines and res.video_lines[0]["text"] == "测试口播"   # B2：片段传递
    assert res.images == []          # 视频笔记不把封面当正文图
    assert a.fetch_media_url(ref).startswith("https://sns-video")


def test_missing_token_harvests_from_explore():
    nid = "6acde40b000000001b02c980"
    note_html = _note_html(_state("normal"))
    explore_html = _fx_text("explore.html")
    s = _SeqSession([
        _Resp(text=explore_html),
        _Resp(text=note_html, url=f"https://www.xiaohongshu.com/explore/{nid}"),
    ])
    a = XiaohongshuAdapter(session=s)
    ref = a.resolve(f"https://www.xiaohongshu.com/explore/{nid}")   # 无 token
    res = a.fetch_content(ref)
    assert res.status == "ok"
    assert "xsec_token=FAKETOKENXHS=" in s.calls[-1]               # 用现取 token 重试


def test_sec404_readable(monkeypatch):
    bad_url = "https://www.xiaohongshu.com/404?source=/404/sec_x"
    # 离线隔离：requests 兜底失败后不要启动真实浏览器
    monkeypatch.setattr(XiaohongshuAdapter, "_harvest_token_browser",
                        lambda self, nid: None)
    a = XiaohongshuAdapter(session=_SeqSession([
        _Resp(text="<html></html>", url=bad_url),   # note 请求 → sec404
        _Resp(text="<html></html>", url=bad_url),   # explore 1
        _Resp(text="<html></html>", url=bad_url),   # explore 2
    ]))
    ref = a.resolve("https://www.xiaohongshu.com/explore/6acde40b000000001b02c980?xsec_token=OLD")
    res = a.fetch_content(ref)
    assert res.status == "empty" and "重新分享" in res.reason


def test_match_and_resolve_user_profile():
    a = XiaohongshuAdapter()
    assert a.match("https://www.xiaohongshu.com/user/profile/" + "a" * 24) is True
    ref = a.resolve("https://www.xiaohongshu.com/user/profile/" + "a" * 24)
    assert ref.extra["kind"] == "user_notes"
    assert ref.extra["user_id"] == "a" * 24


def test_parse_user_notes_from_ssr():
    nid_a, nid_b = "a" * 24, "b" * 24
    state = {"user": {"loggedIn": True, "notes": [[
        {"noteCard": {"noteId": nid_a, "displayTitle": "第一篇", "type": "normal"},
         "xsecToken": "tok1"},
        {"noteCard": {"noteId": nid_b, "displayTitle": "", "type": "video"},
         "xsecToken": "tok2"},
        {"noteCard": {"noteId": nid_a, "displayTitle": "重复", "type": "normal"},
         "xsecToken": "tok3"},
    ]]}}
    html = _note_html(state)
    out = parse_user_notes(html)
    assert [o["note_id"] for o in out] == [nid_a, nid_b]
    assert out[0]["title"] == "第一篇" and out[0]["token"] == "tok1"
    assert out[1]["title"].startswith("小红书笔记")


def test_list_items_user_notes(monkeypatch):
    import providers.xiaohongshu as xhs
    nid = "c" * 24
    state = {"user": {"loggedIn": True, "notes": [[
        {"noteCard": {"noteId": nid, "displayTitle": "我的笔记", "type": "normal"},
         "xsecToken": "tokX"}]]}}
    html = _note_html(state)
    page_payload = {"data": {"notes": [
        {"note_id": nid, "display_title": "重复", "type": "normal", "xsec_token": "tokZ"},
        {"note_id": "d" * 24, "display_title": "第二页", "type": "normal", "xsec_token": "tokW"},
    ], "has_more": False}}
    calls = {}

    class FakeSession:
        def __init__(self, platform, **kw):
            calls["platform"] = platform

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def goto(self, url, **kw):
            calls["goto"] = url

        def html(self):
            return html

        def pause(self, ms):
            calls.setdefault("pauses", []).append(ms)

        def scroll_wheel(self, n):
            calls["wheel"] = n

        def tap_json(self, pattern, action=None, **kw):
            calls["pattern"] = pattern
            calls["min_results"] = kw.get("min_results")
            if action:
                action()
            return [page_payload]

    monkeypatch.setattr(xhs.browserfetch, "BrowserSession", FakeSession)
    a = xhs.XiaohongshuAdapter()
    ref = a.resolve("https://www.xiaohongshu.com/user/profile/" + nid)
    items = a.list_items(ref)
    assert calls["platform"] == "xiaohongshu"
    assert calls["pattern"] == r"/api/sns/web/v1/user_posted"
    assert calls["min_results"] == 0                    # 无分页页时静默回退首屏
    assert len(items) == 2                               # SSR 1 条 + 拦截页新增 1 条（重复去重）
    assert items[0].url == (f"https://www.xiaohongshu.com/explore/{nid}"
                            "?xsec_token=tokX&xsec_source=pc_user")
    assert items[1].title == "第二页"
    assert items[0].extra["kind"] == "note"


def test_list_items_user_notes_not_logged_in(monkeypatch):
    import providers.xiaohongshu as xhs
    state = {"user": {"loggedIn": False, "notes": [[]]}}
    html = _note_html(state)

    class FakeSession:
        def __init__(self, platform, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def goto(self, url, **kw):
            pass

        def html(self):
            return html

    monkeypatch.setattr(xhs.browserfetch, "BrowserSession", FakeSession)
    a = xhs.XiaohongshuAdapter()
    ref = a.resolve("https://www.xiaohongshu.com/user/profile/" + "d" * 24)
    try:
        a.list_items(ref)
        assert False
    except ValueError as e:
        assert "未登录" in str(e)


def test_resolve_user_profile_collect_tab():
    a = XiaohongshuAdapter()
    ref = a.resolve("https://www.xiaohongshu.com/user/profile/" + "e" * 24 + "?tab=collect")
    assert ref.extra["kind"] == "user_notes"
    assert ref.extra["tab"] == "collect"


def test_parse_collect_payload_and_list_items(monkeypatch):
    import providers.xiaohongshu as xhs
    payload = {"code": 0, "data": {"notes": [
        {"note_id": "f" * 24, "display_title": "收藏一", "type": "normal",
         "xsec_token": "tokC"},
        {"note_id": "g" * 24, "display_title": "", "type": "video",
         "xsec_token": "tokD"},
        {"note_id": "f" * 24, "display_title": "重复", "type": "normal",
         "xsec_token": "tokE"},
    ], "cursor": "c1", "has_more": True}}
    out = parse_collect_payload([payload])
    assert [o["note_id"] for o in out] == ["f" * 24, "g" * 24]
    assert out[0]["title"] == "收藏一" and out[0]["token"] == "tokC"

    state_html = _note_html({"user": {"loggedIn": True}})
    calls = {}

    class FakeSession:
        def __init__(self, platform, **kw):
            calls["platform"] = platform

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def goto(self, url, **kw):
            calls["goto"] = url

        def html(self):
            return state_html

        def evaluate(self, js, *args):
            calls.setdefault("evals", []).append(js)
            return "clicked"

        def pause(self, ms):
            calls.setdefault("pauses", []).append(ms)

        def scroll_wheel(self, n):
            calls["wheel"] = n

        def tap_json(self, pattern, action=None, **kw):
            calls["pattern"] = pattern
            if action:
                action()
            return [payload]

    monkeypatch.setattr(xhs.browserfetch, "BrowserSession", FakeSession)
    a = xhs.XiaohongshuAdapter()
    ref = a.resolve("https://www.xiaohongshu.com/user/profile/" + "f" * 24 + "?tab=collect")
    items = a.list_items(ref)
    assert calls["pattern"] == r"/api/sns/web/v2/note/collect/page"
    assert calls["wheel"] == 3
    assert calls["pauses"][0] == 1500                       # 先等页面水合
    assert any("收藏" in js for js in calls["evals"])       # 点击 JS 含目标 tab
    assert len(items) == 2
    assert "xsec_token=tokC" in items[0].url
    assert items[0].sub_id == "1"


def test_extract_share_url_from_text():
    from providers.xiaohongshu import extract_share_url
    text = ("34 小红薯发布了一篇小红书笔记，快来看吧！ 😆 "
            "http://xhslink.com/a/AbCdEf 复制本条信息，打开【小红书】App查看精彩内容！")
    assert extract_share_url(text) == "http://xhslink.com/a/AbCdEf"


def test_match_and_resolve_share_text_with_direct_link():
    a = XiaohongshuAdapter(session=object())
    text = ("看看这篇笔记 https://www.xiaohongshu.com/explore/"
            "6acde40b000000001b02c980?xsec_token=ABC123=&xsec_source=app_share 复制本条信息")
    assert a.match(text)
    ref = a.resolve(text)
    assert ref.item_id == "xhs-6acde40b000000001b02c980"
    assert ref.extra["token"] == "ABC123="


class _DeadSession:
    """requests 路径全部失败（模拟未登录/网络异常）"""
    headers = {}

    def get(self, *a, **k):
        import requests
        raise requests.exceptions.ConnectionError("down")


def test_harvest_token_requests_hit_skips_browser(monkeypatch):
    """requests 访客页已含 token → 不启动浏览器"""
    import providers.xiaohongshu as x
    nid = "c" * 24
    html = f'<a href="/explore/{nid}?xsec_token=REQTOK&amp;xsec_source=pc_feed">'
    a = x.XiaohongshuAdapter(session=_SeqSession([_Resp(text=html)]))
    monkeypatch.setattr(x.browserfetch, "BrowserSession",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应启动浏览器")))
    assert a._harvest_token(nid) == "REQTOK"


def test_harvest_token_browser_scroll_hit(monkeypatch):
    """requests 未命中 → 登录态浏览器滚动 explore 流，按 note_id 命中 token"""
    import types
    import providers.xiaohongshu as x
    nid = "a" * 24
    other = "/explore/" + "b" * 24 + "?xsec_token=OTHER&xsec_source=pc_feed"
    target = f"/explore/{nid}?xsec_token=TOK123&xsec_source=pc_feed"

    class Page:
        mouse = types.SimpleNamespace(move=lambda *a: None, wheel=lambda *a: None)

    class BS:
        evals = 0
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def goto(self, url, **k): pass
        def pause(self, ms): pass
        def evaluate(self, js):
            BS.evals += 1
            return [other] if BS.evals == 1 else [other, target]
        page = Page()

    monkeypatch.setattr(x.browserfetch, "BrowserSession", BS)
    a = x.XiaohongshuAdapter(session=_DeadSession())
    assert a._harvest_token(nid) == "TOK123"
    assert BS.evals == 2                       # 首屏 + 滚动一次


def test_harvest_token_browser_gives_up_after_scrolls(monkeypatch):
    """滚动上限内未命中 → None（evaluate 次数受控，不无限加载）"""
    import types
    import providers.xiaohongshu as x

    class Page:
        mouse = types.SimpleNamespace(move=lambda *a: None, wheel=lambda *a: None)

    class BS:
        evals = 0
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def goto(self, url, **k): pass
        def pause(self, ms): pass
        def evaluate(self, js):
            BS.evals += 1
            return ["/explore/" + "b" * 24 + "?xsec_token=OTHER"]
        page = Page()

    monkeypatch.setattr(x.browserfetch, "BrowserSession", BS)
    a = x.XiaohongshuAdapter(session=_DeadSession())
    assert a._harvest_token("d" * 24) is None
    assert BS.evals == a.harvest_scrolls + 1


def _write_token_cache(path, nid, token, age_sec=0):
    import time
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({nid: {"token": token, "ts": time.time() - age_sec}}, f)


def test_token_cache_saved_after_success(tmp_path, monkeypatch):
    """成功取数后写 token 缓存（供后续无 token 链接复用）"""
    import providers.xiaohongshu as x
    cache = str(tmp_path / "tok.json")
    monkeypatch.setattr(x, "_TOKEN_CACHE", cache)
    nid = "a" * 24
    a = x.XiaohongshuAdapter(session=_SeqSession([
        _Resp(text=_note_html(_state()), url=f"https://www.xiaohongshu.com/explore/{nid}")]))
    ref = a.resolve(f"https://www.xiaohongshu.com/explore/{nid}?xsec_token=T1")
    assert a.fetch_content(ref).status == "ok"
    saved = json.load(open(cache, encoding="utf-8"))
    assert saved[nid]["token"] == "T1"


def test_cached_token_used_when_link_missing_token(tmp_path, monkeypatch):
    """无 token 链接：优先复用缓存 token（确定性路径，不依赖 explore 流）"""
    import providers.xiaohongshu as x
    cache = str(tmp_path / "tok.json")
    monkeypatch.setattr(x, "_TOKEN_CACHE", cache)
    nid = "b" * 24
    _write_token_cache(cache, nid, "CACHED")
    monkeypatch.setattr(x.XiaohongshuAdapter, "_harvest_token_browser",
                        lambda self, n: (_ for _ in ()).throw(AssertionError("不应兜底")))
    s = _SeqSession([_Resp(text=_note_html(_state()),
                           url=f"https://www.xiaohongshu.com/explore/{nid}")])
    a = x.XiaohongshuAdapter(session=s)
    ref = a.resolve(f"https://www.xiaohongshu.com/explore/{nid}")   # 无 token
    assert a.fetch_content(ref).status == "ok"
    assert "xsec_token=CACHED" in s.calls[0]


def test_expired_token_cache_ignored(tmp_path, monkeypatch):
    """过期缓存不采信 → 走兜底；兜底失败给可读错误"""
    import providers.xiaohongshu as x
    cache = str(tmp_path / "tok.json")
    monkeypatch.setattr(x, "_TOKEN_CACHE", cache)
    nid = "c" * 24
    _write_token_cache(cache, nid, "OLD", age_sec=x._TOKEN_TTL + 60)
    monkeypatch.setattr(x.XiaohongshuAdapter, "_harvest_token_browser", lambda self, n: None)
    a = x.XiaohongshuAdapter(session=_SeqSession([
        _Resp(text="<html></html>"), _Resp(text="<html></html>")]))
    ref = a.resolve(f"https://www.xiaohongshu.com/explore/{nid}")
    res = a.fetch_content(ref)
    assert res.status == "empty" and "xsec_token" in res.reason
