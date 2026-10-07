"""播客适配器测试：路由/Apple 查找/RSS 解析/转写稿/媒体直链"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from providers import podcast
from providers.podcast import PodcastAdapter, parse_duration

FIXT = os.path.join(os.path.dirname(__file__), "..", "fixtures", "podcast")


# ---------------- T3：路由 / Apple / 时长 ----------------

def test_match_feed_urls():
    a = PodcastAdapter()
    assert a.match("https://lexfridman.com/feed/podcast/")
    assert a.match("https://feeds.acast.com/public/shows/xxx")
    assert a.match("https://example.com/show.rss")
    assert a.match("https://podcasts.apple.com/us/podcast/npr-news-now/id500005")
    assert a.match("https://www.bilibili.com/video/BV1GJ411x7h7") is False
    assert a.match("") is False


def test_resolve_apple_link(monkeypatch):
    monkeypatch.setattr(podcast, "_itunes_lookup_feed",
                        lambda pid: "https://feeds.npr.org/500005/podcast.xml")
    ref = PodcastAdapter().resolve("https://podcasts.apple.com/us/podcast/npr-news-now/id500005")
    assert ref.extra["feed"] == "https://feeds.npr.org/500005/podcast.xml"
    assert ref.item_id == "https://feeds.npr.org/500005/podcast.xml"


def test_resolve_feed_url():
    ref = PodcastAdapter().resolve("https://example.com/show.rss")
    assert ref.extra["feed"] == "https://example.com/show.rss"


def test_resolve_invalid_raises():
    with pytest.raises(ValueError):
        PodcastAdapter().resolve("https://example.com/plain-page")


def test_sniff_xml(monkeypatch):
    class R:
        status_code = 200
        headers = {"content-type": "application/rss+xml; charset=UTF-8"}
        text = '<?xml version="1.0"?><rss>'
    import requests as req
    monkeypatch.setattr(req, "get", lambda url, headers=None, timeout=None: R())
    assert PodcastAdapter().sniff("https://no-marker.example/thing") is True


def test_duration_parser():
    assert parse_duration("280") == 280
    assert parse_duration("4:40") == 280
    assert parse_duration("5:21:57") == 19317
    assert parse_duration("") == 0
    assert parse_duration("abc") == 0


# ---------------- T4：RSS 解析 ----------------

def _adapter_with_fixture(monkeypatch):
    a = PodcastAdapter()
    xml = open(os.path.join(FIXT, "sample_feed.xml"), encoding="utf-8").read()
    monkeypatch.setattr(a, "_fetch_feed", lambda url: xml)
    return a


def test_list_items_from_fixture(monkeypatch):
    a = _adapter_with_fixture(monkeypatch)
    items = a.list_items(a.resolve("https://example.com/show.rss"))
    assert len(items) == 3
    assert items[0].title == "Episode One" and items[0].duration == 19317
    assert items[1].duration == 280
    assert items[1].extra["enclosure"] == "https://media.example/ep2.mp3"
    assert items[2].duration == 280
    assert items[0].extra["transcript_url"] == "https://media.example/ep1.vtt"


def test_fetch_meta_from_listed_item(monkeypatch):
    a = _adapter_with_fixture(monkeypatch)
    items = a.list_items(a.resolve("https://example.com/show.rss"))
    meta = a.fetch_meta(items[0])
    assert meta.title == "测试播客" and meta.author == "测试主播"
    assert meta.page_part == "Episode One" and meta.duration == 19317


# ---------------- T5：转写稿与媒体直链 ----------------

def _vtt_covering(duration_sec: float, n: int = 12) -> str:
    def fmt(x):
        h = int(x // 3600)
        m = int((x % 3600) // 60)
        return "%02d:%02d:%06.3f" % (h, m, x % 60)
    step = duration_sec / n
    seg = []
    for i in range(n):
        s, e = i * step, i * step + step * 0.9
        seg.append(f"{fmt(s)} --> {fmt(e)}\nline {i}")
    return "WEBVTT\n\n" + "\n\n".join(seg) + "\n"


def test_fetch_content_with_transcript(monkeypatch):
    a = _adapter_with_fixture(monkeypatch)
    items = a.list_items(a.resolve("https://example.com/show.rss"))
    monkeypatch.setattr(a, "_download", lambda url: _vtt_covering(items[0].duration))
    res = a.fetch_content(items[0])
    assert res.status == "ok" and len(res.segments) == 12
    assert res.label == "字幕(转写稿)"


def test_fetch_content_without_transcript_returns_empty(monkeypatch):
    a = _adapter_with_fixture(monkeypatch)
    items = a.list_items(a.resolve("https://example.com/show.rss"))
    res = a.fetch_content(items[1])          # Episode Two 无 transcript
    assert res.status == "empty" and "转写稿" in res.reason


def test_fetch_media_url_returns_enclosure(monkeypatch):
    a = _adapter_with_fixture(monkeypatch)
    items = a.list_items(a.resolve("https://example.com/show.rss"))
    assert a.fetch_media_url(items[0], media="audio") == "https://media.example/ep1.m4a"
    assert a.fetch_media_url(items[0], media="video") is None


def test_contract():
    from tests.contract.harness import run_contract
    assert run_contract(PodcastAdapter()) == []
