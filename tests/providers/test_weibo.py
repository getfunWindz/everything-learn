import pytest
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from providers.weibo import (WeiboAdapter, WeiboError, strip_html, pics_to_urls,
                             video_url, parse_created_at)
from core.models import ItemRef
from tests.contract.harness import run_contract

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "weibo")


def _fx(name):
    return json.load(open(os.path.join(FIX, name), encoding="utf-8"))


def test_match_and_resolve():
    a = WeiboAdapter()
    assert a.match("https://m.weibo.cn/status/4199408960166799")
    assert a.match("https://m.weibo.cn/detail/4199408960166799")
    assert a.match("https://weibo.com/1234567/FFI520Hoj")
    assert a.match("https://m.weibo.cn/u/1234567")
    ref = a.resolve("https://m.weibo.cn/status/4199408960166799")
    assert ref.extra["kind"] == "post" and ref.extra["sid"] == "4199408960166799"
    uref = a.resolve("https://m.weibo.cn/u/1234567")
    assert uref.extra["kind"] == "user" and uref.extra["uid"] == "1234567"
    wref = a.resolve("https://weibo.com/1234567/FFI520Hoj")
    assert wref.extra["kind"] == "post" and wref.extra["sid"] == "FFI520Hoj"


def test_resolve_favorites_page():
    a = WeiboAdapter()
    assert a.match("https://weibo.com/u/favorites?page=1")
    ref = a.resolve("https://weibo.com/u/favorites?page=1")
    assert ref.extra["kind"] == "favorites"
    assert ref.url.startswith("https://weibo.com/u/favorites")
    assert ref.platform == "weibo"


def test_strip_html():
    raw = '亲爱的罐妈，<br/>    投票告急<span class="url-icon"><img src="//x.png"></span>&amp; 加油'
    out = strip_html(raw)
    assert "<" not in out and "亲爱的罐妈" in out and "&" in out and "\n" in out


def test_pics_to_urls_large_preferred():
    pics = [{"url": "https://wxt.sinaimg.cn/thumb/a.jpg",
             "large": {"url": "https://wx1.sinaimg.cn/large/a.jpg"}},
            {"url": "//wxt.sinaimg.cn/thumb/b.jpg"}]
    urls = pics_to_urls(pics)
    assert urls[0] == "https://wx1.sinaimg.cn/large/a.jpg"
    assert urls[1].startswith("https://")


def test_video_url():
    mblog = {"page_info": {"media_info": {"stream_url_hd": "https://f.video/x.mp4"}}}
    assert video_url(mblog) == "https://f.video/x.mp4"
    mblog2 = {"page_info": {"urls": {"mp4_720p_mp4": {"url": "https://f.video/y.mp4"}}}}
    assert video_url(mblog2) == "https://f.video/y.mp4"
    assert video_url({}) is None


def test_parse_created_at():
    assert parse_created_at("Sat Sep 05 01:22:48 +0800 2015") == "2015-09-05"
    assert parse_created_at("2023-03-21 11:22") == "2023-03-21"
    assert parse_created_at("1小时前") == ""


