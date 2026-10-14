import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from providers.douyin import DouyinAdapter, to_netscape, load_cookie, parse_user_post_payload
from core.models import ItemRef
from tests.contract.harness import run_contract

INFO = {
    "id": "7279377986187431202", "title": "模组入门#1", "uploader": "xingduansuzhao",
    "duration": 2838, "upload_date": "20230921", "description": "desc",
    "view_count": 1000, "like_count": 50, "comment_count": 5,
    "formats": [
        {"url": "https://v.douyinvod.com/low.mp4", "height": 480, "tbr": 500},
        {"url": "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=low", "height": 540, "tbr": None},
        {"url": "https://v.douyinvod.com/high.mp4", "height": 1080, "tbr": 2000},
        {"url": "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=high", "height": 720, "tbr": 328},
    ],
}


def test_to_netscape():
    s = to_netscape("ttwid=abc; __ac_nonce=def", domain=".douyin.com")
    assert s.startswith("# Netscape HTTP Cookie File")
    assert ".douyin.com\tTRUE\t/\tFALSE\t4102444800\tttwid\tabc" in s


def test_match_and_resolve():
    a = DouyinAdapter(session=object())
    assert a.match("https://www.douyin.com/video/7279377986187431202")
    assert a.match("https://v.douyin.com/ABC123/")
    assert not a.match("https://www.bilibili.com/video/BV1xx")
    ref = a.resolve("https://www.douyin.com/video/7279377986187431202")
    assert ref.platform == "douyin" and ref.item_id == "7279377986187431202"
    assert ref.url == "https://www.douyin.com/video/7279377986187431202"


def test_resolve_short_link():
    class R:
        url = "https://www.douyin.com/video/7279377986187431202"
    class S:
        headers = {}
        def get(self, url, **kw): return R()
    a = DouyinAdapter(session=S())
    ref = a.resolve("https://v.douyin.com/ABC123/")
    assert ref.item_id == "7279377986187431202"


def test_fetch_meta(monkeypatch):
    a = DouyinAdapter(session=object())
    monkeypatch.setattr(DouyinAdapter, "_info", lambda self, ref: INFO)
    ref = a.resolve("https://www.douyin.com/video/7279377986187431202")
    meta = a.fetch_meta(ref)
    assert meta.title == "模组入门#1" and meta.author == "xingduansuzhao"
    assert meta.duration == 2838 and meta.published == "2023-09-21"
    assert meta.stats.get("view") == 1000


def test_fetch_content_empty_goes_whisper(monkeypatch):
    a = DouyinAdapter(session=object())
    monkeypatch.setattr(DouyinAdapter, "_info", lambda self, ref: INFO)
    ref = a.resolve("https://www.douyin.com/video/7279377986187431202")
    res = a.fetch_content(ref)
    assert res.kind == "timeline" and res.status == "empty" and "Whisper" in res.reason


def test_fetch_media_url_picks_formats(monkeypatch):
    a = DouyinAdapter(session=object())
    monkeypatch.setattr(DouyinAdapter, "_info", lambda self, ref: INFO)
    ref = a.resolve("https://www.douyin.com/video/7279377986187431202")
    # 优先 api-play 直链（CDN douyinvod 常态 403）；音频取最小、视频默认最大、lowest 最小
    assert a.fetch_media_url(ref, media="audio") == "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=low"
    assert a.fetch_media_url(ref, media="video") == "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=high"
    assert a.fetch_media_url(ref, media="video", lowest=True) == "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=low"


def test_cookie_missing_readable(monkeypatch, tmp_path):
    import providers.douyin as dy
    monkeypatch.delenv("DOUYIN_COOKIE", raising=False)
    monkeypatch.setattr(dy, "_COOKIE_FILE", str(tmp_path / "none.txt"))
    a = DouyinAdapter(session=object())
    try:
        a._cookie_file()
        assert False
    except ValueError as e:
        assert "el.py cookie douyin" in str(e)


def test_contract():
    assert run_contract(DouyinAdapter()) == []


def test_session_is_throttled():
    from core.throttle import ThrottledSession
    a = DouyinAdapter()
    assert isinstance(a.session, ThrottledSession) and a.session.platform == "douyin"


def test_cookie_export_spec_declared():
    from providers.douyin import COOKIE_EXPORT
    assert COOKIE_EXPORT["file"] == ".douyin_cookie"
    assert COOKIE_EXPORT["required"] == ["ttwid"]


def test_media_headers():
    a = DouyinAdapter()
    h = a.media_headers()
    assert "douyin.com" in h["Referer"] and h["User-Agent"].startswith("Mozilla/5.0")


def test_match_user_page():
    a = DouyinAdapter()
    assert a.match("https://www.douyin.com/user/MS4wLjABAAAAxyz123") is True
    assert a.match("https://www.douyin.com/video/7279377986187431202") is True
    assert a.match("https://example.com/user/abc") is False


def test_resolve_user_page():
    a = DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/user/MS4wLjABAAAAxyz123?from_tab_name=main")
    assert ref.extra["kind"] == "user_posts"
    assert ref.extra["sec_uid"] == "MS4wLjABAAAAxyz123"
    assert ref.url == "https://www.douyin.com/user/MS4wLjABAAAAxyz123"


