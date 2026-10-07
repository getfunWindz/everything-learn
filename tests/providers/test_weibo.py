import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from providers.weibo import WeiboAdapter, strip_html, pics_to_urls, video_url, parse_created_at
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