def test_fetch_content_from_show(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json", lambda self, url, **kw: _fx("show_sample.json"))
    ref = a.resolve("https://m.weibo.cn/status/4199408960166799")
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.blocks and res.images
    assert len(res.images) == 9 and res.images[0].startswith("https://")
    assert "<" not in res.blocks[0]["text"]


def test_fetch_meta_from_show(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json", lambda self, url, **kw: _fx("show_sample.json"))
    ref = a.resolve("https://m.weibo.cn/status/4199408960166799")
    meta = a.fetch_meta(ref)
    assert meta.author and meta.desc
    assert meta.stats.get("repost") is not None


def test_retweet_included(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json", lambda self, url, **kw: _fx("retweet_sample.json"))
    ref = a.resolve("https://m.weibo.cn/status/3883522454357038")
    res = a.fetch_content(ref)
    assert "转发" in res.blocks[0]["text"]


def test_list_items_from_timeline(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json", lambda self, url, **kw: _fx("timeline_sample.json"))
    ref = a.resolve("https://m.weibo.cn/u/1234567")
    items = a.list_items(ref)
    assert len(items) == 3 and items[0].sub_id == "1"
    assert items[0].extra["mblog"]["text"]
    assert all(i.extra["kind"] == "post" for i in items)


def test_timeline_meta_and_content_use_cached_mblog(monkeypatch):
    """时间线条目自带 mblog → 不再发请求（省请求、降风控）"""
    a = WeiboAdapter(session=object())
    calls = []
    monkeypatch.setattr(WeiboAdapter, "_get_json", lambda self, url, **kw: calls.append(url) or _fx("show_sample.json"))
    mblog = _fx("timeline_sample.json")["data"]["cards"][0]["card_group"][0]["mblog"]
    ref = ItemRef(platform="weibo", item_id=f"post-{mblog['id']}",
                  url="https://m.weibo.cn/status/x", sub_id="1",
                  extra={"kind": "post", "sid": str(mblog.get("bid") or mblog["id"]), "mblog": mblog})
    meta = a.fetch_meta(ref)
    res = a.fetch_content(ref)
    assert meta.author and res.blocks and calls == []


def test_contract():
    assert run_contract(WeiboAdapter()) == []


def test_session_is_throttled():
    from core.throttle import ThrottledSession
    a = WeiboAdapter()
    assert isinstance(a.session, ThrottledSession) and a.session.platform == "weibo"


def test_cookie_export_spec_declared():
    from providers.weibo import COOKIE_EXPORT
    assert COOKIE_EXPORT["file"] == ".weibo_cookie"
    assert "SUB" in COOKIE_EXPORT["required"]


def _mb(i, text="内容"):
    return {"id": str(1000 + i), "bid": f"B{i}", "text": f"{text}{i}",
            "user": {"screen_name": "测试用户"}, "created_at": "Sat Sep 05 01:22:48 +0800 2020"}


def test_timeline_full_pagination(monkeypatch):
    a = WeiboAdapter(session=object())
    pages = [
        {"data": {"cards": [{"mblog": _mb(i)} for i in range(1, 11)],
                  "cardlistInfo": {"since_id": 999}}},
        {"data": {"cards": [{"mblog": _mb(i)} for i in range(11, 16)],
                  "cardlistInfo": {}}},
    ]
    calls = []
    monkeypatch.setattr(WeiboAdapter, "_get_json",
                        lambda self, url: calls.append(url) or pages.pop(0))
    ref = ItemRef(platform="weibo", item_id="user-1", url="", sub_id="1",
                  extra={"kind": "user", "uid": "1"})
    refs = a.list_items(ref)
    assert len(refs) == 15
    assert "since_id=999" in calls[1]          # 第二页带上 since_id


def test_timeline_pagination_stops_on_risk(monkeypatch):
    a = WeiboAdapter(session=object())
    def gen(self, url):
        if "since_id" in url:
            raise WeiboError("微博风控拦截（432/空响应）")
        return {"data": {"cards": [{"mblog": _mb(i)} for i in range(1, 11)],
                         "cardlistInfo": {"since_id": 999}}}
    monkeypatch.setattr(WeiboAdapter, "_get_json", gen)
    ref = ItemRef(platform="weibo", item_id="user-1", url="", sub_id="1",
                  extra={"kind": "user", "uid": "1"})
    refs = a.list_items(ref)                   # 中途风控：已有内容优雅停止，不抛
    assert len(refs) == 10


class _Resp:
    status_code = 200
    text = '{"ok":1}'

    def json(self):
        return {"ok": 1}


def test_get_json_desktop_sends_raw_cookie(monkeypatch):
    """桌面域请求必须带原始 cookie 串（session jar 只挂了 .weibo.cn 域）"""
    sent = {}

    class _S:
        def get(self, url, headers=None, timeout=None):
            sent.update(headers or {})
            return _Resp()

    a = WeiboAdapter(session=_S())
    monkeypatch.setattr("providers.weibo._raw_cookie", lambda: "SUB=abc")
    assert a._get_json_desktop("https://weibo.com/x") == {"ok": 1}
    assert sent.get("Cookie") == "SUB=abc"
    assert sent.get("Referer", "").startswith("https://weibo.com")


def test_raw_cookie_prefers_env(monkeypatch):
    monkeypatch.setenv("WEIBO_COOKIE", "SUB=env")
    from providers.weibo import _raw_cookie
    assert _raw_cookie() == "SUB=env"


def _fav(i, like=None):
    m = {"id": str(5000 + i), "mblogid": f"F{i}", "bid": f"F{i}",
         "text": f"收藏内容{i}", "user": {"screen_name": "收藏博主"},
         "created_at": "Sat Sep 05 01:22:48 +0800 2020"}
    if like is not None:
        m["attitudes_count"] = like
    return m


def test_list_items_favorites_pagination(monkeypatch):
    a = WeiboAdapter(session=object())
    pages = [
        {"ok": 1, "count": 20, "total_page": 2,
         "data": {"status": [_fav(i) for i in range(1, 21)], "total_number": 25}},
        {"ok": 1, "count": 20, "total_page": 2,
         "data": {"status": [_fav(i) for i in range(21, 26)], "total_number": 25}},
    ]
    calls = []
    monkeypatch.setattr(WeiboAdapter, "_get_json_desktop",
                        lambda self, url: calls.append(url) or pages.pop(0))
    refs = a.list_items(a.resolve("https://weibo.com/u/favorites"))
    assert len(refs) == 25
    assert "page=2" in calls[1]
    assert refs[0].sub_id == "1" and refs[24].sub_id == "25"
    assert refs[0].extra["mblog"]["text"] == "收藏内容1"
    assert refs[0].url.endswith("/F1")


def test_list_items_favorites_accepts_plain_list(monkeypatch):
    """兼容不带 with_total 的旧形状（data 为纯数组）"""
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json_desktop",
                        lambda self, url: {"ok": 1, "count": 20, "data": [_fav(1), _fav(2)]})
    refs = a.list_items(a.resolve("https://weibo.com/u/favorites"))
    assert len(refs) == 2


def test_favorites_items_carry_like_count(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json_desktop",
                        lambda self, url: {"ok": 1, "count": 20,
                                           "data": {"status": [_fav(1, like=42)], "total_number": 1}})
    refs = a.list_items(a.resolve("https://weibo.com/u/favorites"))
    assert refs[0].extra["like"] == 42


def test_list_items_favorites_empty(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json_desktop",
                        lambda self, url: {"ok": 1, "count": 20, "total_page": 0,
                                           "data": {"status": [], "total_number": 0}})
    assert a.list_items(a.resolve("https://weibo.com/u/favorites")) == []


def test_list_items_favorites_not_logged_in(monkeypatch):
    a = WeiboAdapter(session=object())
    monkeypatch.setattr(WeiboAdapter, "_get_json_desktop",
                        lambda self, url: {"ok": -100, "data": None})
    with pytest.raises(WeiboError):
        a.list_items(a.resolve("https://weibo.com/u/favorites"))


def test_list_items_favorites_stops_on_risk(monkeypatch):
    """中途风控：已有内容优雅停止，不抛"""
    a = WeiboAdapter(session=object())

    def gen(self, url):
        if "page=2" in url:
            raise WeiboError("微博风控拦截（432/空响应）")
        return {"ok": 1, "count": 20, "total_page": 3,
                "data": {"status": [_fav(i) for i in range(1, 21)], "total_number": 40}}

    monkeypatch.setattr(WeiboAdapter, "_get_json_desktop", gen)
    refs = a.list_items(a.resolve("https://weibo.com/u/favorites"))
    assert len(refs) == 20


def test_fetch_root_meta_favorites():
    a = WeiboAdapter(session=object())
    ref = a.resolve("https://weibo.com/u/favorites")
    meta = a.fetch_root_meta(ref, [])
    assert meta.title == "我的微博收藏"