def test_parse_user_post_payload_normalize_and_dedup():
    payloads = [{"aweme_list": [
        {"aweme_id": "111", "desc": "第一条\n详情", "create_time": 1700000000,
         "statistics": {"play_count": 1000, "digg_count": 50},
         "video": {"duration": 12000}},
        {"aweme_id": "111", "desc": "重复项", "statistics": {}},
        {"aweme_id": "222", "desc": "", "statistics": {}},
    ]}]
    out = parse_user_post_payload(payloads)
    assert [o["aweme_id"] for o in out] == ["111", "222"]
    assert out[0]["title"].startswith("第一条") and out[0]["view"] == 1000
    assert out[0]["duration"] == 12000
    assert out[1]["title"] == "抖音视频 222"


def test_list_items_user_posts_uses_browser_session(monkeypatch):
    import providers.douyin as douyin
    fx = os.path.join(os.path.dirname(__file__), "..", "fixtures", "douyin",
                      "user_posts_sample.json")
    payload = json.load(open(fx, encoding="utf-8"))
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

        def scroll(self, n):
            calls["scroll"] = n

        def tap_json(self, pattern, action=None, **kw):
            calls["pattern"] = pattern
            if action:
                action()
            return [payload]

    monkeypatch.setattr(douyin.browserfetch, "BrowserSession", FakeSession)
    a = douyin.DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/user/MS4wLjABAAAAxyz123")
    items = a.list_items(ref)
    assert calls["platform"] == "douyin"
    assert calls["goto"] == ref.url
    assert len(items) == 2
    assert items[0].url.startswith("https://www.douyin.com/video/")
    assert isinstance(items[0].extra.get("view"), int)
    assert items[0].sub_id == "1" and items[1].sub_id == "2"


def test_list_items_video_ref_unchanged():
    a = DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/video/7279377986187431202")
    assert a.list_items(ref) == [ref]


def test_match_and_resolve_self_page():
    a = DouyinAdapter()
    assert a.match("https://www.douyin.com/user/self") is True
    ref = a.resolve("https://www.douyin.com/user/self?showTab=collection")
    assert ref.extra["kind"] == "self_collect"
    assert ref.url == "https://www.douyin.com/user/self"


def test_list_items_self_collect(monkeypatch):
    import providers.douyin as douyin
    fx = os.path.join(os.path.dirname(__file__), "..", "fixtures", "douyin",
                      "user_posts_sample.json")
    payload = json.load(open(fx, encoding="utf-8"))
    calls = {}

    class FakeSession:
        def __init__(self, platform, **kw):
            calls["platform"] = platform

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def has_cookie(self, name):
            calls["cookie"] = name
            return True

        def goto(self, url, **kw):
            calls["goto"] = url

        def click(self, selector, timeout=5000):
            calls["click"] = selector

        def scroll(self, n):
            calls["scroll"] = n

        def tap_json(self, pattern, action=None, **kw):
            calls["pattern"] = pattern
            if action:
                action()
            return [payload]

    monkeypatch.setattr(douyin.browserfetch, "BrowserSession", FakeSession)
    a = douyin.DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/user/self")
    items = a.list_items(ref)
    assert calls["cookie"] == "sessionid"
    assert calls["goto"] == "https://www.douyin.com/user/self"
    assert calls["click"] == "text=收藏"
    assert calls["pattern"] == r"/aweme/v1/web/aweme/listcollection/"
    assert len(items) == 2 and items[0].sub_id == "1"


def test_list_items_self_collect_not_logged_in(monkeypatch):
    import providers.douyin as douyin

    class FakeSession:
        def __init__(self, platform, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def has_cookie(self, name):
            return False

    monkeypatch.setattr(douyin.browserfetch, "BrowserSession", FakeSession)
    a = douyin.DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/user/self")
    try:
        a.list_items(ref)
        assert False
    except ValueError as e:
        assert "未登录" in str(e)


def test_resolve_self_favorite_tab():
    a = DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/user/self?tab=favorite")
    assert ref.extra["kind"] == "self_favorite"
    assert ref.item_id == "self-favorite"
    assert ref.url == "https://www.douyin.com/user/self"


def test_list_items_self_favorite(monkeypatch):
    import providers.douyin as douyin
    fx = os.path.join(os.path.dirname(__file__), "..", "fixtures", "douyin",
                      "user_posts_sample.json")
    payload = json.load(open(fx, encoding="utf-8"))
    calls = {}

    class FakeSession:
        def __init__(self, platform, **kw):
            calls["platform"] = platform

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def has_cookie(self, name):
            calls["cookie"] = name
            return True

        def goto(self, url, **kw):
            calls["goto"] = url

        def click(self, selector, timeout=5000):
            calls["click"] = selector

        def scroll(self, n):
            calls["scroll"] = n

        def tap_json(self, pattern, action=None, **kw):
            calls["pattern"] = pattern
            if action:
                action()
            return [payload]

    monkeypatch.setattr(douyin.browserfetch, "BrowserSession", FakeSession)
    a = douyin.DouyinAdapter()
    ref = a.resolve("https://www.douyin.com/user/self?tab=favorite")
    items = a.list_items(ref)
    assert calls["cookie"] == "sessionid"
    assert calls["click"] == "text=喜欢"
    assert calls["pattern"] == r"/aweme/v1/web/aweme/favorite/"
    assert len(items) == 2 and items[0].sub_id == "1"
