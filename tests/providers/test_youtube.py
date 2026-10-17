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
    assert res.provenance == "human" and res.language == "en"   # subtitles → 人工


def test_fetch_content_auto_labeled(monkeypatch):
    info = {"duration": 14, "subtitles": {},
            "automatic_captions": {"en": [{"ext": "json3", "url": "http://x/en.json3"}]}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    monkeypatch.setattr(a, "_download",
                        lambda url: open(os.path.join(FIXT, "auto_rolling.json3"), encoding="utf-8").read())
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "ok" and res.label == "字幕(自动)" and len(res.segments) == 12
    assert res.provenance == "auto" and res.language == "en"   # automatic_captions → auto


def test_fetch_content_short_captions_not_suspect(monkeypatch):
    """M13 §8.3：适配器内「段数/覆盖不足 → suspect」启发式已退役；取回即 ok，校验交 core"""
    short = '{"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]}'
    info = {"duration": 600, "subtitles": {"en": [{"ext": "json3", "url": "http://x/s.json3"}]},
            "automatic_captions": {}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    monkeypatch.setattr(a, "_download", lambda url: short)
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "ok" and len(res.segments) == 1
    assert res.provenance == "human"


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
    assert res.provenance == "human"


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


def test_fetch_content_falls_back_to_next_language(monkeypatch):
    """首选语言轨 429 → 依次回退到链上其它语言（如 zh-Hans 挂→en 可用），而非直接放弃"""
    info = {"duration": 14, "subtitles": {},
            "automatic_captions": {"zh-Hans": [{"ext": "json3", "url": "http://x/zh.json3"}],
                                   "en": [{"ext": "json3", "url": "http://x/en.json3"}]}}
    a = YouTubeAdapter()
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    calls = []

    def dl(url):
        calls.append(url)
        if url.endswith("zh.json3"):
            raise RuntimeError("HTTP 429")
        return open(os.path.join(FIXT, "auto_rolling.json3"), encoding="utf-8").read()

    monkeypatch.setattr(a, "_download", dl)
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert res.status == "ok"
    assert calls == ["http://x/zh.json3", "http://x/en.json3"]
    assert res.label == "字幕(自动)"
    assert res.provenance == "auto" and res.language == "en"   # 回退后语言取所选轨道


def test_adapter_declares_has_video():
    """M13 §8.3：YouTube 声明画面层能力；language_hint 不设（自动检测）"""
    a = YouTubeAdapter()
    assert a.has_video is True
    assert not hasattr(a, "language_hint") or getattr(a, "language_hint", None) is None


def test_fetch_content_user_lang_selects_track_and_language(monkeypatch):
    """--lang 指定轨道 → language 记录实际所选轨道语言"""
    info = {"duration": 24,
            "subtitles": {"ja": [{"ext": "vtt", "url": "http://x/ja.vtt"}],
                          "en": [{"ext": "vtt", "url": "http://x/en.vtt"}]},
            "automatic_captions": {}}
    a = YouTubeAdapter()
    a.lang = "ja"
    monkeypatch.setattr(a, "_extract", lambda *args, **kw: info)
    called = {}
    def dl(url):
        called["url"] = url
        return open(os.path.join(FIXT, "manual.vtt"), encoding="utf-8").read()
    monkeypatch.setattr(a, "_download", dl)
    res = a.fetch_content(a.resolve("kCc8FmEb1nY"))
    assert called["url"] == "http://x/ja.vtt"
    assert res.provenance == "human" and res.language == "ja"


def test_caption_candidates_provenance_order():
    """人工轨先于自动轨进入候选；来源标注正确（T19 映射核心）"""
    subs = {"en": [{"ext": "vtt", "url": "http://x/m.vtt"}]}
    autos = {"en": [{"ext": "json3", "url": "http://x/a.json3"}]}
    out = YouTubeAdapter()._caption_candidates(subs, autos, ["en"])
    assert [(label, prov, lang) for _e, label, prov, lang in out] == [
        ("", "human", "en"), ("字幕(自动)", "auto", "en")]


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


def test_pick_video_url_lowest_skips_hls_manifest():
    """HLS 清单条目（protocol=m3u8）不得作为视频直链：低档位必须落到直接 MP4"""
    fmts = [
        {"format_id": "hls-144", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 144, "protocol": "m3u8_native",
         "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/x/index.m3u8"},
        {"format_id": "160", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 360, "url": "http://direct/v360"},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 720, "url": "http://direct/v720"}]
    assert youtube.pick_video_url(fmts, lowest=True) == "http://direct/v360"


def test_pick_video_url_best_skips_hls_manifest():
    """HLS 清单条目（URL 含 hls_playlist/manifest，无 protocol 字段）同样不得入选"""
    fmts = [
        {"format_id": "hls-2160", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 2160,
         "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/x/index.m3u8"},
        {"format_id": "160", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 360, "url": "http://direct/v360"},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 720, "url": "http://direct/v720"}]
    assert youtube.pick_video_url(fmts, lowest=False) == "http://direct/v720"


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


