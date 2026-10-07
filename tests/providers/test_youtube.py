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
