import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import json
import pytest
from core.adapter import SearchNeeded
from providers.x_twitter import XTwitterAdapter, tweet_token, parse_home_timeline_payload
from tests.contract.harness import run_contract


def test_tweet_token_known_values():
    assert tweet_token("20") == "6dq1a2xwd93"
    assert tweet_token("2108563438901350773") == "548wn4l3l3v1jvk2r19k9"
    assert tweet_token("2109957980011552839") == "544mmistwz2qs6skyb9"


def test_match_forms():
    a = XTwitterAdapter(session=object())
    assert a.match("https://x.com/NASA/status/2108563438901350773")
    assert a.match("https://twitter.com/NASA/status/2108563438901350773?s=20&t=abc")
    assert a.match("https://x.com/i/status/2108563438901350773")
    assert a.match("https://twitter.com/i/web/status/2108563438901350773")
    assert a.match("https://mobile.twitter.com/NASA/status/2108563438901350773")
    assert a.match("https://t.co/AbCdEf123")
    assert not a.match("https://x.com/NASA")
    assert not a.match("https://example.com/NASA/status/2108563438901350773")


def test_match_short_historical_id():
    a = XTwitterAdapter(session=object())
    assert a.match("https://x.com/jack/status/20")
    assert a.resolve("https://x.com/jack/status/20").item_id == "tw-20"


def test_resolve_status_forms():
    a = XTwitterAdapter(session=object())
    for url in ("https://x.com/NASA/status/2108563438901350773",
                "https://twitter.com/i/web/status/2108563438901350773?s=20"):
        ref = a.resolve(url)
        assert ref.platform == "x_twitter" and ref.item_id == "tw-2108563438901350773"
        assert ref.extra["kind"] == "tweet"


def test_resolve_name_needs_search():
    a = XTwitterAdapter(session=object())
    with pytest.raises(SearchNeeded):
        a.resolve("一条推文标题")


def test_resolve_tco_expands(monkeypatch):
    class R:
        url = "https://x.com/SpaceX/status/2109957980011552839"

        def close(self):
            pass

    class S:
        headers = {}
        def get(self, url, **kw):
            return R()
    a = XTwitterAdapter(session=S())
    ref = a.resolve("https://t.co/abc123")
    assert ref.item_id == "tw-2109957980011552839"


def test_list_items_and_contract():
    a = XTwitterAdapter(session=object())
    ref = a.resolve("https://x.com/NASA/status/2108563438901350773")
    assert a.list_items(ref) == [ref]
    assert run_contract(a) == []


FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "x_twitter")


def _fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class _JsonResp:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ApiSession:
    def __init__(self, resp):
        self._resp = resp
        self.headers = {}

    def get(self, url, **kw):
        return self._resp


def test_parse_tweet_photo():
    a = XTwitterAdapter(session=_ApiSession(_JsonResp(_fx("tweet_photo.json"))))
    ref = a.resolve("https://x.com/NASA/status/2108563438901350773")
    meta = a.fetch_meta(ref)
    assert "There are no adjectives" in meta.title
    assert meta.author == "NASA"
    assert meta.published
    res = a.fetch_content(ref)
    assert res.kind == "document" and res.status == "ok"
    assert any("Houston We Have a" in b["text"] for b in res.blocks)
    assert res.images and res.images[0].startswith("https://pbs.twimg.com/media/")


def test_video_variant_prefers_720p_mp4():
    a = XTwitterAdapter(session=_ApiSession(_JsonResp(_fx("tweet_video.json"))))
    ref = a.resolve("https://x.com/SpaceX/status/2109957980011552839")
    assert a._data(ref)["video_url"].endswith("1280x720/PY83zJ1WKRFOQwzC.mp4?tag=29")
    assert a.fetch_media_url(ref, media="audio") == a._data(ref)["video_url"]


def test_video_transcribed_block(monkeypatch):
    a = XTwitterAdapter(session=_ApiSession(_JsonResp(_fx("tweet_video.json"))))
    ref = a.resolve("https://x.com/SpaceX/status/2109957980011552839")
    import transcriber
    monkeypatch.setattr(transcriber, "transcribe_url",
                        lambda url, **kw: [{"start": 0.0, "end": 2.0, "text": "Falcon has landed"}])
    res = a.fetch_content(ref)
    assert any(b["heading"] == "视频口播内容" and "Falcon has landed" in b["text"]
               for b in res.blocks)
    assert res.video_lines and res.video_lines[0]["text"] == "Falcon has landed"   # B2：片段传递
    assert res.video_duration > 0                                                  # B2：视频时长