def test_fetch_media_url_video_fallback_excludes_hls(monkeypatch):
    """formats 仅有 HLS 清单 → 兜底选择器必须带 protocol!*=m3u8，且返回直接 MP4"""
    only_hls = {"formats": [
        {"format_id": "hls-144", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 144, "protocol": "m3u8_native",
         "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/x/index.m3u8"}]}
    a = YouTubeAdapter()
    opts = []

    def fake_extract(url, **kw):
        opts.append(kw)
        return {"url": "http://direct/v.mp4"} if "format" in kw else only_hls

    monkeypatch.setattr(a, "_extract", fake_extract)
    ref = a.resolve("kCc8FmEb1nY")
    assert a.fetch_media_url(ref, media="video", lowest=True) == "http://direct/v.mp4"
    assert opts[-1]["format"] == "worst[ext=mp4][protocol!*=m3u8]/worst[protocol!*=m3u8]"


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


def test_download_waits_throttle(monkeypatch):
    """字幕下载前按 youtube 缓速（反爬基座）"""
    import requests as req_mod
    from core import throttle as throttle_mod
    waits, order = [], []
    monkeypatch.setattr(throttle_mod, "wait", lambda p: waits.append(p))

    class R:
        status_code, encoding, text = 200, "utf-8", "ok"
        def raise_for_status(self): pass

    def fake_get(url, headers=None, timeout=None):
        order.append("get")
        return R()
    monkeypatch.setattr(req_mod, "get", fake_get)
    assert YouTubeAdapter()._download("http://x/sub") == "ok"
    assert waits == ["youtube"] and order == ["get"]


def test_match_and_resolve_channel():
    a = YouTubeAdapter()
    for url in ("https://www.youtube.com/@NASA/videos",
                "https://www.youtube.com/@NASA",
                "https://www.youtube.com/channel/UC1234567890abcdefghij",
                "https://www.youtube.com/c/SomeChannel",
                "https://www.youtube.com/user/SomeUser"):
        assert a.match(url), url
    ref = a.resolve("https://www.youtube.com/@NASA")
    assert ref.extra.get("channel") is True


def test_channel_list_items(monkeypatch):
    a = YouTubeAdapter()
    flat = {"entries": [
        {"id": "vid001", "title": "第一个视频", "duration": 300},
        {"id": "vid002", "title": "第二个视频", "duration": 120},
    ]}
    captured = {}

    def fake_extract(self, url, **kw):
        captured["url"] = url
        return flat

    monkeypatch.setattr(YouTubeAdapter, "_extract", fake_extract)
    ref = a.resolve("https://www.youtube.com/@NASA")
    items = a.list_items(ref)
    assert [r.item_id for r in items] == ["vid001", "vid002"]
    assert captured["url"].endswith("/videos")


def test_fetch_media_urls_audio_diverse_hosts(monkeypatch):
    """媒体候选列表：主选与 fetch_media_url 一致 + 去重 + 主机多样性（抗单点网络故障）"""
    fmts = [
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128,
         "url": "https://a.googlevideo.com/1"},
        {"format_id": "140b", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128,
         "url": "https://a.googlevideo.com/1"},                       # 重复 URL → 去重
        {"format_id": "140c", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128,
         "url": "https://b.googlevideo.com/2"},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 160,
         "url": "https://c.googlevideo.com/3"},
    ]
    a = YouTubeAdapter()
    monkeypatch.setattr(YouTubeAdapter, "_info", lambda self, ref: {"formats": fmts})
    ref = a.resolve("kCc8FmEb1nY")
    urls = a.fetch_media_urls(ref, media="audio")
    assert urls[0] == "https://a.googlevideo.com/1"        # 与 fetch_media_url 主选一致
    assert urls.count("https://a.googlevideo.com/1") == 1  # 去重
    assert len(urls) == 3
    assert len({u.split("/")[2] for u in urls}) == 3       # a/b/c 三主机多样性


def test_fetch_media_urls_video_lowest_filters_hls(monkeypatch):
    """视频候选：排除 HLS/manifest/storyboard；lowest 升序且主机多样化"""
    fmts = [
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 720, "url": "https://a.googlevideo.com/h720"},
        {"format_id": "135", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 480, "url": "https://a.googlevideo.com/h480"},
        {"format_id": "160", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 360, "url": "https://b.googlevideo.com/h360"},
        {"format_id": "sb", "ext": "mhtml", "vcodec": "none", "acodec": "none",
         "height": 27, "url": "https://b.googlevideo.com/sb"},
        {"format_id": "hls", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 240, "protocol": "m3u8_native",
         "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/x/index.m3u8"},
    ]
    a = YouTubeAdapter()
    monkeypatch.setattr(YouTubeAdapter, "_info", lambda self, ref: {"formats": fmts})
    ref = a.resolve("kCc8FmEb1nY")
    urls = a.fetch_media_urls(ref, media="video", lowest=True)
    assert urls[0] == "https://b.googlevideo.com/h360"     # 与 pick_video_url(lowest) 一致
    assert all("m3u8" not in u and "manifest" not in u for u in urls)
    assert len(urls) == 3


def test_ydl_opts_have_socket_timeout():
    """实测：yt-dlp 提取在弱网下 28 分钟无返回；必须有 socket 超时"""
    a = YouTubeAdapter()
    opts = a._ydl_opts()
    assert opts["socket_timeout"] >= 10
    assert opts["skip_download"] is True


def test_media_budget_is_relaxed():
    """实测：googlevideo 下载常失败；预算放宽到 45s，失败即回退字幕"""
    a = YouTubeAdapter()
    assert 0 < a.media_budget_sec <= 60
