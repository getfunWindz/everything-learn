import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import json
import pytest
from core.adapter import SearchNeeded
from providers.xiaohongshu import XiaohongshuAdapter, parse_user_notes, parse_collect_payload
from tests.contract.harness import run_contract


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


def test_sec404_readable():
    bad_url = "https://www.xiaohongshu.com/404?source=/404/sec_x"
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
