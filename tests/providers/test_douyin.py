import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
from providers.douyin import DouyinAdapter, to_netscape, load_cookie
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
