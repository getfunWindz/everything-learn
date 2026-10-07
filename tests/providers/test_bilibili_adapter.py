"""B站适配器测试：契约 + match/resolve/list_items/fetch_* 映射"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from providers.bilibili import (BilibiliAdapter, VideoInfo, SubtitleResult, SubtitleLine,
                                SearchNeeded)
from tests.contract.harness import run_contract

VIEW_DATA = {
    "bvid": "BV1GJ411x7h7", "aid": 170001, "title": "测试视频",
    "owner": {"name": "测试UP"}, "duration": 300, "pubdate": 1600000000, "desc": "简介",
    "stat": {"view": 1},
    "pages": [
        {"cid": 1001, "page": 1, "part": "P1 基础", "duration": 100},
        {"cid": 1002, "page": 2, "part": "P2 进阶", "duration": 200},
    ],
}


class FakeClient:
    def get_video_info(self, bvid="", aid=0):
        return VideoInfo(VIEW_DATA)

    def fetch_subtitle(self, bvid, cid, duration=None, lang=None, title="", desc=""):
        return SubtitleResult([SubtitleLine(0.0, 1.0, "你好")], "ok", "")

    def get_video_url(self, bvid, cid, lowest=False):
        return "http://video/low" if lowest else "http://video/high"

    def get_audio_url(self, bvid, cid):
        return "http://audio/stream"


def _adapter():
    return BilibiliAdapter(client=FakeClient())


def test_contract_passes():
    assert run_contract(_adapter()) == []


def test_cookie_file_points_to_scripts_dir():
    """cookie 文件固定在 scripts/.bili_cookie（provider 搬到子目录后不能指向 providers/）"""
    from providers import bilibili
    assert os.path.basename(os.path.dirname(bilibili._COOKIE_FILE)) == "scripts"
    assert os.path.basename(bilibili._COOKIE_FILE) == ".bili_cookie"


def test_match():
    a = _adapter()
    assert a.match("https://www.bilibili.com/video/BV1GJ411x7h7") is True
    assert a.match("BV1GJ411x7h7") is True
    assert a.match("https://www.bilibili.com/video/av170001") is True
    assert a.match("https://youtube.com/watch?v=x") is False
    assert a.match("") is False


def test_resolve_link_with_page():
    ref = _adapter().resolve("https://www.bilibili.com/video/BV1GJ411x7h7/?p=2")
    assert ref.platform == "bilibili" and ref.item_id == "BV1GJ411x7h7" and ref.sub_id == "2"


def test_resolve_name_raises_search_needed():
    with pytest.raises(SearchNeeded):
        _adapter().resolve("某个关键词")


def test_list_items_expands_pages():
    a = _adapter()
    items = a.list_items(a.resolve("BV1GJ411x7h7"))
    assert len(items) == 2
    assert items[0].sub_id == "1" and items[0].extra["cid"] == 1001
    assert items[1].title == "P2 进阶" and items[1].duration == 200


def test_fetch_meta():
    a = _adapter()
    item = a.list_items(a.resolve("BV1GJ411x7h7"))[0]
    meta = a.fetch_meta(item)
    assert meta.author == "测试UP" and meta.title == "测试视频"  # 视频级标题（用于命名）
    assert meta.page_part == "P1 基础" and meta.page == "1"
    assert meta.stats["view"] == 1


def test_fetch_content_maps_subtitle_result():
    a = _adapter()
    item = a.list_items(a.resolve("BV1GJ411x7h7"))[0]
    res = a.fetch_content(item)
    assert res.kind == "timeline" and res.status == "ok"
    assert res.segments[0]["text"] == "你好" and res.segments[0]["start"] == 0.0


def test_fetch_media_url():
    a = _adapter()
    item = a.list_items(a.resolve("BV1GJ411x7h7"))[0]
    assert a.fetch_media_url(item) == "http://audio/stream"                       # 默认音频（Whisper）
    assert a.fetch_media_url(item, media="video") == "http://video/high"          # 画面流（复检抽帧）
    assert a.fetch_media_url(item, media="video", lowest=True) == "http://video/low"
