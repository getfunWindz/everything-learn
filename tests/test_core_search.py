import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

from core import search


def test_available_covers_all_platforms():
    caps = search.available()
    assert caps["bilibili"] == "native"
    assert caps["youtube"] == "api"
    assert caps["zhihu"] == "browser"
    assert caps["xiaohongshu"] == "browser"
    assert "x_twitter" in caps


def test_anchor_items_dedup_filter_and_title():
    anchors = [
        {"href": "https://www.zhihu.com/question/123", "own": "如何评价 DeepSeek V4.1？"},
        {"href": "https://www.zhihu.com/question/123", "own": "重复"},
        {"href": "https://www.zhihu.com/people/x", "own": "导航"},
        {"href": "https://zhuanlan.zhihu.com/p/456", "own": "专栏标题\n作者A"},
    ]
    hits = search._anchor_items(anchors, r"zhihu\.com/(?:question/\d+|p/\d+)", 10, "zhihu")
    assert len(hits) == 2
    assert hits[0]["title"].startswith("如何评价")
    assert hits[1]["title"] == "专栏标题" and hits[1]["author"] == "作者A"


def test_anchor_items_extracts_ted_duration():
    anchors = [{"href": "https://www.ted.com/talks/some_talk",
                "own": "09:02\nAI brain fry is real\nJULIE BEDARD"}]
    hits = search._anchor_items(anchors, r"ted\.com/talks/[a-z0-9_]+$", 5, "ted",
                                title_mode="first")
    assert hits[0]["duration_text"] == "09:02"
    assert hits[0]["title"] == "AI brain fry is real"


def test_youtube_hits_shapes_entries():
    info = {"entries": [{"id": "abc", "title": "T", "uploader": "U", "duration": 95},
                        {"id": None, "title": "bad"}]}
    hits = search._youtube_hits(info, 5)
    assert hits == [{"platform": "youtube", "title": "T",
                     "url": "https://www.youtube.com/watch?v=abc",
                     "author": "U", "duration": 95, "duration_text": "1:35"}]


def test_itunes_hits_shapes_episodes():
    payload = {"results": [{"trackName": "EP", "collectionName": "Show",
                            "trackViewUrl": "https://podcasts.apple.com/x",
                            "trackTimeMillis": 820000}]}
    hits = search._itunes_hits(payload, 5)
    assert hits[0]["title"] == "EP" and hits[0]["author"] == "Show"
    assert hits[0]["duration"] == 820 and hits[0]["duration_text"] == "13:40"


def test_search_unsupported_platform():
    with pytest.raises(search.SearchUnavailable):
        search.search("twitter_old", "x")


def test_search_dispatch_browser(monkeypatch):
    calls = {}

    def fake_browser(platform, keyword, limit):
        calls.update(platform=platform, keyword=keyword, limit=limit)
        return [{"platform": platform, "title": "T", "url": "u"}]

    monkeypatch.setattr(search, "_browser_search", fake_browser)
    hits = search.search("zhihu", "DeepSeek", limit=3)
    assert hits[0]["platform"] == "zhihu"
    assert calls == {"platform": "zhihu", "keyword": "DeepSeek", "limit": 3}


def test_search_wraps_errors_as_readable(monkeypatch):
    monkeypatch.setattr(search, "_browser_search",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(search.SearchUnavailable) as e:
        search.search("weibo", "x")
    assert "搜索失败" in str(e.value)


def test_anchor_items_weibo_uses_first_text_line():
    anchors = [{"href": "https://weibo.com/123/AbCd",
                "own": "今天 17:04 来自 HUAWEI P60",
                "card": ("今天 17:04 来自 HUAWEI P60" + chr(10)
                         + "DeepSeek V4.1 Flash 刚刚发布，成本降到每任务 0.2 美元" + chr(10)
                         + "赞 12")}]
    hits = search._anchor_items(anchors, r"weibo\.com/\d+/\w+", 5, "weibo",
                                title_mode="first")
    assert hits[0]["title"].startswith("DeepSeek V4.1 Flash")
    assert hits[0]["duration_text"] == ""
