"""适配器契约测试：M0 起所有适配器（含自进化生成的）都必须通过"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from ._fakes import FakeAdapter
from .harness import run_contract
from core.adapter import SourceAdapter, SearchNeeded  # noqa: F401  （SearchNeeded 供适配器使用）

_FIXT = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def test_contract_accepts_protocol_shape():
    assert run_contract(FakeAdapter()) == []
    assert isinstance(FakeAdapter(), SourceAdapter)


def test_contract_rejects_incomplete():
    class Bad:
        pass
    problems = run_contract(Bad())
    assert any("name" in p for p in problems)
    assert any("kinds" in p for p in problems)
    assert any("match" in p for p in problems)


def test_contract_requires_media_channel_kwargs():
    """统一多模态复检通道：fetch_media_url 必须接受 media/lowest（core 用它们取画面/音轨）"""
    class NoKwargs(FakeAdapter):
        def fetch_media_url(self, ref):        # 缺 media/lowest → 通道不可用
            return None
    problems = run_contract(NoKwargs())
    assert any("fetch_media_url" in p for p in problems)


def test_timeline_adapters_declare_source_flags():
    """M13 §12：timeline 适配器显式声明 has_video / language_hint（自动检测则缺省）"""
    from providers.bilibili import BilibiliAdapter
    from providers.douyin import DouyinAdapter
    from providers.podcast import PodcastAdapter
    from providers.ted import TedAdapter
    from providers.youtube import YouTubeAdapter
    assert BilibiliAdapter.language_hint == "zh" and BilibiliAdapter.has_video is True
    assert DouyinAdapter.language_hint == "zh" and DouyinAdapter.has_video is True
    assert TedAdapter.has_video is True and getattr(TedAdapter, "language_hint", None) is None
    assert YouTubeAdapter.has_video is True and getattr(YouTubeAdapter, "language_hint", None) is None
    assert PodcastAdapter.has_video is False


# ---------------- D1：逐平台 provenance 契约（T18-20 评审修复） ----------------

def test_provenance_bilibili_follows_selected_track():
    """bilibili：provenance 由实际所选字幕轨道决定（覆盖 human/auto 两例）"""
    from core.models import ItemRef
    from providers.bilibili import BilibiliAdapter, SubtitleLine

    class FakeClient:
        def __init__(self, provenance):
            self.provenance = provenance

        def fetch_subtitle(self, bvid, cid, duration=None, lang=None):
            return [SubtitleLine(0.0, 1.0, "内容")], self.provenance

    ref = ItemRef(platform="bilibili", item_id="BV1GJ411x7h7", sub_id="1",
                  duration=10, extra={"cid": 1001})
    assert BilibiliAdapter(client=FakeClient("human")).fetch_content(ref).provenance == "human"
    assert BilibiliAdapter(client=FakeClient("auto")).fetch_content(ref).provenance == "auto"


def test_provenance_youtube_manual_is_human_auto_is_auto(monkeypatch):
    """youtube：manual 轨 → human；automatic_captions → auto"""
    from core.models import ItemRef
    from providers.youtube import YouTubeAdapter

    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"
    json3 = '{"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "hi"}]}]}'
    ref = ItemRef(platform="youtube", item_id="kCc8FmEb1nY", sub_id="1", duration=10)

    manual = YouTubeAdapter()
    monkeypatch.setattr(manual, "_extract",
                        lambda *a, **k: {"duration": 10,
                                         "subtitles": {"en": [{"ext": "vtt",
                                                                "url": "http://x/m.vtt"}]},
                                         "automatic_captions": {}})
    monkeypatch.setattr(manual, "_download", lambda url: vtt)
    assert manual.fetch_content(ref).provenance == "human"

    auto = YouTubeAdapter()
    monkeypatch.setattr(auto, "_extract",
                        lambda *a, **k: {"duration": 10, "subtitles": {},
                                         "automatic_captions": {"en": [{"ext": "json3",
                                                                          "url": "http://x/a.json3"}]}})
    monkeypatch.setattr(auto, "_download", lambda url: json3)
    assert auto.fetch_content(ref).provenance == "auto"


def test_provenance_ted_is_official():
    """ted：官网字幕一律 official"""
    import json as _json
    from providers.ted import TedAdapter

    class Resp:
        def __init__(self, code, text):
            self.status_code, self.text = code, text

        def json(self):
            return _json.loads(self.text)

    class FakeSession:
        headers = {}

        def get(self, url, **kw):
            if "/lang/" in url:
                return Resp(200, _json.dumps(
                    {"captions": [{"content": "内容", "startTime": 0, "duration": 1000}]}))
            vd = _json.load(open(os.path.join(_FIXT, "ted", "talk_2458.json"), encoding="utf-8"))
            return Resp(200, '<script id="__NEXT_DATA__">' + _json.dumps(vd, ensure_ascii=False)
                        + '</script>')

    a = TedAdapter(session=FakeSession())
    ref = a.resolve("https://www.ted.com/talks/tim_urban_inside_the_mind_of_a_master_procrastinator")
    res = a.fetch_content(ref)
    assert res.status == "ok" and res.provenance == "official"


def test_provenance_podcast_unknown_or_none(monkeypatch):
    """podcast：有第三方转写稿 → unknown；无转写稿 → none（将走 Whisper）"""
    from providers.podcast import PodcastAdapter

    a = PodcastAdapter()
    xml = open(os.path.join(_FIXT, "podcast", "sample_feed.xml"), encoding="utf-8").read()
    monkeypatch.setattr(a, "_fetch_feed", lambda url: xml)
    items = a.list_items(a.resolve("https://example.com/show.rss"))
    monkeypatch.setattr(a, "_download",
                        lambda url: "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nx\n")
    assert a.fetch_content(items[0]).provenance == "unknown"      # Episode One 有转写稿
    res = a.fetch_content(items[1])
    assert res.status == "empty" and res.provenance == "none"     # Episode Two 无转写稿


def test_provenance_douyin_is_none_on_both_paths(monkeypatch):
    """douyin：无平台字幕 → 两条返回路径（提取失败/无字幕）都显式 provenance=none"""
    from providers.douyin import DouyinAdapter, DouyinError

    a = DouyinAdapter(session=object())
    ref = a.resolve("https://www.douyin.com/video/7279377986187431202")

    monkeypatch.setattr(DouyinAdapter, "_info", lambda self, r: {})
    assert a.fetch_content(ref).provenance == "none"              # 正常路径

    def boom(self, r):
        raise DouyinError("cookie 失效")

    monkeypatch.setattr(DouyinAdapter, "_info", boom)
    res = a.fetch_content(ref)
    assert res.status == "empty" and res.provenance == "none"     # 提取失败路径
