"""YouTube 适配器测试：路由/解析/懒加载/契约"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from providers import youtube
from providers.youtube import YouTubeAdapter, YtDlpMissing


def test_match():
    a = YouTubeAdapter()
    assert a.match("https://www.youtube.com/watch?v=kCc8FmEb1nY")
    assert a.match("https://youtu.be/kCc8FmEb1nY")
    assert a.match("https://www.youtube.com/shorts/abcdEFGHijk")
    assert a.match("https://www.youtube.com/playlist?list=PL123")
    assert a.match("kCc8FmEb1nY") is False       # 裸 ID 由 resolve 处理，不算 URL 路由
    assert a.match("https://www.bilibili.com/video/BV1GJ411x7h7") is False
    assert a.match("") is False


def test_resolve_forms():
    a = YouTubeAdapter()
    assert a.resolve("kCc8FmEb1nY").item_id == "kCc8FmEb1nY"
    r = a.resolve("https://youtu.be/kCc8FmEb1nY?t=10")
    assert r.platform == "youtube" and r.item_id == "kCc8FmEb1nY"
    r2 = a.resolve("https://www.youtube.com/watch?v=kCc8FmEb1nY&list=PL123")
    assert r2.item_id == "kCc8FmEb1nY" and not r2.extra.get("playlist")  # 单视频优先
    pl = a.resolve("https://www.youtube.com/playlist?list=PL123")
    assert pl.extra["playlist"] is True and pl.item_id == "PL123"


def test_resolve_invalid_raises():
    with pytest.raises(ValueError):
        YouTubeAdapter().resolve("https://example.com/x")


def test_ytdlp_missing_guidance(monkeypatch):
    monkeypatch.setattr(youtube, "_yt_dlp_module", None)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    with pytest.raises(YtDlpMissing):
        youtube._load_ytdlp()


def test_contract():
    from tests.contract.harness import run_contract
    assert run_contract(YouTubeAdapter()) == []


# ---------------- T3：字幕解析与提取 ----------------

FIXT = os.path.join(os.path.dirname(__file__), "..", "fixtures", "youtube")


def test_parse_json3_rolling_dedup():
    segs = youtube.parse_json3(open(os.path.join(FIXT, "auto_rolling.json3"), encoding="utf-8").read())
    texts = [s["text"] for s in segs]
    assert len(texts) == 12
    assert texts[0] == "hi everyone" and texts[1] == "so by now" and texts[2] == "you have probably"
    joined = " ".join(texts)
    assert joined.count("hi everyone") == 1   # 滚动重复被去重
    assert joined.count("probably") == 1
    assert all(segs[i]["start"] <= segs[i + 1]["start"] for i in range(len(segs) - 1))


def test_parse_vtt():
    segs = youtube.parse_vtt(open(os.path.join(FIXT, "manual.vtt"), encoding="utf-8").read())
    assert len(segs) == 12
    assert segs[0]["text"] == "Hello and welcome."
    assert abs(segs[1]["start"] - 3.0) < 0.01


def test_fetch_content_manual_ok(monkeypatch):
    info = {"duration": 24, "subtitles": {"en": [{"ext": "vtt", "url": "http://x/en.vtt"}]},
            "automatic_captions": {}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    monkeypatch.setattr(a, "_download",
                        lambda url: open(os.path.join(FIXT, "manual.vtt"), encoding="utf-8").read())
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "ok" and res.label == "" and len(res.segments) == 12


def test_fetch_content_auto_labeled(monkeypatch):
    info = {"duration": 14, "subtitles": {},
            "automatic_captions": {"en": [{"ext": "json3", "url": "http://x/en.json3"}]}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    monkeypatch.setattr(a, "_download",
                        lambda url: open(os.path.join(FIXT, "auto_rolling.json3"), encoding="utf-8").read())
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "ok" and res.label == "字幕(自动)" and len(res.segments) == 12


def test_fetch_content_prefers_manual_over_auto(monkeypatch):
    info = {"duration": 24, "subtitles": {"en": [{"ext": "vtt", "url": "http://x/m.vtt"}]},
            "automatic_captions": {"en": [{"ext": "json3", "url": "http://x/a.json3"}]}}
    a = YouTubeAdapter()
    called = {}
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    def dl(url):
        called["url"] = url
        return open(os.path.join(FIXT, "manual.vtt"), encoding="utf-8").read()
    monkeypatch.setattr(a, "_download", dl)
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert called["url"] == "http://x/m.vtt" and res.label == ""


def test_fetch_content_none_returns_empty(monkeypatch):
    info = {"duration": 100, "subtitles": {}, "automatic_captions": {}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "empty"


def test_fetch_content_download_error_falls_back_empty(monkeypatch):
    info = {"duration": 24, "subtitles": {"en": [{"ext": "vtt", "url": "http://x/m.vtt"}]},
            "automatic_captions": {}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    monkeypatch.setattr(a, "_download",
                        lambda url: (_ for _ in ()).throw(RuntimeError("boom")))
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "empty" and "下载失败" in res.reason


# ---------------- T4：媒体直链 ----------------

def test_pick_audio_best_m4a():
    fmts = [{"format_id": "140", "ext": "m4a", "vcodec": "none", "abr": 129, "url": "a"},
            {"format_id": "251", "ext": "webm", "vcodec": "none", "abr": 160, "url": "b"},
            {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "url": "c"}]
    assert youtube.pick_audio_url(fmts) == "a"   # 优先 m4a（Whisper 兼容性好）


def test_pick_video_lowest_excludes_storyboard():
    fmts = [{"format_id": "sb3", "ext": "mhtml", "vcodec": "none", "acodec": "none",
             "height": 27, "url": "sb"},
            {"format_id": "160", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
             "height": 144, "url": "v144"},
            {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
             "height": 1080, "url": "v1080"}]
    assert youtube.pick_video_url(fmts, lowest=True) == "v144"     # 排除 storyboard
    assert youtube.pick_video_url(fmts, lowest=False) == "v1080"


def test_fetch_media_url_returns_selected(monkeypatch):
    info = {"formats": [
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "abr": 129, "url": "http://a/1"},
        {"format_id": "160", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 144, "url": "http://v/1"}]}
    a = YouTubeAdapter()
    calls = {"n": 0}
    def fake_extract(*args, **kw):
        calls["n"] += 1
        return info
    monkeypatch.setattr(a, "_extract", fake_extract)
    ref = a.resolve("kCc8FmEb1nY")
    assert a.fetch_media_url(ref, media="audio") == "http://a/1"
    assert a.fetch_media_url(ref, media="video", lowest=True) == "http://v/1"
    assert calls["n"] == 1   # 单次运行内复用同一份 info（避免重复提取）


def test_download_retries_on_429(monkeypatch):
    """字幕下载遇 429 限流 → 有界退避重试"""
    import requests as req_mod
    from core import retry as retry_mod
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
    calls = {"n": 0}
    class R:
        def __init__(self, code):
            self.status_code, self.encoding, self.text = code, "utf-8", "ok"
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")
    def fake_get(url, headers=None, timeout=None):
        calls["n"] += 1
        return R(429) if calls["n"] < 3 else R(200)
    monkeypatch.setattr(req_mod, "get", fake_get)
    assert YouTubeAdapter()._download("http://x/sub") == "ok"
    assert calls["n"] == 3