def test_video_transcribe_failure_note(monkeypatch):
    a = XTwitterAdapter(session=_ApiSession(_JsonResp(_fx("tweet_video.json"))))
    ref = a.resolve("https://x.com/SpaceX/status/2109957980011552839")
    import transcriber
    def boom(*a, **k):
        raise RuntimeError("no whisper")
    monkeypatch.setattr(transcriber, "transcribe_url", boom)
    res = a.fetch_content(ref)
    assert any(b["heading"] == "视频口播内容" and "转写失败" in b["text"] for b in res.blocks)


def test_404_readable():
    a = XTwitterAdapter(session=_ApiSession(_JsonResp(text="<html>Not Found</html>", status=404)))
    ref = a.resolve("https://x.com/NASA/status/2108563438901350773")
    res = a.fetch_content(ref)
    assert res.status == "empty" and "不存在" in res.reason


def test_match_and_resolve_home():
    a = XTwitterAdapter(session=object())
    assert a.match("https://x.com/home") is True
    assert a.match("https://twitter.com/home") is True
    assert a.match("https://x.com/NASA/status/2108563438901350773") is True
    ref = a.resolve("https://x.com/home")
    assert ref.extra["kind"] == "home_timeline"
    assert ref.item_id == "self-home"
    assert ref.url == "https://x.com/home"


def _home_payload():
    return {"data": {"home": {"home_timeline_urt": {"instructions": [
        {"type": "TimelineAddEntries", "entries": [
            {"entryId": "tweet-111", "content": {"itemContent": {"tweet_results": {
                "result": {"rest_id": "111", "legacy": {"full_text": "第一条\n详情"}}}}}},
            {"entryId": "tweet-111", "content": {"itemContent": {"tweet_results": {
                "result": {"rest_id": "111", "legacy": {"full_text": "重复"}}}}}},
            {"entryId": "cursor-bottom-1", "content": {"value": "abc"}},
            {"entryId": "tweet-222", "content": {"itemContent": {"tweet_results": {
                "result": {"__typename": "TweetWithVisibilityResults", "tweet":
                           {"rest_id": "222", "legacy": {"full_text": "第二条"}}}}}}},
        ]}
    ]}}}}


def test_parse_home_timeline_payload():
    out = parse_home_timeline_payload([_home_payload()])
    assert [o["tweet_id"] for o in out] == ["111", "222"]
    assert out[0]["title"].startswith("第一条")
    assert out[1]["text"] == "第二条"          # 可见性包装兼容


def test_list_items_home_timeline(monkeypatch):
    import providers.x_twitter as xt
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

        def scroll_wheel(self, n):
            calls["wheel"] = n

        def tap_json(self, pattern, action=None, **kw):
            calls["pattern"] = pattern
            calls["min_results"] = kw.get("min_results")
            calls["settle_ms"] = kw.get("settle_ms")
            if action:
                action()
            return [_home_payload()]

    monkeypatch.setattr(xt.browserfetch, "BrowserSession", FakeSession)
    a = xt.XTwitterAdapter(session=object())
    ref = a.resolve("https://x.com/home")
    items = a.list_items(ref)
    assert calls["platform"] == "x_twitter"
    assert calls["cookie"] == "auth_token"
    assert calls["pattern"] == r"/graphql/[^/]+/HomeTimeline"
    assert calls["min_results"] == 0
    assert calls["wheel"] == 1
    assert len(items) == 2
    assert items[0].url == "https://x.com/i/status/111"
    assert items[0].sub_id == "1" and items[1].sub_id == "2"


def test_list_items_home_not_logged_in(monkeypatch):
    import providers.x_twitter as xt

    class FakeSession:
        def __init__(self, platform, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def has_cookie(self, name):
            return False

    monkeypatch.setattr(xt.browserfetch, "BrowserSession", FakeSession)
    a = xt.XTwitterAdapter(session=object())
    ref = a.resolve("https://x.com/home")
    try:
        a.list_items(ref)
        assert False
    except ValueError as e:
        assert "未登录" in str(e)


def test_video_hallucination_is_not_written(monkeypatch):
    """实测：游戏录屏被写成 Thanks for watching ×2，必须丢弃并标注"""
    a = XTwitterAdapter(session=_ApiSession(_JsonResp(_fx("tweet_video.json"))))
    ref = a.resolve("https://x.com/SpaceX/status/2109957980011552839")
    import transcriber

    def fake(url, **kw):
        kw.get("stats", {}).update({"no_speech_ratio": 0.95})
        return [{"start": 0.0, "end": 2.0,
                 "text": "Thanks for watching! If you enjoyed this video, please like and subscribe!"}] * 2

    monkeypatch.setattr(transcriber, "transcribe_url", fake)
    res = a.fetch_content(ref)
    block = [b for b in res.blocks if b["heading"] == "视频口播内容"][0]
    assert "幻觉" in block["text"]
    assert res.video_lines == []
